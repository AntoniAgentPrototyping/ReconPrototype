"""Retention: the disposable classes actually get disposed of (Phase 6 / C10).

Nothing here touches a deliverable or an audit record. What ages out, and why it
is safe to:

* **Scratch** (`scratch_root/job-*`, `scratch_root/incoming/*`). Job directories
  are kept on failure *for diagnosis* — `retention_scratch_days` is the diagnosis
  window, after which a 9 GB materialized window is just disk. Incoming files are
  per-request temporaries the upload handler already unlinks; anything still here
  is the residue of a crashed request.
* **`run_log_lines`** older than `retention_log_days`, for FINISHED runs only.
  This table is the *mirror* that lets a browser poll a log mid-run; the durable
  copy is `run_log.txt` in the artifact store, written by the same
  `write_artifacts` as the workbook. The `runs` row — status, findings, metrics,
  config version — is never touched.
* **`user_sessions`** rows that have been *dead* (revoked, or past their absolute
  expiry) longer than `retention_sessions_days`. Live sessions are never touched;
  the grace period exists because a dead session is still evidence while someone
  might want to look at it (003_password_auth.sql records client_ip and
  user_agent for exactly that).

What is deliberately NOT swept: uploads in bucket mode (the MinIO lifecycle rule
owns that — a bucket rule cannot silently stop running, which an application
loop can, and that argument is in docker-compose.yml); artifacts (the
deliverable); every other table.

The sweep also checks free space on the scratch volume and WARNS below
`disk_free_min_gb` — the failure C10 predicts is a full volume discovered at
month end, and a warning line every sweep is what makes it discoverable earlier.

Runs from two places: the worker loop (every `retention_interval_s`, best-effort,
never fails a settlement run) and `python -m service.admin retention sweep`
(which is also where `--dry-run` lives).
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import ServiceSettings


@dataclass
class RetentionReport:
    dry_run: bool
    scratch_dirs_removed: list[str] = field(default_factory=list)
    incoming_files_removed: int = 0
    scratch_bytes_freed: int = 0
    log_lines_deleted: int = 0
    sessions_deleted: int = 0
    disk_free_gb: float | None = None
    warnings: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The report as sentences, for the run log / CLI / service log."""
        verb = "would remove" if self.dry_run else "removed"
        out = [
            f"retention: {verb} {len(self.scratch_dirs_removed)} scratch dir(s) "
            f"and {self.incoming_files_removed} incoming file(s) "
            f"({self.scratch_bytes_freed / 1e6:,.0f} MB)",
            f"retention: {verb} {self.log_lines_deleted} run-log line(s) "
            f"(finished runs; run_log.txt artifacts are the durable copy)",
            f"retention: {verb} {self.sessions_deleted} dead session row(s)",
        ]
        if self.disk_free_gb is not None:
            out.append(f"retention: scratch volume has {self.disk_free_gb:,.1f} GB free")
        out += [f"retention WARNING: {w}" for w in self.warnings]
        return out


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _older_than(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def sweep(repo, settings: ServiceSettings, *, dry_run: bool = False,
          now: float | None = None) -> RetentionReport:
    """One pass. Returns what happened (or would, under `dry_run`)."""
    report = RetentionReport(dry_run=dry_run)
    now = time.time() if now is None else now
    scratch = Path(settings.scratch_root)

    # -- scratch ------------------------------------------------------------
    cutoff = now - settings.retention_scratch_days * 86400
    if scratch.is_dir():
        for entry in sorted(scratch.glob("job-*")):
            if entry.is_dir() and _older_than(entry, cutoff):
                report.scratch_bytes_freed += _dir_size(entry)
                report.scratch_dirs_removed.append(entry.name)
                if not dry_run:
                    shutil.rmtree(entry, ignore_errors=True)
        incoming = scratch / "incoming"
        if incoming.is_dir():
            for entry in sorted(incoming.iterdir()):
                if entry.is_file() and _older_than(entry, cutoff):
                    try:
                        report.scratch_bytes_freed += entry.stat().st_size
                    except OSError:
                        pass
                    report.incoming_files_removed += 1
                    if not dry_run:
                        entry.unlink(missing_ok=True)

        try:
            free = shutil.disk_usage(scratch).free
            report.disk_free_gb = free / 1e9
            if report.disk_free_gb < settings.disk_free_min_gb:
                report.warnings.append(
                    f"scratch volume is down to {report.disk_free_gb:,.1f} GB free "
                    f"(threshold {settings.disk_free_min_gb:g} GB). A window "
                    f"materializes up to ~10 GB; a run may fail mid-download.")
        except OSError:
            pass

    # -- database mirrors ----------------------------------------------------
    # hasattr-guarded per method: the M4 repository has runs and log lines but no
    # sessions table, and a test double may have neither. Absence degrades the
    # sweep, never crashes it — but it is counted as 0, not hidden.
    if hasattr(repo, "prune_run_log_lines"):
        report.log_lines_deleted = repo.prune_run_log_lines(
            settings.retention_log_days, dry_run=dry_run)
    if hasattr(repo, "prune_dead_sessions"):
        report.sessions_deleted = repo.prune_dead_sessions(
            settings.retention_sessions_days, dry_run=dry_run)

    return report
