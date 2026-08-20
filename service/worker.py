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

**One job at a time, per process.** Not a thread pool. Two reasons, and the order
changed on 2026-08-19:

1. **Memory.** A window's frames peak well into the GBs, and peak RSS is the
   binding constraint on the container (`docs/10-ROADMAP.md` reads it for the
   engine-port trigger). Two windows in one process is the one way to double it.
2. **Per-run mutable state.** `build_context` returns a fresh settings dict per job
   and `apply_partial_roster` writes into it, so a cached context would leak a
   roster relaxation from one window into the next.

The *original* first reason was `settings["_vat_sku"]`, a mutable back-channel
inside the settings dict that would have cross-contaminated VAT rates between
windows (defect 1.9). That channel is gone — the map is `RunContext.vat_sku`, a
field on a frozen dataclass — so it is no longer the argument for this design, and
`tests/service/test_worker.py::test_each_job_gets_its_own_settings_dict` now asserts
it has not come back. The design itself is unchanged: concurrency comes from
running more worker PROCESSES, which is what `FOR UPDATE SKIP LOCKED` is for.
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

from src import master_summary, pipeline
from src.pipeline import RunStatus

from .artifacts import ArtifactStore, build_artifact_store
from .config import ServiceSettings
from .models import Job, JobKind, JobState
from .repository import Repository
from .runlog import QueueRunLog, RepositoryLogSink


@dataclass
class JobOutcome:
    job_id: int
    run_id: int
    status: RunStatus
    artifacts: list[str]
    # What this job queued next, if anything (M8 Phase 3). Carried here rather
    # than written to the run log, because the log has already been flushed and
    # stored by the time the chain runs — see `_chain_month_master`.
    chained: str | None = None


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

    def heartbeat_path(self) -> Path:
        """Where this worker says it is alive (**C2**).

        A file, not an HTTP endpoint. The worker has no server and giving it one
        to answer a healthcheck would mean a port, a framework and a second thing
        that can fail — in a process whose entire job is to hold one settlement
        run at a time.

        Touched at the top of every loop turn. That makes it a real liveness
        signal rather than a "the process exists" signal: a worker wedged inside
        `pipeline.run` stops touching it, which is exactly the state that needs
        noticing. The threshold has to allow for a long run — see the Dockerfile,
        which sets it from the lease rather than from the poll interval.
        """
        return self.settings.scratch_root / "worker.alive"

    def _touch_heartbeat(self) -> None:
        try:
            path = self.heartbeat_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.settings.worker_id, encoding="utf-8")
        except OSError:                                         # pragma: no cover
            # A worker that cannot write its heartbeat is a worker that will be
            # reported unhealthy, which is the correct outcome. It must not be a
            # worker that crashes instead of doing its job.
            pass

    def serve(self, *, once: bool = False, drain: bool = False) -> list[JobOutcome]:
        outcomes: list[JobOutcome] = []
        while not self._stopping:
            self._touch_heartbeat()
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

        if job.kind is JobKind.MONTH_MASTER:
            return self._execute_month_master(job, run_id, log, scratch)

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
            self._report_order_coverage(job, log)
            partial = self._roster_declared_partial(job, log)
            refs = self._references(job, log)

            # A fresh settings dict per job — see the module docstring.
            ctx = pipeline.build_context(
                job.platform, job.period,
                config_dir=self.settings.config_dir,
                input_root=mat.input_root,
                output_root=scratch,
                refs=refs, log=log, partial_roster=partial,
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
            # After the log is flushed and the artifacts are stored — see the
            # method's docstring for why both orderings matter.
            chained = self._chain_month_master(job, result)

            self.repo.finish_run(
                run_id, status=result.status, findings=result.findings,
                checks=result.checks, metrics=result.metrics.to_dict(),
                roster_missing=mat.roster_missing,
                # B1: a `ReconHardStop` message is already written for a human,
                # and `docs/09-OPERATIONS.md` is written against these strings, so
                # it passes through untouched. The class-name prefix is dropped —
                # it was jargon in front of a sentence, and nothing asserts on it.
                error=None if result.error is None
                else (str(result.error) or type(result.error).__name__))
            self.repo.finish_job(job.id, JobState.DONE)

            # A hard stop is a job that ran and a run that concluded "nothing was
            # produced" — the two axes the schema keeps apart. The job is DONE
            # either way; retrying bad input just produces the same answer
            # (docs/06-DECISIONS.md#d3).
            self._cleanup(scratch, keep=result.error is not None)
            return JobOutcome(job.id, run_id, result.status, stored, chained=chained)

        except BaseException as exc:                                # noqa: BLE001
            # Reaching here means the WORKER broke, not the run: run() catches
            # data problems itself and returns HARD_STOP. So record it against
            # the job as an infrastructure failure and leave the scratch
            # directory behind for diagnosis.
            from . import failures

            detail = failures.technical(exc)
            try:
                # B1: the traceback goes to the LOG, which is where detail belongs
                # and which the run page already renders on demand. Not a disclosure
                # improvement — both routes are VIEWER — but the first thing a
                # person sees stops being a stack trace.
                log.warn(f"worker failure: {detail}")
                log.warn("".join(traceback.format_exception(exc)).rstrip())
                log.flush()
            except Exception:                                       # pragma: no cover
                pass
            self.repo.finish_run(run_id, status=RunStatus.HARD_STOP, findings=[],
                                 error=failures.humanise(exc))
            # `jobs.error` stays technical: the job record is the operator's view
            # (`service/admin.py job list`), not the finance user's.
            self.repo.finish_job(job.id, JobState.ERROR, error=detail)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return JobOutcome(job.id, run_id, RunStatus.HARD_STOP, [])

    # -- the month-end master (M8 Phase 3) ----------------------------------

    def _chain_month_master(self, job: Job, result) -> str | None:
        """Queue the month's master after a window that produced a workbook.

        Returns a one-line description for the caller to record, or None.

        **Runs AFTER this run's artifacts are stored, and writes nothing to the
        run log.** Both halves were found by tests already in the tree.

        *After the artifacts*, because the master reads each window's
        `finance_file.xlsx` out of the artifact store. Queue it first and another
        worker can claim it in the gap before `_store_artifacts` commits, which
        would exclude this very window from its own month's master.

        *Not into the run log*, because `write_artifacts` has already written
        `run_log.txt` by this point. A line added here would exist in the database
        copy and not in the stored one, and
        `test_the_stored_log_and_the_database_log_are_the_same_log` exists to say
        those two must be the same log. The queued job is visible on the board and
        in `python -m service.admin job list`, which is where a queued job belongs.

        **Best-effort.** The settlement workbook is already written and stored; a
        cross-month aggregation that cannot even be QUEUED must not turn a good
        settlement run into a failed one (task 3.4).

        `ActiveJobExists` is the normal case, not an error: several windows of a
        month commonly finish close together, and the master already queued will
        read whichever windows have finished when it runs. A second would build
        the same file twice.
        """
        if result.status is RunStatus.HARD_STOP:
            return None
        from .models import ALL_PLATFORMS
        from .month_master import month_of
        from .repository import ActiveJobExists

        month = month_of(job.period)
        try:
            chained, created = self.repo.enqueue(
                ALL_PLATFORMS, month, kind=JobKind.MONTH_MASTER.value,
                requested_by=f"run {result.context.period}",
                priority=-1)          # behind settlement work: it is a summary
        except ActiveJobExists:
            return f"month-end master for {month} already queued"
        except Exception as exc:                                # noqa: BLE001
            return (f"could not queue the month-end master for {month}: "
                    f"{type(exc).__name__}: {exc} (this window's finance file is "
                    f"unaffected)")
        return (f"queued the month-end master for {month} (job {chained.id})"
                if created else None)

    def _execute_month_master(self, job: Job, run_id: int, log, scratch: Path) -> JobOutcome:
        """Consolidate a month's finished windows. Its own run, log and artifacts.

        Adds no arithmetic: `src/master_summary.build` does the aggregation and
        returns an unwritten Workbook, and `pipeline.write_artifacts` writes it —
        the same single writer the settlement path uses (D31).
        """
        from src import config as src_config

        from . import month_master

        try:
            settings = src_config.load_settings(self.settings.config_dir)
            log.section(f"MONTH-END MASTER {job.period}")
            coverage, windows = month_master.collect(
                self.repo, self.store, job.period, scratch=scratch, log=log,
                built_by=job.requested_by or "")

            ctx = pipeline.RunContext(
                platform=job.platform, period=job.period,
                input_root=scratch, output_root=scratch,
                config_dir=self.settings.config_dir, settings=settings, log=log)
            result = pipeline.RunResult(context=ctx, workbook_name="month_master.xlsx")
            result.workbook = master_summary.build(
                coverage, windows, month_master.brand_map(self.settings.config_dir))

            written = pipeline.write_artifacts(result)
            stored = self._store_artifacts(job, run_id, written)

            # A partial master is NOT a variance — nothing disagrees. It is a
            # master that does not yet cover the month, which is the normal state
            # for most of it. UNVERIFIED says "completed, but do not read this as
            # the month's total", which is exactly right.
            status = RunStatus.OK if coverage.complete else RunStatus.UNVERIFIED
            findings = [("unverified",
                         f"{platform} {period} is not in this master — {why}")
                        for platform, period, why in coverage.missing]
            log.add(f"  status: {status.name}")
            log.flush()

            self.repo.finish_run(run_id, status=status, findings=findings,
                                 checks=[], metrics=result.metrics.to_dict())
            self.repo.finish_job(job.id, JobState.DONE)
            self._cleanup(scratch, keep=False)
            return JobOutcome(job.id, run_id, status, stored)

        except BaseException as exc:                            # noqa: BLE001
            from . import failures

            detail = failures.technical(exc)
            try:
                log.warn(f"month-end master failed: {detail}")
                log.warn("".join(traceback.format_exception(exc)).rstrip())
                log.flush()
            except Exception:                                   # pragma: no cover
                pass
            self.repo.finish_run(run_id, status=RunStatus.HARD_STOP, findings=[],
                                 error=failures.humanise(exc))
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

    def _report_order_coverage(self, job: Job, log) -> None:
        """Say, before the run, which settled orders this window's own exports miss.

        **Log lines only.** The worker adds no compute to the money path
        ([D31](../docs/06-DECISIONS.md#d31)); the authoritative per-store coverage is
        computed inside the run by `tieout.coverage_by_store` from the frames it
        actually reads. This is the same question asked of the *uploads*, which is the
        only place the CROSS-window half can be answered — an order's lines living in a
        sibling window's export is invisible to a run that only ever opens its own
        window's folder (defect 2.12).

        The two halves are deliberately different in tone. Orders missing from this
        window's own export are **expected to have traffic** — the documented ~21%
        reconciling class, which the team's own VLOOKUP drops too — so they are
        reported as counts. Orders whose lines sit in an *earlier* window's export are
        the shape with **zero** legitimate traffic, so each one names the window, the
        file and the upload id: that is a sentence somebody can act on.

        Guarded on the repository, like `_roster_declared_partial`: an M4 repository has
        neither method and a window with no indexed uploads answers empty, so this is
        silent rather than broken in both cases. Never fatal — a report that cannot be
        produced must not stop a settlement run.
        """
        if not (hasattr(self.repo, "order_coverage")
                and hasattr(self.repo, "cross_window_order_holders")):
            return
        try:
            coverage = self.repo.order_coverage(job.platform, job.period)
            holders = self.repo.cross_window_order_holders(job.platform, job.period)
        except Exception as exc:                            # noqa: BLE001
            log.warn(f"order coverage not reported: {exc}")
            return

        for row in coverage:
            if row.get("unmatched_orders"):
                # `add`, not `warn`: this class is expected to have traffic, and a
                # warning per store would make the counter meaningless.
                log.add(
                    f"order coverage {row['store']}: {row['unmatched_orders']:,} of "
                    f"{row['income_orders']:,} settled orders have no lines in this "
                    f"window's own order export")
        for row in holders:
            log.warn(
                f"CROSS-WINDOW ORDERS {row['store']}: {row['orders']:,} settled "
                f"order(s) have their lines in {row['holder_period']} "
                f"({row['filename']}, upload {row['upload_id']}) and not in this "
                f"window. This window's export does not cover what it settles "
                f"(defect 2.12); the revenue leaves the invoice as unmatched.")

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

    def _references(self, job: Job, log) -> dict:
        """The team's figures this run gets to be checked against (A3).

        Read from the WINDOW, the same way `partial_roster` is: a re-run must make
        the same claim the first run did, and `jobs.refs` is per job. A job that
        carries its own refs still wins — `tools/devrun.py --refs` and the M4 api
        both pass them explicitly, and an explicit answer is not overridden by a
        standing one (`service/references.merge`).

        Logged either way. "This run was compared against figures supplied by X" and
        "nothing corroborated this run" are different claims and the log must not
        leave them looking the same.
        """
        from . import references as references_lib

        record = (self.repo.window_references(job.platform, job.period)
                  if hasattr(self.repo, "window_references") else None)
        merged = references_lib.merge((record or {}).get("refs"), job.refs)
        if record is not None:
            log.add(f"  reference totals supplied by {record['supplied_by']} on "
                    f"{record['supplied_at']:%Y-%m-%d}: "
                    f"{references_lib.summarise(job.platform, merged)}")
        elif not merged.get("grand"):
            log.warn(f"no reference totals for {job.platform} {job.period} — this "
                     f"run will report UNVERIFIED, meaning it completed but nothing "
                     f"corroborated its numbers")
        return merged

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
        if o.chained:
            print(f"      {o.chained}")
    # Worker exit status reports the WORKER, not the reconciliation: a run with
    # variances is a successful worker (the variance is recorded and readable).
    # An operator wanting the run's verdict reads run.exit_code.
    return 0


if __name__ == "__main__":
    sys.exit(main())
