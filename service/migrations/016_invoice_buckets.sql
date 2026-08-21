-- A14 — the last bucket lists join the contract (the tail of M8/1.7's move).
--
-- `src/finance_template.py` held the invoice-bucket lists (which stores land in
-- "KAO 8", "Merries 8", "Curel", ... and what the catch-all is) and the VAT-rate
-- list the workbooks split by; `src/lazada.py` held the ledger's revenue/promo
-- bucket names. All of them decide what a workbook CELL holds, and none of them
-- could be seen, versioned or edited through the config that claims to be the
-- domain contract (docs/14-PRODUCTION-READINESS.md A14, the residual D4 named).
--
-- The values do not change. The rows are seeded from `config/settings.yaml`, which
-- gains `invoice_buckets`, `fee_buckets` and `vat_factors.rates` in the same
-- change (the rate list is a `config_scalars` row and needs no table here).
--
-- What deliberately stays code: the workbook TAB layout — which bucket gets a tab,
-- in what order, and the control-block cell positions. That is template geometry
-- pinned to the team's own files; each builder hard-stops when the contract names
-- a bucket its template has no tab for, so the two cannot silently disagree.
--
-- Both tables keep `invalidates_goldens` at its default TRUE: a row here decides
-- which bucket a store's money lands in, which is a workbook cell by definition.

-- ---------------------------------------------------------------------------
-- Store name -> invoice bucket, per platform
-- ---------------------------------------------------------------------------
--
-- `needle` is matched as a substring of the LOWERCASED store name, first hit wins
-- (walk order = sort_order). A NULL needle is the catch-all row: the bucket a
-- store falls into when no needle matches. `unique nulls not distinct` makes the
-- catch-all a real, editable row — one per platform — rather than a sentinel
-- string pretending to be a needle.

create table if not exists config_invoice_buckets (
    platform    text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    needle      text,                             -- null = the catch-all bucket
    bucket      text        not null,

    invalidates_goldens boolean not null default true,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    unique nulls not distinct (platform, needle)
);

comment on table config_invoice_buckets is
    'which invoice bucket a store''s lines land in; needle matched against the '
    'lowercased store name, null needle = the catch-all (A14)';
comment on column config_invoice_buckets.needle is
    'substring of the lowercased store name; null marks the platform''s catch-all '
    'row — removing that row hard-stops the next run rather than guessing';

-- ---------------------------------------------------------------------------
-- The Lazada ledger's bucket roles
-- ---------------------------------------------------------------------------
--
-- `config_fee_types` maps each fee NAME into a bucket; this table says which
-- bucket IS revenue and which ones net into the invoiced unit price. Exactly one
-- revenue row per platform — enforced in `config_rows`, where the refusal can be
-- a sentence — and any number of promo rows.

create table if not exists config_fee_buckets (
    platform    text        not null check (platform in ('lazada')),
    role        text        not null check (role in ('revenue', 'promo')),
    bucket      text        not null,

    invalidates_goldens boolean not null default true,

    evidence    text        not null default '',
    changed_by  text        not null,
    changed_at  timestamptz not null default now(),
    source      text        not null check (source in ('import', 'proposal', 'seed')),
    sort_order  integer     not null,

    primary key (platform, role, bucket)
);

comment on table config_fee_buckets is
    'the Lazada ledger''s bucket vocabulary: which bucket is revenue, which net '
    'into the invoiced unit price as promo (A14). Dropping a promo bucket '
    'OVER-states invoices (docs/08 #110), which is why absence hard-stops.';
