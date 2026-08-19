-- Which uploaded file contains which (store, order_id) — defect 2.12's detection half.
--
-- **The question that could not be asked.** `explode_to_sku_*` joins a window's income
-- to the order files staged in THAT window's folder. An order settled in `w2` may have
-- been created days earlier, so its SKU lines live in `w1`'s export; the income row
-- matches nothing and the money leaves the invoice through the documented "~21%
-- unmatched" door, which is expected to have traffic. July's first external month-end
-- comparison found 4,527,401,608 VND of understatement this way.
--
-- Seeing it requires answering "does some OTHER window's order file hold this order's
-- lines?", and nothing could: `uploads_for_window` is hard-keyed to one (platform,
-- period), and the sanitized objects are opaque blobs. This table is that answer, and
-- it is the ONLY new question the database learns to answer.
--
-- **What this is not.** It is not a step toward computing money in SQL. The money math
-- in `src/` was ported formula-by-formula from the team's own workbooks and verified
-- row-by-row against their output; a SQL reimplementation would be a second,
-- unverified implementation of the path that produces the invoice — the D31 failure,
-- and the reason the worker adds no compute either. The rule this table is built under:
-- **the database may know where every number came from; it may never compute one.**
-- Every column here is an identifier or a count. No amount, no rate, no total.
--
-- **Exposure.** `store` and `order_id` are already persisted per row in
-- `run_exceptions` (fingerprint inputs) and named in `run_log_lines`, so this is the
-- exposure accepted in defect 2.6, not a new one. No customer field, no cell value.
--
-- **Size.** July is the largest month staged: ~8.4M distinct (store, order_id) pairs
-- across orders and income for two platforms, with an order commonly appearing in
-- several exports (TikTok re-ships each store's prior-month pull in every weekly
-- folder, deliberately — the cross-period stitch needs it). At ~90 bytes/row including
-- both indexes that is well under 1 GB/year, against ~9.8 GB of raw exports per month.
-- Derived, rebuildable from the digest-verified objects, and prunable.
create table if not exists upload_order_index (
    upload_id bigint not null references uploads(id) on delete cascade,
    store     text   not null,
    order_id  text   not null,
    primary key (upload_id, store, order_id)
);

-- The lookup that matters: given an order, which uploads hold it. Leads on
-- (store, order_id) because that is the composite identity everything else keys on
-- since M2.5 — an order id is unique per store, not globally (defect 1.5 / 2.9).
create index if not exists upload_order_index_lookup
    on upload_order_index (store, order_id);

alter table uploads add column if not exists settles_from date;
alter table uploads add column if not exists settles_to   date;
-- NULL means "not yet indexed", which is deliberately distinguishable from
-- "indexed and found no order-id column". The second is a legitimate answer for a
-- file kind that carries none; the first is work outstanding, and
-- `python -m service.order_index --backfill` is what clears it.
alter table uploads add column if not exists indexed_at timestamptz;
