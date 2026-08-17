"""The config editor — the one part of M5 that can change what gets invoiced.

`config/settings.yaml` is the domain contract and **its comments are the audit
trail** ([D2](docs/06-DECISIONS.md#d2)). An editor that quietly dropped them
would leave a file that looks the same and can no longer be defended, so the
first test here is a canary on the real file: load it, dump it, and demand the
bytes back unchanged. If that ever fails, every diff this feature produces
becomes unreadable and the feature should be turned off until it is fixed.

The approval model was *configurable* through M5 because
[open question 13](docs/11-OPEN-QUESTIONS.md) — who owns configuration, who signs
off a rate change — had not been answered by a human. **It is answered in M6**
(closing defect 2.7): `recon.user` and `recon.admin` propose, `recon.viewer`
cannot, only `recon.admin` decides, and self-approval is permitted and RECORDED
rather than forbidden. So the three-mode policy object is gone and the rule lives
where every other authorization rule does — on the route.
"""

from __future__ import annotations


import pytest

from service import config_store
from service.config_store import ConfigEditError

REAL_CONFIG = config_store  # for the canary below

pytest.importorskip("httpx")


# `sandbox_settings` and `editor_client` moved to conftest.py in M6, when
# test_admin_cli.py needed them too for the proposal-withdrawal authorship tests.


# ---------------------------------------------------------------------------
# The canary
# ---------------------------------------------------------------------------

def test_round_trip_is_byte_identical_on_the_real_settings_file(service_settings):
    """Load and re-dump the real contract without editing: the bytes must match.

    This is the property the whole feature rests on. `PyYAML` fails it outright —
    it discards every comment — which is why D2 requires `ruamel.yaml` and why
    reaching for the simpler library here is the most damaging shortcut
    available.
    """
    before = config_store.read_text(service_settings.config_dir)
    assert config_store.round_trip(before) == before


def test_an_edit_moves_exactly_one_line(service_settings):
    """A VAT change should be a one-line diff. If unrelated lines move, no
    reviewer can see what actually changed."""
    before = config_store.read_text(service_settings.config_dir)
    after = config_store.apply_edit(before, ["vat_factors", "default"], 1.10)

    diff = config_store.diff(before, after)
    changed = [l for l in diff.splitlines()
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    assert len(changed) == 2, f"expected one -/+ pair, got:\n{diff}"
    assert "1.1" in "".join(changed)


def test_comments_survive_an_edit(service_settings):
    """The evidence attached to each value is the reason this file is worth
    anything. Count them before and after."""
    before = config_store.read_text(service_settings.config_dir)
    after = config_store.apply_edit(before, ["vat_factors", "default"], 1.10)

    count = lambda text: sum(1 for l in text.splitlines() if l.lstrip().startswith("#"))  # noqa: E731
    assert count(after) == count(before) > 50


def test_vietnamese_keys_and_quoting_survive(service_settings):
    """Several column-map keys are Vietnamese header strings whose quoting is
    load-bearing — a bare rewrite can change what a key IS."""
    before = config_store.read_text(service_settings.config_dir)
    after = config_store.apply_edit(before, ["vat_factors", "default"], 1.10)
    for line in before.splitlines():
        if line.strip().startswith('"') and ":" in line:
            assert line in after, f"a quoted key was rewritten: {line.strip()[:60]}"


# ---------------------------------------------------------------------------
# What the editor refuses
# ---------------------------------------------------------------------------

def test_inventing_a_key_is_refused(service_settings):
    """Every key in this file exists because something reads it. A new one
    produces config the pipeline ignores, which then looks like a pipeline bug."""
    before = config_store.read_text(service_settings.config_dir)
    with pytest.raises(ConfigEditError, match="does not invent them"):
        config_store.apply_edit(before, ["vat_factors", "brand_new_key"], 1.0)
    with pytest.raises(ConfigEditError, match="no such config path"):
        config_store.apply_edit(before, ["not_a_section", "x"], 1.0)


def test_editing_the_whole_document_is_refused(service_settings):
    before = config_store.read_text(service_settings.config_dir)
    with pytest.raises(ConfigEditError):
        config_store.apply_edit(before, [], {"anything": 1})


def test_read_value_walks_the_same_paths(service_settings):
    before = config_store.read_text(service_settings.config_dir)
    assert config_store.read_value(before, ["vat_factors", "default"]) == 1.08
    with pytest.raises(ConfigEditError):
        config_store.read_value(before, ["vat_factors", "nope"])


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
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "the 8% concession has ended"}).json()

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
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "the 8% concession has ended"}).json()

    admin = editor_client("recon.admin", "approver@test")
    assert admin.post(f"/config/proposals/{created['id']}/approve",
                      json={}).status_code == 200
    assert repo.proposal(created["id"])["self_approved"] is False


# ---------------------------------------------------------------------------
# The flow, over HTTP
# ---------------------------------------------------------------------------

def test_the_vat_revert_end_to_end(editor_client, repo, sandbox_settings):
    """The scenario the roadmap names: the 8% -> 10% VAT revert is one line."""
    operator = editor_client("recon.user", subject="finance@ada")
    admin = editor_client("recon.admin", subject="antoni@ada")

    proposed = operator.post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "VAT revert 8% -> 10% effective 2026-07-01"}).json()
    assert proposed["state"] == "pending"
    assert "1.1" in proposed["diff"] and "1.08" in proposed["diff"]

    # Nothing has changed on disk yet.
    assert config_store.read_value(
        config_store.read_text(sandbox_settings.config_dir), ["vat_factors", "default"]) == 1.08

    approved = admin.post(f"/config/proposals/{proposed['id']}/approve",
                          json={"note": "confirmed with finance"}).json()
    assert approved["state"] == "approved" and approved["decided_by"] == "antoni@ada"

    applied = admin.post(f"/config/proposals/{proposed['id']}/apply").json()
    assert applied["state"] == "applied"

    # Now it has — and the comments came with it.
    after = config_store.read_text(sandbox_settings.config_dir)
    assert config_store.read_value(after, ["vat_factors", "default"]) == 1.10
    assert sum(1 for l in after.splitlines() if l.lstrip().startswith("#")) > 50

    version = repo.config_version(applied["config_version_id"])
    assert version["source"] == "proposal" and version["created_by"] == "antoni@ada"


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
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "single-maintainer deployment"}).json()
    assert admin.post(f"/config/proposals/{proposal['id']}/approve",
                      json={}).status_code == 200
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 200
    assert repo.proposal(proposal["id"])["self_approved"] is True


def test_a_viewer_still_cannot_propose(editor_client):
    """The capability that survived collapsing the policy object: a read-only
    account can see the rules and every proposal, and change nothing."""
    r = editor_client("recon.viewer").post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "a viewer should not be able to do this"})
    assert r.status_code == 403


def test_apply_refuses_if_the_file_moved_underneath(editor_client, sandbox_settings):
    """No three-way merge of a file whose comments are evidence — that produces
    something nobody wrote and everyone later has to defend."""
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "based on the file as it was a moment ago"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})

    path = sandbox_settings.config_dir / "settings.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")

    r = admin.post(f"/config/proposals/{proposal['id']}/apply")
    assert r.status_code == 409 and "has changed since" in r.json()["detail"]


def test_a_no_op_change_is_refused(editor_client):
    admin = editor_client("recon.admin")
    r = admin.post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.08,
        "summary": "setting it to what it already is"})
    assert r.status_code == 422


def test_only_pending_proposals_can_be_decided(editor_client):
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "decided twice on purpose"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})

    again = admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    assert again.status_code == 409
    assert admin.post(f"/config/proposals/{proposal['id']}/reject",
                      json={}).status_code == 409


def test_a_rejected_proposal_cannot_be_applied(editor_client):
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10,
        "summary": "this one is going to be rejected"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/reject",
               json={"note": "wrong effective date"})
    assert admin.post(f"/config/proposals/{proposal['id']}/apply").status_code == 409


def test_the_config_endpoint_returns_the_file_verbatim(editor_client, sandbox_settings):
    body = editor_client("recon.viewer").get("/config").json()
    assert body["content"] == config_store.read_text(sandbox_settings.config_dir)
    assert "#" in body["content"], "comments are the audit trail and must be served"
    assert len(body["sha256"]) == 64


def test_a_viewer_cannot_propose(editor_client):
    r = editor_client("recon.viewer").post("/config/proposals", json={
        "path": ["vat_factors", "default"], "value": 1.10, "summary": "not allowed here"})
    assert r.status_code == 403


def test_the_write_lands_even_when_git_is_unavailable(sandbox_settings):
    """The sandbox is not a git repo. The config still has to be written and the
    database still records who approved it — refusing the change because the
    reviewable *form* of the audit trail failed would be the wrong trade."""
    content = config_store.read_text(sandbox_settings.config_dir) + "\n# marker\n"
    commit = config_store.write_and_commit(sandbox_settings.config_dir, content,
                                           message="test", author="tester")
    assert commit is None
    assert "# marker" in config_store.read_text(sandbox_settings.config_dir)
