"""Backfill the order index for uploads that predate it — defect 2.12's detection half.

`POST /uploads` has indexed every arriving file since 2026-08-19. Everything uploaded
before that has `indexed_at is null`, and until it is indexed the cross-window question
— *do this order's SKU lines exist in some OTHER window's export?* — cannot be answered
for those files. This is the CLI that clears the backlog:

    python -m service.order_index --backfill

**Why this is not the D26 self-certification trap.** The rule broken by
[defect 2.10](../docs/08-KNOWN-DEFECTS.md) / [D52](../docs/06-DECISIONS.md#d52) is
"never derive an integrity reference from the thing it is supposed to check": recomputing
`object_sha256` from whatever the store holds today writes down the current bytes as the
expected bytes, and passes even if they were already replaced. This tool does the
opposite. The expected digest was recorded independently, at the door, from the bytes
that were sanitized there; here it is **checked, never derived**, before a single order id
is read out of the file. A mismatch refuses to index and says so. Nothing this module
writes is ever an integrity reference for anything else — the index is derived,
rebuildable, reporting-only data.

**A NULL `object_sha256` is skipped and reported, not indexed on trust.** Those uploads
predate M8/2.5, so nothing can establish that the stored bytes are the uploaded bytes.
`service/materialize.py` already refuses to *run* them for exactly this reason; indexing
them anyway would put unverifiable provenance into the table that answers "where did this
order's lines come from", which is the one question the table exists to answer honestly.
The remedy is the same one materialisation names: re-upload the export.

**What this module may not become.** The index holds identifiers and counts. The money
math stays in `src/`, where it was ported formula-by-formula from the team's own workbooks
and verified row-by-row; a SQL reimplementation of it would be a second, unverified
implementation of the path that produces the invoice — the
[D31](../docs/06-DECISIONS.md#d31) failure, and the same reason the worker adds no compute.
**The database may know where every number came from; it may never compute one.**
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from . import db, materialize, objects as object_lib, uploads as upload_lib
from .config import ServiceSettings
from .repository_m5 import M5Repository


@dataclass
class Outcome:
    """One upload's result. Counted, and each skip carries its own reason so the
    operator is never left with a bare total."""
    indexed: int = 0
    order_rows: int = 0
    skipped_no_digest: int = 0
    skipped_unreadable: int = 0
    refused_mismatch: int = 0
    notes: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.notes is None:
            self.notes = []


def _domain(settings: ServiceSettings, repo: M5Repository, platform: str,
            period: str) -> dict:
    """The config THIS window runs under, so the column map and date format used to
    read a file are the ones its own run would use.

    A window pinned to an older config may spell a column differently; reading it
    through today's map would index the wrong column or none at all.
    """
    from src import config as src_config

    if hasattr(repo, "pinned_config"):
        pinned = repo.pinned_config(platform, period)
        if pinned is not None and pinned.get("content"):
            return src_config.parse_settings(pinned["content"])
    return src_config.load_settings(settings.config_dir)


def backfill(repo: M5Repository, store, settings: ServiceSettings, *,
             limit: int = 500, dry_run: bool = False) -> Outcome:
    """Index every upload that has no index rows yet.

    Idempotent by construction: `record_order_index` replaces an upload's rows rather
    than adding to them, so running this twice cannot double anything.
    """
    out = Outcome()
    pending = repo.uploads_unindexed(limit=limit)

    with TemporaryDirectory(prefix="order-index-") as tmp:
        scratch = Path(tmp)
        for row in pending:
            name = row.get("filename") or f"upload {row['id']}"

            if not (row.get("object_sha256") or "").strip():
                out.skipped_no_digest += 1
                out.notes.append(
                    f"skipped {name} (upload {row['id']}): predates the M8/2.5 "
                    f"integrity check and carries no digest of its stored bytes. "
                    f"Nothing can establish that the stored file is the uploaded "
                    f"file, so it is not indexed. Re-upload the export.")
                continue
            if not row.get("object_key"):
                out.skipped_unreadable += 1
                out.notes.append(
                    f"skipped {name} (upload {row['id']}): no object key, so there "
                    f"is nothing to read.")
                continue

            target = scratch / f"{row['id']}-{name}"
            try:
                # Streamed, like materialisation: these are marketplace exports and
                # some are hundreds of MB.
                store.download_to(row["object_key"], target)
            except Exception as exc:                        # noqa: BLE001
                out.skipped_unreadable += 1
                out.notes.append(f"skipped {name} (upload {row['id']}): "
                                 f"could not read {row['object_key']!r} ({exc}).")
                continue

            # CHECKED, not derived. See this module's docstring.
            actual = materialize.digest_file(target)
            expected = row["object_sha256"].strip().lower()
            if actual != expected:
                out.refused_mismatch += 1
                out.notes.append(
                    f"REFUSED {name} (upload {row['id']}): stored bytes do not match "
                    f"the digest recorded at the door — recorded {expected[:12]}…, "
                    f"found {actual[:12]}…. Not indexed. This is the same condition "
                    f"that stops a run (defect 2.10); the window is not safe to "
                    f"invoice from until it is explained.")
                target.unlink(missing_ok=True)
                continue

            platform, period, kind = (row.get("platform"), row.get("period"),
                                      row.get("kind"))
            try:
                domain = _domain(settings, repo, platform, period)
                colmap = upload_lib.column_map_for(domain, platform, kind)
                order_ids, first, last = upload_lib.identify_file(
                    target, colmap, settings=domain, platform=platform, kind=kind)
            except Exception as exc:                        # noqa: BLE001
                out.skipped_unreadable += 1
                out.notes.append(f"skipped {name} (upload {row['id']}): "
                                 f"could not read its columns ({exc}).")
                continue
            finally:
                target.unlink(missing_ok=True)

            if not dry_run:
                out.order_rows += repo.record_order_index(
                    row["id"], row.get("store_canonical") or row.get("store") or "",
                    order_ids, settles_from=first, settles_to=last)
            else:
                out.order_rows += len(order_ids)
            out.indexed += 1

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m service.order_index",
        description="Index which uploaded file holds which (store, order_id).")
    parser.add_argument("--backfill", action="store_true",
                        help="index every upload that has no index rows yet")
    parser.add_argument("--limit", type=int, default=500,
                        help="how many uploads to consider in one pass")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be indexed and write nothing")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    if not args.backfill:
        parser.error("nothing to do: pass --backfill")

    settings = ServiceSettings.from_env()
    if args.database_url:
        settings = replace(settings, database_url=args.database_url)
    if not settings.database_url:
        print("no database configured: set RECON_DATABASE_URL or pass --database-url",
              file=sys.stderr)
        return 2

    pool = db.make_pool(settings.database_url, min_size=1, max_size=2)
    try:
        with pool.connection() as conn:
            db.migrate(conn)
        repo = M5Repository(pool)
        store = object_lib.upload_store(settings)
        out = backfill(repo, store, settings, limit=args.limit, dry_run=args.dry_run)
    finally:
        pool.close()

    for note in out.notes:
        print(note)
    if out.notes:
        print()
    verb = "would index" if args.dry_run else "indexed"
    print(f"{verb} {out.indexed} upload(s), {out.order_rows:,} order id(s)")
    if out.skipped_no_digest:
        print(f"skipped {out.skipped_no_digest} with no recorded digest "
              f"(re-upload to index)")
    if out.skipped_unreadable:
        print(f"skipped {out.skipped_unreadable} that could not be read")
    if out.refused_mismatch:
        print(f"REFUSED {out.refused_mismatch} whose stored bytes do not match "
              f"their recorded digest")
    # A digest mismatch is a finding, not housekeeping: exit non-zero so a scheduled
    # run cannot report success while an object store is serving altered bytes.
    return 1 if out.refused_mismatch else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
