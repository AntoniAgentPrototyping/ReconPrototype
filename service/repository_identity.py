"""M6's SQL: users and sessions.

A third module, following the precedent `repository_m5.py`'s own header sets — a
separate file when it answers a different question. `repository.py` is the queue,
`repository_m5.py` is the record-keeping around a run, and this is identity.

The class chain stays linear so callers still hold exactly one object:

    Repository  ->  IdentityRepository  ->  M5Repository

Two properties are load-bearing and are pinned by tests, so read the comments
before changing a query here:

1. **The role is resolved by JOIN, never copied onto a session row.** 002_m5.sql
   put revocation and expiry in a WHERE clause so a revoked credential died on the
   very next request rather than whenever a cache felt like it. A `role` column on
   `user_sessions` would reintroduce exactly that staleness in a worse place: a
   demoted admin would keep admin until they signed out.
2. **Every liveness condition lives in one statement.** Signed out, idle-timed
   out, absolutely expired, owner disabled — all four are in
   `principal_for_session`'s WHERE, so there is one place a session can be live.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from .auth import Principal, Role, SessionRecord, UserRecord
from .repository import NotFound, Repository


class DuplicateUser(RuntimeError):
    """That username already exists. 409, not 500."""

    def __init__(self, username: str) -> None:
        super().__init__(f"a user named {username!r} already exists")
        self.username = username


class LastAdminProtected(RuntimeError):
    """Refused: the change would leave the deployment with no enabled admin.

    409 rather than 403 — the caller is allowed to do this in general, but not
    this particular instance of it.
    """


class IdentityRepository(Repository):

    # -- users -------------------------------------------------------------

    def create_user(self, *, username: str, password_hash: str, role: Role,
                    display_name: str | None = None,
                    created_by: str | None = None,
                    must_change_password: bool = True) -> UserRecord:
        try:
            with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    insert into users (username, password_hash, role, display_name,
                                       created_by, must_change_password)
                    values (%s, %s, %s, %s, %s, %s)
                    returning *
                """, (username, password_hash, role.value, display_name,
                      created_by, must_change_password))
                return UserRecord.from_row(self._one(cur))
        except psycopg.errors.UniqueViolation as exc:
            raise DuplicateUser(username) from exc

    def user_for_login(self, username: str) -> dict | None:
        """The ONE query that returns `password_hash`.

        Everything else goes through `UserRecord`, which has no such field, so a
        hash cannot reach a response body by accident.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from users where username = %s", (username,))
            row = self._one(cur)
        return dict(row) if row is not None else None

    def user(self, user_id: int) -> UserRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from users where id = %s", (user_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"user {user_id}")
        return UserRecord.from_row(row)

    def user_by_username(self, username: str) -> UserRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from users where username = %s", (username,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"user {username!r}")
        return UserRecord.from_row(row)

    def list_users(self, *, include_disabled: bool = True) -> list[UserRecord]:
        clause = "" if include_disabled else "where disabled_at is null"
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"select * from users {clause} order by id")
            return [UserRecord.from_row(dict(r)) for r in cur.fetchall()]

    def note_login_failure(self, user_id: int, *, limit: int,
                           cooloff_s: int) -> None:
        """Count a failure and, at the threshold, set a SHORT self-clearing lock.

        The decision is in SQL rather than in Python so there is exactly one place
        a lock can be set, and so two concurrent failures cannot both read
        `failed_attempts` as one-below-threshold and both decline to lock.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update users
                set failed_attempts = failed_attempts + 1,
                    last_failed_at = now(),
                    locked_until = case
                        when failed_attempts + 1 >= %s
                            then now() + make_interval(secs => %s)
                        else locked_until
                    end
                where id = %s
            """, (limit, cooloff_s, user_id))

    def note_login_success(self, user_id: int) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update users
                set last_login_at = now(), failed_attempts = 0,
                    last_failed_at = null, locked_until = null
                where id = %s
            """, (user_id,))

    def touch_password(self, user_id: int, password_hash: str, *,
                       must_change_password: bool) -> UserRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update users
                set password_hash = %s, password_changed_at = now(),
                    must_change_password = %s,
                    failed_attempts = 0, locked_until = null
                where id = %s
                returning *
            """, (password_hash, must_change_password, user_id))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"user {user_id}")
        return UserRecord.from_row(row)

    # -- the last-admin guard ----------------------------------------------

    def _assert_not_last_admin(self, cur, user_id: int) -> None:
        """Refuse if `user_id` is the only enabled admin left.

        Called INSIDE the caller's transaction, after a `for update` lock on the
        admin rows. Without that lock two concurrent requests disabling two
        DIFFERENT admins would each observe two admins, each proceed, and leave a
        deployment nobody can administer. This repo already reasons carefully
        about `FOR UPDATE SKIP LOCKED` in the queue; the same care belongs here.
        """
        cur.execute("""
            select id from users
            where role = %s and disabled_at is null
            for update
        """, (Role.ADMIN.value,))
        admins = [r["id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
        if user_id in admins and len(admins) <= 1:
            raise LastAdminProtected(
                "this is the only enabled admin; promote or enable another "
                "account first, or the deployment cannot be administered")

    def set_user_disabled(self, user_id: int, *, disabled: bool,
                          by: str | None = None) -> UserRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            if disabled:
                self._assert_not_last_admin(cur, user_id)
            cur.execute("""
                update users
                set disabled_at = case when %s then now() else null end,
                    disabled_by = case when %s then %s else null end
                where id = %s
                returning *
            """, (disabled, disabled, by, user_id))
            row = self._one(cur)
            if row is None:
                raise NotFound(f"user {user_id}")
            return UserRecord.from_row(row)

    def set_user_role(self, user_id: int, role: Role, *,
                      by: str | None = None) -> UserRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            if role is not Role.ADMIN:
                self._assert_not_last_admin(cur, user_id)
            cur.execute("""
                update users set role = %s where id = %s returning *
            """, (role.value, user_id))
            row = self._one(cur)
            if row is None:
                raise NotFound(f"user {user_id}")
            return UserRecord.from_row(row)

    def count_active_admins(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                select count(*) from users
                where role = %s and disabled_at is null
            """, (Role.ADMIN.value,))
            return int(cur.fetchone()[0])

    # -- sessions ----------------------------------------------------------

    def create_session(self, *, user_id: int, digest: str, absolute_expires_at: Any,
                       user_agent: str | None = None,
                       client_ip: str | None = None) -> SessionRecord:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into user_sessions (user_id, token_sha256, absolute_expires_at,
                                           user_agent, client_ip)
                values (%s, %s, %s, %s, %s)
                returning *
            """, (user_id, digest, absolute_expires_at, user_agent, client_ip))
            return SessionRecord.from_row(self._one(cur))

    def principal_for_session(self, digest: str, *,
                              idle_seconds: int = 3600) -> Principal | None:
        """Resolve a session token to a caller, and bump `last_seen_at`.

        The direct successor to M5's `principal_for_digest`, and the method
        `create_app` probes for when deciding whether a repository can authenticate
        at all.

        Every liveness condition is in the WHERE: signed out, idle, absolutely
        expired, owner disabled. Nothing is cached, so a disable, a demotion and a
        password reset all take effect on the caller's NEXT request.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update user_sessions s
                set last_seen_at = case
                        -- Coalesced: the run page polls its log about once a
                        -- second, and a row write per poll is WAL churn for
                        -- nothing. Skipping the write when last_seen_at is under a
                        -- minute old cannot expire a session early — a row that
                        -- recent is unambiguously active.
                        when s.last_seen_at < now() - interval '1 minute' then now()
                        else s.last_seen_at
                    end
                from users u
                where s.user_id = u.id
                  and s.token_sha256 = %s
                  and s.revoked_at is null
                  and s.absolute_expires_at > now()
                  -- Idle window. An UPDATE's WHERE sees the row as it was BEFORE
                  -- the SET, which is what makes checking and bumping
                  -- last_seen_at in one statement correct.
                  and s.last_seen_at > now() - make_interval(secs => %s)
                  and u.disabled_at is null
                returning s.id as session_id, u.username, u.role,
                          u.must_change_password, u.display_name
            """, (digest, idle_seconds))
            row = self._one(cur)
        if row is None:
            return None
        return Principal(subject=row["username"], role=Role(row["role"]),
                         session_id=row["session_id"], method="password",
                         must_change_password=bool(row["must_change_password"]),
                         display_name=row["display_name"])

    def revoke_session(self, session_id: int, *, reason: str) -> bool:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update user_sessions
                set revoked_at = now(), revoked_reason = %s
                where id = %s and revoked_at is null
            """, (reason, session_id))
            return cur.rowcount > 0

    def revoke_sessions_for_user(self, user_id: int, *, reason: str,
                                 except_session_id: int | None = None) -> int:
        """Sign a person out everywhere. One statement, so password-change,
        disable, demote and "this person has left" are all immediate."""
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update user_sessions
                set revoked_at = now(), revoked_reason = %s
                where user_id = %s and revoked_at is null
                  and (%s::bigint is null or id <> %s::bigint)
            """, (reason, user_id, except_session_id, except_session_id))
            return cur.rowcount

    def list_sessions_for_user(self, user_id: int, *,
                               include_revoked: bool = False) -> list[SessionRecord]:
        clause = "" if include_revoked else "and revoked_at is null"
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"""
                select * from user_sessions
                where user_id = %s {clause}
                order by id desc
            """, (user_id,))
            return [SessionRecord.from_row(dict(r)) for r in cur.fetchall()]
