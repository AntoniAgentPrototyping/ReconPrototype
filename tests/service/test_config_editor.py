"""The config editor — the one part of the service that can change what gets invoiced.

**What changed in M8/1.6.** The contract is edited as ROWS in the `config_*` tables,
not as dotted paths into `settings.yaml` text. `service/config_rows.py` argues why;
the short version is that a row carries its own evidence and its own "this can move
a cell" flag, and a comment above a line can carry neither — it can only caption a
top-level key, and it is left describing its neighbour when the entry it documented
is removed.

The file has not stopped mattering. It is still what a run is pinned to, still
served verbatim, still comment-carrying — it is now *rendered* from the rows rather
than hand-edited, and `config/settings.yaml` in git is the seed a fresh deployment
imports from. So the canary below still holds: a round trip of the real contract
must be byte-identical, because `service/sampledata.py` still round-trips it and
because a renderer whose output wobbled would mint a config version per call.

The approval model was *configurable* through M5 because
[open question 13](docs/11-OPEN-QUESTIONS.md) — who owns configuration, who signs
off a rate change — had not been answered by a human. **It is answered in M6**
(closing defect 2.7): `recon.user` and `recon.admin` propose, `recon.viewer` cannot,
only `recon.admin` decides, and self-approval is permitted and RECORDED rather than
forbidden. So the three-mode policy object is gone and the rule lives where every
other authorization rule does — on the route.
"""

from __future__ import annotations

import pytest

from service import config_store
from service.config_rows import RowEditError

pytest.importorskip("httpx")


# `sandbox_settings`, `editor_client` and `seeded_config` live in conftest.py.


def vat(value: float) -> dict:
    """The 8% -> 10% revert, in the wire format. The scenario the roadmap names."""
    return {"table": "config_scalars", "op": "upsert",
            "key": {"key": "vat_factors.default"}, "values": {"value": value}}


# ---------------------------------------------------------------------------
# The canary
# ---------------------------------------------------------------------------

def test_round_trip_is_byte_identical_on_the_real_settings_file(service_settings):
    """Load and re-dump the real contract without editing: the bytes must match.

    `PyYAML` fails this outright — it discards every comment — which is why D2
    requires `ruamel.yaml` and why reaching for the simpler library is the most
    damaging shortcut available here. Still load-bearing after 1.6:
    `service/sampledata.py` round-trips the contract to substitute the demo roster.
    """
    before = config_store.read_text(service_settings.config_dir)
    assert config_store.round_trip(before) == before


def test_the_rendered_contract_is_what_the_editor_diffs_against(editor_client, repo):
    """`GET /config` serves the RENDERED contract, not this process's disk copy.

    That is defect A1 restated at the editor. The api and the worker are separate
    containers with their own baked copies of `config/`, so a page that diffed
    against its own file would show an operator a change against bytes the worker
    never reads.
    """
    body = editor_client("recon.viewer").get("/config").json()
    assert body["content"] == repo.render_config()
    assert "#" in body["content"], "evidence is served, not stripped"
    assert len(body["sha256"]) == 64


# ---------------------------------------------------------------------------
# The flow, over HTTP
# ---------------------------------------------------------------------------

def test_the_vat_revert_end_to_end(editor_client, repo, sandbox_settings):
    """The scenario the roadmap names: the 8% -> 10% VAT revert is one change."""
    operator = editor_client("recon.user", subject="finance@ada")
    admin = editor_client("recon.admin", subject="antoni@ada")

    proposed = operator.post("/config/proposals", json={
        "edits": [vat(1.10)],
        "summary": "VAT revert 8% -> 10% effective 2026-07-01"}).json()
    assert proposed["state"] == "pending"
    assert "1.1" in proposed["diff"] and "1.08" in proposed["diff"]

    # Nothing has changed yet — not in the tables, not in the rendered contract.
    assert config_store.read_value(
        repo.render_config(), ["vat_factors", "default"]) == 1.08

    approved = admin.post(f"/config/proposals/{proposed['id']}/approve",
                          json={"note": "confirmed with finance"}).json()
    assert approved["state"] == "approved" and approved["decided_by"] == "antoni@ada"

    applied = admin.post(f"/config/proposals/{proposed['id']}/apply").json()
    assert applied["state"] == "applied"

    # Now it has — in the rows, so the worker's next unpinned run renders it.
    after = repo.render_config()
    assert config_store.read_value(after, ["vat_factors", "default"]) == 1.10
    assert sum(1 for l in after.splitlines() if l.lstrip().startswith("#")) > 50

    version = repo.config_version(applied["config_version_id"])
    assert version["source"] == "rendered" and version["created_by"] == "antoni@ada"


def test_an_applied_change_reaches_the_contract_an_unpinned_run_would_use(
        editor_client, repo, sandbox_settings):
    """**Defect A1, at the layer 1.6 owns.**

    Through M6 the api wrote `settings.yaml` into its own container's writable
    layer while the worker resolved an unpinned window from its own untouched copy,
    so a rate change approved in the browser never reached the process that computes
    the money. `resolve_for_window` now renders from the shared tables, and this
    asserts the whole path: propose, approve, apply, then ask the resolver what a
    run of an unpinned window would compute under.
    """
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "the concession has ended"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    admin.post(f"/config/proposals/{proposal['id']}/apply")

    resolved = config_store.resolve_for_window(
        repo, sandbox_settings.config_dir, "tiktok", "2026-09_w1")
    assert not resolved.pinned
    assert config_store.read_value(
        resolved.content, ["vat_factors", "default"]) == 1.10


def test_the_file_is_still_written_so_the_cli_keeps_working(editor_client,
                                                            sandbox_settings):
    """The tables are the source of truth; the file stays a usable seed.

    `tools/devrun.py` and the golden gate read `config/settings.yaml` with the
    service switched off (D24), so an applied change still lands there. It is no
    longer how the change reaches the worker — that is the point of 1.6 — but a
    contract only the database can read would break the CLI path.
    """
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "keep the CLI runnable"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    admin.post(f"/config/proposals/{proposal['id']}/apply")

    on_disk = config_store.read_text(sandbox_settings.config_dir)
    assert config_store.read_value(on_disk, ["vat_factors", "default"]) == 1.10


def test_preview_shows_the_diff_and_commits_nothing(editor_client, repo):
    """The whole justification for a form over a text box: the operator sees the
    change in the contract's own terms before proposing it.

    Produced by applying and rolling back, so the preview cannot differ from what
    would be proposed — a simulation would be a second implementation of the write.
    """
    before = repo.render_config()
    body = editor_client("recon.user").post("/config/preview", json={
        "edits": [vat(1.10)], "summary": "just looking"}).json()

    assert body["changed"] is True
    assert "1.08" in body["diff"] and "1.1" in body["diff"]
    assert body["invalidates_goldens"] == ["Single settings"]
    assert repo.render_config() == before, "a preview wrote to the tables"


def test_several_edits_land_as_one_change(editor_client, repo):
    """The reason a proposal takes a list: with one edit each, adding a storefront
    and the alias pointing at it was two proposals, two approvals and two commits —
    so people did it in one hand-edit instead and the audit trail recorded nothing.
    """
    admin = editor_client("recon.admin")
    created = admin.post("/config/proposals", json={
        "edits": [
            {"table": "config_stores", "op": "upsert",
             "key": {"platform": "tiktok", "store": "Demo Storefront"},
             "values": {"optional": True},
             "evidence": "onboarded 2026-09-01, trades from w2 only"},
            {"table": "config_store_aliases", "op": "upsert",
             "key": {"platform": "tiktok", "raw": "Demo Store"},
             "values": {"canonical": "Demo Storefront"},
             "evidence": "the September export spells it without 'front'"},
        ],
        "summary": "a new storefront and its export spelling"}).json()

    assert created["state"] == "pending"
    admin.post(f"/config/proposals/{created['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{created['id']}/apply").status_code == 200

    rendered = config_store.parse(repo.render_config())
    assert "Demo Storefront" in rendered["expected_stores"]["tiktok"]
    assert "Demo Storefront" in rendered["stores_optional"]["tiktok"]
    assert rendered["store_aliases"]["tiktok"]["Demo Store"] == "Demo Storefront"


def test_edits_are_applied_in_the_order_given(editor_client):
    """An alias may only point at a storefront in the roster. Adding both in one
    proposal has to work, and it only works if the order is honoured."""
    admin = editor_client("recon.admin")
    r = admin.post("/config/preview", json={
        "edits": [
            {"table": "config_store_aliases", "op": "upsert",
             "key": {"platform": "tiktok", "raw": "Ghost Store"},
             "values": {"canonical": "Not Yet Added"},
             "evidence": "this alias precedes its storefront"},
            {"table": "config_stores", "op": "upsert",
             "key": {"platform": "tiktok", "store": "Not Yet Added"},
             "values": {}, "evidence": "added after the alias, which is too late"},
        ],
        "summary": "the wrong way round"})
    assert r.status_code == 422
    assert "roster" in r.json()["detail"]


# ---------------------------------------------------------------------------
# What the editor refuses
# ---------------------------------------------------------------------------

def test_the_pii_control_cannot_be_changed_through_the_form(editor_client):
    """A privacy incident should not be two clicks.

    `drop_unmapped_columns` is the PII control in two places and its diff reads as
    an ordinary boolean flip. Locked is a COLUMN, so the API refuses the write —
    rather than the UI hiding the control, which would make a field nobody may
    change indistinguishable from a field nobody thought about.
    """
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_scalars", "op": "upsert",
                   "key": {"key": "drop_unmapped_columns"},
                   "values": {"value": False}}],
        "summary": "turning off PII stripping"})
    assert r.status_code == 422
    assert "not editable" in r.json()["detail"]
    assert "customer names" in r.json()["detail"], "the refusal must say why"


def test_a_closed_table_refuses_a_new_row_and_says_why(editor_client):
    """`config_scalars` holds keys `src/` reads by name. A new one would be config
    the pipeline silently ignores, which then looks like a bug in the pipeline."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_scalars", "op": "upsert",
                   "key": {"key": "brand_new_setting"}, "values": {"value": 1},
                   "evidence": "invented out of nothing at all"}],
        "summary": "inventing a setting"})
    assert r.status_code == 422
    assert "silently ignores" in r.json()["detail"]


def test_a_tolerance_cannot_be_invented(editor_client):
    """A tolerance nothing reads is inert config — exactly what Phase 1.1 deleted
    three of. Adding one means adding the check in `src/tieout.py` that reads it."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_tolerances", "op": "upsert",
                   "key": {"platform": "tiktok", "name": "invented_vnd"},
                   "values": {"vnd": 500}, "evidence": "a number nothing reads"}],
        "summary": "a tolerance with no reader"})
    assert r.status_code == 422
    assert "nothing reads" in r.json()["detail"]


def test_a_column_map_may_only_target_the_pipelines_own_vocabulary(editor_client):
    """The right-hand side of a column map is a closed set derived from
    `src/ingest.py`. Mapping a header to an invented name produces a column
    nothing reads."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_column_maps", "op": "upsert",
                   "key": {"platform": "tiktok", "kind": "orders",
                           "raw_header": "Some New Header"},
                   "values": {"canonical": "not_a_real_field"},
                   "evidence": "the September export added this column"}],
        "summary": "a header mapped to nothing"})
    assert r.status_code == 422
    assert "understands" in r.json()["detail"]


def test_an_alias_to_a_storefront_nobody_expects_is_refused(editor_client):
    """An alias reassigns a whole file's rows. Pointing one at a storefront outside
    the roster produces a run that silently attributes revenue to a name no check
    knows about."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_store_aliases", "op": "upsert",
                   "key": {"platform": "tiktok", "raw": "Some Spelling"},
                   "values": {"canonical": "A Storefront That Does Not Exist"},
                   "evidence": "seen in the September export"}],
        "summary": "an alias pointing nowhere"})
    assert r.status_code == 422
    assert "roster" in r.json()["detail"]


def test_a_new_row_needs_a_reason(editor_client):
    """Evidence is a column, and on a new row it is required. It is what makes the
    entry defensible in six months, and unlike a comment it cannot end up
    captioning the entry below it."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_stores", "op": "upsert",
                   "key": {"platform": "tiktok", "store": "Unexplained"},
                   "values": {}}],
        "summary": "a storefront with no justification"})
    assert r.status_code == 422
    assert "needs a reason" in r.json()["detail"]


def test_a_settlement_bound_needs_more_than_a_sentence(editor_client):
    """The one table where evidence is load-bearing rather than helpful: a bound
    silently drops settlement rows from a finance file, so the reason has to be the
    proof that those rows are already in the adjacent window."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_settlement_bounds", "op": "upsert",
                   "key": {"period": "2026-09_w1"},
                   "values": {"from_date": "2026-09-01"},
                   "evidence": "looked wrong"}],
        "summary": "a bound with a thin reason"})
    assert r.status_code == 422
    assert "40 characters" in r.json()["detail"]


def test_a_scalar_keeps_the_type_it_was_seeded_with(editor_client):
    """`config_scalars.value` is jsonb, so nothing at the database level stops
    `dedupe_rows` becoming the string "false" — which is truthy in Python and would
    silently invert the flag, dropping legitimate duplicate order lines."""
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_scalars", "op": "upsert",
                   "key": {"key": "dedupe_rows"}, "values": {"value": "perhaps"}}],
        "summary": "a boolean that is not one"})
    assert r.status_code == 422
    assert "on or off" in r.json()["detail"]


def test_a_no_op_change_is_refused(editor_client):
    admin = editor_client("recon.admin")
    r = admin.post("/config/proposals", json={
        "edits": [vat(1.08)], "summary": "setting it to what it already is"})
    assert r.status_code == 422


def test_apply_refuses_if_the_contract_moved_underneath(editor_client, repo):
    """No three-way merge of a contract whose evidence is part of it — that
    produces something nobody wrote and everyone later has to defend."""
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)],
        "summary": "based on the contract as it was a moment ago"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})

    # Somebody else changes something in the meantime.
    other = admin.post("/config/proposals", json={
        "edits": [{"table": "config_tolerances", "op": "upsert",
                   "key": {"platform": "tiktok", "name": "pv_sum_vnd"},
                   "values": {"vnd": 12}}],
        "summary": "an unrelated change that lands first"}).json()
    admin.post(f"/config/proposals/{other['id']}/approve", json={})
    admin.post(f"/config/proposals/{other['id']}/apply")

    r = admin.post(f"/config/proposals/{proposal['id']}/apply")
    assert r.status_code == 409 and "has changed since" in r.json()["detail"]


def test_a_stale_proposal_can_be_replayed_rather_than_retyped(editor_client, repo):
    """`config_proposals.edits` records what was ASKED FOR, not only what the
    contract became. That is what makes a rebase possible — it re-runs the stated
    intent and produces a fresh diff for a fresh review, which is not a merge."""
    admin = editor_client("recon.admin")
    stale = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "made against an older contract"}).json()

    other = admin.post("/config/proposals", json={
        "edits": [{"table": "config_tolerances", "op": "upsert",
                   "key": {"platform": "tiktok", "name": "pv_sum_vnd"},
                   "values": {"vnd": 12}}],
        "summary": "lands first"}).json()
    admin.post(f"/config/proposals/{other['id']}/approve", json={})
    admin.post(f"/config/proposals/{other['id']}/apply")

    rebased = admin.post(f"/config/proposals/{stale['id']}/rebase").json()
    assert rebased["rebased_from"] == stale["id"]
    assert rebased["base_sha256"] == repo.content_digest(repo.render_config())
    admin.post(f"/config/proposals/{rebased['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{rebased['id']}/apply").status_code == 200


def test_a_proposal_from_the_old_editor_is_refused_rather_than_misread(
        editor_client, repo):
    """A pre-1.6 proposal records dotted-path operations. Replayed as row edits they
    would fail confusingly, and applied as text they would overwrite the tables with
    a file. `config_proposals.edit_model` is read before either (migration 008)."""
    admin = editor_client("recon.admin")
    legacy = repo.create_proposal(
        base_sha256=repo.content_digest(repo.render_config()),
        content=repo.render_config() + "\n# hand-made\n",
        summary="made by the M6 editor", diff="", proposed_by="old@ada",
        edits=[{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        edit_model="path")
    admin.post(f"/config/proposals/{legacy['id']}/approve", json={})

    applied = admin.post(f"/config/proposals/{legacy['id']}/apply")
    assert applied.status_code == 422 and "earlier editor" in applied.json()["detail"]
    rebased = admin.post(f"/config/proposals/{legacy['id']}/rebase")
    assert rebased.status_code == 422


# ---------------------------------------------------------------------------
# D6 — the two widgets that were promised and were not there
# ---------------------------------------------------------------------------

def test_the_roster_optional_flag_is_a_real_column_now(editor_client, repo):
    """The roster widget's help text described an "optional" flag through M5 and M6
    and the schema had no field for it, so `stores_optional` could not be added to
    at all. It is a boolean on the store's own row (docs/14 D6)."""
    admin = editor_client("recon.admin")
    rendered = config_store.parse(repo.render_config())
    store = rendered["expected_stores"]["tiktok"][0]
    assert store not in (rendered.get("stores_optional", {}).get("tiktok") or [])

    proposal = admin.post("/config/proposals", json={
        "edits": [{"table": "config_stores", "op": "upsert",
                   "key": {"platform": "tiktok", "store": store},
                   "values": {"optional": True}}],
        "summary": "this storefront trades in only some windows"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 200

    after = config_store.parse(repo.render_config())
    assert store in after["stores_optional"]["tiktok"]
    assert store in after["expected_stores"]["tiktok"], (
        "optional means 'warn when absent', not 'drop from the roster'")


def test_settlement_bounds_are_editable_and_not_merely_declared(editor_client, repo):
    """`window_settlement_bounds` was declared editable with a date-picker widget
    and rendered read-only (docs/14 D6). It is a table with two date columns."""
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [{"table": "config_settlement_bounds", "op": "upsert",
                   "key": {"period": "2026-09_w1"},
                   "values": {"from_date": "2026-09-01", "to_date": "2026-09-07"},
                   "evidence": "the export was pulled with a 2026-08-25 start; "
                               "rows before 2026-09-01 are proven present in "
                               "2026-08_w5, checked by order id"}],
        "summary": "deduplicating a mis-pulled export"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 200

    bounds = config_store.parse(repo.render_config())["window_settlement_bounds"]
    assert bounds["2026-09_w1"] == {"from": "2026-09-01", "to": "2026-09-07"}


def test_a_bound_whose_dates_are_the_wrong_way_round_is_refused(editor_client):
    r = editor_client("recon.admin").post("/config/preview", json={
        "edits": [{"table": "config_settlement_bounds", "op": "upsert",
                   "key": {"period": "2026-09_w2"},
                   "values": {"from_date": "2026-09-30", "to_date": "2026-09-01"},
                   "evidence": "a long enough reason to get past the evidence "
                               "check and reach the ordering check"}],
        "summary": "backwards dates"})
    assert r.status_code == 422 and "after the to date" in r.json()["detail"]


# ---------------------------------------------------------------------------
# The approval model — a decision now, not a setting
# ---------------------------------------------------------------------------

def test_the_approval_policy_object_is_gone():
    """`ApprovalPolicy` existed only because open question 13 was unanswered.

    It is answered (closing defect 2.7): user and admin propose, viewer cannot,
    only admin decides. That is expressed where every other authorization rule in
    this service is — as the role on the route, walked by
    `test_auth.py::test_the_required_role_of_every_route_is_declared` — rather than
    as a policy object a deployment can weaken.
    """
    assert not hasattr(config_store, "ApprovalPolicy")
    assert not hasattr(config_store, "ApprovalDenied")


def test_the_old_environment_variable_is_refused_not_ignored(monkeypatch):
    """A deployment that set RECON_CONFIG_APPROVAL was expressing an intent about
    who may approve a rate change. Silently dropping that on upgrade would be the
    worst kind of quiet, so it fails loudly and says what replaced it."""
    from service.config import ConfigError, ServiceSettings

    monkeypatch.setenv("RECON_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("RECON_CONFIG_APPROVAL", "separate")
    with pytest.raises(ConfigError, match="no longer exists"):
        ServiceSettings.from_env()


def test_self_approval_is_recorded_rather_than_forbidden(editor_client, repo):
    """Forbidding it deadlocks a single-admin deployment and pushes the edit back to
    hand-editing settings.yaml, which has no audit trail at all.

    `self_approved` is a GENERATED column, so it cannot be set to a convenient
    value — a reviewer counting self-approvals is reading a fact.
    """
    client = editor_client("recon.admin", "solo@test")
    created = client.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "the 8% concession has ended"}).json()

    approved = client.post(f"/config/proposals/{created['id']}/approve",
                           json={"note": "sole admin"})
    assert approved.status_code == 200
    row = repo.proposal(created["id"])
    assert row["decided_by"] == "solo@test"
    assert row["self_approved"] is True, (
        "the fact that nobody else reviewed this must be recorded — closing "
        "defect 2.7 is only honest with that caveat attached")


def test_a_change_reviewed_by_someone_else_is_not_flagged(editor_client, repo):
    proposer = editor_client("recon.user", "asker@test")
    created = proposer.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "the 8% concession has ended"}).json()

    admin = editor_client("recon.admin", "approver@test")
    assert admin.post(f"/config/proposals/{created['id']}/approve",
                      json={}).status_code == 200
    assert repo.proposal(created["id"])["self_approved"] is False


def test_a_single_admin_can_carry_a_change_all_the_way_through(editor_client, repo):
    """The behaviour that replaced 'separate' mode.

    M5 refused this with a 403, on the grounds that a change should have a second
    reviewer. That is right in principle and wrong in this deployment: there is one
    admin, so the 403 did not produce a second reviewer — it produced a hand-edit of
    settings.yaml with no proposal, no diff and no audit row at all. Permitting it
    and RECORDING it is the strictly better trade.
    """
    admin = editor_client("recon.admin", subject="solo@ada")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "single-maintainer deployment"}).json()
    assert admin.post(f"/config/proposals/{proposal['id']}/approve",
                      json={}).status_code == 200
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 200
    assert repo.proposal(proposal["id"])["self_approved"] is True


def test_a_viewer_still_cannot_propose(editor_client):
    """The capability that survived collapsing the policy object: a read-only
    account can see the rules and every proposal, and change nothing."""
    r = editor_client("recon.viewer").post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "a viewer should not be able to do this"})
    assert r.status_code == 403


def test_only_pending_proposals_can_be_decided(editor_client):
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "decided twice on purpose"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})

    again = admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    assert again.status_code == 409
    assert admin.post(f"/config/proposals/{proposal['id']}/reject",
                      json={}).status_code == 409


def test_a_rejected_proposal_cannot_be_applied(editor_client):
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "this one is going to be rejected"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/reject",
               json={"note": "wrong effective date"})
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 409


def test_the_write_lands_even_when_git_is_unavailable(sandbox_settings):
    """The sandbox is not a git repo. The config still has to be written and the
    database still records who approved it — refusing the change because the
    reviewable *form* of the audit trail failed would be the wrong trade."""
    content = config_store.read_text(sandbox_settings.config_dir) + "\n# marker\n"
    commit = config_store.write_and_commit(sandbox_settings.config_dir, content,
                                           message="test", author="tester")
    assert commit is None
    assert "# marker" in config_store.read_text(sandbox_settings.config_dir)


def test_an_editor_with_no_seeded_tables_says_so_rather_than_editing_a_file(
        repo, store, sandbox_settings, issue_session):
    """A deployment that has never been seeded has nothing to edit.

    It must not silently fall back to editing this process's copy of `config/` —
    that is exactly the shape of A1, and the message names the command that fixes
    it. `build_app` seeds on first boot; this is what a test double sees.
    """
    from fastapi.testclient import TestClient

    from service.api import create_app
    from service.auth import AuthPolicy

    client = TestClient(create_app(repo, store, settings=sandbox_settings,
                                   policy=AuthPolicy(enabled=True)))
    client.headers["Authorization"] = f"Bearer {issue_session('recon.admin')}"
    r = client.post("/config/proposals", json={
        "edits": [vat(1.10)], "summary": "against empty tables"})
    assert r.status_code == 503 and "config_import" in r.json()["detail"]
