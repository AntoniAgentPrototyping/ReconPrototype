"""The worker, end to end, on a synthetic Lazada window.

Synthetic because it must run on a machine with no client data — the same reason
`tools/smoke_test.py` generates its own window, and this reuses that generator
rather than growing a second one.

The claims here are about the wrapper, not the arithmetic: that a job becomes a
run, that the run's own conclusion and the worker's success are recorded on
separate axes, that the log is readable while the run is still going, and that
nothing leaks between two jobs in one process.
"""

from __future__ import annotations

import pytest

from service.models import JobState
from service.repository import Repository
from service.worker import Worker
from src.pipeline import RunStatus

pytest.importorskip("pandas")

from tools.smoke_test import PERIOD, build_window  # noqa: E402


@pytest.fixture
def window(service_settings):
    """A believable Lazada weekly export under the worker's input root."""
    build_window(service_settings.input_root.parent)
    return PERIOD


@pytest.fixture
def worker(repo, store, service_settings):
    return Worker(repo, store, service_settings)


def test_a_job_becomes_a_run_with_artifacts(repo: Repository, worker: Worker, window):
    job, _ = repo.enqueue("lazada", window)
    outcomes = worker.serve(once=True)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert repo.get_job(job.id).state is JobState.DONE

    run = repo.get_run(outcome.run_id)
    assert run.status is RunStatus.UNVERIFIED, "no refs were supplied"
    assert run.exit_code == 2
    assert not run.in_flight

    # The three files a CLI run leaves behind, all indexed.
    assert {a.name for a in repo.artifacts(run.id)} >= {
        "finance_file.xlsx", "run_log.txt", "run_metrics.json"}


def test_metrics_reach_the_run_row(repo: Repository, worker: Worker, window):
    repo.enqueue("lazada", window)
    run_id = worker.serve(once=True)[0].run_id

    run = repo.get_run(run_id)
    assert run.wall_s and run.wall_s > 0
    assert run.io_s and run.io_s > 0
    assert run.peak_rss_mb and run.peak_rss_mb > 0, (
        "peak RSS reporting 0 is the silent-zero failure mode from D27 — a metric "
        "that is always 0 never fires a trigger and never looks broken")


def test_the_stored_log_and_the_database_log_are_the_same_log(
        repo: Repository, worker: Worker, store, window):
    """Two copies, one producer. If they diverge, QueueRunLog has started
    formatting its own text."""
    repo.enqueue("lazada", window)
    run_id = worker.serve(once=True)[0].run_id

    lines, _, complete = repo.log_lines(run_id, limit=5000)
    assert complete
    mirrored = [l.text for l in lines]

    on_disk = store.open(repo.artifact(run_id, "run_log.txt").uri)
    body = on_disk.read_text(encoding="utf-8").split("\n")
    # The file adds a timestamp header (2 lines) and a warnings footer (2 lines);
    # everything between is the mirrored log, in order.
    assert body[2:2 + len(mirrored)] == mirrored


def test_seq_is_gapless_over_a_whole_run(repo: Repository, worker: Worker, window):
    repo.enqueue("lazada", window)
    run_id = worker.serve(once=True)[0].run_id
    lines, _, _ = repo.log_lines(run_id, limit=5000)
    assert [l.seq for l in lines] == list(range(len(lines)))
    assert len(lines) > 20, "a real run emits more than a handful of lines"


def test_the_log_is_written_before_the_run_finishes(
        repo: Repository, store, service_settings, window):
    """The point of mirroring mid-run: an operator can watch a 171-second run
    progress. A log that lands only at the end is a batch job with extra steps."""

    class Observer(Repository):
        def __init__(self, inner: Repository) -> None:
            super().__init__(inner._pool)
            self.order: list[str] = []

        def append_log(self, run_id, rows):
            self.order.append("log")
            return super().append_log(run_id, rows)

        def finish_run(self, *args, **kwargs):
            self.order.append("finish")
            return super().finish_run(*args, **kwargs)

    observer = Observer(repo)
    observer.enqueue("lazada", window)
    Worker(observer, store, service_settings).serve(once=True)

    assert observer.order.count("log") > 1, "the log went in a single batch at the end"
    assert observer.order.index("log") < observer.order.index("finish")


def test_the_lease_is_extended_by_the_log(repo: Repository, store, service_settings, window):
    """Liveness is measured by "is this run still saying anything", so a flush is
    the heartbeat rather than a separate timer that would keep a hung run looking
    healthy."""
    beats: list[int] = []

    class Counting(Repository):
        def heartbeat(self, job_id, worker_id, lease_seconds):
            beats.append(job_id)
            return super().heartbeat(job_id, worker_id, lease_seconds)

    counting = Counting(repo._pool)
    counting.enqueue("lazada", window)
    Worker(counting, store, service_settings).serve(once=True)
    assert len(beats) > 1


# ---------------------------------------------------------------------------
# The two axes: did the worker work, and what did the run conclude
# ---------------------------------------------------------------------------

def test_a_missing_window_is_a_hard_stop_and_a_successful_job(
        repo: Repository, worker: Worker):
    """No input staged. `run()` returns HARD_STOP rather than raising, so the job
    executed correctly and the RUN is what failed — the distinction the schema
    keeps and a single status column would lose."""
    job, _ = repo.enqueue("lazada", "2026-05_never_staged")
    outcome = worker.serve(once=True)[0]

    assert repo.get_job(job.id).state is JobState.DONE
    assert repo.get_job(job.id).error is None
    run = repo.get_run(outcome.run_id)
    assert run.status is RunStatus.HARD_STOP and run.exit_code == 3
    assert run.error, "the reason must be recorded, not just the status"

    # The log still exists — that is the whole point of write_artifacts running
    # even on a hard stop (docs/08-KNOWN-DEFECTS.md#17).
    lines, _, _ = repo.log_lines(run.id, limit=5000)
    assert any("HARD STOP" in l.text for l in lines)


def test_a_hard_stop_is_never_retried(repo: Repository, worker: Worker):
    """Retrying bad input produces the same answer and hides the input problem."""
    job, _ = repo.enqueue("lazada", "2026-05_never_staged", max_attempts=3)
    worker.serve(once=True)
    assert repo.get_job(job.id).state is JobState.DONE
    assert worker.serve(once=True) == [], "nothing left to claim"


def test_a_worker_failure_is_recorded_against_the_job(
        repo: Repository, worker: Worker, window, monkeypatch):
    """Reaching the worker's own except block means the WORKER broke, not the run.
    That is the one case that marks the job ERROR.

    **Changed in M8/4.5 (B1), deliberately.** This test used to assert
    `"worker failure" in run.error` and `"Traceback" in run.error` — it pinned the
    behaviour where the run record carried the formatted traceback, which the run
    page renders directly to whoever opens it. The traceback did not disappear; it
    moved to the run log, and `test_a_worker_failure_reaches_the_run_as_a_sentence_
    and_the_log_as_a_traceback` asserts both halves. `jobs.error` stays technical
    because the job record is the operator's view, not the finance user's.
    """
    from src import pipeline

    def explode(_ctx):
        raise MemoryError("container ran out of memory")

    monkeypatch.setattr(pipeline, "run", explode)

    job, _ = repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    failed = repo.get_job(job.id)
    assert failed.state is JobState.ERROR
    assert "MemoryError" in failed.error
    run = repo.get_run(outcome.run_id)
    assert run.status is RunStatus.HARD_STOP
    assert "ran out of memory" in run.error, "MemoryError has its own sentence"
    assert "Traceback" not in run.error
    lines = "\n".join(l.text for l in repo.log_lines(outcome.run_id, limit=5000)[0])
    assert "Traceback" in lines, "an infrastructure failure needs its traceback SOMEWHERE"


def test_scratch_is_cleaned_up_on_success_and_kept_on_a_hard_stop(
        repo: Repository, worker: Worker, service_settings, window):
    repo.enqueue("lazada", window)
    good = worker.serve(once=True)[0]
    assert good.status is RunStatus.UNVERIFIED
    assert not (service_settings.scratch_root / f"job-{good.job_id}").exists()

    repo.enqueue("lazada", "2026-05_never_staged")
    bad = worker.serve(once=True)[0]
    assert (service_settings.scratch_root / f"job-{bad.job_id}").exists(), (
        "a failed run's working directory is evidence")


# ---------------------------------------------------------------------------
# Isolation between jobs in one process
# ---------------------------------------------------------------------------

def test_each_job_gets_its_own_settings_dict(
        repo: Repository, worker: Worker, window, monkeypatch):
    """Two runs must not share mutable per-run state, so the worker builds a fresh
    context per job rather than caching one.

    **What this test was originally guarding, and what changed.** Until 2026-08-19
    the per-SKU VAT map travelled as `settings["_vat_sku"]` — the config contract
    used as a data channel (defect 1.9) — and a shared dict would have
    cross-contaminated VAT rates between windows. That back-channel is gone: the map
    is now `RunContext.vat_sku`, a field on a frozen dataclass, alongside
    `masters_source` / `masters_searched` which had been a second copy of the same
    pattern.

    The isolation assertion stays, because it is about the *dict*, which is still
    mutable and still per-run (`apply_partial_roster` writes into it). The added
    assertions are the ones that keep the fix from silently reverting: a future
    `settings["_vat_sku"] = ...` would restore the channel while every other test
    still passed.
    """
    from src import pipeline

    seen: list[int] = []
    real = pipeline.build_context

    def spy(*args, **kwargs):
        ctx = real(*args, **kwargs)
        seen.append(id(ctx.settings))
        assert "_vat_sku" not in ctx.settings, (
            "the VAT map is back in the settings dict — defect 1.9 reintroduced")
        assert not [k for k in ctx.settings if str(k).startswith("_")], (
            f"settings carries back-channel keys: "
            f"{[k for k in ctx.settings if str(k).startswith('_')]}")
        assert isinstance(ctx.vat_sku, dict), "the VAT map must reach the run as a field"
        return ctx

    monkeypatch.setattr(pipeline, "build_context", spy)

    repo.enqueue("lazada", window)
    worker.serve(once=True)
    repo.enqueue("lazada", window)
    worker.serve(once=True)

    assert len(seen) == 2 and seen[0] != seen[1]


def test_two_runs_of_one_window_do_not_overwrite_each_others_artifacts(
        repo: Repository, worker: Worker, window):
    repo.enqueue("lazada", window)
    first = worker.serve(once=True)[0]
    repo.enqueue("lazada", window)
    second = worker.serve(once=True)[0]

    a = repo.artifact(first.run_id, "finance_file.xlsx")
    b = repo.artifact(second.run_id, "finance_file.xlsx")
    assert a.uri != b.uri, "run-scoped paths, so a re-run keeps the evidence"


def test_drain_empties_the_queue(repo: Repository, worker: Worker, window):
    repo.enqueue("lazada", window)
    repo.enqueue("lazada", "2026-05_never_staged")
    outcomes = worker.serve(drain=True)
    # THREE, not two: the window that produced a workbook queues its month's
    # master, and drain means drain — a queue that refills while it is being
    # emptied must still end empty (M8 Phase 3, docs/06-DECISIONS.md#d55).
    # The never-staged window hard-stops and chains nothing.
    assert len(outcomes) == 3
    assert [o.job_id for o in outcomes] == [1, 2, 3]
    assert repo.list_jobs(state=JobState.QUEUED) == []


def test_a_cancelled_job_is_never_claimed(repo: Repository, worker: Worker, window):
    job, _ = repo.enqueue("lazada", window)
    repo.cancel_job(job.id)
    assert worker.serve(once=True) == []
    assert repo.run_for_job(job.id) is None


def test_stop_ends_the_loop_without_claiming(repo: Repository, worker: Worker, window):
    repo.enqueue("lazada", window)
    worker.stop()
    assert worker.serve(drain=True) == []
    assert repo.get_job(1).state is JobState.QUEUED


def test_partial_roster_reaches_the_run_and_says_so(
        repo: Repository, worker: Worker, window):
    """The relaxation has to be visible in the audit trail, or a subset run's
    totals get read as the month's (docs/06-DECISIONS.md#d23)."""
    repo.enqueue("lazada", window, partial_roster=True)
    outcome = worker.serve(once=True)[0]

    lines, _, _ = repo.log_lines(outcome.run_id, limit=5000)
    assert any("PARTIAL ROSTER" in l.text for l in lines)
    assert any(l.kind.value == "warning" and "PARTIAL ROSTER" in l.text for l in lines), (
        "a roster relaxation is a warning, not a footnote")


def test_refs_from_the_job_are_used_for_the_tie_out(repo: Repository, worker: Worker, window):
    """A run WITH refs must stop reporting UNVERIFIED — otherwise the refs column
    is decoration."""
    repo.enqueue("lazada", window)
    unverified = worker.serve(once=True)[0]
    assert repo.get_run(unverified.run_id).status is RunStatus.UNVERIFIED

    refs = {"grand": {"pre_vat": 1.0}, "grand_tolerance": 1.0}
    repo.enqueue("lazada", window, refs=refs)
    checked = worker.serve(once=True)[0]
    run = repo.get_run(checked.run_id)
    assert run.status is RunStatus.VARIANCE, (
        "a deliberately wrong reference total must be reported as a variance")
    assert run.variances


# ---------------------------------------------------------------------------
# The unstick path (M8/4.5)
# ---------------------------------------------------------------------------

def test_a_worker_failure_reaches_the_run_as_a_sentence_and_the_log_as_a_traceback(
        repo: Repository, worker: Worker, window, monkeypatch):
    """**B1.** `runs.error` carried `worker failure: TypeError: …` followed by the
    whole formatted traceback, and the run page renders that field directly. A
    finance user opening a failed run got a Python stack.

    Both halves are asserted. Dropping the traceback entirely would be the easy
    way to pass the first assertion and would make the failure undiagnosable.
    """
    def explode(*_args, **_kwargs):
        raise RuntimeError("connection to postgresql://recon:hunter2@db/recon lost")

    monkeypatch.setattr(worker, "_materialize", explode)
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    run = repo.get_run(outcome.run_id)
    assert run.status is RunStatus.HARD_STOP
    assert "Traceback" not in (run.error or "")
    assert "hunter2" not in (run.error or ""), (
        "an unrecognised exception's own text is not shown — it is not written for "
        "this reader and routinely contains a path or a connection string")

    lines = "\n".join(l.text for l in repo.log_lines(outcome.run_id, limit=5000)[0])
    assert "Traceback" in lines, "the detail has to survive somewhere"
    assert "RuntimeError" in lines


def test_a_hard_stop_message_is_not_prefixed_with_its_class_name(
        repo: Repository, worker: Worker, window, monkeypatch):
    """A `ReconHardStop` message is already written for a human and
    `docs/09-OPERATIONS.md` quotes these strings. The `ReconHardStop: ` prefix in
    front of one was jargon, not information."""
    from src.errors import ReconHardStop

    def refuse(*_args, **_kwargs):
        raise ReconHardStop("Store-count check FAILED for lazada/ledger.")

    monkeypatch.setattr("src.lazada.read_ledger", refuse)
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    run = repo.get_run(outcome.run_id)
    assert run.status is RunStatus.HARD_STOP
    assert (run.error or "").startswith("Store-count check FAILED")


def test_the_worker_reports_itself_alive_between_jobs(
        repo: Repository, worker: Worker, service_settings):
    """**C2.** The worker had no healthcheck at all, so a wedged one looked exactly
    like a busy one and Docker would never restart it. It has no HTTP server, so
    the signal is a file touched at the top of each loop turn."""
    assert not worker.heartbeat_path().exists()
    worker.serve(once=True)          # no jobs queued: one turn, then out
    assert worker.heartbeat_path().exists()
    assert worker.heartbeat_path().read_text(encoding="utf-8") == service_settings.worker_id


# ---------------------------------------------------------------------------
# D3 — the declaration names its stores, and a stale one is said aloud
# ---------------------------------------------------------------------------

def test_a_blanket_declaration_still_works_and_is_named_as_one(
        repo: Repository, worker: Worker, window):
    """A declaration with no store list is the pre-021 blanket: every expected
    store optional. It must keep working — a re-run must make the same claim the
    first run did — and it must be CALLED a blanket, because it is exactly the
    state where a forgotten store is waved through."""
    repo.declare_window_roster("lazada", window, partial=True,
                               reason="declared before the store list existed",
                               declared_by="test")
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    assert outcome.status is not RunStatus.HARD_STOP
    lines, _, _ = repo.log_lines(outcome.run_id, limit=5000)
    text = "\n".join(l.text for l in lines)
    assert "ROSTER DECLARED PARTIAL" in text
    assert "blanket declaration" in text
    assert "PARTIAL ROSTER" in text


def test_a_declaration_naming_an_unknown_store_hard_stops_naming_it(
        repo: Repository, worker: Worker, window):
    """The declaration was written against SOME roster; if the run's own config
    does not know the name (a repin, a rename, an empty roster), silently
    ignoring it would resurrect the misleading 'missing store' stop one step
    later. Better to stop HERE with the bad name in the sentence."""
    repo.declare_window_roster("lazada", window, partial=True,
                               reason="names a store lazada's roster lacks",
                               declared_by="test", stores=["Pediasure"])
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    assert outcome.status is RunStatus.HARD_STOP
    run = repo.get_run(outcome.run_id)
    assert "Pediasure" in (run.error or ""), \
        "the hard stop must name the store the roster does not know"


def test_a_stale_declaration_is_said_aloud(repo: Repository, worker: Worker):
    """D3's re-evaluation, on the seam: `_report_stale_declaration` compares the
    declared-absent set against what actually materialised. Unit-level because
    the smoke window materialises from a directory, where there is no upload set
    to compare (and the method must stay silent there — also asserted)."""
    from pathlib import Path

    from service.materialize import Materialization, MaterializedFile
    from src.runlog import RunLog

    class Recording(RunLog):
        def __init__(self):
            self.warned: list[str] = []
        def warn(self, msg):                                    # noqa: D102
            self.warned.append(msg)
        def add(self, msg):                                     # noqa: D102
            pass

    def mat(source: str, stores: list[str], missing: list[str]) -> Materialization:
        m = Materialization(input_root=Path("x"), source=source)
        m.files = [MaterializedFile(upload_id=1, kind="weekly", store=s,
                                    store_canonical=s, original=f"{s}.xlsx",
                                    name=f"{s}.xlsx", object_key="k", sha256="0" * 64,
                                    bytes=1) for s in stores]
        m.missing_stores = missing
        return m

    job = repo.enqueue("lazada", "2026-05_stale")[0]

    # A declared-absent store that now HAS files: warned, with the store named.
    log = Recording()
    worker._report_stale_declaration(job, mat("uploads", ["KAO"], []), ["KAO"], True, log)
    assert any("ROSTER DECLARATION STALE" in w and "KAO" in w for w in log.warned)

    # Blanket declaration on a complete window: warned as outgrown.
    log = Recording()
    worker._report_stale_declaration(job, mat("uploads", ["KAO"], []), None, True, log)
    assert any("every expected store has a file" in w for w in log.warned)

    # A declaration the window has NOT outgrown: silent.
    log = Recording()
    worker._report_stale_declaration(job, mat("uploads", ["KAO"], ["Masan"]),
                                     ["Masan"], True, log)
    assert log.warned == []

    # Directory-staged input: no upload set to compare, so no claim is made.
    log = Recording()
    worker._report_stale_declaration(job, mat("input_root", ["KAO"], []), ["KAO"], True, log)
    assert log.warned == []

    # Not declared partial at all: nothing to re-evaluate.
    log = Recording()
    worker._report_stale_declaration(job, mat("uploads", ["KAO"], []), None, False, log)
    assert log.warned == []
