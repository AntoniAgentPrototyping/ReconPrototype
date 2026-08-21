"""Retention actually disposes of the disposable classes (Phase 6 / C10).

Four claims, each pinned: aged scratch goes and fresh scratch stays; the DB log
mirror is pruned for old FINISHED runs only (the runs row survives — it is the
audit); only DEAD sessions past the grace period go; and `--dry-run` reports the
same numbers while removing nothing.
"""

from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg")

from service import retention  # noqa: E402


def _aged(path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


@pytest.fixture
def scratch(service_settings):
    root = service_settings.scratch_root
    (root / "job-1" / "input").mkdir(parents=True)
    (root / "job-1" / "input" / "big.xlsx").write_bytes(b"x" * 1000)
    (root / "job-2").mkdir()
    (root / "incoming").mkdir()
    (root / "incoming" / "orphan.xlsx").write_bytes(b"y" * 100)
    (root / "incoming" / "fresh.xlsx").write_bytes(b"z")
    # job-1 and the orphan are past the window; job-2 and fresh are not.
    _aged(root / "job-1", 20)
    _aged(root / "incoming" / "orphan.xlsx", 20)
    return root


def test_aged_scratch_goes_and_fresh_scratch_stays(repo, service_settings, scratch):
    report = retention.sweep(repo, service_settings)

    assert report.scratch_dirs_removed == ["job-1"]
    assert report.incoming_files_removed == 1
    assert not (scratch / "job-1").exists()
    assert (scratch / "job-2").exists(), "inside the diagnosis window — stays"
    assert (scratch / "incoming" / "fresh.xlsx").exists()
    assert report.scratch_bytes_freed >= 1100


def test_dry_run_reports_the_same_numbers_and_removes_nothing(
        repo, service_settings, scratch):
    report = retention.sweep(repo, service_settings, dry_run=True)

    assert report.scratch_dirs_removed == ["job-1"]
    assert report.incoming_files_removed == 1
    assert (scratch / "job-1").exists(), "dry run must not remove"
    assert (scratch / "incoming" / "orphan.xlsx").exists()


def test_old_finished_run_logs_are_pruned_and_the_run_row_survives(
        repo, service_settings):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("w1", 60)
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.append_log(run_id, [(1, "line", "line one"), (2, "line", "line two")])
    from src.pipeline import RunStatus
    repo.finish_run(run_id, status=RunStatus.OK, findings=[], checks=[], metrics={})
    with repo._conn() as conn, conn.cursor() as cur:            # noqa: SLF001
        cur.execute("update runs set finished_at = now() - interval '120 days' "
                    "where id = %s", (run_id,))

    report = retention.sweep(repo, service_settings)

    assert report.log_lines_deleted == 2
    lines, _, _ = repo.log_lines(run_id, after_seq=0, limit=10)
    assert lines == []
    run = repo.get_run(run_id)
    assert run.status is RunStatus.OK, "the run row is the audit; it stays"


def test_a_recent_or_in_flight_runs_log_is_untouched(repo, service_settings):
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    repo.claim("w1", 60)
    run_id = repo.start_run(job.id, "lazada", "2026-05_l1")
    repo.append_log(run_id, [(1, "line", "still running")])

    report = retention.sweep(repo, service_settings)

    assert report.log_lines_deleted == 0
    lines, _, _ = repo.log_lines(run_id, after_seq=0, limit=10)
    assert len(lines) == 1


def test_only_sessions_dead_past_the_grace_period_are_pruned(
        repo, service_settings, make_user):
    user = make_user("recon.viewer", "retiree@test")
    now = datetime.now(timezone.utc)

    def session(digest: str, expires: datetime) -> int:
        return repo.create_session(user_id=user.id, digest=digest,
                                   absolute_expires_at=expires).id

    live = session("a" * 64, now + timedelta(hours=12))
    freshly_dead = session("b" * 64, now - timedelta(days=2))
    long_dead = session("c" * 64, now - timedelta(days=90))

    report = retention.sweep(repo, service_settings)

    assert report.sessions_deleted == 1
    with repo._conn() as conn, conn.cursor() as cur:            # noqa: SLF001
        cur.execute("select id from user_sessions order by id")
        kept = [r[0] for r in cur.fetchall()]
    assert live in kept, "a live session must never be a retention candidate"
    assert freshly_dead in kept, "dead, but inside the review grace period"
    assert long_dead not in kept


def test_the_disk_warning_fires_below_the_threshold(repo, service_settings, scratch):
    # A threshold no real machine passes, so the warning path is exercised
    # without faking disk_usage.
    generous = replace(service_settings, disk_free_min_gb=10_000_000.0)
    report = retention.sweep(repo, generous)
    assert report.warnings and "free" in report.warnings[0]
