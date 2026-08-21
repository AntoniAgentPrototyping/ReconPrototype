-- Phase 6 / C6 (second half) — worker liveness an OPERATOR can see.
--
-- Phase 4.5 gave the worker a container HEALTHCHECK reading a heartbeat *file*,
-- which restarts a hung worker but is invisible outside its own container. The
-- operator-facing question — "is anything going to pick this queued job up?" —
-- was still unanswerable: /healthz reported queue depth and nothing else, so
-- "queued with no worker" read exactly like "queued a second ago".
--
-- One row per worker id, upserted from two places:
--
--   * the top of every idle loop turn (every poll_interval_s, default 2s), and
--   * every job-lease extension (each run-log flush, ~1s during a run),
--
-- so a worker beats through a 269-second Shopee window rather than appearing
-- dead mid-run. /healthz counts a worker "alive" on a 60-second threshold —
-- generous against both cadences, and a worker that stops beating for a minute
-- is exactly the state worth surfacing.
--
-- Identifiers and timestamps only, no client data. Rows are never deleted by the
-- application: a worker that stopped existing keeps its last_seen as the record
-- of when it was last known good, which is the fact an operator wants.

create table if not exists worker_heartbeats (
    worker_id   text        primary key,
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now()
);

comment on table worker_heartbeats is
    'liveness, one row per worker id; upserted each idle loop turn and each '
    'lease extension. /healthz counts last_seen within 60s as alive (C6).';
