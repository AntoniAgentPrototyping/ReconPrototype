-- M8 Phase 3 task 3.4 — the month-end master becomes a job like any other.
--
-- A successful window run enqueues one of these for its month. NOT inline at the
-- end of the window run, and the distinction is load-bearing:
--
--   * a settlement run must not fail because a cross-month aggregation failed —
--     the workbook a person invoices from is already written and stored by then;
--   * the window run's artifact set is golden-gated, and appending a second,
--     differently-shaped workbook to it would move every window's manifest.
--
-- So it is a separate job, with its own run record, its own log and its own
-- artifacts, and it shows up on the board without anyone asking for it.

alter table jobs add column if not exists kind text not null default 'window';

alter table jobs drop constraint if exists jobs_kind_check;
alter table jobs add  constraint jobs_kind_check
    check (kind in ('window', 'month_master'));

-- `platform` is 'all' for a month master: it consolidates every platform, so any
-- one platform's name would be a lie, and NULL would break the double-run guard
-- below (NULLs are not equal to each other, so two concurrent masters for one
-- month would both be allowed).
alter table jobs drop constraint if exists jobs_platform_check;
alter table jobs add  constraint jobs_platform_check check (
    (kind = 'window'       and platform in ('tiktok', 'shopee', 'lazada')) or
    (kind = 'month_master' and platform = 'all'));

-- `period` holds the MONTH ('2026-07') rather than a window ('2026-07_w1'), so
-- the existing unique index `jobs_one_active_per_window (platform, period)` where
-- state in ('queued','leased') already does the right thing for free: at most one
-- master job in flight per month. A window finishing while one is queued must not
-- start a second — it must let the queued one pick up its result.

comment on column jobs.kind is
    'window = reconcile one settlement window (the default, and everything before '
    'M8 Phase 3). month_master = consolidate a month''s finished windows into the '
    'month-end master. The worker branches on this and on nothing else.';

create index if not exists jobs_kind on jobs (kind) where state in ('queued', 'leased');
