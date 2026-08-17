"""The worker: claim a job, run the pipeline, store what it produced.

    python -m service.worker            # loop until interrupted
    python -m service.worker --once     # claim at most one job, then exit
    python -m service.worker --drain    # keep going until the queue is empty

**It adds no compute and no formatting.** The whole execution is four calls into
`src/`, in the order the CLI makes them:

    ctx    = pipeline.build_context(...)
    result = pipeline.run(ctx)
    pipeline.log_result(result)
    pipeline.write_artifacts(result)

Everything else here is bookkeeping: leases, the run record, artifact upload,
job state. If this file grew a calculation it would be a second, unverified
compute layer and the numbers a finance user reads in a browser would stop being
the numbers `tools/full_run.py` produces (docs/06-DECISIONS.md#d24).

**One job at a time, per process.** Not a thread pool — `settings["_vat_sku"]`
is a mutable back-channel inside the settings dict, and two runs sharing one
dict cross-contaminate VAT rates (docs/02-ARCHITECTURE.md#import-hygiene). A
fresh context per job is what keeps that safe, and concurrency comes from running
more worker PROCESSES, which is what `FOR UPDATE SKIP LOCKED` is for.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from src import pipeline
from src.pipeline import RunStatus

from .artifacts import ArtifactStore, build_artifact_store
from .config import ServiceSettings
from .models import Job, JobState
from .repository import Repository
from .runlog import QueueRunLog, RepositoryLogSink


@dataclass
class JobOutcome:
    job_id: int
    run_id: int
    status: RunStatus
    artifacts: list[str]


class Worker:
    def __init__(self, repo: Repository, store: ArtifactStore,
                 settings: ServiceSettings) -> None:
        self.repo = repo
        self.store = store
        self.settings = settings
        self._stopping = False

    # -- the loop -----------------------------------------------------------

    def stop(self) -> None:
        """Finish the job in hand, then exit.

        There is no mid-run cancellation: `pipeline.run()` has no cancellation
        point, and killing it partway through `build_workbook` would leave a
        truncated .xlsx that looks like a deliverable.
        """
        self._stopping = True

    def serve(self, *, once: bool = False, drain: bool = False) -> list[JobOutcome]:
        outcomes: list[JobOutcome] = []
        while not self._stopping:
            self.repo.reclaim_expired()
            job = self.repo.claim(self.settings.worker_id, self.settings.lease_seconds)
            if job is None:
                if once or drain:
                    break
                time.sleep(self.settings.poll_interval_s)
                continue
            outcomes.append(self.execute(job))
            if once:
                break
        return outcomes

    # -- one job ------------------------------------------------------------

    def execute(self, job: Job) -> JobOutcome:
        run_id = self.repo.start_run(job.id, job.platform, job.period)
        sink = RepositoryLogSink(
            self.repo, run_id,
            heartbeat=lambda: self.repo.heartbeat(job.id, self.settings.worker_id,
                                                  self.settings.lease_seconds))
        log = QueueRunLog(sink, flush_every=self.settings.log_flush_lines,
                          flush_interval_s=self.settings.log_flush_seconds)
        scratch = Path(self.settings.scratch_root) / f"job-{job.id}"

        try:
            # Which RULES this run uses, decided before it starts. A window that
            # has run before is pinned to the config it ran under, so an edit
            # made since cannot change what a re-run produces
            # (docs/08-KNOWN-DEFECTS.md 2.5).
            resolved = self._resolve_config(job, log)

            # Assemble the window's input from what was uploaded. Under the
            # resolved config, not today's: a window pinned to May's config must
            # have its filenames parsed by May's store_from_filename, or a re-run
            # reads the same files as different stores (service/materialize.py).
            mat = self._materialize(job, scratch, resolved, log)
            partial = self._roster_declared_partial(job, log)

            # A fresh settings dict per job — see the module docstring.
            ctx = pipeline.build_context(
                job.platform, job.period,
                config_dir=self.settings.config_dir,
                input_root=mat.input_root,
                output_root=scratch,
                refs=job.refs, log=log, partial_roster=partial,
                settings_text=resolved.content if resolved else None)

            result = pipeline.run(ctx)
            pipeline.log_result(result)
            written = pipeline.write_artifacts(result)

            stored = self._store_artifacts(job, run_id, written)
            self._record_exceptions(run_id, result)
            if resolved is not None:
                self._settle_config(job, run_id, resolved, result)
            self._settle_uploads(run_id, mat, result)
            log.flush()

            self.repo.finish_run(
                run_id, status=result.status, findings=result.findings,
                checks=result.checks, metrics=result.metrics.to_dict(),
                roster_missing=mat.roster_missing,
                error=None if result.error is None
                else f"{type(result.error).__name__}: {result.error}")
            self.repo.finish_job(job.id, JobState.DONE)

            # A hard stop is a job that ran and a run that concluded "nothing was
            # produced" — the two axes the schema keeps apart. The job is DONE
            # either way; retrying bad input just produces the same answer
            # (docs/06-DECISIONS.md#d3).
            self._cleanup(scratch, keep=result.error is not None)
            return JobOutcome(job.id, run_id, result.status, stored)

        except BaseException as exc:                                # noqa: BLE001
            # Reaching here means the WORKER broke, not the run: run() catches
            # data problems itself and returns HARD_STOP. So record it against
            # the job as an infrastructure failure and leave the scratch
            # directory behind for diagnosis.
            detail = f"{type(exc).__name__}: {exc}"
            try:
                log.warn(f"worker failure: {detail}")
                log.flush()
            except Exception:                                       # pragma: no cover
                pass
            self.repo.finish_run(run_id, status=RunStatus.HARD_STOP, findings=[],
                                 error=f"worker failure: {detail}\n"
                                       f"{''.join(traceback.format_exception(exc))}")
            self.repo.finish_job(job.id, JobState.ERROR, error=detail)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return JobOutcome(job.id, run_id, RunStatus.HARD_STOP, [])

    # -- input -------------------------------------------------------------

    def _materialize(self, job: Job, scratch: Path, resolved, log):
        """Download this window's uploads into scratch, or use the input volume.

        Both modes are real. With uploads recorded, the bucket is the window and
        the worker needs no input mount at all; without them, it reads
        `settings.input_root` exactly as M4 did — which is what keeps every
        existing worker test passing verbatim, and what lets a developer run a
        window they copied in by hand.
        """
        from . import materialize as materialize_lib
        from src import config as src_config

        domain = (src_config.parse_settings(resolved.content) if resolved is not None
                  else src_config.load_settings(self.settings.config_dir))
        return materialize_lib.materialize_window(
            self.repo, self.settings, job.platform, job.period,
            scratch=scratch, log=log, domain_settings=domain)

    def _roster_declared_partial(self, job: Job, log) -> bool:
        """Whether this WINDOW was declared partial — not whether this run asked.

        The per-run flag is gone (M6, workstream C). `check_stores` is unchanged
        and still hard-stops an undeclared incomplete window; all that moved is
        where the override is stated. `jobs.partial_roster` is still read as a
        fallback so a job enqueued by an older api is not silently reinterpreted.
        """
        if not hasattr(self.repo, "window_declaration"):
            return job.partial_roster
        declaration = self.repo.window_declaration(job.platform, job.period)
        if declaration is None:
            return job.partial_roster
        if declaration["roster_declared_partial"]:
            log.warn(f"ROSTER DECLARED PARTIAL for {job.platform} {job.period} by "
                     f"{declaration['declared_by']}: {declaration['reason']}")
            return True
        return False

    def _settle_uploads(self, run_id: int, mat, result) -> None:
        """Attribute the files this run read.

        Only on a run that produced a workbook, matching the config-pinning rule
        directly above: a hard stop should not mark an export consumed, because
        the fix may well be to reject one of them and upload the right file.
        """
        if not mat.upload_ids or not hasattr(self.repo, "mark_uploads_consumed"):
            return
        if result.status is RunStatus.HARD_STOP:
            return
        self.repo.mark_uploads_consumed(mat.upload_ids, run_id)

    # -- config versioning -------------------------------------------------

    def _resolve_config(self, job: Job, log) -> "Any":
        """Pinned config if this window has one, else today's disk config.

        Returns None — meaning "behave exactly as M4 did" — when the repository
        predates M5. That keeps `Worker` usable with a plain `Repository`, which
        the M4 tests still construct.
        """
        if not hasattr(self.repo, "pinned_config"):
            return None
        from . import config_store
        resolved = config_store.resolve_for_window(
            self.repo, self.settings.config_dir, job.platform, job.period)
        if resolved.pinned:
            log.warn(f"config PINNED to version {resolved.version_id} "
                     f"({resolved.sha256[:12]}) — this window has run before, so "
                     f"edits made since do not apply to it")
        else:
            log.add(f"  config version {resolved.version_id} ({resolved.sha256[:12]}), "
                    f"from disk")
        return resolved

    def _settle_config(self, job: Job, run_id: int, resolved, result) -> None:
        """Record what this run used, and pin the window if it has not been.

        Pinning on the FIRST successful run rather than at configuration time is
        what keeps an ordinary first run behaving exactly as it did before M5.
        A hard stop pins nothing: a run that produced no workbook should not
        freeze the rules — the fix for it may well be a config change.
        """
        self.repo.attach_config_to_run(run_id, resolved.version_id, pinned=resolved.pinned)
        if resolved.pinned or result.status is RunStatus.HARD_STOP:
            return
        self.repo.pin_period_config(
            job.platform, job.period, resolved.version_id,
            pinned_by=f"run {run_id}",
            reason="pinned automatically by the first run that produced a workbook")

    # -- the exception queue ----------------------------------------------

    def _record_exceptions(self, run_id: int, result) -> None:
        """Persist the exception frames the run produced.

        Best-effort by design: these rows are a triage aid, and losing them must
        not cost a finance file that has already been written. A failure is
        logged loudly rather than raised.
        """
        if not hasattr(self.repo, "record_exceptions"):
            return
        from . import exceptions as exc_module
        cap = self.settings.exception_row_cap
        for sheet, frame in (result.exceptions or {}).items():
            try:
                rows, total = exc_module.frame_rows(frame, cap=cap)
                if total == 0:
                    continue
                self.repo.record_exceptions(
                    run_id, sheet, rows, total=total,
                    fingerprint_of=lambda r, s=sheet: exc_module.fingerprint(s, r))
            except Exception as exc:                                # noqa: BLE001
                result.context.log.warn(
                    f"could not record the {sheet!r} exception sheet: "
                    f"{type(exc).__name__}: {exc}")

    def _store_artifacts(self, job: Job, run_id: int, written: list[Path]) -> list[str]:
        names: list[str] = []
        for path in written:
            if not path.is_file():                                  # pragma: no cover
                continue
            art = self.store.put(period=job.period, platform=job.platform,
                                 run_id=run_id, path=path)
            self.repo.record_artifact(run_id, name=art.name, uri=art.uri,
                                      bytes_=art.bytes, sha256=art.sha256)
            names.append(art.name)
        return names

    @staticmethod
    def _cleanup(scratch: Path, *, keep: bool) -> None:
        if keep or not scratch.exists():
            return
        shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_worker(settings: ServiceSettings) -> tuple[Worker, "object"]:
    from . import db
    from .repository_m5 import M5Repository
    pool = db.make_pool(settings.database_url, min_size=1, max_size=4)
    with pool.connection() as conn:
        db.migrate(conn)
    # M5Repository, not the plain queue: the worker now reads uploads and the
    # window's roster declaration, both of which live on it. It stays duck-typed
    # about them (`hasattr`) so a plain `Repository` remains constructible, which
    # the M4 tests rely on.
    repo = M5Repository(pool)
    store = build_artifact_store(settings)
    return Worker(repo, store, settings), pool


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    ap.add_argument("--drain", action="store_true", help="run until the queue is empty")
    args = ap.parse_args(argv)

    settings = ServiceSettings.from_env()
    worker, pool = build_worker(settings)
    print(f"worker {settings.worker_id}  ·  artifacts -> {settings.artifact_root}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: worker.stop())
        except (ValueError, OSError):                               # pragma: no cover
            pass                                                    # not the main thread

    try:
        outcomes = worker.serve(once=args.once, drain=args.drain)
    finally:
        pool.close()

    for o in outcomes:
        print(f"  job {o.job_id} -> run {o.run_id}: {o.status.name} "
              f"({len(o.artifacts)} artifact(s))")
    # Worker exit status reports the WORKER, not the reconciliation: a run with
    # variances is a successful worker (the variance is recorded and readable).
    # An operator wanting the run's verdict reads run.exit_code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
