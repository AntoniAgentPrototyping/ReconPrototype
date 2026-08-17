"""Every SQL statement in the service, in one file.

Keeping the SQL together rather than scattered through the api and the worker is
what makes the queue's correctness reviewable: the whole claim protocol is
`claim`, `heartbeat` and `reclaim_expired`, and they are on one screen.

The queue is Postgres, not Redis/Celery, because ~14 jobs a month does not
justify a second stateful service — and because the jobs table doubles as the
audit record finance will ask for. `FOR UPDATE SKIP LOCKED` is the whole
mechanism; see `claim`.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from src.pipeline import EXIT_CODES, RunStatus

from .models import Artifact, Job, JobState, LogLine, Run

# The partial unique index from migrations/001_init.sql. Matched by name so a
# violation can be reported as "that window is already running" rather than as a
# 500 with a Postgres string in it.
ACTIVE_WINDOW_INDEX = "jobs_one_active_per_window"


class ActiveJobExists(RuntimeError):
    """A queued or leased job already covers this settlement window.

    Not an error in the pipeline's sense — it is the double-run guard doing its
    job. The caller gets the existing job so a UI can navigate to it.
    """

    def __init__(self, existing: Job) -> None:
        super().__init__(
            f"job {existing.id} is already {existing.state.value} for "
            f"{existing.platform} {existing.period}")
        self.existing = existing


class NotFound(LookupError):
    pass


class Repository:
    """Data access for the api and the worker.

    Every method is one transaction. That matters most for `append_log`: log
    lines must be committed as the run proceeds, or "watch the run" degrades to
    "read the log after it finishes", which is the feature.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # -- plumbing ----------------------------------------------------------

    def _conn(self):
        return self._pool.connection()

    @staticmethod
    def _one(cur) -> dict | None:
        row = cur.fetchone()
        return dict(row) if row else None

    def healthcheck(self) -> dict:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select count(*) as migrations from schema_migrations")
            migrations = self._one(cur)["migrations"]
            cur.execute("""
                select
                    count(*) filter (where state = 'queued') as queued,
                    count(*) filter (where state = 'leased') as leased
                from jobs
            """)
            counts = self._one(cur)
        return {"database": "ok", "migrations": migrations, **counts}

    # -- enqueue and inspect ------------------------------------------------

    def enqueue(self, platform: str, period: str, *, refs: dict | None = None,
                partial_roster: bool = False, priority: int = 0,
                max_attempts: int = 1, requested_by: str | None = None,
                idempotency_key: str | None = None) -> tuple[Job, bool]:
        """Queue one settlement window. Returns (job, created).

        Two distinct guards, doing different things:

        * `idempotency_key` — a retried POST returns the SAME job, created=False.
          Protects against a flaky network, not against a second opinion.
        * the active-window index — refuses a second live job for one window even
          under a different key, raising ActiveJobExists. This is the guard that
          matters: two concurrent runs of one window is the double-invoicing
          shape (docs/06-DECISIONS.md#d9).
        """
        params = {
            "platform": platform, "period": period,
            "partial_roster": partial_roster,
            "refs": Jsonb(refs) if refs else None,
            "priority": priority, "max_attempts": max_attempts,
            "requested_by": requested_by, "idempotency_key": idempotency_key,
        }
        try:
            with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
                cur.execute("""
                    insert into jobs (platform, period, partial_roster, refs, priority,
                                      max_attempts, requested_by, idempotency_key)
                    values (%(platform)s, %(period)s, %(partial_roster)s, %(refs)s,
                            %(priority)s, %(max_attempts)s, %(requested_by)s,
                            %(idempotency_key)s)
                    on conflict (idempotency_key) do nothing
                    returning *
                """, params)
                row = self._one(cur)
            if row is not None:
                return Job.from_row(row), True
        except psycopg.errors.UniqueViolation as exc:
            if (exc.diag.constraint_name or "") != ACTIVE_WINDOW_INDEX:
                raise
            existing = self.active_job_for(platform, period)
            if existing is None:                       # raced with a finishing job
                return self.enqueue(
                    platform, period, refs=refs, partial_roster=partial_roster,
                    priority=priority, max_attempts=max_attempts,
                    requested_by=requested_by, idempotency_key=idempotency_key)
            raise ActiveJobExists(existing) from exc

        # `do nothing` fired: the idempotency key is already in the table.
        assert idempotency_key is not None
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from jobs where idempotency_key = %s", (idempotency_key,))
            row = self._one(cur)
        if row is None:                                # pragma: no cover - lost race
            raise NotFound(f"idempotency_key {idempotency_key!r} vanished mid-insert")
        return Job.from_row(row), False

    def active_job_for(self, platform: str, period: str) -> Job | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select * from jobs
                where platform = %s and period = %s and state in ('queued', 'leased')
            """, (platform, period))
            row = self._one(cur)
        return Job.from_row(row) if row else None

    def get_job(self, job_id: int) -> Job:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select j.*, r.id as run_id
                from jobs j left join runs r on r.job_id = j.id
                where j.id = %s
            """, (job_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"job {job_id}")
        return Job.from_row(row)

    def list_jobs(self, *, state: JobState | None = None, platform: str | None = None,
                  period: str | None = None, limit: int = 50) -> list[Job]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                select j.*, r.id as run_id
                from jobs j left join runs r on r.job_id = j.id
                where (%(state)s::text is null or j.state = %(state)s)
                  and (%(platform)s::text is null or j.platform = %(platform)s)
                  and (%(period)s::text is null or j.period = %(period)s)
                order by j.id desc
                limit %(limit)s
            """, {"state": state.value if state else None, "platform": platform,
                  "period": period, "limit": limit})
            return [Job.from_row(dict(r)) for r in cur.fetchall()]

    def cancel_job(self, job_id: int) -> Job:
        """Only a queued job can be cancelled.

        A leased job is executing inside `pipeline.run()`, which has no
        cancellation point — and interrupting a run mid-workbook would leave a
        partial artifact that looks like a deliverable. Let it finish; the run
        record says what it concluded.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update jobs set state = 'cancelled', updated_at = now(), finished_at = now()
                where id = %s and state = 'queued'
                returning *
            """, (job_id,))
            row = self._one(cur)
        if row is not None:
            return Job.from_row(row)
        current = self.get_job(job_id)                 # raises NotFound if absent
        raise ValueError(f"job {job_id} is {current.state.value}, not queued — "
                         f"only a queued job can be cancelled")

    # -- the claim protocol -------------------------------------------------

    def claim(self, worker_id: str, lease_seconds: int) -> Job | None:
        """Take the next queued job, or None.

        `for update skip locked` inside the subquery is the entire concurrency
        story: each worker locks a different row instead of queueing behind the
        same one, and a worker never waits on a lock it cannot use. Two workers
        calling this at the same instant get two different jobs or one gets None
        — never the same job twice.

        `order by priority desc, id` makes it a queue rather than a bag, and the
        partial index `jobs_queued_order` matches it exactly.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update jobs
                set state = 'leased',
                    attempts = attempts + 1,
                    leased_by = %(worker)s,
                    lease_expires_at = now() + make_interval(secs => %(lease)s),
                    updated_at = now()
                where id = (
                    select id from jobs
                    where state = 'queued'
                    order by priority desc, id
                    for update skip locked
                    limit 1
                )
                returning *
            """, {"worker": worker_id, "lease": lease_seconds})
            row = self._one(cur)
        return Job.from_row(row) if row else None

    def heartbeat(self, job_id: int, worker_id: str, lease_seconds: int) -> bool:
        """Extend the lease. False means this worker no longer holds it.

        Called from QueueRunLog on every flush, so the run log is the liveness
        signal — a run that has stopped logging for longer than the lease is
        genuinely stuck, which is exactly what the reclaim sweep should act on.
        """
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update jobs
                set lease_expires_at = now() + make_interval(secs => %s), updated_at = now()
                where id = %s and leased_by = %s and state = 'leased'
            """, (lease_seconds, job_id, worker_id))
            return cur.rowcount == 1

    def reclaim_expired(self) -> dict[str, list[int]]:
        """Deal with leases whose worker stopped talking.

        Requeue while attempts remain; otherwise mark the job `error` and stop.
        The default max_attempts is 1, so by default a dead worker leaves a job
        that a human must look at. That is deliberate: an automatic retry of a
        settlement run is a second write of the same money, and the only failure
        it can fix is an infrastructural one (docs/06-DECISIONS.md#d3).

        Any run row the dead worker left in flight is closed as hard_stop, so
        `finished_at is null` keeps meaning "actually running".
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                update jobs set state = 'queued', leased_by = null,
                                lease_expires_at = null, updated_at = now()
                where state = 'leased' and lease_expires_at < now()
                  and attempts < max_attempts
                returning id
            """)
            requeued = [r["id"] for r in cur.fetchall()]

            cur.execute("""
                update jobs set state = 'error', leased_by = null, lease_expires_at = null,
                                updated_at = now(), finished_at = now(),
                                error = 'lease expired: the worker holding this job stopped '
                                        'reporting and no attempts remain'
                where state = 'leased' and lease_expires_at < now()
                  and attempts >= max_attempts
                returning id
            """)
            dead = [r["id"] for r in cur.fetchall()]

            if requeued or dead:
                cur.execute("""
                    update runs
                    set status = 'hard_stop', exit_code = %s, finished_at = now(),
                        error = 'the worker executing this run stopped reporting'
                    where job_id = any(%s) and finished_at is null
                """, (EXIT_CODES[RunStatus.HARD_STOP], requeued + dead))

        return {"requeued": requeued, "dead": dead}

    def finish_job(self, job_id: int, state: JobState, *, error: str | None = None) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update jobs set state = %s, error = %s, leased_by = null,
                                lease_expires_at = null, updated_at = now(),
                                finished_at = now()
                where id = %s
            """, (state.value, error, job_id))

    # -- runs ---------------------------------------------------------------

    def start_run(self, job_id: int, platform: str, period: str) -> int:
        """Open the run record. Status stays NULL until the run concludes.

        The row has to exist before the first log line, because the log is a
        child of the run — and the first log line is emitted by
        `pipeline.run()`'s opening section.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                insert into runs (job_id, platform, period)
                values (%s, %s, %s)
                on conflict (job_id) do update
                    set status = null, exit_code = null, finished_at = null,
                        error = null, started_at = now(),
                        findings = '[]'::jsonb, checks = '[]'::jsonb
                returning id
            """, (job_id, platform, period))
            run_id = self._one(cur)["id"]
            # A retried attempt restarts the log rather than appending to the
            # previous one: two attempts interleaved in one seq space would be
            # unreadable, and the seq contract promises no gaps.
            cur.execute("delete from run_log_lines where run_id = %s", (run_id,))
            cur.execute("delete from artifacts where run_id = %s", (run_id,))
        return run_id

    def finish_run(self, run_id: int, *, status: RunStatus, findings: Sequence[tuple[str, str]],
                   checks: Sequence[Any] = (), metrics: dict | None = None,
                   error: str | None = None, roster_missing: int | None = None) -> None:
        m = metrics or {}
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                update runs set
                    status = %(status)s, exit_code = %(exit_code)s, finished_at = now(),
                    findings = %(findings)s, checks = %(checks)s,
                    wall_s = %(wall_s)s, io_s = %(io_s)s, compute_s = %(compute_s)s,
                    serialize_s = %(serialize_s)s, peak_rss_mb = %(peak_rss_mb)s,
                    error = %(error)s, roster_missing = %(roster_missing)s
                where id = %(run_id)s
            """, {
                "run_id": run_id,
                "status": status.value,
                "exit_code": EXIT_CODES[status],
                # list(...) not tuple: psycopg adapts a tuple to a SQL record.
                "findings": Jsonb([list(f) for f in findings]),
                "checks": Jsonb(_jsonable(checks)),
                "wall_s": m.get("wall_s"), "io_s": m.get("io_s"),
                "compute_s": m.get("compute_s"), "serialize_s": m.get("serialize_s"),
                "peak_rss_mb": m.get("peak_rss_mb"),
                "error": error,
                # None when the window's input came from a directory rather than
                # from uploads: no roster preview was computed, and 0 would read as
                # "nothing missing" (service/materialize.py::roster_missing).
                "roster_missing": roster_missing,
            })

    def get_run(self, run_id: int) -> Run:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from runs where id = %s", (run_id,))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"run {run_id}")
        return Run.from_row(row)

    def run_for_job(self, job_id: int) -> Run | None:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from runs where job_id = %s", (job_id,))
            row = self._one(cur)
        return Run.from_row(row) if row else None

    # -- the log ------------------------------------------------------------

    def append_log(self, run_id: int, rows: Iterable[tuple[int, str, str]]) -> int:
        """Append (seq, kind, text) rows. Returns how many were new.

        `on conflict do nothing` makes a re-flush harmless: the producer owns
        `seq`, so replaying a batch after a network wobble cannot duplicate or
        reorder a line.
        """
        batch = [(run_id, seq, kind, text) for seq, kind, text in rows]
        if not batch:
            return 0
        with self._conn() as conn, conn.cursor() as cur:
            cur.executemany("""
                insert into run_log_lines (run_id, seq, kind, text)
                values (%s, %s, %s, %s)
                on conflict (run_id, seq) do nothing
            """, batch)
        return len(batch)

    def log_lines(self, run_id: int, *, after_seq: int = -1,
                  limit: int = 1000) -> tuple[list[LogLine], int, bool]:
        """(lines, next_seq, complete) — the polling contract.

        `next_seq` is what the client passes back as `after_seq`, so a client
        needs no arithmetic and no knowledge of how many lines it received.
        `complete` says the run has finished, which is how a poller knows to
        stop rather than guessing from an empty page.

        This is also the seam for server-sent events later: SSE would push the
        same rows in the same order with the same seq, so it needs no schema
        change. Polling first, because streaming dies silently through corporate
        proxies and is miserable to debug at month end.
        """
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select finished_at from runs where id = %s", (run_id,))
            head = self._one(cur)
            if head is None:
                raise NotFound(f"run {run_id}")
            cur.execute("""
                select seq, kind, text, at from run_log_lines
                where run_id = %s and seq > %s
                order by seq
                limit %s
            """, (run_id, after_seq, limit))
            lines = [LogLine.from_row(dict(r)) for r in cur.fetchall()]
        next_seq = lines[-1].seq if lines else after_seq
        return lines, next_seq, head["finished_at"] is not None

    # -- artifacts ----------------------------------------------------------

    def record_artifact(self, run_id: int, *, name: str, uri: str, bytes_: int,
                        sha256: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("""
                insert into artifacts (run_id, name, uri, bytes, bytes_sha256)
                values (%s, %s, %s, %s, %s)
                on conflict (run_id, name) do update
                    set uri = excluded.uri, bytes = excluded.bytes,
                        bytes_sha256 = excluded.bytes_sha256, created_at = now()
            """, (run_id, name, uri, bytes_, sha256))

    def artifacts(self, run_id: int) -> list[Artifact]:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from artifacts where run_id = %s order by name", (run_id,))
            return [Artifact.from_row(dict(r)) for r in cur.fetchall()]

    def artifact(self, run_id: int, name: str) -> Artifact:
        with self._conn() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute("select * from artifacts where run_id = %s and name = %s",
                        (run_id, name))
            row = self._one(cur)
        if row is None:
            raise NotFound(f"artifact {name!r} of run {run_id}")
        return Artifact.from_row(row)


def _jsonable(value: Any) -> Any:
    """Template check blocks arrive as whatever finance_template built — often
    tuples, sometimes numpy scalars. Coerce rather than let a 500 out of a
    workbook detail nobody would connect to the failure."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):                     # numpy scalar
        try:
            return value.item()
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
