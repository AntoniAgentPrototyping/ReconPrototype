-- M6, workstream B and C: uploads become the input, and the roster declaration
-- moves from a per-run checkbox to a per-window statement.
--
-- ---------------------------------------------------------------------------
-- What this supersedes in 002_m5.sql
-- ---------------------------------------------------------------------------
--
-- 002 cannot be edited (service/db.py refuses if an applied file's sha256
-- changes), so its comments about `uploads` would otherwise become uncorrectable
-- lies. Three of them are now wrong:
--
--  * `uploads.state` allowed 'staged', and `staged_at` recorded when a file was
--    copied into `input/<period>/<platform>/`. There is no staging step any more.
--    The bucket IS the store: `POST /uploads/{id}/stage` is deleted and the
--    worker materialises the whole window into its own scratch at run time. The
--    state is now 'stored' (in the bucket, waiting for a run) or 'consumed' (a
--    run read it, and `consumed_by_run_id` says which). 'staged' is kept as an
--    accepted value ONLY so a row written by an M5 deployment still satisfies
--    the check constraint — nothing writes it.
--
--  * `uploads.uri` was a `file:` URI into the api's own quarantine directory,
--    which is why the api needed an input volume the worker also had. That
--    conflation is the root of defect 2.4. The address is now `object_key`, and
--    `uri` keeps the storage-qualified form (`s3://bucket/key` or `file:`) so a
--    row stays locatable if the deployment's storage mode changes.
--
--  * `uploads` recorded no store. It could not: the store is derived from the
--    filename by a regex that has changed three times in four months, and
--    deriving it at read time meant every window's roster check happened only
--    after a run had started. It is now resolved at the door, confirmed by the
--    person uploading, and recorded — which is what makes the upload screen able
--    to say "12 of 25 expected stores have files" before anything is queued.
--
-- `filename` keeps its meaning: the name the operator's browser sent. The
-- uniform name is NOT stored, because it is a function of the whole window and
-- is recomputed per run from the window's sorted originals (service/naming.py).
-- Storing it would freeze an ordinal that a later upload legitimately shifts.


-- ---------------------------------------------------------------------------
-- uploads
-- ---------------------------------------------------------------------------

alter table uploads add column if not exists store           text;
alter table uploads add column if not exists store_canonical text;
alter table uploads add column if not exists object_key      text;
alter table uploads add column if not exists consumed_by_run_id bigint
    references runs (id) on delete set null;
alter table uploads add column if not exists consumed_at     timestamptz;

-- `store` is what the filename says; `store_canonical` is what it means after
-- store_aliases is applied. Both, not one: the alias table is itself editable
-- config, and keeping the raw value is what lets a later alias change be
-- understood rather than just observed.
comment on column uploads.store is
    'store as derived from the filename by the pipeline''s own store_from_filename';
comment on column uploads.store_canonical is
    'store after store_aliases — what check_stores will compare against the roster';

-- 002 left `kind` unconstrained, so a typo reached the sanitizer and produced a
-- confusing "none of the configured columns are present". The valid set is a
-- property of the platform, hence the pair check rather than two independent ones.
alter table uploads drop constraint if exists uploads_kind_matches_platform;
alter table uploads add  constraint uploads_kind_matches_platform check (
    kind is null or platform is null or (
        (platform in ('tiktok', 'shopee') and kind in ('orders', 'income')) or
        (platform = 'lazada'              and kind in ('weekly', 'daily'))
    )
);

alter table uploads drop constraint if exists uploads_state_check;
alter table uploads add  constraint uploads_state_check check (
    state in ('stored', 'consumed', 'rejected', 'received', 'staged'));

-- Rows written before this migration were quarantined on a volume with no
-- object key, so they cannot be materialised. Renaming their state to 'rejected'
-- would be a lie about why. They keep 'received', and `consume_window` skips any
-- row with a null object_key while SAYING SO in the run log — a silent skip here
-- would produce a window that ran on fewer files than an operator uploaded.

create index if not exists uploads_window_kind on uploads (period, platform, kind);
-- One physical file per window slot, per store, per digest is already enforced by
-- the unique sha256 in 002. This index is for the roster count the upload screen
-- renders on every keystroke.
create index if not exists uploads_store on uploads (period, platform, store_canonical);


-- ---------------------------------------------------------------------------
-- windows — the roster declaration
-- ---------------------------------------------------------------------------

-- Replaces `jobs.partial_roster`, and the replacement is the point.
--
-- `src/ingest.py::check_stores` hard-stops when a window's stores do not match
-- the roster, and that control caught a real Shopee window arriving with 16 of
-- 17 stores absent. Its behaviour is UNCHANGED by M6 — what changes is where the
-- override comes from. A checkbox on the queue form is ticked every run by
-- whoever is in a hurry, leaves no reason behind, and is invisible to the person
-- reviewing the numbers afterwards. A row here is written once per window, needs
-- a reason, names its author, and is rendered on the board.
--
-- Absence of a row means the hard stop applies, which is today's behaviour. That
-- direction matters: an incomplete window fails closed.
create table if not exists windows (
    platform    text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    period      text        not null,

    roster_declared_partial boolean not null default false,
    -- Not nullable when the declaration is true — enforced below rather than by a
    -- `not null`, because a complete window has nothing to explain.
    reason      text,

    declared_by text        not null,
    declared_at timestamptz not null default now(),

    primary key (platform, period),

    constraint windows_partial_needs_reason check (
        not roster_declared_partial or (reason is not null and length(trim(reason)) >= 8))
);

comment on table windows is
    'per-window roster declaration; absence means check_stores hard-stops as usual';


-- ---------------------------------------------------------------------------
-- runs — the rendered count
-- ---------------------------------------------------------------------------

-- How many expected stores had no files. Purely for display: nothing branches on
-- it, and adding it moves no cell in any workbook. The stronger control — stamping
-- `ROSTER: n expected store(s) absent` into the finance workbook's own control
-- block, so the artefact the team invoices from carries its own caveat — moves
-- cells and is deferred to its own commit and rebaseline (M6-PLAN concern 2).
alter table runs add column if not exists roster_missing int;
