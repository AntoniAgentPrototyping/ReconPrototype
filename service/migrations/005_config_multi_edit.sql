-- M6, workstream D: a proposal is a set of edits, and applying one measures its
-- effect on the numbers.
--
-- ---------------------------------------------------------------------------
-- What this supersedes in 002_m5.sql
-- ---------------------------------------------------------------------------
--
-- 002 cannot be edited (service/db.py refuses if an applied file's sha256 changes),
-- so its comments about `config_proposals` would otherwise become uncorrectable
-- lies. Two are now wrong:
--
--  * "The approval MODEL is deliberately not baked in ... because who owns
--    configuration and who signs off a rate change is an open question for a human"
--    — that question has been ANSWERED (docs/11-OPEN-QUESTIONS.md #13, closing
--    defect 2.7). `recon.user` and `recon.admin` propose; `recon.viewer` cannot;
--    only `recon.admin` approves, rejects or applies. `ApprovalPolicy`,
--    `ApprovalDenied` and RECON_CONFIG_APPROVAL are deleted — they existed only to
--    avoid assuming an answer.
--
--    Self-approval is ALLOWED and RECORDED rather than forbidden. Forbidding it
--    deadlocks a single-admin deployment and pushes the edit back to hand-editing
--    settings.yaml, which has no audit trail at all. `self_approved` below is a
--    GENERATED column so it cannot be set to a convenient value: it is computed
--    from the two names, and a reviewer counting self-approvals is reading a fact.
--
--    The honest form of closing 2.7: this is RECORDED EVIDENCE, not separation of
--    duties. A single-admin deployment has no second person, and no schema can
--    invent one.
--
--  * The proposal carried `content` and `diff` only, so it recorded WHAT the file
--    became and not WHAT WAS ASKED FOR. That difference matters twice over: a
--    proposal made against a file that has since moved cannot be replayed (it could
--    only be refused, which is what `apply` does today), and a reviewer reading
--    "the diff" cannot tell an intentional two-line change from a whitespace
--    artifact. `edits` records the operations, so a stale proposal can be REBASED —
--    replayed against current bytes into a new pending proposal — instead of being
--    retyped from memory.
--
--    Rebase is NOT a merge. D38 refuses a three-way merge of a file whose comments
--    are evidence, and it is right: a merge produces something nobody wrote and
--    everybody would later have to defend. A replay re-runs the stated intent and
--    produces a fresh diff for a fresh review.


-- ---------------------------------------------------------------------------
-- config_proposals
-- ---------------------------------------------------------------------------

-- The operations that were requested, as [{op, path, value?, key?, comment?}].
-- Nullable: proposals written by M5 carry only their content, and backfilling a
-- guess at what they meant would be inventing an audit trail.
alter table config_proposals add column if not exists edits jsonb;

-- Set when this proposal is a replay of a stale one, so the chain stays readable:
-- "withdrawn, rebased as 41" is a different history from two people proposing the
-- same change independently.
alter table config_proposals add column if not exists rebased_from bigint
    references config_proposals (id);

-- Computed, never written. See the note above.
alter table config_proposals drop column if exists self_approved;
alter table config_proposals add column self_approved boolean
    generated always as (decided_by is not null and decided_by = proposed_by) stored;

comment on column config_proposals.self_approved is
    'computed: the approver was the proposer. Allowed and recorded, not forbidden — '
    'a single-admin deployment has no second person and no schema can invent one';

create index if not exists config_proposals_state on config_proposals (state, id desc);


-- ---------------------------------------------------------------------------
-- config_versions — did this change move a number?
-- ---------------------------------------------------------------------------

-- The replacement for M1's deleted `oracle_rev` (docs/06-DECISIONS.md#d26).
--
-- `oracle_rev` keyed every golden manifest on a hash of src/ + config/, so ANY
-- change to either orphaned every golden, the manifest lookup missed, and the
-- zero-tolerance gate silently degraded into a skip. A gate that turns itself off
-- when the code changes is worse than no gate, because it reports green.
--
-- This inverts the assumption. Instead of declaring every golden invalid, applying
-- a change that touches a field which CAN move a cell re-runs a canary window under
-- the new config and compares the workbook to its committed digest. The outcome is
-- recorded here. Nothing is blocked — the change lands and the system says what it
-- did.
--
--   'verified'        a canary ran, no cell moved
--   'cells_moved'     a canary ran, cells moved — the goldens need a deliberate
--                     re-baseline with a stated reason
--   'unavailable'     no canary window exists in this deployment; NO CLAIM MADE
--   'failed'          the canary run itself broke
--   'not_applicable'  nothing this change touched can move a cell
--
-- The three states 'verified', 'unavailable' and 'not_applicable' are deliberately
-- distinct. Collapsing them into a boolean is exactly how "we never checked" comes
-- to read as "we checked and it was fine".
alter table config_versions add column if not exists verification_state text
    check (verification_state is null or verification_state in
           ('verified', 'cells_moved', 'unavailable', 'failed', 'not_applicable'));

-- WHICH window answered, because the strength of the claim depends on it. A real
-- committed golden exercises the real column maps, sheet names and header
-- spellings; the synthetic demo window only exercises the paths its own generator
-- emits, so a column-map edit for a header the generator never writes would move
-- nothing there and everything in production.
alter table config_versions add column if not exists verified_window text;
alter table config_versions add column if not exists verified_window_is_real boolean;

-- Sheet-level, and named as such: the committed side is only ever a DIGEST, so the
-- honest unit is "cells in the sheets that differ" rather than an exact moved-cell
-- count nobody can compute from a hash.
alter table config_versions add column if not exists cells_moved int;
alter table config_versions add column if not exists verification jsonb;
alter table config_versions add column if not exists verified_at timestamptz;

comment on column config_versions.verified_window_is_real is
    'false for the synthetic demo window — a genuinely weaker claim, kept distinct '
    'so the UI cannot render it as the same statement';
