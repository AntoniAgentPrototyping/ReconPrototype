"""The queue's claims, against a real Postgres.

These are the tests that could not be faked. `FOR UPDATE SKIP LOCKED` is a
statement about what two transactions do to each other, and lease expiry is a
statement about `now()`; a stand-in database or an in-process double would prove
only that the double behaves as written.

The one that matters most is `test_skip_locked_never_double_leases`. Everything
else in M4 is bookkeeping; if that claim is false, two workers reconcile the same
settlement window at the same time and the service becomes a way to invoice
twice.
"""

from __future__ import annotations

import threading

import pytest

from service import db
from service.models import JobState
from service.repository import ActiveJobExists, NotFound, Repository
from src.pipeline import RunStatus


def test_migrations_are_idempotent(pool):
    with pool.connection() as conn:
        assert db.migrate(conn) == [], "the session fixture already migrated"


def test_editing_an_applied_migration_is_refused(pool, tmp_path, monkeypatch):
    """Two databases claiming the same history with different schemas is the one
    failure a migration table exists to prevent."""
    other = tmp_path / "migrations"
    other.mkdir()
    (other / "900_probe.sql").write_text("create table probe_a (id int)", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", other)

    with pool.connection() as conn:
        assert db.migrate(conn) == ["900_probe.sql"]

        (other / "900_probe.sql").write_text("create table probe_b (id int)", encoding="utf-8")
        with pytest.raises(RuntimeError, match="has been edited since it was applied"):
            db.migrate(conn)

        with conn.cursor() as cur:
            cur.execute("drop table probe_a")
            cur.execute("delete from schema_migrations where filename = '900_probe.sql'")
        conn.commit()


# ---------------------------------------------------------------------------
# The claim protocol
# ---------------------------------------------------------------------------

def test_claim_on_an_empty_queue_returns_none(repo: Repository):
    assert repo.claim("w1", 60) is None


def test_claim_marks_the_lease_completely(repo: Repository):
    repo.enqueue("lazada", "2026-05_l1")
    job = repo.claim("w1", 60)
    assert job is not None
    assert job.state is JobState.LEASED
    assert job.leased_by == "w1"
    assert job.attempts == 1
    # jobs_lease_is_complete would have rejected the row otherwise, but assert it
    # here too: the reclaim sweep reads both fields and a null expiry would make
    # a dead lease immortal.
    assert job.lease_expires_at is not None


def test_skip_locked_never_double_leases(pool, repo: Repository):
    """Eight threads, five jobs, one instant.

    Without SKIP LOCKED the claim query serializes: each transaction waits on the
    row the previous one locked, then re-reads it — the classic double-dispatch.
    The assertion is on the multiset of claimed ids, not just the count, because
    "5 claims" would also be satisfied by one job handed out five times.
    """
    for i in range(5):
        repo.enqueue("lazada", f"2026-05_l{i + 1}")

    claimed: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            barrier.wait(timeout=10)
            job = repo.claim(f"w{n}", 60)
            if job is not None:
                with lock:
                    claimed.append(job.id)
        except BaseException as exc:                                # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    assert sorted(claimed) == [1, 2, 3, 4, 5], (
        f"five queued jobs must be leased exactly once each, got {sorted(claimed)}")
    assert len(claimed) == len(set(claimed)), "a job was leased twice"

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from jobs where state = 'queued'")
        assert cur.fetchone()[0] == 0


def test_priority_then_fifo(repo: Repository):
    low, _ = repo.enqueue("lazada", "2026-05_l1")
    high, _ = repo.enqueue("lazada", "2026-05_l2", priority=10)
    later_high, _ = repo.enqueue("lazada", "2026-05_l3", priority=10)

    assert [repo.claim("w", 60).id for _ in range(3)] == [high.id, later_high.id, low.id]


# ---------------------------------------------------------------------------
# The double-run guard
# ---------------------------------------------------------------------------

def test_a_second_live_job_for_one_window_is_refused(repo: Repository):
    first, created = repo.enqueue("tiktok", "2026-05_w1")
    assert created

    with pytest.raises(ActiveJobExists) as exc:
        repo.enqueue("tiktok", "2026-05_w1")
    assert exc.value.existing.id == first.id

    # A leased job is still live.
    repo.claim("w1", 60)
    with pytest.raises(ActiveJobExists):
        repo.enqueue("tiktok", "2026-05_w1")

    # ...and once it is finished, a deliberate re-run is allowed again. That is
    # the point of scoping the index to queued/leased: re-running a window after
    # fixing a column map is normal.
    repo.finish_job(first.id, JobState.DONE)
    second, created = repo.enqueue("tiktok", "2026-05_w1")
    assert created and second.id != first.id


def test_the_guard_is_per_window_not_per_platform(repo: Repository):
    repo.enqueue("tiktok", "2026-05_w1")
    repo.enqueue("tiktok", "2026-05_w2")            # different window, fine
    repo.enqueue("shopee", "2026-05_w1")            # different platform, fine
    assert len(repo.list_jobs()) == 3


def test_idempotency_key_returns_the_same_job(repo: Repository):
    first, created = repo.enqueue("lazada", "2026-05_l1", idempotency_key="abc")
    assert created
    again, created = repo.enqueue("lazada", "2026-05_l1", idempotency_key="abc")
    assert not created and again.id == first.id
    assert len(repo.list_jobs()) == 1


def test_a_different_key_for_a_live_window_still_hits_the_guard(repo: Repository):
    """The two guards are not interchangeable: an idempotency key protects a
    retried request, the index protects the window."""
    repo.enqueue("lazada", "2026-05_l1", idempotency_key="one")
    with pytest.raises(ActiveJobExists):
        repo.enqueue("lazada", "2026-05_l1", idempotency_key="two")


# ---------------------------------------------------------------------------
# Leases that go stale
# ---------------------------------------------------------------------------

def test_expired_lease_is_requeued_while_attempts_remain(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1", max_attempts=2)
    claimed = repo.claim("dead-worker", -1)          # already expired
    assert claimed.attempts == 1

    assert repo.reclaim_expired() == {"requeued": [job.id], "dead": []}
    assert repo.get_job(job.id).state is JobState.QUEUED

    # And it can be picked up again, by a different worker.
    again = repo.claim("live-worker", 60)
    assert again.id == job.id and again.attempts == 2


def test_expired_lease_with_no_attempts_left_becomes_an_error(repo: Repository):
    """The default is max_attempts=1, so a dead worker leaves a job a human must
    look at. Automatic retry of a settlement run is a second write of the same
    money (docs/06-DECISIONS.md#d3)."""
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("dead-worker", -1)

    assert repo.reclaim_expired() == {"requeued": [], "dead": [job.id]}
    reclaimed = repo.get_job(job.id)
    assert reclaimed.state is JobState.ERROR
    assert "lease expired" in reclaimed.error
    assert reclaimed.finished_at is not None


def test_reclaim_closes_the_run_the_dead_worker_left_open(repo: Repository):
    """`finished_at is null` has to keep meaning "actually running", or every
    crashed run looks like a run in progress forever."""
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("dead-worker", -1)
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    assert repo.get_run(run_id).in_flight

    repo.reclaim_expired()
    run = repo.get_run(run_id)
    assert not run.in_flight
    assert run.status is RunStatus.HARD_STOP
    assert run.exit_code == 3
    assert "stopped reporting" in run.error


def test_reclaim_leaves_live_leases_alone(repo: Repository):
    repo.enqueue("lazada", "2026-05_l1")
    repo.claim("live", 600)
    assert repo.reclaim_expired() == {"requeued": [], "dead": []}
    assert repo.get_job(1).state is JobState.LEASED


def test_heartbeat_only_works_for_the_lease_holder(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("mine", 1)

    assert repo.heartbeat(job.id, "mine", 600) is True
    assert repo.heartbeat(job.id, "someone-else", 600) is False, (
        "a worker that lost its lease must learn that from the heartbeat")

    # Extended, so the sweep leaves it alone.
    assert repo.reclaim_expired() == {"requeued": [], "dead": []}


def test_heartbeat_on_a_finished_job_reports_false(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("mine", 60)
    repo.finish_job(job.id, JobState.DONE)
    assert repo.heartbeat(job.id, "mine", 60) is False


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def test_only_a_queued_job_can_be_cancelled(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    assert repo.cancel_job(job.id).state is JobState.CANCELLED

    other, _ = repo.enqueue("lazada", "2026-05_l2")
    repo.claim("w", 60)
    with pytest.raises(ValueError, match="not queued"):
        repo.cancel_job(other.id)


def test_cancelling_an_unknown_job_is_not_found(repo: Repository):
    with pytest.raises(NotFound):
        repo.cancel_job(9999)


def test_a_cancelled_window_frees_the_guard(repo: Repository):
    first, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.cancel_job(first.id)
    second, created = repo.enqueue("lazada", "2026-05_l1")
    assert created and second.id != first.id


# ---------------------------------------------------------------------------
# The run record
# ---------------------------------------------------------------------------

def test_a_run_is_in_flight_until_it_concludes(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")

    run = repo.get_run(run_id)
    assert run.status is None and run.in_flight, (
        "there is no RunStatus for 'still going', and inventing one would put a "
        "value in the column that src/ cannot produce")

    repo.finish_run(run_id, status=RunStatus.OK, findings=[])
    assert not repo.get_run(run_id).in_flight


def test_exit_code_comes_from_the_pipelines_own_table(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.finish_run(run_id, status=RunStatus.UNVERIFIED, findings=[])
    assert repo.get_run(run_id).exit_code == 2, (
        "2 means 'ran clean but had nothing to check against' — the CLI and the "
        "run record must not be able to disagree about that")


def test_findings_keep_their_order_and_their_two_kinds(repo: Repository):
    """The interleaving is the contract: the pipeline emits variances and
    unverified stores store by store, and that order is committed inside
    variances.json's digest (docs/02-ARCHITECTURE.md#the-run-seam)."""
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    findings = [("unverified", "store A: no team reference found"),
                ("variance", "store B pre_vat: +1,234"),
                ("unverified", "store C: no team reference found")]
    repo.finish_run(run_id, status=RunStatus.VARIANCE, findings=findings)

    run = repo.get_run(run_id)
    assert run.findings == findings
    assert run.variances == ["store B pre_vat: +1,234"]
    assert [f[1] for f in run.findings] == [f[1] for f in findings]


def test_metrics_land_as_columns(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.finish_run(run_id, status=RunStatus.OK, findings=[],
                    metrics={"wall_s": 120.7, "io_s": 79.5, "serialize_s": 38.7,
                             "compute_s": 2.5, "peak_rss_mb": 832.0})
    run = repo.get_run(run_id)
    assert (run.wall_s, run.io_s, run.compute_s, run.serialize_s) == (120.7, 79.5, 2.5, 38.7)
    assert run.peak_rss_mb == 832.0


def test_restarting_a_run_clears_the_previous_attempts_log(repo: Repository):
    """A retried attempt restarts the log rather than appending: two attempts
    interleaved in one seq space would be unreadable, and `seq` promises no
    gaps."""
    job, _ = repo.enqueue("lazada", "2026-05_l1", max_attempts=2)
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.append_log(run_id, [(0, "line", "first attempt")])
    repo.record_artifact(run_id, name="a.xlsx", uri="file:///a.xlsx", bytes_=1, sha256="x")

    again = repo.start_run(job.id, "lazada", "2026-05_l1")
    assert again == run_id, "one run row per job"
    lines, _, _ = repo.log_lines(run_id)
    assert lines == []
    assert repo.artifacts(run_id) == []


# ---------------------------------------------------------------------------
# The log's polling contract
# ---------------------------------------------------------------------------

def test_log_paginates_by_seq(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.append_log(run_id, [(i, "line", f"line {i}") for i in range(10)])

    first, next_seq, complete = repo.log_lines(run_id, limit=4)
    assert [l.seq for l in first] == [0, 1, 2, 3]
    assert next_seq == 3
    assert not complete, "the run has not finished"

    rest, next_seq, _ = repo.log_lines(run_id, after_seq=next_seq)
    assert [l.seq for l in rest] == [4, 5, 6, 7, 8, 9]
    assert next_seq == 9

    # Polling past the end returns nothing and does not move next_seq — which is
    # how a client distinguishes "nothing new" from "I lost a line".
    empty, still, _ = repo.log_lines(run_id, after_seq=next_seq)
    assert empty == [] and still == 9


def test_complete_flips_when_the_run_finishes(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.append_log(run_id, [(0, "section", "FULL RUN")])
    assert repo.log_lines(run_id)[2] is False

    repo.finish_run(run_id, status=RunStatus.OK, findings=[])
    assert repo.log_lines(run_id)[2] is True


def test_replaying_a_batch_is_harmless(repo: Repository):
    """The producer owns `seq`, so a re-flush after a network wobble cannot
    duplicate or reorder a line."""
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    batch = [(0, "line", "a"), (1, "line", "b")]
    repo.append_log(run_id, batch)
    repo.append_log(run_id, batch)

    lines, _, _ = repo.log_lines(run_id)
    assert [l.text for l in lines] == ["a", "b"]


def test_log_of_an_unknown_run_is_not_found(repo: Repository):
    with pytest.raises(NotFound):
        repo.log_lines(4242)


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def test_artifacts_are_recorded_and_upserted(repo: Repository):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.record_artifact(run_id, name="finance_file.xlsx", uri="file:///x", bytes_=10,
                         sha256="aa")
    repo.record_artifact(run_id, name="finance_file.xlsx", uri="file:///y", bytes_=20,
                         sha256="bb")

    arts = repo.artifacts(run_id)
    assert len(arts) == 1 and arts[0].uri == "file:///y" and arts[0].bytes == 20


def test_healthcheck_counts_the_queue(repo: Repository):
    repo.enqueue("lazada", "2026-05_l1")
    repo.enqueue("lazada", "2026-05_l2")
    repo.claim("w", 60)
    health = repo.healthcheck()
    assert health["database"] == "ok"
    assert (health["queued"], health["leased"]) == (1, 1)
    assert health["migrations"] >= 1
