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


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every unapplied migration. Returns the filenames applied.

    An already-applied file whose contents changed is an error rather than a
    re-run: editing a shipped migration means two databases with the same
    recorded history and different schemas, which is the one failure mode a
    migration table exists to prevent.
    """
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
