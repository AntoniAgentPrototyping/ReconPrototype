-- M4 schema — the job queue, the run record, the log, the artifacts.
--
-- Two axes are kept deliberately separate, because conflating them is how a
-- reconciliation service starts lying:
--
--   jobs.state   did the WORKER manage to execute this job?
--   runs.status  what did the RUN conclude?  (mirrors src.pipeline.RunStatus)
--
-- A run that hard-stops on bad input is a job that executed perfectly and a run
-- that concluded "nothing was produced". Retrying it would produce the same
-- answer and hide the input problem (docs/06-DECISIONS.md#d3), so retries exist
-- for infrastructure failure only — see jobs.max_attempts.

create table if not exists jobs (
    id                bigserial primary key,
    platform          text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    period            text        not null,

    -- Roster relaxation is a property of the run, never a config edit
    -- (docs/06-DECISIONS.md#d23). It travels on the job so the run record says
    -- what it was, and a subset run can never be mistaken for a full one.
    partial_roster    boolean     not null default false,

    -- The team's reference totals, inline rather than a file path: a worker in a
    -- container has no access to the operator's filesystem. Same shape the CLI's
    -- --refs takes.
    refs              jsonb,

    state             text        not null default 'queued'
                                  check (state in ('queued', 'leased', 'done', 'error', 'cancelled')),

    -- attempts counts LEASES taken, not pipeline failures. max_attempts 1 means
    -- "if the worker dies holding this job, a human decides what happens next" —
    -- the safe default for money. Raise it per job only when the failure mode is
    -- known to be infrastructural (an OOM kill, a container eviction).
    attempts          int         not null default 0,
    max_attempts      int         not null default 1 check (max_attempts >= 1),
    priority          int         not null default 0,

    leased_by         text,
    lease_expires_at  timestamptz,

    requested_by      text,
    -- Idempotency for the enqueue call itself: a retried POST /jobs with the
    -- same key returns the SAME job rather than queueing a second run of the
    -- same settlement window.
    idempotency_key   text        unique,
    error             text,

    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    finished_at       timestamptz,

    -- A lease must name its holder and its expiry, or the reclaim sweep cannot
    -- tell a live worker from a dead one.
    constraint jobs_lease_is_complete check (
        (state <> 'leased') or (leased_by is not null and lease_expires_at is not null))
);

-- The double-run guard. Two concurrent runs of one settlement window is the
-- double-invoicing shape this pipeline already defends against by
-- one-folder-per-window discipline (docs/06-DECISIONS.md#d5, #d9); a queue would
-- reintroduce it in one impatient double-click. Finished jobs are excluded, so
-- a deliberate re-run after a fix is still allowed.
create unique index if not exists jobs_one_active_per_window
    on jobs (platform, period)
    where state in ('queued', 'leased');

-- The claim query's access path: state + priority + id.
create index if not exists jobs_queued_order on jobs (priority desc, id) where state = 'queued';
create index if not exists jobs_lease_expiry on jobs (lease_expires_at) where state = 'leased';


create table if not exists runs (
    id           bigserial primary key,
    job_id       bigint      not null unique references jobs (id) on delete cascade,
    platform     text        not null,
    period       text        not null,

    -- NULL until the run ends: there is no RunStatus for "still going", and
    -- inventing one would put a value in this column that src/ cannot produce.
    -- finished_at is null  <=>  status is null  <=>  the run is in flight.
    status       text        check (status in ('ok', 'variance', 'unverified', 'hard_stop')),
    exit_code    smallint,

    -- The ordered findings list, verbatim: [[kind, message], ...]. Order is part
    -- of the contract — it interleaves variances with unverified stores store by
    -- store, and that interleaving is committed inside variances.json's digest
    -- (docs/02-ARCHITECTURE.md#the-run-seam). A jsonb array preserves it.
    findings     jsonb       not null default '[]'::jsonb,
    checks       jsonb       not null default '[]'::jsonb,

    -- Metrics ride on RunResult, never on the log (docs/06-DECISIONS.md#d27), so
    -- they land here as columns rather than log text. peak_rss_mb against a
    -- container limit is the engine-port memory trigger.
    wall_s       double precision,
    io_s         double precision,
    compute_s    double precision,
    serialize_s  double precision,
    peak_rss_mb  double precision,

    error        text,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz,

    constraint runs_finish_is_atomic check (
        (finished_at is null) = (status is null))
);

create index if not exists runs_window on runs (period, platform);


-- The run log, one row per line, in the pipeline's own order.
--
-- `seq` is assigned by the PRODUCER (service.runlog.QueueRunLog), not by a
-- database sequence: a bigserial has gaps under concurrency, and a gapless
-- per-run counter is what lets a client prove it lost nothing. It is also the
-- whole streaming contract — `?after_seq=N` serves polling today and
-- server-sent events later with no schema change.
create table if not exists run_log_lines (
    run_id  bigint      not null references runs (id) on delete cascade,
    seq     bigint      not null check (seq >= 0),
    kind    text        not null check (kind in ('line', 'warning', 'section')),
    text    text        not null,
    at      timestamptz not null default now(),
    primary key (run_id, seq)
);


create table if not exists artifacts (
    id          bigserial   primary key,
    run_id      bigint      not null references runs (id) on delete cascade,
    name        text        not null,
    uri         text        not null,
    bytes       bigint      not null check (bytes >= 0),

    -- Transfer integrity ONLY. This is never a content-equality check: openpyxl
    -- stamps timestamps into docProps/core.xml, so two byte-different .xlsx
    -- files can hold identical data (docs/06-DECISIONS.md#d16). Comparing
    -- workbooks is tests/goldens/cellset.py's job, not this column's.
    bytes_sha256 text       not null,

    created_at  timestamptz not null default now(),
    unique (run_id, name)
);
