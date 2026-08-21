"""Connections and migrations. Hand-written SQL, applied in file order.

**No ORM and no migration framework.** The schema is four tables and the queue's
correctness lives in one `FOR UPDATE SKIP LOCKED` statement — the exact thing an
ORM hides behind a `.with_for_update(skip_locked=True)` that reads as a config
flag rather than as the lock it is. Alembic would add a dependency, a code
generator and an autogenerate diff that nobody reviews, in exchange for
managing a handful of `.sql` files that already read as the schema.

Each migration runs in its own transaction and is recorded by filename, so a
half-applied migration cannot exist and re-running is a no-op.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_SCHEMA_MIGRATIONS = """
create table if not exists schema_migrations (
    filename    text        primary key,
    sha256      text        not null,
    applied_at  timestamptz not null default now()
)
"""


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    """A pool, because the api serves sync endpoints from a threadpool and one
    shared connection would serialize every request behind the slowest one."""
    return ConnectionPool(database_url, min_size=min_size, max_size=max_size,
                          open=True, kwargs={"autocommit": False})


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_migrations(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_MIGRATIONS)
        cur.execute("select filename, sha256 from schema_migrations")
        return dict(cur.fetchall())
    # NB: the caller commits — see migrate().


# One fixed key, shared by every process that migrates this database. The value
# is arbitrary but must never change: two processes agreeing on the lock IS the
# mechanism ('RECON' as a 40-bit integer).
MIGRATE_LOCK_KEY = 0x5245434F4E


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every unapplied migration. Returns the filenames applied.

    An already-applied file whose contents changed is an error rather than a
    re-run: editing a shipped migration means two databases with the same
    recorded history and different schemas, which is the one failure mode a
    migration table exists to prevent.

    **Serialized across processes by a Postgres advisory lock (C8).** The api and
    the worker both migrate on start with no ordering between them, so first boot
    was a race: the loser crashed on a duplicate `schema_migrations` key and
    `restart: unless-stopped` masked it as an unexplained restart. The lock is
    taken BEFORE `applied_migrations` is read — the loser blocks, then reads the
    winner's rows and applies nothing. Session-level (it survives the per-file
    commits below) and released in `finally`, so a failed migration does not leave
    the database unlockable until the connection dies.
    """
    with conn.cursor() as cur:
        cur.execute("select pg_advisory_lock(%s)", (MIGRATE_LOCK_KEY,))
    conn.commit()
    try:
        return _migrate_locked(conn)
    finally:
        # Roll back first: a failed migration leaves the transaction aborted, and
        # an aborted transaction refuses every statement including the unlock.
        # Advisory locks are session-level, so the rollback does not release it —
        # the explicit unlock below does.
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(%s)", (MIGRATE_LOCK_KEY,))
        conn.commit()


def _migrate_locked(conn: psycopg.Connection) -> list[str]:
    applied = applied_migrations(conn)
    conn.commit()

    newly: list[str] = []
    for path in migration_files():
        body = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if path.name in applied:
            if applied[path.name] != digest:
                raise RuntimeError(
                    f"migration {path.name} has been edited since it was applied "
                    f"(recorded {applied[path.name][:12]}, on disk {digest[:12]}). "
                    f"Add a new migration instead — editing a shipped one leaves two "
                    f"databases claiming the same history with different schemas.")
            continue
        with conn.cursor() as cur:
            cur.execute(body)
            cur.execute("insert into schema_migrations (filename, sha256) values (%s, %s)",
                        (path.name, digest))
        conn.commit()
        newly.append(path.name)
    return newly
