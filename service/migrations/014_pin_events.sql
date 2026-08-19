-- An append-only history of config pins and unpins (defect 2.5's sharper half).
--
-- `period_config` is CURRENT state: one row per (platform, period), upserted by a
-- pin and DELETED by an unpin. That delete was the gap. After it, nothing anywhere
-- recorded that the window had been pinned, to which config version, or why it was
-- released — in the system whose entire M5/M6 rationale is the audit trail, and
-- which records `config_proposals.self_approved` as a GENERATED column precisely so
-- it cannot be set to a convenient value.
--
-- Why unpinning is the act that most needs a record: a pin freezes the rules a
-- window was invoiced under, so releasing it means the next re-run of that window
-- may produce different numbers than the run the invoice came from. That is a
-- deliberate, rare, consequential act, and it was the only one on the config path
-- leaving no trace.
--
-- **Append-only by construction, not by convention.** No update or delete path is
-- written against this table anywhere in `service/`; a correction is a new row. The
-- `action` check keeps the vocabulary closed — a third verb would need a migration,
-- which is the conversation worth forcing.
--
-- `config_version_id` is NOT a foreign key on purpose for unpin rows: it records
-- which version was released, and `config_versions` rows are never deleted, but a
-- future retention policy on that table must not be able to erase this history by
-- cascade. It stays a plain integer, and the join is best-effort at read time.
create table if not exists config_pin_events (
    id                bigserial primary key,
    platform          text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    period            text        not null,
    action            text        not null check (action in ('pin', 'unpin')),
    config_version_id bigint,
    -- The session principal for a person, or `run <id>` for the worker's automatic
    -- pin. Never taken from a request body — the same rule as `requested_by`.
    actor             text        not null,
    -- Required for a person's action, carried over from the auto-pin's own string.
    reason            text        not null,
    at                timestamptz not null default now()
);

create index if not exists config_pin_events_window
    on config_pin_events (platform, period, at desc);
