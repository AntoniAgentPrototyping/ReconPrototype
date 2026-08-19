-- M8 / Phase 1.6 — the config tables become the thing that is edited.
--
-- 007 made the tables the thing that is RENDERED. The editor still edited YAML
-- text and `service/api.py::_reimport_config` re-read the file into the tables
-- after every apply — a bridge, and an honest one, but it meant a per-entry
-- justification could only ever be a comment in a file, and that "which cells can
-- this move" was inferred from a dotted path by `config_schema.invalidates_goldens`
-- rather than recorded against the thing being changed.
--
-- This migration adds the one column that lets the inference go away.
--
-- ---------------------------------------------------------------------------
-- invalidates_goldens, on every config table
-- ---------------------------------------------------------------------------
--
-- `config_scalars` already carried it. The other ten did not, so applying a change
-- to a column map or an alias asked `config_schema.field_for` to resolve a dotted
-- path back to a declared Field and read the flag off that — which works only while
-- the schema and the tables agree about what exists, and silently answers "unknown,
-- therefore invalidating" when they drift. Unknown-means-invalidating is the right
-- default and it is preserved (`default true` below); what changes is that a row
-- now answers for itself.
--
-- The seeded values below are decisions, not a mechanical copy of the old Field
-- flags, and two of them deliberately TIGHTEN what the old inference said:
--
--   store_aliases   old: false.  An alias reassigns a whole file's rows to a
--                   different storefront, which moves every per-store total in the
--                   workbook. The old `false` was the absence of a decision (the
--                   Field simply never set the flag), not a claim that it was safe.
--   store_brands    old: absent entirely — `store_to_brand` had no Field at all
--                   (docs/14-PRODUCTION-READINESS.md D6), so it inferred `unknown`
--                   => invalidating. Brand is a column in the finance workbook, so
--                   `true` records what the inference was already doing.
--
-- Two are deliberately `false`, and that is also a decision rather than an omission:
--
--   config_stores       the roster governs whether a run STOPS, not what a cell
--                       holds. Adding a store cannot move a cell in a golden that
--                       was generated without it; running the canary would burn
--                       eleven minutes to prove nothing. `check_stores` is the
--                       control here, not the golden gate.
--   config_tolerances   a tolerance decides whether a difference is reported as a
--                       variance. It is read by `src/tieout.py` and by nothing that
--                       writes a workbook cell. Widening one is dangerous for a
--                       completely different reason, recorded on the table itself.
--
-- Tightening is free (an extra canary run costs time); loosening is not, so
-- anything not listed keeps the `true` default.

alter table config_platforms          add column if not exists invalidates_goldens boolean not null default true;
alter table config_reading            add column if not exists invalidates_goldens boolean not null default true;
alter table config_column_maps        add column if not exists invalidates_goldens boolean not null default true;
alter table config_stores             add column if not exists invalidates_goldens boolean not null default true;
alter table config_store_aliases      add column if not exists invalidates_goldens boolean not null default true;
alter table config_store_brands       add column if not exists invalidates_goldens boolean not null default true;
alter table config_tolerances         add column if not exists invalidates_goldens boolean not null default true;
alter table config_settlement_bounds  add column if not exists invalidates_goldens boolean not null default true;
alter table config_fee_types          add column if not exists invalidates_goldens boolean not null default true;
alter table config_vat_sku            add column if not exists invalidates_goldens boolean not null default true;

-- The two that are false, applied to rows already present. New rows get their
-- value from `service/config_rows.py`, which holds the same two decisions next to
-- the table it describes; this statement is only for a database migrated in place.
update config_stores     set invalidates_goldens = false;
update config_tolerances set invalidates_goldens = false;

comment on column config_stores.invalidates_goldens is
    'false: the roster decides whether a run stops, not what a cell holds';
comment on column config_tolerances.invalidates_goldens is
    'false: read by src/tieout.py, which reports variances and writes no cell';
comment on column config_store_aliases.invalidates_goldens is
    'true: an alias reassigns a file''s rows to a different storefront';

-- ---------------------------------------------------------------------------
-- What a proposal is
-- ---------------------------------------------------------------------------
--
-- `config_proposals.edits` is jsonb and already holds whatever the editor produced,
-- so the wire format changing from dotted-path operations to row operations needs
-- no column. It does need to be TELLABLE APART: a pre-1.6 proposal replayed as if
-- it were row edits would fail confusingly, and `rebase` exists precisely to replay
-- a stale proposal's recorded intent.
--
-- 'path' = M6's dotted-path edits (service/config_edits.py, now deleted)
-- 'row'  = M8/1.6's per-table row operations (service/config_rows.py)
-- null   = M5, which recorded only the resulting file
alter table config_proposals add column if not exists edit_model text
    check (edit_model in ('path', 'row'));

update config_proposals set edit_model = 'path' where edits is not null and edit_model is null;

comment on column config_proposals.edit_model is
    'which editor produced `edits`; null means M5, which recorded only the file. '
    'A proposal cannot be rebased across models — the operations do not mean the '
    'same thing — so this is read before replaying one.';
