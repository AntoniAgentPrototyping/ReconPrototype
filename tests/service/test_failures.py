"""A worker crash becomes a sentence, not a stack trace (**B1**).

**What this is and is not.** `GET /runs/{id}` and `GET /runs/{id}/log` are both
`VIEWER`, so moving a traceback from one to the other restricts nothing and this
is not a disclosure fix. It is a legibility fix: the first thing on the run page
stops being a Python stack and becomes something a finance user can act on, with
the detail one click away in the log.

The distinction being kept is the one the schema already draws. `run()` catches
data problems and returns `HARD_STOP` with a message written for a human, and
`docs/09-OPERATIONS.md` is written against those strings — so those pass through
untouched. Only *infrastructure* failures are translated.
"""

from __future__ import annotations

import pytest

from service import failures


def test_a_known_infrastructure_failure_gets_a_sentence():
    """Matched on the class NAME so this module imports nothing — it has to be safe
    to call from the failure path of a worker whose imports may be what broke. That
    also means a driver's own `OperationalError` matches without psycopg being
    importable here, which is the point of the name-matching."""
    class OperationalError(Exception):
        pass

    message = failures.humanise(OperationalError("connection to db failed"))
    assert "database became unreachable" in message
    assert "OperationalError" not in message, "a type name is not an explanation"
    assert "connection to db failed" not in message


def test_an_exception_that_already_speaks_to_this_reader_is_left_alone():
    """**Caught by three materialize tests, and the right answer was theirs.**

    The first version of this module gave `MaterializationError` a canned sentence.
    But its own docstring promises to "always name the file or the store", and the
    tests assert on that text — `"not in the store" in run.error`. Replacing
    `upload 3 (6. income Unilever.xlsx) is recorded but its bytes are not in the
    store` with "this window's files could not be assembled" throws away the most
    actionable message in the system to satisfy a rule aimed at Python's own.

    `ReconHardStop` is the same case and was exempt from the start;
    `docs/09-OPERATIONS.md` quotes those strings verbatim.
    """
    from service.materialize import MaterializationError
    from src.errors import ReconHardStop

    named = "upload 7 (6. income KAO.xlsx) is recorded but its bytes are not in the store"
    assert failures.humanise(MaterializationError(named)) == named

    stop = "Store-count check FAILED for shopee/orders. Missing stores: ['KAO']."
    assert failures.humanise(ReconHardStop(stop)) == stop


def test_an_unknown_failure_does_not_leak_its_message():
    """**The property that makes the fallback safe.** An unrecognised exception's
    text is by definition something nobody wrote for this audience — half of them
    are a file path or a connection string. Interpolating `str(exc)` would be the
    obvious implementation and would put exactly that on screen."""
    secret = "could not connect to postgresql://recon:hunter2@db:5432/recon"
    message = failures.humanise(RuntimeError(secret))
    assert "hunter2" not in message
    assert "postgresql" not in message
    assert "run log" in message, "and it has to say where the detail IS"


def test_a_subclass_gets_its_parents_sentence():
    """Matched along the MRO, so a driver's specific error class inherits the
    sentence written for the general one rather than falling back to nothing."""
    class SomeDriverOperationalError(OSError):
        pass

    assert failures.humanise(SomeDriverOperationalError()) == failures.humanise(OSError())


def test_the_technical_form_is_type_and_message_only():
    """What goes in the LOG. No traceback here — the caller writes that
    separately, so this is safe to put in a single log line."""
    line = failures.technical(ValueError("boom"))
    assert line == "ValueError: boom"
    assert "\n" not in line


@pytest.mark.parametrize("exc", [
    MemoryError(), PermissionError(), FileNotFoundError(), TimeoutError(),
])
def test_every_named_failure_says_whether_money_was_touched(exc):
    """The one question a person actually has. Each sentence answers it, because
    "did this write a finance file" is not something they should have to infer."""
    message = failures.humanise(exc)
    assert any(phrase in message.lower()
               for phrase in ("nothing was invoiced", "did not finish", "not a problem with your data")), message


# ---------------------------------------------------------------------------
# The unstick path over HTTP (C1)
# ---------------------------------------------------------------------------

pytest.importorskip("httpx")


def test_reclaim_is_admin_only(make_client):
    """It closes out someone else's in-flight work, and if a lease expired on a
    worker that is alive but slow it ends a run that was going to finish."""
    assert make_client("recon.user").post("/jobs/reclaim").status_code == 403
    assert make_client("recon.viewer").post("/jobs/reclaim").status_code == 403


def test_reclaiming_nothing_says_so_rather_than_succeeding_silently(make_client):
    """**The point of the button.** "Nothing to reclaim" and "closed out three
    jobs" are different answers to "is something stuck", and a control that looked
    identical either way would teach an operator nothing."""
    response = make_client("recon.admin").post("/jobs/reclaim")
    assert response.status_code == 200
    body = response.json()
    assert body["requeued"] == [] and body["failed"] == []
    assert "every lease is still live" in body["message"].lower()


def test_a_dead_workers_job_is_closed_out_without_being_retried(repo, make_client):
    """The situation this exists for: a worker dies mid-run, its job stays
    `leased`, and the board shows the window running forever. The sweep that fixes
    it runs inside the worker loop — useless when the dead worker was the only one.

    It must NOT re-run the window. `max_attempts` defaults to 1 because an
    automatic retry of a settlement run is a second write of the same money (D30),
    so the job is marked failed and a person decides what happens next.
    """
    job, _ = repo.enqueue("lazada", "2026-05_stuck")
    repo.claim("worker-that-died", lease_seconds=-1)             # already expired

    body = make_client("recon.admin").post("/jobs/reclaim").json()
    assert body["requeued"] == [], "default max_attempts=1 means no automatic retry"
    assert job.id in body["failed"]
    assert repo.get_job(job.id).state.value == "error"
