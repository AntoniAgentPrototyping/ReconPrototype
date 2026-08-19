-- M8 / Phase 1.2 — configuration becomes rows.
--
-- ---------------------------------------------------------------------------
-- What this reverses, and why that is defensible
-- ---------------------------------------------------------------------------
--
-- 002_m5.sql said, in this schema: "Storing parsed YAML would discard exactly the
-- part that carries the evidence." D2 said the same — config stays git-backed YAML
-- because a database "would destroy the evidence comments and make month-end depend
-- on the app being up".
--
-- Both objections are answered by the shape below rather than waved away:
--
--   * EVIDENCE BECOMES A COLUMN. Every table here carries `evidence`, and it is the
--     verbatim comment block lifted out of settings.yaml by the importer using the
--     same `config_store.evidence_for` the editor already renders. A column is
--     strictly stronger than a comment: it is queryable, it carries an author and a
--     date, and it cannot be orphaned by an edit to a neighbouring key — which is a
--     real failure mode the editor had to model (`config_edits.OrphanedEvidence`).
--
--   * THE RENDERED FILE STILL EXISTS. `service/config_render.py` turns these rows
--     back into settings.yaml text WITH the evidence re-emitted as comments, and
--     that text is what `config_versions.content` stores and what a run is pinned
--     to. So the immutable audit record is unchanged: still a whole file, still
--     verbatim, still comment-carrying. These tables are the EDITABLE WORKING SET;
--     `config_versions` remains the archive. Period pinning (defect 2.5) is
--     untouched.
--
--   * MONTH-END DOES NOT NEWLY DEPEND ON POSTGRES. The worker already cannot claim
--     a job without it. `config/settings.yaml` stays in git as the seed and as the
--     CLI's input, and `service.admin config export` writes the current rows back to
--     it, so `tools/devrun.py` and the golden gate never require the service.
--
-- The reason to do it at all is not tidiness. It is that a config change applied in
-- the browser TODAY DOES NOT REACH THE WORKER: config/ is baked into the image, no
-- volume is mounted, and the api writes settings.yaml into its own container's
-- writable layer while the worker reads its own untouched copy. Rows in a shared
-- database are how one edit reaches both (docs/14-PRODUCTION-READINESS.md A1).
--
-- ---------------------------------------------------------------------------
-- The shape, and the two rules every table follows
-- ---------------------------------------------------------------------------
--
-- 1. `sort_order` on everything. Rendering must be BYTE-STABLE for a given row set,
--    because `config_versions` is content-addressed on sha256 — unstable ordering
--    would mint a new "version" on every render and break pin de-duplication.
--    Ordering is explicit, never insertion order and never a bare text sort.
--
-- 2. `invalidates_goldens` defaults to TRUE. An unknown path already counts as
--    invalidating in `config_schema.invalidates_goldens`, for the reason recorded
--    there: defaulting to "harmless" is how a gate degrades into a skip. The column
--    inherits that posture rather than re-deciding it.
--
-- `reader` names the MODULE ONLY, with no line number. The old schema carried
-- "src/ingest.py:290 check_stores" on every field and the line numbers had rotted —
-- check_stores was at 309, read_excel_sheet at 131 not 148, report_unparseable at
-- 102 not 125 — while being rendered to a finance user on every control. A module
-- name is provenance; a line number is a maintenance liability that nothing tested.
--
-- ---------------------------------------------------------------------------
-- What is deliberately NOT a table
-- ---------------------------------------------------------------------------
--
-- The canonical field vocabulary (order_id, gross_revenue, ...). It stays DERIVED
-- from `src.ingest`'s own constants by `config_schema.canonical_fields`. A table
-- would be a second definition of what the pipeline understands, free to drift from
-- the code that actually consumes it; the derivation cannot. It is the right-hand
-- side of every column-map dropdown, so being wrong there is expensive.

-- ---------------------------------------------------------------------------
-- Scalars: one row per top-level (or shallowly nested) single value
-- ---------------------------------------------------------------------------
--
-- `value` is jsonb so one table holds a bool, a number, a string and the one small
-- list (file_formats) without a column per type. The renderer reads the jsonb type
-- to decide how to emit it; nothing downstream infers a type from the text.

create table if not exists config_scalars (
    key         text        primary key,          -- dotted: "vat_factors.default"
    value       jsonb       not null,
    reader      text        not null,             -- module only, no line number
    label       text        not null,
    help        text        not null default '',

    -- The PII control is the only locked field. Locked means the API refuses the
    -- write, not that the UI hides the control — a field a form omits is
    -- indistinguishable from a field nobody thought about.
    locked          boolean not null default false,
    locked_reason   text    not null default '',

    invalidates_goldens boolean not null default true,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    constraint config_scalars_locked_needs_reason check (
        not locked or length(trim(locked_reason)) >= 20)
);

comment on table config_scalars is
    'single-valued settings; `key` is the dotted path the renderer emits';
comment on column config_scalars.locked is
    'true only for drop_unmapped_columns — the PII control. A privacy incident '
    'should not be two clicks (D42).';

-- ---------------------------------------------------------------------------
-- Per-platform and per-platform/kind reading rules
-- ---------------------------------------------------------------------------
--
-- settings.yaml spreads these across six sibling maps keyed identically —
-- sheet_names, sheet_patterns, header_rows, skip_rows_after_header, reader_engine
-- (platform/kind) and dayfirst (platform). Reading one export's rules meant looking
-- in six places and inferring absence. One row per platform/kind puts them together
-- and makes "what does the reader do with this file" a single lookup.

create table if not exists config_platforms (
    platform    text        primary key check (platform in ('tiktok', 'shopee', 'lazada')),
    dayfirst    boolean     not null default false,

    -- The regex whose group 1 IS the store. Store identity comes from the filename
    -- because TikTok and Shopee exports carry no store column (D6), so this is the
    -- highest-consequence string in the file: a wrong capture reassigns a
    -- storefront's revenue. Lazada's equivalent lives in src/lazada.py until 1.7.
    store_from_filename text,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null
);

create table if not exists config_reading (
    platform        text    not null check (platform in ('tiktok', 'shopee', 'lazada')),
    kind            text    not null,             -- orders | income | weekly | daily

    sheet_name      text,                         -- exact sheet; null = first sheet
    sheet_pattern   text,                         -- regex; every match is concatenated
    header_row      integer not null default 1,   -- 1-based row of the LEAF header
    skip_rows_after_header integer not null default 0,
    reader_engine   text    check (reader_engine in ('openpyxl', 'calamine')),

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, kind),

    -- Both at once is ambiguous: read_parts prefers the pattern and would silently
    -- ignore the exact name, which is the kind of "configured and inert" state this
    -- migration exists to make impossible.
    constraint config_reading_one_sheet_selector check (
        sheet_name is null or sheet_pattern is null)
);

comment on column config_reading.header_row is
    '1-based. Shopee income is 3: two band rows sit above the leaf header.';
comment on column config_reading.skip_rows_after_header is
    'TikTok orders is 1: a per-column description row sits under the header and '
    'must not be ingested as data.';

-- ---------------------------------------------------------------------------
-- Column maps — the biggest win of normalising
-- ---------------------------------------------------------------------------
--
-- In YAML a renamed header is a second key with a comment beside it. As rows,
-- "which spellings have we seen for gross_revenue" is a query, a superseded
-- spelling can be deactivated WITH THE DATE IT STOPPED APPEARING instead of being
-- deleted or silently kept, and monthly format drift becomes data rather than
-- archaeology across git history.
--
-- `active` is why this is not just a dict: June 2026 renamed "Order\adjustment ID"
-- to "Order/Adjustment ID" and BOTH still resolve, because the pipeline may be
-- pointed at an older export at any time. Deleting the old spelling would break a
-- re-run of May; keeping it with no marker loses the fact that it is historical.

create table if not exists config_column_maps (
    platform    text    not null check (platform in ('tiktok', 'shopee', 'lazada')),
    kind        text    not null,
    raw_header  text    not null,             -- the header as the export spells it
    canonical   text    not null,             -- the pipeline's own vocabulary

    active      boolean not null default true,
    retired_at  date,                         -- when this spelling stopped appearing

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, kind, raw_header),

    constraint config_column_maps_retired_is_inactive check (
        retired_at is null or not active)
);

create index if not exists config_column_maps_canonical
    on config_column_maps (platform, kind, canonical);

comment on column config_column_maps.raw_header is
    'NFC-normalised at import, because ingest.read_parts NFC-normalises headers '
    'before matching and a decomposed key here would never match (defect 1.2).';
comment on column config_column_maps.canonical is
    'must be a name src/ingest.py understands — validated against '
    'config_schema.canonical_fields, which derives it from the pipeline itself '
    'rather than from a table that could drift.';

-- ---------------------------------------------------------------------------
-- Stores: one table, not two lists that have to be kept in step
-- ---------------------------------------------------------------------------
--
-- expected_stores and stores_optional were parallel lists under separate top-level
-- keys, and the editor could not add to the second one at all — `stores_optional`
-- had no schema field, so `_check_may_add` refused it while the roster widget's
-- help text described an "optional" flag that did not exist. One row with a boolean
-- is what that help text always meant.
--
-- The absence of a lazada row here is a real gap, not an omission of this migration:
-- there is no Lazada roster and `_run_lazada` never calls check_stores
-- (docs/14-PRODUCTION-READINESS.md A6). Phase 1.7 wires it.

create table if not exists config_stores (
    platform    text    not null check (platform in ('tiktok', 'shopee', 'lazada')),
    store       text    not null,

    -- Absence warns instead of hard-stopping. Used for stores that legitimately do
    -- not trade in every window — onboarded mid-month, or a 3-day window where the
    -- income export is header-only.
    optional    boolean not null default false,

    active      boolean not null default true,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, store)
);

-- ---------------------------------------------------------------------------
-- Store aliases
-- ---------------------------------------------------------------------------
--
-- `canonical` is NULLABLE and null means "nobody has decided yet". That replaces
-- the "TODO-HUMAN" sentinel string, which had to be special-cased in three places
-- (ingest.read_parts skips it, materialize.canonical_store must resolve it to
-- itself, the UI relabels it "undecided") and which one wrong comparison would have
-- turned into a store literally named TODO-HUMAN.

create table if not exists config_store_aliases (
    platform    text    not null check (platform in ('tiktok', 'shopee', 'lazada')),
    raw         text    not null,             -- as it appears in a file name
    canonical   text,                         -- null = unresolved, warn on sight

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, raw),

    -- An alias to itself is a no-op that reads as a decision.
    constraint config_store_aliases_not_identity check (
        canonical is null or canonical <> raw)
);

-- ---------------------------------------------------------------------------
-- Store -> brand
-- ---------------------------------------------------------------------------
--
-- This absorbs TWO mappings that exist today and disagree by construction:
-- `store_to_brand` in settings.yaml is `{}` (so every store falls back to its own
-- name with a loud ingest warning on every run), while config/brand_map.csv holds
-- 60 rows and is read only by tools/build_master_summary.py — never by the
-- pipeline. One store therefore has one brand in the month-end master and a
-- different one in the weekly finance file (docs/14-PRODUCTION-READINESS.md D12).
--
-- `confidence` and `note` come straight from brand_map.csv, which had already
-- invented the evidence-as-a-column pattern this whole migration generalises.

create table if not exists config_store_brands (
    platform    text    not null check (platform in ('tiktok', 'shopee', 'lazada')),
    store       text    not null,
    brand       text    not null,
    confidence  text    not null default 'confirmed'
                        check (confidence in ('confirmed', 'needs_confirmation')),

    -- Whether the PIPELINE reads this row, i.e. whether it renders into
    -- `store_to_brand`. False for every row imported from brand_map.csv.
    --
    -- This column is the difference between a migration and a behaviour change.
    -- `store_to_brand` is `{}` today, so `ingest.derive_brand` falls back to the
    -- store name for every store; brand_map.csv holds 60 rows that only the
    -- month-end master reads. Rendering those 60 rows into `store_to_brand` would
    -- silently give every store a different brand than the run before — measured,
    -- 28 stores would change — inside what is supposed to be an output-identical
    -- refactor. Reconciling the two mappings is a real decision with a stated
    -- delta and its own commit (docs/14-PRODUCTION-READINESS.md D12); until then
    -- both live here and only one is in force.
    in_pipeline_contract boolean not null default false,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, store)
);

-- ---------------------------------------------------------------------------
-- Tolerances
-- ---------------------------------------------------------------------------
--
-- Seven tolerances are READ by src/tieout.py and were never configured, so their
-- code literals were the source of truth by accident — including every Lazada
-- tolerance, which had no YAML entry at all. Importing them as rows with their
-- current literal values makes the contract say what the pipeline actually does.
--
-- `platform` is nullable for a future file-wide tolerance. There are none today:
-- the two that existed (split_rounding_vnd, exact_check_vnd) were read by nothing
-- and were deleted in Phase 1.1.

create table if not exists config_tolerances (
    platform    text    check (platform in ('tiktok', 'shopee', 'lazada')),
    name        text    not null,             -- pv_sum_vnd, conservation_vnd, ...
    vnd         numeric not null check (vnd >= 0),

    reader      text        not null,
    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null
);

-- A partial unique index rather than a primary key, because `platform` is nullable
-- and NULLs do not compare equal in a unique constraint.
create unique index if not exists config_tolerances_platform_name
    on config_tolerances (coalesce(platform, ''), name);

comment on table config_tolerances is
    'widening one to make a check pass is how the original checks became '
    'worthless — diff first and understand the number (D12).';

-- ---------------------------------------------------------------------------
-- Settlement bounds
-- ---------------------------------------------------------------------------
--
-- Deduplication of a PULL ARTIFACT, never a rule. An entry belongs here only when a
-- raw export was pulled with the wrong start/end date AND its out-of-window rows
-- are proven already present in the adjacent window. `evidence` is not decorative
-- on this table — it is the proof, and the check below refuses a row without one.

create table if not exists config_settlement_bounds (
    period      text    primary key,
    from_date   date,
    to_date     date,

    evidence    text        not null,
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    constraint config_settlement_bounds_has_a_bound check (
        from_date is not null or to_date is not null),
    constraint config_settlement_bounds_ordered check (
        from_date is null or to_date is null or from_date <= to_date),

    -- The one table where evidence is load-bearing rather than helpful: this
    -- silently drops settlement rows from a finance file.
    constraint config_settlement_bounds_needs_evidence check (
        length(trim(evidence)) >= 40)
);

-- ---------------------------------------------------------------------------
-- The team-owned master, as rows
-- ---------------------------------------------------------------------------
--
-- Seeded from the committed CSV snapshots (lazada_fee_types.csv, 118 rows;
-- lazada_vat_sku.csv, 668 rows). The live "Lib & VAT rate.xlsb" is still read at
-- runtime and still wins — these are the fallback, exactly as the CSVs were, and
-- drift between the two is still reported every run.
--
-- Phase 1.8 may make an upload the way the master arrives, which would close open
-- question 8 (nobody owns that file as a runtime dependency). That is a change to
-- how the TEAM works, so it is not decided here.
--
-- Worth knowing before trusting config_vat_sku: its 668 SKUs match ZERO of the SKUs
-- traded at every sampled store on all three platforms, so the per-SKU override has
-- never fired in production and everything invoices at the 1.08 default. Coverage
-- is counted and logged every run. That is open question 9, not a bug.

create table if not exists config_fee_types (
    fee_name    text        primary key,
    bucket      text        not null,
    status      text        not null default '',

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null
);

create table if not exists config_vat_sku (
    sku         text        primary key,
    rate        numeric     not null check (rate > 0),

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null
);

-- ---------------------------------------------------------------------------
-- Where the rendered file came from
-- ---------------------------------------------------------------------------
--
-- config_versions already stores rendered text content-addressed by sha256. This
-- column records that a given version was RENDERED FROM THESE TABLES rather than
-- read off disk, so "which config did this run use, and where did it come from"
-- stays answerable after the file stops being the source of truth.
--
-- `source` on config_versions is currently 'disk' | 'proposal'; 'rendered' joins
-- them. The existing check constraint is replaced rather than dropped, so an
-- unknown value is still refused.

alter table config_versions drop constraint if exists config_versions_source_check;
alter table config_versions add constraint config_versions_source_check
    check (source in ('disk', 'proposal', 'rendered'));

comment on column config_versions.source is
    'disk = read from config/settings.yaml (pre-M8, and the seed path); '
    'proposal = the text an approved proposal produced; '
    'rendered = generated from the config_* tables by service/config_render.py.';
