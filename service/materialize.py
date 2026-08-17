"""Turn a window's uploaded exports into the input tree the pipeline reads.

The last step of workstream B, and the one that dissolves the manual-staging
error class entirely: an operator uploads files in a browser, and the worker
assembles `input/<period>/<platform>/<folder>/` itself, in scratch, immediately
before `build_context`.

**Why this lives in `service/` and not in `src/`.** `run()` writes nothing and
`write_artifacts()` is the only writer in the codebase
([tests/test_io_boundary.py](../tests/test_io_boundary.py) enforces that
per-function). Downloading objects is both a write and a network call, so putting
it in `src/` would need a new I/O grant and a storage driver next to the money
math — and the I/O-boundary lint scopes to `src/**`, so exempting it is exactly
the erosion that expires the deferred-engine-port bet
([D25](../docs/06-DECISIONS.md#d25)). Here it is ordinary service plumbing:
`run()` still receives a directory of files and still writes nothing.

**Local-disk mode stays, and is not a fallback for tests.** A window with no
uploads materialises to `settings.input_root` unchanged, which is what lets a
developer run a window they copied in by hand and what keeps every M4 worker test
passing verbatim. The mode is *reported*, never inferred by the reader — a run
whose input came from a volume and a run whose input came from a bucket are two
different provenance claims and the log says which.

**Nothing here interprets a file.** No reading, no sanitizing, no column
inspection: the sanitize already happened at the upload door, and doing it again
here would be a second transformation on the path that produced every verified
number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import naming


class MaterializationError(RuntimeError):
    """The window cannot be assembled. Always names the file or the store.

    Raised rather than worked around: a window quietly missing one export
    produces a workbook that looks complete and under-invoices one storefront.
    """


@dataclass(frozen=True)
class MaterializedFile:
    upload_id: int
    kind: str
    store: str
    store_canonical: str
    original: str
    name: str
    object_key: str
    sha256: str
    bytes: int

    @property
    def renamed(self) -> bool:
        return self.name != self.original


@dataclass
class Materialization:
    """What the worker is about to run on, and where it came from."""

    input_root: Path
    # "uploads" — assembled from the bucket. "input_root" — a directory somebody
    # populated. Recorded on the run so the two are never confused later.
    source: str
    files: list[MaterializedFile] = field(default_factory=list)
    # Expected stores with no file in this window. A count, rendered on the board;
    # `check_stores` is still the control that stops the run.
    missing_stores: list[str] = field(default_factory=list)
    unexpected_stores: list[str] = field(default_factory=list)
    # Uploads that could not be materialised, each with a stated reason. Never
    # silent: a window that ran on fewer files than were uploaded must say so.
    skipped: list[str] = field(default_factory=list)

    @property
    def upload_ids(self) -> list[int]:
        return [f.upload_id for f in self.files]

    @property
    def roster_missing(self) -> int | None:
        return len(self.missing_stores) if self.source == "uploads" else None

    def provenance(self) -> dict:
        """The record written next to the scratch tree.

        Keyed by the uniform name, because that is the name that appears in the
        run log and in `source_file` inside the workbook — so an auditor reading
        `003_KAO.xlsx` in a finance file can find which uploaded bytes it was.
        Digests and counts only; no cell values ever enter this file.
        """
        return {
            "source": self.source,
            "input_root": str(self.input_root),
            "files": [
                {"name": f.name, "original": f.original, "kind": f.kind,
                 "store": f.store, "store_canonical": f.store_canonical,
                 "upload_id": f.upload_id, "object_key": f.object_key,
                 "sha256": f.sha256, "bytes": f.bytes}
                for f in self.files
            ],
            "missing_stores": self.missing_stores,
            "unexpected_stores": self.unexpected_stores,
            "skipped": self.skipped,
        }


def roster_gap(settings: dict, platform: str,
               found: set[str]) -> tuple[list[str], list[str]]:
    """Which expected stores are absent, and which present ones are unexpected.

    Deliberately the same set arithmetic as `ingest.check_stores` — optional
    stores excluded from `missing` — so the number the upload screen shows and the
    control that stops the run cannot disagree. It is a *preview* of that check,
    not a second one: nothing here decides anything.
    """
    expected = set((settings.get("expected_stores") or {}).get(platform) or [])
    optional = set((settings.get("stores_optional") or {}).get(platform) or [])
    if not expected:
        return [], []
    return sorted(expected - found - optional), sorted(found - expected)


def canonical_store(settings: dict, platform: str, store: str) -> str:
    """The store after `store_aliases`, which is what the roster is checked against.

    `TODO-HUMAN` is an unresolved alias and must resolve to itself, not to the
    literal string — `read_parts` skips those too, and mapping a real store onto a
    placeholder would put "TODO-HUMAN" in the roster comparison.
    """
    aliases = (settings.get("store_aliases") or {}).get(platform) or {}
    mapped = aliases.get(store)
    return mapped if mapped and mapped != "TODO-HUMAN" else store


# ---------------------------------------------------------------------------
# The assembly
# ---------------------------------------------------------------------------

def materialize_window(repo, settings, platform: str, period: str, *,
                       scratch: Path, log, domain_settings: dict,
                       objects=None) -> Materialization:
    """Assemble the window, or report that it came from a directory instead.

    Called by the worker immediately before `build_context`, with the config that
    run has already resolved — so a window pinned to an older config is renamed
    under *that* config's `store_from_filename`, not today's. Getting this wrong
    would make a re-run of May read its filenames with August's regex.
    """
    if not hasattr(repo, "uploads_for_window"):
        # An M4 repository. Behave exactly as M4 did, and say so rather than
        # leaving an operator to wonder which mode ran.
        return Materialization(input_root=Path(settings.input_root),
                               source="input_root")

    rows = repo.uploads_for_window(platform, period)
    if not rows:
        log.add(f"  input from {settings.input_root} (no uploads recorded for "
                f"{platform} {period})")
        return Materialization(input_root=Path(settings.input_root),
                               source="input_root")

    from .objects import ObjectNotFound, upload_store
    objects = objects if objects is not None else upload_store(settings)

    target_root = Path(scratch) / "input"
    result = Materialization(input_root=target_root, source="uploads")

    usable: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("object_key"):
            # Written by an M5 deployment into a quarantine volume this container
            # may not even have. Skipped LOUDLY — see 004_uploads_objects.sql.
            result.skipped.append(
                f"upload {row['id']} ({row['filename']}): no object key — it was "
                f"quarantined on a volume before M6 and cannot be materialised")
            continue
        usable.setdefault(row["kind"], []).append(row)

    for kind, group in sorted(usable.items()):
        by_name: dict[str, dict] = {}
        for row in group:
            existing = by_name.get(row["filename"])
            if existing is not None:
                raise MaterializationError(
                    f"two different {platform}/{kind} uploads in {period} are both "
                    f"named {row['filename']!r} (uploads {existing['id']} and "
                    f"{row['id']}). One would overwrite the other and the window "
                    f"would run on the wrong bytes. Reject the stale one.")
            by_name[row["filename"]] = row

        try:
            planned = naming.plan_window(list(by_name), platform, kind, domain_settings)
        except naming.NamingError as exc:
            raise MaterializationError(
                f"cannot build uniform names for {platform}/{kind} in {period}: "
                f"{exc}") from exc

        for item in planned:
            row = by_name[item.original]
            target = naming.target_path(target_root, period, platform, kind, item.name)
            try:
                objects.download_to(row["object_key"], target)
            except ObjectNotFound as exc:
                raise MaterializationError(
                    f"upload {row['id']} ({item.original}) is recorded but its bytes "
                    f"are not in the store at {row['object_key']!r}. The window is "
                    f"incomplete; running it would under-report "
                    f"{item.store!r}.") from exc
            result.files.append(MaterializedFile(
                upload_id=row["id"], kind=kind, store=item.store,
                store_canonical=row.get("store_canonical") or item.store,
                original=item.original, name=item.name,
                object_key=row["object_key"], sha256=row["sha256"],
                bytes=int(row["bytes"])))

    found = {f.store_canonical for f in result.files}
    result.missing_stores, result.unexpected_stores = roster_gap(
        domain_settings, platform, found)

    _log(result, log, platform, period)
    (Path(scratch) / "materialized.json").write_text(
        json.dumps(result.provenance(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return result


def _log(result: Materialization, log, platform: str, period: str) -> None:
    renamed = sum(1 for f in result.files if f.renamed)
    log.add(f"  input materialized from {len(result.files)} upload(s) "
            f"({renamed} renamed to the uniform scheme), {len(result.files) - renamed} "
            f"already uniform")
    for message in result.skipped:
        log.warn(f"upload SKIPPED — {message}")
    if result.missing_stores:
        # A warning, not the decision. `check_stores` hard-stops on this unless the
        # window carries a roster declaration, and that ordering is deliberate: the
        # control lives in the verified pipeline, not in the service.
        log.warn(f"ROSTER: {len(result.missing_stores)} expected {platform} "
                 f"store(s) have no file in {period}: {result.missing_stores}")
    if result.unexpected_stores:
        log.warn(f"ROSTER: {len(result.unexpected_stores)} store(s) uploaded for "
                 f"{platform} {period} are not on the roster: "
                 f"{result.unexpected_stores}")
