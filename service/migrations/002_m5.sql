-- M5 — identity, config versioning, the upload boundary, and the exception queue.
--
-- The through-line: M4 built a service that could run the pipeline. Everything
-- here exists so that a HUMAN can be held to what it did — who asked for a run,
-- which rules it ran under, where the file came from, and what it flagged.

-- ---------------------------------------------------------------------------
-- Identity
-- ---------------------------------------------------------------------------

-- Bearer tokens, hashed. The raw token is shown once at creation and never
-- stored, so a database leak cannot be replayed as access.
--
-- Why sha256 and not bcrypt/argon2: those exist to make LOW-ENTROPY secrets
-- expensive to guess. A token here is 32 bytes from os.urandom — brute force is
-- not on the table, and a slow KDF would only add latency to every request.
-- The rule this depends on is that tokens are GENERATED, never user-chosen;
-- service/auth.py is the only thing that mints them.
create table if not exists api_tokens (
    id            bigserial   primary key,
    name          text        not null,
    token_sha256  text        not null unique,

    -- The same three strings Entra will emit in its `roles` claim, so swapping
    -- token auth for OIDC later changes who supplies the role, not what a role
    -- means (docs/13-ENTRA-SETUP.md).
    role          text        not null check (role in ('recon.viewer', 'recon.operator', 'recon.admin')),

    -- Who this token acts as. This is what lands in jobs.requested_by, which is
    -- the whole point: before M5 that column was a free-text claim supplied by
    -- the caller, i.e. not evidence of anything.
    subject       text        not null,

    created_at    timestamptz not null default now(),
    created_by    text,
    expires_at    timestamptz,
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    revoked_by    text
);

create index if not exists api_tokens_live on api_tokens (token_sha256)
    where revoked_at is null;


-- ---------------------------------------------------------------------------
-- Config versioning  (docs/08-KNOWN-DEFECTS.md 2.5)
-- ---------------------------------------------------------------------------

-- The full text of a settings.yaml, verbatim.
--
-- The whole file, not a parsed structure, because the COMMENTS are the audit
-- trail (docs/06-DECISIONS.md#d2) — an alias entry cites its order-ID-overlap
-- proof, a reader-engine choice cites the malformed tag it works around.
-- Storing parsed YAML would discard exactly the part that carries the evidence.
-- The file is ~300 lines; there is no size argument for being clever here.
create table if not exists config_versions (
    id          bigserial   primary key,
    sha256      text        not null unique,
    content     text        not null,
    -- 'disk'      captured from the filesystem at run time
    -- 'proposal'  produced by an approved edit through the config editor
    source      text        not null check (source in ('disk', 'proposal')),
    git_commit  text,
    created_at  timestamptz not null default now(),
    created_by  text
);

-- Which config a given settlement window is pinned to.
--
-- This is the actual fix for "changing a rate in August must not change what a
-- re-run of May produces". Without a pin, a re-run reads whatever is on disk
-- today, and a re-run that quietly produces different numbers than the run that
-- was invoiced from is the worst failure this system could have.
create table if not exists period_config (
    platform          text        not null check (platform in ('tiktok', 'shopee', 'lazada')),
    period            text        not null,
    config_version_id bigint      not null references config_versions (id),
    pinned_at         timestamptz not null default now(),
    pinned_by         text,
    reason            text,
    primary key (platform, period)
);

-- Every run records the config it actually ran under — pinned or not. A run
-- whose config cannot be identified afterwards cannot be defended, and this is
-- the column that makes "why did May produce this number" answerable.
alter table runs add column if not exists config_version_id bigint references config_versions (id);
alter table runs add column if not exists config_was_pinned boolean not null default false;


-- Proposed edits, and their approval.
--
-- The approval MODEL is deliberately not baked in: whether a proposer may
-- approve their own change is service.config_store's `approval` setting, because
-- who owns configuration and who signs off a rate change is an open question for
-- a human, not an assumption for a schema (docs/11-OPEN-QUESTIONS.md #13).
-- What the schema does fix is that the two acts are SEPARATELY RECORDED, so
-- whatever policy is chosen later can be audited against.
create table if not exists config_proposals (
    id            bigserial   primary key,

    -- What the editor was looking at when it made the change. An apply whose
    -- base no longer matches the current version is refused rather than merged:
    -- a silent three-way merge of a file whose comments are evidence is not a
    -- thing anyone should build.
    base_sha256   text        not null,
    content       text        not null,
    summary       text        not null,
    diff          text        not null,

    state         text        not null default 'pending'
                              check (state in ('pending', 'approved', 'rejected', 'applied', 'withdrawn')),
    proposed_by   text        not null,
    proposed_at   timestamptz not null default now(),
    decided_by    text,
    decided_at    timestamptz,
    decision_note text,
    applied_version_id bigint references config_versions (id),

    constraint config_proposals_decision_is_complete check (
        (state in ('pending', 'withdrawn')) or (decided_by is not null and decided_at is not null))
);


-- ---------------------------------------------------------------------------
-- The upload boundary  (docs/08-KNOWN-DEFECTS.md 2.3)
-- ---------------------------------------------------------------------------

-- One row per uploaded raw export.
--
-- `sha256` is unique because that is the M2.5 staging control brought forward:
-- byte-identical duplicate exports are the DOUBLE-PULL class, and one of those
-- carried 5.97B VND of double-invoicing risk (docs/06-DECISIONS.md#d9). The
-- database refusing the second copy is cheaper than a tool noticing it.
create table if not exists uploads (
    id             bigserial   primary key,
    filename       text        not null,
    sha256         text        not null unique,
    bytes          bigint      not null check (bytes >= 0),

    platform       text        check (platform in ('tiktok', 'shopee', 'lazada')),
    period         text,
    kind           text,

    -- Raw exports carry customer PII. It is stripped when the pipeline READS a
    -- file, but an upload endpoint puts the unstripped original on a server, so
    -- the strip has to happen here too. This records what was removed, because a
    -- privacy control nobody can evidence is not a control
    -- (docs/04-DATA-FLOW.md#pii--what-must-never-leave).
    pii_columns_dropped text[]  not null default '{}',
    sanitized      boolean     not null default false,

    state          text        not null default 'received'
                               check (state in ('received', 'staged', 'rejected')),
    reason         text,
    uri            text,

    uploaded_by    text        not null,
    created_at     timestamptz not null default now(),
    staged_at      timestamptz
);

create index if not exists uploads_window on uploads (platform, period);


-- ---------------------------------------------------------------------------
-- The exception queue
-- ---------------------------------------------------------------------------

-- What each sheet of exceptions.xlsx held, and how much of it was kept.
--
-- `total_rows` vs `stored_rows` exists so a cap is never silent. TikTok's
-- unmatched-settlement class alone is ~11,765 orders; storing a bounded slice is
-- reasonable, storing it while implying completeness is not.
create table if not exists run_exception_sheets (
    run_id      bigint      not null references runs (id) on delete cascade,
    sheet       text        not null,
    total_rows  int         not null,
    stored_rows int         not null,
    primary key (run_id, sheet)
);

-- Individual exception rows.
--
-- `fingerprint` is a stable identity for one exception across runs — the same
-- unmatched order in next week's re-run hashes the same. M6 hangs dispositions
-- off this so a decision survives a re-run; M5 only needs it to exist and be
-- stable, and getting it wrong later is far more expensive than defining it now.
create table if not exists run_exceptions (
    id          bigserial   primary key,
    run_id      bigint      not null references runs (id) on delete cascade,
    sheet       text        not null,
    fingerprint text        not null,
    payload     jsonb       not null,
    created_at  timestamptz not null default now()
);

create index if not exists run_exceptions_run   on run_exceptions (run_id, sheet);
create index if not exists run_exceptions_print on run_exceptions (fingerprint);
