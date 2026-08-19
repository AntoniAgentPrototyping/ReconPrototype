"""Where a run's output files end up.

**Why write-then-upload rather than a second writer.** The obvious reading of
"the worker puts artifacts in blob storage while the CLI puts them on disk" is
that the worker gets its own writer. It must not. `finance_template.write_workbook`
is the code path that produces the deliverable the team invoices from, and a
second implementation of it would be a second thing to verify — the goldens are
generated through the CLI path, so the service's copy would be the unverified
one (docs/06-DECISIONS.md#d12, #d19).

So the worker calls the same `pipeline.write_artifacts()` the CLI calls, pointed
at a scratch directory, and the store then takes the finished files. `run()` and
`write_artifacts()` stay the only two functions either caller uses, and the bytes
a service run produces are the bytes a CLI run produces.

The cost is one local copy per artifact. For a cloud store that copy is the
upload and would have happened anyway.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol


@dataclass(frozen=True)
class StoredArtifact:
    name: str
    uri: str
    bytes: int
    sha256: str
    # Where it can be read from now. None for a store with no local view — an
    # object-store implementation would leave this unset and the api would
    # stream from the store instead.
    local_path: Path | None = None


def sha256_of(path: Path, *, chunk: int = 1 << 20) -> str:
    """Digest for transfer integrity.

    **Never** a content-equality check on a workbook: openpyxl stamps timestamps
    into docProps/core.xml, so two runs producing identical numbers produce
    different bytes (docs/06-DECISIONS.md#d16). Comparing workbooks is
    tests/goldens/cellset.py's job.
    """
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def sha256_of_chunks(chunks: Iterator[bytes]) -> str:
    """The same digest over a stream, in constant memory.

    Used to verify an artifact before it is served. Deliberately does NOT collect the
    bytes: a 30 MB workbook buffered in the api to check its digest would undo the
    reason `stream()` exists. The store is read twice instead — once to verify, once
    to serve — which is the right trade for a file a human downloads occasionally.
    """
    h = hashlib.sha256()
    for block in chunks:
        h.update(block)
    return h.hexdigest()


class ArtifactStore(Protocol):
    def put(self, *, period: str, platform: str, run_id: int, path: Path) -> StoredArtifact: ...

    def open(self, artifact_uri: str) -> Path | None:
        """A local path for the api to serve, or None if the store has no local
        view — an object store has none, and answers `stream()` instead."""
        ...

    def stream(self, artifact_uri: str, *, chunk: int = 1 << 20) -> Iterator[bytes] | None:
        """Bytes for the api to relay, or None if this store cannot address that
        URI at all.

        The one method M6 added, and it is what stops
        `GET /runs/{id}/artifacts/{name}` returning 501 in the deployment being
        targeted. `open()` is still tried first: a local file served by
        `FileResponse` gets sendfile and a Range header for free, which a
        generator does not.

        **Not a presigned URL.** That would be a bearer credential in a query
        string `service/auth.py` never sees, granting anyone holding the link a
        workbook containing every store's revenue (docs/06-DECISIONS.md#d43).
        """
        ...


class LocalArtifactStore:
    """Files under a root directory, laid out the way `output/` already is.

    `<root>/<period>/<platform>/run-<id>/<name>` — the window path an operator
    already knows, plus the run id, because two runs of one window are a normal
    thing to compare and overwriting the earlier one would destroy the evidence.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def location(self, period: str, platform: str, run_id: int) -> Path:
        return self.root / period / platform / f"run-{run_id}"

    def put(self, *, period: str, platform: str, run_id: int, path: Path) -> StoredArtifact:
        target_dir = self.location(period, platform, run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.resolve() != path.resolve():
            shutil.copy2(path, target)
        return StoredArtifact(name=path.name, uri=target.resolve().as_uri(),
                              bytes=target.stat().st_size, sha256=sha256_of(target),
                              local_path=target)

    def open(self, artifact_uri: str) -> Path | None:
        from urllib.parse import unquote, urlparse
        parsed = urlparse(artifact_uri)
        if parsed.scheme != "file":
            return None
        # urlparse puts a Windows drive letter in the path as "/C:/..." — strip
        # the leading slash before handing it to Path.
        raw = unquote(parsed.path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        candidate = Path(raw)
        return candidate if candidate.is_file() else None

    def stream(self, artifact_uri: str, *, chunk: int = 1 << 20) -> Iterator[bytes] | None:
        """Present so `ArtifactStore` is one Protocol rather than two.

        A local store always has a path, so the api never reaches this — but a
        Protocol with an optional method is a Protocol nobody can rely on, and
        `hasattr(store, "stream")` at the call site is exactly the drift the
        deletable-wrapper tests exist to prevent.
        """
        local = self.open(artifact_uri)
        if local is None:
            return None
        return _iter_file(local, chunk)


def _iter_file(path: Path, chunk: int) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            yield block


class S3ArtifactStore:
    """Artifacts in a bucket, addressed `s3://<bucket>/artifacts/...`.

    Same layout as `LocalArtifactStore` — `<period>/<platform>/run-<id>/<name>` —
    because an operator reading a `uri` out of the database should not have to
    know which mode wrote it, and because a key that mirrors the folder an
    operator already knows is one someone can find with the MinIO console at 2am.

    **The bytes are still written by `pipeline.write_artifacts` first.** This
    uploads a finished file; it never formats one. A second writer would be a
    second unverified implementation of the deliverable the team invoices from
    ([D31](docs/06-DECISIONS.md#d31)), and the goldens are generated through the
    local path, so the service's copy would be the unverified one.
    """

    def __init__(self, objects: "ObjectStore", *, prefix: str = "artifacts") -> None:
        self.objects = objects
        self.prefix = prefix.strip("/")

    def key_for(self, period: str, platform: str, run_id: int, name: str) -> str:
        return f"{self.prefix}/{period}/{platform}/run-{run_id}/{name}"

    def put(self, *, period: str, platform: str, run_id: int, path: Path) -> StoredArtifact:
        data = path.read_bytes()
        ref = self.objects.put(self.key_for(period, platform, run_id, path.name), data)
        # local_path stays None on purpose. The file it would point at is the
        # worker's scratch copy, which `Worker._cleanup` is about to delete — a
        # path the api could not read is worse than no path at all.
        return StoredArtifact(name=path.name, uri=ref.uri, bytes=ref.bytes,
                              sha256=ref.sha256, local_path=None)

    def open(self, artifact_uri: str) -> Path | None:
        return None

    def stream(self, artifact_uri: str, *, chunk: int = 1 << 20) -> Iterator[bytes] | None:
        from .objects import ObjectNotFound, parse_s3_uri
        parsed = parse_s3_uri(artifact_uri)
        if parsed is None:
            # A `file:` URI from a run this deployment made before it moved to a
            # bucket. Returning None rather than guessing is the honest answer:
            # the api reports which URI it could not serve.
            return None
        _bucket, key = parsed
        try:
            iterator = self.objects.stream(key, chunk=chunk)
            first = next(iterator, b"")
        except ObjectNotFound:
            return None

        def chained() -> Iterator[bytes]:
            if first:
                yield first
            yield from iterator

        return chained()


def build_artifact_store(settings) -> "ArtifactStore":
    """The store this deployment's configuration asks for.

    One function, called by both `api.build_app` and `worker.build_worker`, so
    the two cannot end up writing and reading different stores — which is the
    exact failure the shared-volume assumption produced (defect 2.4).
    """
    if getattr(settings, "s3", None) is None:
        return LocalArtifactStore(settings.artifact_root)
    from .objects import artifact_object_store
    return S3ArtifactStore(artifact_object_store(settings))
