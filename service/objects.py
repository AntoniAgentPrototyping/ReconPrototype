"""Bytes that outlive one container: uploaded exports and finished artifacts.

**Why this exists at all**, since M4 was perfectly happy with a shared volume:
`api` and `worker` are already separate containers, and the worker writes the
workbook bytes while the api serves them. On the deployment being targeted that
sharing is not expressible — Railway's own documentation is unambiguous:

    "Each service can only have a single volume"

with no cross-service mounting. So `GET /runs/{id}/artifacts/{name}` would 501
forever in production while passing every test locally, which is the worst shape
a defect can have. Object storage is the only option that makes both the upload
path and the download path work in the same deployment (docs/06-DECISIONS.md#d43).

**Why `boto3` and not the `minio` client.** S3's vocabulary is what MinIO,
Railway, Cloudflare R2 and S3 itself all speak, so the same code reaches a local
container and a managed bucket. It is a `service` extra, never a core dependency:
a machine regenerating a golden must not need an HTTP client for object storage
to produce the invoicing workbook.

**Deliberately not presigned URLs.** A presigned URL is a bearer credential in a
query string that `service/auth.py` never sees — for its whole lifetime anyone
holding the link downloads a workbook containing every store's revenue, with no
role check and no audit line. The api streams instead, which costs a proxy hop
and keeps every download inside the authorization model.

`LocalDirObjects` is not a test double. It is the mode a single-machine
deployment runs in, and it is what keeps `settings.s3 is None` a supported
configuration rather than a broken one — which in turn is what lets every M4
worker test keep passing verbatim.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol
from urllib.parse import unquote, urlparse


class ObjectNotFound(KeyError):
    """No object under that key. Named rather than a bare KeyError because the
    api turns it into a 404 and a worker turns it into a hard stop."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:                                   # pragma: no cover
        return f"no object at {self.key!r}"


@dataclass(frozen=True)
class ObjectRef:
    key: str
    bytes: int
    sha256: str
    # A stable, storage-qualified address. `s3://bucket/key` or a `file:` URI.
    # Recorded on the row so an artifact stays locatable if the deployment's
    # storage mode changes underneath it — the URI says which mode wrote it.
    uri: str


class ObjectStore(Protocol):
    """Five operations, and no more.

    Nothing here lists, copies server-side or sets a lifecycle rule. Retention on
    `recon-uploads` is a bucket policy applied by `minio-init`, not something the
    application does per object: an application-side deletion loop is a scheduled
    job that silently stops running, and the promise in docs/04-DATA-FLOW.md that
    raw uploads are short-lived deserves a mechanism that cannot silently stop.
    """

    def put(self, key: str, data: bytes) -> ObjectRef: ...

    def get(self, key: str) -> bytes: ...

    def download_to(self, key: str, target: Path) -> Path: ...

    def stream(self, key: str, *, chunk: int = 1 << 20) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...

    def uri_for(self, key: str) -> str: ...


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_key(key: str) -> str:
    """A key is a path in someone else's namespace, so it gets the same guard a
    filename does.

    `..` matters more here than it looks: `LocalDirObjects` joins the key onto a
    directory, so an unchecked `../../` would write outside the store — and the
    keys are partly built from a period the api already character-checks, which
    is exactly the kind of two-layer guard that rots when only one layer exists.
    """
    if not key or key.startswith("/") or key.endswith("/"):
        raise ValueError(f"unusable object key: {key!r}")
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"unusable object key: {key!r}")
    return key


def parse_s3_uri(uri: str) -> tuple[str, str] | None:
    """`s3://bucket/key` → `(bucket, key)`, or None if it is not an s3 URI."""
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        return None
    key = unquote(parsed.path).lstrip("/")
    return (parsed.netloc, key) if key else None


# ---------------------------------------------------------------------------
# A directory
# ---------------------------------------------------------------------------

class LocalDirObjects:
    """Objects as files under a root. The single-machine mode.

    Not a stub: with no `RECON_S3_ENDPOINT` set this is what the api and the
    worker use, and it is the reason a developer needs no container to run the
    whole upload → run → download path.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, key: str, data: bytes) -> ObjectRef:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ObjectRef(key=key, bytes=len(data), sha256=digest_of(data),
                         uri=self.uri_for(key))

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        return path.read_bytes()

    def download_to(self, key: str, target: Path) -> Path:
        source = self._path(key)
        if not source.is_file():
            raise ObjectNotFound(key)
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # copyfile, not a symlink or a hardlink: the worker's scratch copy is
        # about to be handed to pandas and then deleted, and a link would make
        # `shutil.rmtree(scratch)` a way to delete the stored object.
        shutil.copyfile(source, target)
        return target

    def stream(self, key: str, *, chunk: int = 1 << 20) -> Iterator[bytes]:
        path = self._path(key)
        if not path.is_file():
            raise ObjectNotFound(key)
        with path.open("rb") as fh:
            while block := fh.read(chunk):
                yield block

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def uri_for(self, key: str) -> str:
        return self._path(key).resolve().as_uri()


# ---------------------------------------------------------------------------
# S3 / MinIO
# ---------------------------------------------------------------------------

class S3Objects:
    """One bucket, reached over the S3 API.

    `addressing_style: path` is not optional for MinIO: virtual-host addressing
    resolves `bucket.minio` in DNS, which does not exist inside a compose
    network. `signature_version: s3v4` matches what MinIO accepts.

    The client is built once and shared. botocore's client is thread-safe for
    calls, which is what FastAPI's threadpool needs; the resource layer is not
    used precisely because it is not.
    """

    def __init__(self, bucket: str, *, endpoint_url: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None,
                 region: str = "us-east-1", client: object | None = None) -> None:
        self.bucket = bucket
        if client is not None:
            self._client = client
            return
        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": "path"},
                          # Three tries, then fail loudly. A settlement run must
                          # not hang for minutes on a store that is simply down.
                          retries={"max_attempts": 3, "mode": "standard"}))

    # -- error translation ---------------------------------------------------

    @staticmethod
    def _is_missing(exc: Exception) -> bool:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        return str(code) in ("404", "NoSuchKey", "NoSuchBucket", "NotFound")

    # -- the five operations ------------------------------------------------

    def put(self, key: str, data: bytes) -> ObjectRef:
        check_key(key)
        digest = digest_of(data)
        self._client.put_object(
            Bucket=self.bucket, Key=key, Body=data,
            # The digest travels with the object, so a later download can be
            # checked against what was stored without a second table lookup.
            Metadata={"sha256": digest})
        return ObjectRef(key=key, bytes=len(data), sha256=digest, uri=self.uri_for(key))

    def get(self, key: str) -> bytes:
        check_key(key)
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:                                    # noqa: BLE001
            if self._is_missing(exc):
                raise ObjectNotFound(key) from exc
            raise

    def download_to(self, key: str, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.get(key))
        return target

    def stream(self, key: str, *, chunk: int = 1 << 20) -> Iterator[bytes]:
        check_key(key)
        try:
            body = self._client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except Exception as exc:                                    # noqa: BLE001
            if self._is_missing(exc):
                raise ObjectNotFound(key) from exc
            raise
        try:
            while block := body.read(chunk):
                yield block
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        check_key(key)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:                                    # noqa: BLE001
            if self._is_missing(exc):
                return False
            raise

    def delete(self, key: str) -> bool:
        check_key(key)
        if not self.exists(key):
            return False
        self._client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def ensure_bucket(self) -> bool:
        """Create the bucket if it is absent. Returns whether it was created.

        `minio-init` does this in compose, and this exists for the deployment
        where it does not — a managed bucket that has to be created by hand is a
        first-run failure with a confusing message.
        """
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return False
        except Exception as exc:                                    # noqa: BLE001
            if not self._is_missing(exc):
                raise
        self._client.create_bucket(Bucket=self.bucket)
        return True


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

UPLOAD_PREFIX = "uploads"
ARTIFACT_PREFIX = "artifacts"


def upload_store(settings) -> ObjectStore:
    """The store uploaded exports go to, whichever mode this deployment is in."""
    if settings.s3 is not None:
        return _s3(settings, settings.s3.uploads_bucket)
    root = settings.upload_root or (settings.scratch_root / "uploads")
    return LocalDirObjects(Path(root) / "objects")


def artifact_object_store(settings) -> ObjectStore:
    if settings.s3 is not None:
        return _s3(settings, settings.s3.artifacts_bucket)
    return LocalDirObjects(Path(settings.artifact_root) / "objects")


def _s3(settings, bucket: str) -> S3Objects:
    s3 = settings.s3
    return S3Objects(bucket, endpoint_url=s3.endpoint_url, access_key=s3.access_key,
                     secret_key=s3.secret_key, region=s3.region)
