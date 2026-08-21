-- Phase 6 / C12 — who downloaded a workbook, recorded.
--
-- Every finance workbook carries every store's revenue, and the refusal to use
-- presigned URLs (service/objects.py) was justified partly on audit grounds —
-- yet the download itself left no record. This is that record: appended by the
-- api's artifact handler after the digest check passes and before a byte is
-- streamed, read by `python -m service.admin audit downloads`.
--
-- The insert is IN the request path on purpose. An audit row that is allowed to
-- fail silently recreates the gap under a thin coat of paint; the database is
-- already a hard dependency of the same request (the artifact row comes from it),
-- so the added failure surface is nil.
--
-- `downloaded_by` is the session's subject — an account name, not client data.
-- `on delete cascade` follows the run: this table answers "who has seen this
-- deliverable", not "what did a deleted run once contain".

create table if not exists artifact_downloads (
    id             bigserial   primary key,
    run_id         bigint      not null references runs (id) on delete cascade,
    artifact_name  text        not null,
    downloaded_by  text        not null,
    at             timestamptz not null default now()
);

create index if not exists artifact_downloads_by_run
    on artifact_downloads (run_id, at desc);

comment on table artifact_downloads is
    'append-only read audit for artifact downloads (C12); written by the api on '
    'every successful download, listed by service.admin audit downloads';
