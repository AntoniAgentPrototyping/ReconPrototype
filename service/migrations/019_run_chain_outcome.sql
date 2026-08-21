-- A4 — what a window run queued next, durably.
--
-- A finished window auto-queues its month's master (`worker._chain_month_master`,
-- M8 Phase 3). The chain's outcome — queued, already queued, or COULD NOT QUEUE —
-- was carried on `JobOutcome.chained` and printed to the worker's stdout, so a
-- settlement run could succeed while its month's master silently failed to even
-- get queued, with no browser- or API-visible trace.
--
-- It cannot go into the run log: `write_artifacts` has already stored
-- `run_log.txt` by the time the chain runs, and
-- `test_the_stored_log_and_the_database_log_are_the_same_log` holds that the
-- database copy and the stored copy are the same log. So the RUN ROW carries the
-- sentence instead — one nullable text column, written by `finish_run` alongside
-- the rest of the run's conclusion.
--
-- NULL means "nothing was chained and nothing failed": a hard stop, a master run
-- (masters chain nothing), or a pre-A4 row. The distinction that matters is
-- carried by the sentence itself, which is written for a person.

alter table runs add column if not exists chained text;

comment on column runs.chained is
    'what this run queued next (the month-end master chain, A4): the outcome '
    'sentence, including a failure to queue. NULL = nothing chained.';
