"""Concurrent migrate-on-start is serialized, not raced (Phase 6 / C8).

The api and the worker both call `db.migrate` on boot with no ordering between
them. Before the advisory lock, first boot was a coin flip: both read the same
"unapplied" list, both applied, and the loser crashed on `schema_migrations`'s
primary key — masked by `restart: unless-stopped` as an unexplained restart.

The test stages one throwaway migration in a temp directory and runs `migrate`
from two threads released together. Without the lock this fails two ways at
once: a duplicate-key error on `schema_migrations` in one thread, and the probe
row inserted twice. With it, one thread applies and the other finds it applied.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from service import db  # noqa: E402

PROBE = """
create table if not exists _migrate_lock_probe (applied_marker int not null);
insert into _migrate_lock_probe values (1);
"""


@pytest.fixture
def probe_migrations(tmp_path: Path, monkeypatch):
    """A migrations dir holding ONLY the probe. `migrate` tolerates recorded
    filenames that are not on disk (it walks disk, not history), so pointing it
    at a directory without 001-018 applies nothing but the probe."""
    (tmp_path / "999_lock_probe.sql").write_text(PROBE, encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    return tmp_path


def _cleanup(pool):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("drop table if exists _migrate_lock_probe")
        cur.execute("delete from schema_migrations where filename = '999_lock_probe.sql'")
        conn.commit()


def test_two_processes_migrating_at_once_apply_each_migration_once(
        pool, probe_migrations):
    results: list[list[str]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def boot():
        try:
            with pool.connection() as conn:
                barrier.wait(timeout=10)
                results.append(db.migrate(conn))
        except BaseException as exc:                            # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=boot) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, f"a racing boot crashed: {errors!r}"
        applied = sorted(len(r) for r in results)
        assert applied == [0, 1], (
            f"expected one winner and one no-op, got {results!r}")

        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("select count(*) from _migrate_lock_probe")
            assert cur.fetchone()[0] == 1, "the probe migration ran twice"
            cur.execute("select count(*) from schema_migrations "
                        "where filename = '999_lock_probe.sql'")
            assert cur.fetchone()[0] == 1
    finally:
        _cleanup(pool)


def test_a_failed_migration_releases_the_lock(pool, tmp_path, monkeypatch):
    """The unlock lives in `finally`: a migration that raises must not leave the
    database unlockable until the connection dies, or the next boot hangs on a
    lock nobody will ever release."""
    (tmp_path / "999_broken.sql").write_text("select 1/0;", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)

    with pool.connection() as conn:
        with pytest.raises(Exception, match="division by zero"):
            db.migrate(conn)
        conn.rollback()
        with conn.cursor() as cur:
            # pg_try_advisory_lock returns immediately: True means nobody holds it.
            cur.execute("select pg_try_advisory_lock(%s)", (db.MIGRATE_LOCK_KEY,))
            assert cur.fetchone()[0] is True, "the migrate lock leaked"
            cur.execute("select pg_advisory_unlock(%s)", (db.MIGRATE_LOCK_KEY,))
