-- D1 — the exception queue becomes workable.
--
-- The stable fingerprint (`service/exceptions.py`, NFC-normalized identity
-- columns, sheet folded into the hash) has existed since M5, and cross-run
-- history has been queryable the whole time. What never existed is the other
-- half the roadmap defined M6 as: a way to attach a DECISION to a fingerprint,
-- so every run re-presented the same list as if nobody had ever looked at it.
--
-- Same two-table shape as the config pins (migration 014), because it is the
-- same problem: a judgment call that must survive across runs, attributed, with
-- a reason, and never silently lost.
--
--   * `exception_dispositions` — CURRENT state, one row per fingerprint. Keyed
--     on the fingerprint and not on any run's copy of it, because the decision
--     is about the recurring thing ("this store's unmatched orders are known
--     and excluded"), not about one week's row.
--   * `exception_disposition_events` — append-only history. `clear` rows record
--     what was released, because re-opening a previously "expected" exception is
--     exactly as consequential as unpinning a config and must not erase the
--     record that it was ever marked.
--
-- **Dispositions annotate; they never hide.** The read path joins this onto the
-- queue and the screen may FILTER by it, but the API's default answer always
-- carries every row — the fingerprint hashes identity columns, not amounts, so
-- an "expected" variance that has quietly grown must still be in front of
-- someone. (Decided 2026-08-21; see docs/06-DECISIONS.md.)
--
-- No TTL. With annotate-never-hide the row resurfaces every run anyway, so an
-- expiry would only manufacture re-approval work; `decided_at` is on the badge
-- and a stale decision is visible for a person to reconsider.
--
-- The vocabulary is CHECK-closed like the pin events': a third disposition or a
-- third action needs a migration, which is the conversation worth forcing.

create table if not exists exception_dispositions (
    fingerprint text        primary key,
    disposition text        not null check (disposition in ('reviewed', 'expected')),
    reason      text        not null,
    -- The session principal. Never taken from a request body — the same rule
    -- as requested_by/declared_by/uploaded_by.
    actor       text        not null,
    decided_at  timestamptz not null default now()
);

create table if not exists exception_disposition_events (
    id          bigserial   primary key,
    fingerprint text        not null,
    action      text        not null check (action in ('mark', 'clear')),
    disposition text        not null check (disposition in ('reviewed', 'expected')),
    reason      text        not null,
    actor       text        not null,
    at          timestamptz not null default now()
);

create index if not exists exception_disposition_events_fingerprint
    on exception_disposition_events (fingerprint, at desc);

comment on table exception_dispositions is
    'current decision per exception fingerprint (D1). Joined onto the queue at '
    'read time; never hides a row — the queue can be filtered, the answer is whole.';
comment on table exception_disposition_events is
    'append-only mark/clear history per fingerprint; clear rows record what was '
    'released (the pin-events pattern, migration 014).';
