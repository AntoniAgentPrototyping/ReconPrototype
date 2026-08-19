-- M8 / Phase 1.7 — Lazada's rules join the contract.
--
-- `src/lazada.py` held its own column maps, sheet names and filename regex as
-- module constants, and four modules in `service/` and `tools/` imported them
-- directly and said so in a comment. That is the last part of the domain contract
-- that could not be seen, edited or versioned through the config it claims to live
-- in (docs/14-PRODUCTION-READINESS.md D4, and half of A6).
--
-- The rules themselves do not change. `config_column_maps`, `config_reading` and
-- `config_platforms` already have the right shape and a `platform` check that
-- allows 'lazada'; the rows are seeded from `config/settings.yaml`, which gains
-- them in the same change. This migration exists for one thing that would
-- otherwise be silently wrong.
--
-- ---------------------------------------------------------------------------
-- dayfirst becomes nullable, and null means "this reader does not consume it"
-- ---------------------------------------------------------------------------
--
-- `config_platforms.dayfirst` was `not null default false`, and the renderer emits
-- one `dayfirst.<platform>` entry per row. A Lazada row would therefore put
-- `dayfirst: lazada: false` into the contract — and `lazada.read_ledger` calls
-- `pd.to_datetime` with no `dayfirst` argument at all, so nothing would read it.
--
-- Both ways of avoiding that are worse:
--
--   * Emitting `false` anyway adds a key nothing reads. That is precisely what
--     Phase 1.1 deleted five of, and the reason a reader has to spend time
--     deciding a setting does not matter.
--   * Making `read_ledger` honour it is a BEHAVIOUR change — Lazada dates could
--     shift by up to eleven days — inside what has to be an output-identical
--     refactor. If Lazada's dates should be day-first, that is its own commit with
--     its own stated delta and its own golden run.
--
-- So null is modelled honestly: the column is nullable, the renderer skips a null,
-- and the Lazada row says in its evidence why it is null. A future commit that
-- teaches `read_ledger` to read it sets the value and states the delta.

alter table config_platforms alter column dayfirst drop not null;
alter table config_platforms alter column dayfirst drop default;

comment on column config_platforms.dayfirst is
    'null = this platform''s reader does not consume a day-first setting, so a '
    'value here would be config nothing reads. Lazada is null: '
    'lazada.read_ledger calls pd.to_datetime with no dayfirst argument.';
