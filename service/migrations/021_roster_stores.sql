-- D3 (first clause) — the roster declaration names WHICH stores are absent.
--
-- `roster_declared_partial` was a boolean, and `apply_partial_roster` turned it
-- into "every expected store is optional" — so a genuinely forgotten store was
-- waved through with the legitimately absent ones. The declaration now carries
-- the store list, and the pipeline relaxes ONLY those names; a missing store the
-- declaration does not name hard-stops again, which is `check_stores`' ordinary
-- set arithmetic doing the work.
--
-- A COLUMN, not a child table. The declaration is one act — one person, one
-- reason, one timestamp, upserted whole — and per-store rows would imply
-- per-store reasons and lifecycles nobody has asked for. There is also nothing
-- to foreign-key against: the roster lives in the config contract, not in a
-- service table.
--
-- **NULL means the legacy blanket: every expected store optional.** Kept
-- expressible on purpose — existing declared windows keep working (a re-run must
-- make the same claim the first run did), and a declaration legitimately
-- predates knowing which stores will be missing. The worker warns on a blanket
-- and the UI nudges toward naming stores. No backfill: NULL is the honest state
-- for every declaration made before this column existed.
--
-- Store membership is deliberately NOT validated here or at the API door: the
-- window may be pinned to an older config whose roster differs from today's.
-- `apply_partial_roster` validates against the roster of the config the run
-- actually uses, and hard-stops on a name it does not know.

alter table windows add column if not exists declared_absent_stores text[];

alter table windows add constraint windows_stores_only_when_partial
    check (declared_absent_stores is null or roster_declared_partial);
alter table windows add constraint windows_stores_not_empty
    check (declared_absent_stores is null
           or cardinality(declared_absent_stores) >= 1);

comment on column windows.declared_absent_stores is
    'which expected stores this partial declaration covers (D3). NULL = the '
    'pre-021 blanket: every expected store optional. Validated against the '
    'run''s own config by apply_partial_roster, not here.';
