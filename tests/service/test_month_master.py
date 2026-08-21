"""The month-end master as a chained job (M8 Phase 3, tasks 3.3–3.5).

Synthetic window, for the same reason `test_worker.py` uses one: this must run on
a machine with no client data. The claims are about the *wrapper* — that a
finished window queues a master, that the master's window list comes from the
database rather than a hardcoded table, that it gets its own run record, log and
artifacts, and that a month it cannot fully cover says so instead of looking
complete.
"""

from __future__ import annotations

import pytest

from service.models import ALL_PLATFORMS, JobKind, JobState
from service.worker import Worker
from src.pipeline import RunStatus

pytest.importorskip("pandas")

from tools.smoke_test import PERIOD, build_window  # noqa: E402

MONTH = PERIOD.split("_", 1)[0]


@pytest.fixture
def window(service_settings):
    build_window(service_settings.input_root.parent)
    return PERIOD


@pytest.fixture
def worker(repo, store, service_settings):
    return Worker(repo, store, service_settings)


def _log(repo, run_id: int) -> str:
    """The run log as one string. `log_lines` returns (lines, next_seq, complete)."""
    lines, _, _ = repo.log_lines(run_id, limit=5000)
    return "\n".join(line.text for line in lines)


def _drain(worker: Worker, limit: int = 5) -> list:
    """Run queued jobs until the queue is empty. The master is a SECOND job."""
    return worker.serve(drain=True)[:limit]


# ---------------------------------------------------------------------------
# 3.4 — the chain
# ---------------------------------------------------------------------------

def test_a_finished_window_queues_the_month_master(repo, worker, window):
    repo.enqueue("lazada", window)
    worker.serve(once=True)                 # the window run only

    queued = [j for j in repo.list_jobs() if j.kind is JobKind.MONTH_MASTER]
    assert len(queued) == 1, "a successful window must queue its month's master"
    assert queued[0].platform == ALL_PLATFORMS
    assert queued[0].period == MONTH, "the master's period is the MONTH"
    assert queued[0].state is JobState.QUEUED


def test_the_master_is_its_own_run_with_its_own_artifacts(repo, worker, window):
    repo.enqueue("lazada", window)
    outcomes = _drain(worker)

    assert len(outcomes) == 2, "one window run, then one master run"
    master = outcomes[-1]
    job = repo.get_job(master.job_id)
    assert job.kind is JobKind.MONTH_MASTER
    assert repo.get_job(job.id).state is JobState.DONE

    names = {a.name for a in repo.artifacts(master.run_id)}
    assert "month_master.xlsx" in names, "the master is a deliverable of its own"
    assert "finance_file.xlsx" not in names, \
        "the master must not overwrite or duplicate a settlement workbook"
    assert "run_log.txt" in names

    # And it has a readable log, like any other run.
    text = _log(repo, master.run_id)
    assert "MONTH-END MASTER" in text
    assert PERIOD in text, "the log must name the windows it consolidated"


def test_a_hard_stop_queues_nothing(repo, worker, service_settings):
    """A window that produced no workbook has nothing to consolidate."""
    repo.enqueue("lazada", "2026-01_l9")     # no input staged for it
    worker.serve(once=True)

    assert repo.get_run(1).status is RunStatus.HARD_STOP
    assert not [j for j in repo.list_jobs() if j.kind is JobKind.MONTH_MASTER]


def test_a_second_window_does_not_queue_a_second_master(repo, worker, window):
    """Several windows of a month finish close together. The queued master will
    read whichever have finished when it runs, so a second one is pure waste."""
    repo.enqueue("lazada", window)
    worker.serve(once=True)
    # Simulate another window of the same month finishing while the master waits.
    repo.enqueue("lazada", f"{MONTH}_l9")
    worker.serve(once=True)

    masters = [j for j in repo.list_jobs() if j.kind is JobKind.MONTH_MASTER]
    assert len(masters) == 1


# ---------------------------------------------------------------------------
# 3.3 — the window list comes from the database
# ---------------------------------------------------------------------------

def test_the_window_list_is_whatever_ran(repo, worker, window):
    """Not a hardcoded table. `month_windows` must report the window that ran."""
    repo.enqueue("lazada", window)
    worker.serve(once=True)

    rows = repo.month_windows(MONTH)
    assert any(r["platform"] == "lazada" and r["period"] == PERIOD
               and r["status"] in ("ok", "variance", "unverified") for r in rows)


def test_a_sub_batch_window_is_listed(repo, worker, window):
    """`s2x`-shaped windows had no row in the hardcoded table this replaced (A5),
    and that table's own tie check re-read it, so the omission was invisible.

    Also covers the other half of `month_windows`: a window known ONLY from a
    declaration — nobody has run it — must still be listed, because that is what
    makes "missing" reportable rather than silently absent.
    """
    repo.enqueue("lazada", window)
    worker.serve(once=True)
    assert PERIOD in {r["period"] for r in repo.month_windows(MONTH)}

    odd = f"{MONTH}_l2x"
    repo.declare_window_roster("lazada", odd, partial=False, reason=None,
                               declared_by="test")
    listed = {r["period"]: r for r in repo.month_windows(MONTH)}
    assert odd in listed
    assert listed[odd]["latest_run_id"] is None, "it has never run"


# ---------------------------------------------------------------------------
# 3.5 — a partial master says so
# ---------------------------------------------------------------------------

def test_a_month_with_an_unrun_window_reports_it_as_missing(repo, worker, window):
    """The master is rebuilt whenever a window finishes, so it is partial for most
    of the month. Looking complete when it is not is the failure this prevents."""
    repo.enqueue("lazada", window)
    worker.serve(once=True)
    # A window the system knows about (declared) but which has never run.
    repo.declare_window_roster("lazada", f"{MONTH}_l7", partial=False, reason=None,
                               declared_by="test")

    outcomes = _drain(worker)
    master = outcomes[-1]
    run = repo.get_run(master.run_id)

    assert run.status is RunStatus.UNVERIFIED, \
        "a master that does not cover its month is not OK"
    assert any(f"{MONTH}_l7" in message for _, message in run.findings)
    text = _log(repo, master.run_id)
    assert "MISSING" in text and f"{MONTH}_l7" in text


def test_a_master_with_nothing_to_consolidate_stops(repo, worker):
    """Rather than writing an empty workbook that looks like a month."""
    repo.enqueue(ALL_PLATFORMS, "2031-01", kind=JobKind.MONTH_MASTER.value)
    outcomes = worker.serve(once=True)

    assert outcomes[0].status is RunStatus.HARD_STOP
    run = repo.get_run(outcomes[0].run_id)
    assert "nothing to consolidate" in (run.error or "").lower() \
        or "no window" in (run.error or "").lower()


# ---------------------------------------------------------------------------
# A4 — the chain's outcome is durable, and a person can queue a master
# ---------------------------------------------------------------------------

def test_the_chain_outcome_is_on_the_run_row(repo, worker, window):
    """The chain cannot write to the run log (it is already stored), and worker
    stdout is not a record. `runs.chained` is where the sentence lands (019)."""
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    run = repo.get_run(outcome.run_id)
    assert run.chained is not None
    assert "queued the month-end master" in run.chained
    assert run.chained == outcome.chained, \
        "the stdout sentence and the stored sentence must be the same sentence"


def test_a_hard_stop_chains_nothing_and_says_nothing(repo, worker, service_settings):
    repo.enqueue("lazada", "2026-01_l9")     # no input staged for it
    outcome = worker.serve(once=True)[0]
    assert repo.get_run(outcome.run_id).chained is None


def test_a_master_can_be_queued_by_hand(repo, make_client):
    """A4's manual trigger: rebuilding a master must not require re-running a
    window as a side effect — that would be a second settlement run."""
    client = make_client("recon.user", username="planner@ada")
    r = client.post(f"/months/{MONTH}/master")
    assert r.status_code == 201

    body = r.json()
    assert body["platform"] == ALL_PLATFORMS
    assert body["period"] == MONTH
    assert body["kind"] == "month_master"
    # From the session, never a body field — the standing audit rule.
    assert body["requested_by"] == "planner@ada"


def test_a_second_hand_queued_master_is_refused_while_one_waits(repo, make_client):
    client = make_client("recon.user")
    assert client.post(f"/months/{MONTH}/master").status_code == 201
    r = client.post(f"/months/{MONTH}/master")
    assert r.status_code == 409, \
        "one master in flight per month — the queued one will read every " \
        "window finished by the time it runs"


def test_a_malformed_month_is_refused_not_queued(make_client):
    """A wrong month would not error later — it would queue a master that covers
    nothing and report a clean-looking empty file. Refuse it at the door."""
    client = make_client("recon.user")
    assert client.post("/months/2026-13/master").status_code == 422
    assert client.post("/months/2026-05_l1/master").status_code == 422
    assert client.post("/months/202607/master").status_code == 422
