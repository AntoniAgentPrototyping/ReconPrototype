"""The sectioned editor: its schema, its evidence extraction, and its five ops.

Everything here runs against the **real** `config/settings.yaml`. A fixture with a
simplified file would prove nothing about the 400-line contract with Vietnamese
header keys, 200 comment lines, and patterns that have changed three times in four
months — which is exactly the file the editor has to not damage.

The six ruamel canaries from the pre-implementation probe live here as permanent
regression tests. They are the reason the design is safe, so they are the first
thing to run when it stops being.
"""

from __future__ import annotations

import pytest

from service import config_edits, config_schema, config_store
from service.config_store import ConfigEditError

pytest.importorskip("httpx")


@pytest.fixture
def content(service_settings) -> str:
    return config_store.read_text(service_settings.config_dir)


@pytest.fixture
def parsed(content: str):
    return config_store.parse(content)


# ---------------------------------------------------------------------------
# The six ruamel canaries
# ---------------------------------------------------------------------------

def _comment_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.lstrip().startswith("#"))


def test_canary_1_load_dump_is_byte_identical(content):
    """If this fails, every diff the editor produces becomes unreadable."""
    assert config_store.round_trip(content) == content
    assert _comment_lines(content) > 150


def test_canary_2_appending_to_a_list_leaves_everything_else_alone(content, parsed):
    """`expected_stores.tiktok` has comments interleaved between its items. Adding
    a store must not move them."""
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="append_list_item", path=("expected_stores", "tiktok"), value="Zed Test")])
    assert _comment_lines(after) == _comment_lines(content)

    before_lines = content.splitlines()
    after_lines = after.splitlines()
    # Everything before the edited block is byte-identical.
    start = next(i for i, line in enumerate(before_lines)
                 if line.startswith("expected_stores:"))
    assert before_lines[:start] == after_lines[:start]
    assert '"Zed Test"' in after or "Zed Test" in after


def test_canary_3_an_appended_item_carries_its_why_as_a_comment(content):
    """This is the mechanism that keeps an alias citing its own justification.
    Without it the editor would produce entries that are correct and undefendable.

    Measured: the note lands as an **EOL** comment on the new item's own line, not as
    a block above it. That is the placement we want — it binds the justification to
    the entry, and `test_removing_an_item_takes_its_eol_note_with_it` shows the
    binding actually holds. So the count of lines *starting* with `#` is unchanged.
    """
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="append_list_item", path=("expected_stores", "tiktok"), value="Zed Test",
        comment="onboarded 2026-08, confirmed by finance")])
    note_line = next(l for l in after.splitlines() if "Zed Test" in l)
    assert note_line.strip() == "- Zed Test # onboarded 2026-08, confirmed by finance"
    assert _comment_lines(after) == _comment_lines(content)
    assert after.count("#") == content.count("#") + 1


def test_canary_4_a_new_mapping_key_and_its_note_survive(content):
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="set_map_entry", path=("store_aliases", "tiktok"), key="zed test",
        value="Zed Test", comment="same storefront, 0 order-id overlap")])
    assert "same storefront, 0 order-id overlap" in after
    assert config_store.read_value(after, ["store_aliases", "tiktok"])["zed test"] \
        == "Zed Test"


def test_canary_5_removing_an_item_under_a_comment_block_must_be_answered(content):
    """**The M6 plan was wrong about this, and the correction is the test.**

    The plan asserted that removing a commented item takes its comment with it
    ("200 -> 199, which is the desired semantics"). Measured against the real file,
    that holds only for an EOL comment. For a comment **block** above the item,
    ruamel leaves the block exactly where it was — so removing `"Merries"` from
    `stores_optional.tiktok` left its two-line July-w5 justification captioning
    `"Veet & Reckitt Personal Care"`, a store it does not describe.

    Evidence silently re-attached to the wrong entry is the worst outcome available
    to this module: it looks authoritative and is false. So the removal is refused
    until somebody says which it was.
    """
    stores = config_store.read_value(content, ["stores_optional", "tiktok"])
    assert "Merries" in stores, "the fixture assumption changed"

    with pytest.raises(config_edits.OrphanedEvidence) as caught:
        config_edits.apply_edits(content, [config_edits.Edit(
            op="remove_list_item", path=("stores_optional", "tiktok"),
            value="Merries")])
    assert any("July w5" in line for line in caught.value.block)

    # "it described only this entry" — the block goes too.
    removed = config_edits.apply_edits(content, [config_edits.Edit(
        op="remove_list_item", path=("stores_optional", "tiktok"), value="Merries",
        comment_disposition="remove")])
    assert "Merries" not in config_store.read_value(
        removed, ["stores_optional", "tiktok"])
    assert "July w5" not in removed
    assert _comment_lines(removed) == _comment_lines(content) - 2

    # "it describes the group" — the block stays, deliberately and on the record.
    kept = config_edits.apply_edits(content, [config_edits.Edit(
        op="remove_list_item", path=("stores_optional", "tiktok"), value="Merries",
        comment_disposition="keep")])
    assert "July w5" in kept
    assert _comment_lines(kept) == _comment_lines(content)


def test_removing_an_item_takes_its_eol_note_with_it(content):
    """The other half of the measurement: an EOL note IS bound to its item, which is
    why `comment=` writes notes there rather than as a block."""
    with_note = config_edits.apply_edits(content, [config_edits.Edit(
        op="append_list_item", path=("expected_stores", "tiktok"), value="Zed Test",
        comment="onboarded 2026-08")])
    removed = config_edits.apply_edits(with_note, [config_edits.Edit(
        op="remove_list_item", path=("expected_stores", "tiktok"), value="Zed Test")])
    assert "Zed Test" not in removed
    assert "onboarded 2026-08" not in removed


def test_removing_an_item_with_no_comment_above_it_needs_no_answer(content):
    """The question is only asked where it is real — otherwise every roster removal
    would prompt for nothing."""
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="remove_list_item", path=("expected_stores", "tiktok"), value="AHC")])
    assert "AHC" not in config_store.read_value(after, ["expected_stores", "tiktok"])


def test_an_appended_item_is_not_left_under_the_next_keys_comment(content):
    """**A second thing the plan did not anticipate**, found by the test above.

    `expected_stores.tiktok` ends with a two-line block that introduces the NEXT key
    (`# Shopee: 17 stores per the May data...`). ruamel appends after that block, so
    a new TikTok store rendered underneath a comment announcing Shopee's roster —
    visually a Shopee store. The item is reflowed above the block instead.
    """
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="append_list_item", path=("expected_stores", "tiktok"), value="Zed Test")])
    lines = after.splitlines()
    at = next(i for i, l in enumerate(lines) if l.strip().startswith("- ")
              and "Zed Test" in l)

    assert not lines[at - 1].strip().startswith("#"), (
        f"the new item sits under a comment that does not describe it:\n"
        f"{lines[at - 1]}\n{lines[at]}")
    # And it is still inside tiktok's list, not shopee's.
    assert "Zed Test" in config_store.read_value(after, ["expected_stores", "tiktok"])
    assert "Zed Test" not in config_store.read_value(after, ["expected_stores", "shopee"])
    # The introducing comment still introduces expected_stores.shopee — located from
    # `expected_stores:` onward, because `shopee:` appears under several other keys.
    section_at = next(i for i, l in enumerate(lines) if l.startswith("expected_stores:"))
    shopee_at = next(i for i, l in enumerate(lines[section_at:], section_at)
                     if l.strip() == "shopee:")
    assert "Shopee: 17 stores" in lines[shopee_at - 2]
    assert at < shopee_at, "the new tiktok store must stay above shopee's list"


def test_canary_6_setting_a_scalar_changes_exactly_one_line(content):
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="set", path=("vat_factors", "default"), value=1.10)])
    diff = config_store.diff(content, after)
    changed = [l for l in diff.splitlines()
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    assert len(changed) == 2, f"expected one -/+ pair, got:\n{diff}"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def test_evidence_is_the_comment_block_a_human_would_attribute(content):
    """The whole answer to "a form would strip the evidence for every value".

    Read from the TEXT, not from ruamel's `.ca` — measured, that attaches a block to
    the key PRECEDING the one it documents, so `.ca` would caption almost every field
    with the previous field's justification.
    """
    vat = config_store.evidence_for(content, ["vat_factors", "default"])
    assert any("TEMPORARY tax concession" in line for line in vat), vat
    assert any("1.10" in line for line in vat)

    pattern = config_store.evidence_for(content, ["store_from_filename", "shopee"])
    assert any("Xmenforboss" in line for line in pattern), (
        "the August sub-window evidence is what explains the trailing-range rule")

    bounds = config_store.evidence_for(content, ["window_settlement_bounds"])
    assert any("DEDUPLICATION OF A PULL ARTIFACT" in line for line in bounds)


def test_a_key_with_no_comment_of_its_own_inherits_its_parents(content):
    """What a reader does: `vat_factors.default` is documented by the block above
    `vat_factors`, not by nothing."""
    parent = config_store.evidence_for(content, ["vat_factors"])
    child = config_store.evidence_for(content, ["vat_factors", "default"])
    assert child == parent and child


def test_an_inline_comment_is_included_but_a_hash_inside_a_key_is_not(content):
    """`"Phone #": phone` is a real column-map key, so splitting on the first `#`
    would turn half a key into a caption."""
    tolerance = config_store.evidence_for(content, ["tolerances", "tiktok", "pv_sum_vnd"])
    assert any("per-store totals" in line for line in tolerance), tolerance

    assert config_store._inline_comment('  "Phone #": phone') == ""
    assert config_store._inline_comment("  key: 1   # a note") == "a note"


def test_evidence_for_an_unknown_path_is_empty_not_an_error(content):
    assert config_store.evidence_for(content, ["nope", "not_here"]) == []


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

def test_every_field_names_the_module_that_reads_it(parsed):
    """A setting whose reader cannot be named is a setting nobody should edit."""
    for field in config_schema.all_fields(parsed):
        assert field.reader, f"{field.dotted} names no reader"


def test_no_field_renders_as_a_raw_text_box_for_structured_data(parsed):
    """The requirement said "a text input". A bare text input is the wrong
    affordance for two thirds of this file, and for a non-technical user it is worse
    than what it replaced — so a container gets a purpose-built widget."""
    for field in config_schema.all_fields(parsed):
        value = config_schema._value_at(parsed, field.path)
        if isinstance(value, (dict, list)) and field.editable:
            assert field.widget not in (config_schema.Widget.TEXT,
                                        config_schema.Widget.NUMBER), (
                f"{field.dotted} holds a {type(value).__name__} and would render as "
                f"a plain input")


def test_the_pii_control_is_locked_and_says_why(parsed):
    """A privacy incident should not be two clicks. Its diff reads as an ordinary
    boolean flip rather than as "customer names now enter the pipeline"."""
    field = config_schema.field_for(parsed, ["drop_unmapped_columns"])
    assert field is not None
    assert field.widget == config_schema.Widget.LOCKED
    assert not field.editable
    assert "PII" in field.locked_reason


def test_dead_keys_are_shown_as_dead_rather_than_hidden(parsed):
    """A control on a key nothing reads invites an edit that appears to work and
    changes no behaviour. Hiding it instead makes it a forgotten key rather than a
    known one."""
    for path in (["vat_rate"], ["periods", "rolling_window_months"]):
        field = config_schema.field_for(parsed, path)
        assert field is not None and field.widget == config_schema.Widget.DEAD
        assert field.reader == "nothing"


def test_locked_and_dead_fields_cannot_be_edited_through_the_ops(content, parsed):
    for path in (("drop_unmapped_columns",), ("vat_rate",)):
        with pytest.raises(ConfigEditError, match="not editable"):
            config_edits.apply_edits(content, [config_edits.Edit(
                op="set", path=path, value=False)])


def test_the_canonical_field_list_is_derived_not_hardcoded(parsed):
    """The biggest usability win: the right-hand side of a column map becomes a
    dropdown, so mapping a drifted header stops requiring anyone to know the
    pipeline's internal vocabulary."""
    fields = config_schema.canonical_fields(parsed)
    assert "order_id" in fields and "gross_revenue" in fields
    # Set by the pipeline itself, never by a map.
    assert "store" not in fields and "source_file" not in fields
    # Every name a real column map already targets must be offerable, or the
    # dropdown could not represent the current file.
    for platform_maps in parsed["column_maps"].values():
        for kind_map in platform_maps.values():
            for target in kind_map.values():
                assert str(target) in fields, f"{target} is not offerable"


def test_a_dotted_path_is_never_part_of_the_rendered_payload(parsed, content):
    """The dotted path exists only in the wire format. A section's `title`, `blurb`,
    field `label` and `help` are what a user reads, and none of them may leak it."""
    for section in config_schema.payload(parsed, content):
        for field in section["fields"]:
            if field["widget"] == config_schema.Widget.DEAD:
                # A dead key is labelled with its own name ON PURPOSE: the point is
                # to say "this specific key is read by nothing", which needs naming.
                continue
            for human in (field["label"], field["help"], field["locked_reason"]):
                assert field["dotted"] not in human, (
                    f"{field['dotted']} appears in text shown to a user")


# ---------------------------------------------------------------------------
# invalidates_goldens — the input to the verification run
# ---------------------------------------------------------------------------

def test_the_fields_that_can_move_a_cell_are_marked(parsed):
    for path in (["column_maps", "tiktok", "orders"], ["store_from_filename", "tiktok"],
                 ["header_rows", "shopee", "income"], ["vat_factors", "default"],
                 ["dedupe_rows"], ["number_style"]):
        assert config_schema.invalidates_goldens(parsed, [path]) == [".".join(path)], (
            f"{path} can move a workbook cell and is not marked")


def test_a_harmless_change_is_not_marked(parsed):
    """Most changes — a tolerance, an alias, a roster addition — move nothing, and
    saying so is the outcome `oracle_rev` could never report."""
    for path in (["tolerances", "tiktok", "pv_sum_vnd"], ["store_aliases", "tiktok"],
                 ["expected_stores", "tiktok"], ["masters_file"]):
        assert config_schema.invalidates_goldens(parsed, [path]) == []


def test_an_unknown_path_counts_as_invalidating(parsed):
    """The whole lesson of `oracle_rev` (D26): a path nothing describes is exactly
    the case where NO claim can be made, and defaulting to "harmless" there is how a
    gate degrades into a silent skip."""
    assert config_schema.invalidates_goldens(parsed, [["something", "new"]]) \
        == ["something.new"]


# ---------------------------------------------------------------------------
# The honesty rule on new keys
# ---------------------------------------------------------------------------

def test_a_closed_mapping_refuses_a_new_key(content):
    """Stricter than the old rule, not looser. `apply_edit` refused a new key
    because "the key must already exist" — a proxy. The real property is that
    `src/masters.py:144` reads exactly `.get("default")`, so any other key in
    `vat_factors` is config the pipeline silently ignores."""
    with pytest.raises(ConfigEditError, match="closed mapping"):
        config_edits.apply_edits(content, [config_edits.Edit(
            op="set_map_entry", path=("vat_factors",), key="tiktok", value=1.10)])


def test_an_open_container_accepts_a_new_key(content):
    """And the declaration that makes it open NAMES the reader that loops over it."""
    field = config_schema.field_for(config_store.parse(content),
                                    ["store_aliases", "tiktok"])
    assert field is not None and field.allows_new_keys()
    assert "read_parts" in field.reader

    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="set_map_entry", path=("store_aliases", "tiktok"), key="new spelling",
        value="AHC")])
    assert config_store.read_value(after, ["store_aliases", "tiktok"])["new spelling"] \
        == "AHC"


def test_an_absent_open_container_is_created(content):
    """Measured need: `expected_stores.lazada` and `store_aliases.lazada` are ABSENT
    from the real file, so without this a form offering "add a Lazada store" would
    render, accept input, and fail on submit with "no such config path"."""
    assert config_store.read_value(content, ["expected_stores"]).get("lazada") is None
    after = config_edits.apply_edits(content, [config_edits.Edit(
        op="append_list_item", path=("expected_stores", "lazada"), value="KAO")])
    assert config_store.read_value(after, ["expected_stores", "lazada"]) == ["KAO"]


def test_an_undescribed_container_is_refused(content):
    with pytest.raises(ConfigEditError, match="not described in the config schema"):
        config_edits.apply_edits(content, [config_edits.Edit(
            op="set_map_entry", path=("store_to_brand",), key="x", value="y")])


# ---------------------------------------------------------------------------
# Multi-edit
# ---------------------------------------------------------------------------

def test_several_edits_land_as_one_change(content):
    """The reason this exists: with one edit per proposal, adding a store and its
    alias was two proposals, two approvals and two commits — so people would do it
    in one hand-edit instead and the audit trail would record nothing."""
    after = config_edits.apply_edits(content, [
        config_edits.Edit(op="append_list_item", path=("expected_stores", "tiktok"),
                          value="Zed Test"),
        config_edits.Edit(op="set_map_entry", path=("store_aliases", "tiktok"),
                          key="zed", value="Zed Test"),
        config_edits.Edit(op="set", path=("vat_factors", "default"), value=1.10),
    ])
    assert "Zed Test" in config_store.read_value(after, ["expected_stores", "tiktok"])
    assert config_store.read_value(after, ["store_aliases", "tiktok"])["zed"] == "Zed Test"
    assert config_store.read_value(after, ["vat_factors", "default"]) == 1.10
    assert _comment_lines(after) == _comment_lines(content)


def test_edits_are_applied_in_the_order_given(content):
    """An operator who removes a store and adds its replacement expects that order;
    reordering could turn a valid pair into a duplicate refusal."""
    after = config_edits.apply_edits(content, [
        config_edits.Edit(op="remove_list_item", path=("expected_stores", "tiktok"),
                          value="AHC"),
        config_edits.Edit(op="append_list_item", path=("expected_stores", "tiktok"),
                          value="AHC"),
    ])
    stores = config_store.read_value(after, ["expected_stores", "tiktok"])
    assert stores.count("AHC") == 1 and stores[-1] == "AHC"


def test_an_empty_or_absurd_proposal_is_refused(content):
    with pytest.raises(ConfigEditError, match="changes nothing"):
        config_edits.apply_edits(content, [])
    many = [config_edits.Edit(op="set", path=("vat_factors", "default"), value=1.10)] * 201
    with pytest.raises(ConfigEditError, match="not reviewable"):
        config_edits.apply_edits(content, many)


def test_a_duplicate_list_item_is_refused(content):
    with pytest.raises(ConfigEditError, match="already in"):
        config_edits.apply_edits(content, [config_edits.Edit(
            op="append_list_item", path=("expected_stores", "tiktok"), value="AHC")])


def test_removing_something_that_is_not_there_is_refused(content):
    with pytest.raises(ConfigEditError, match="is not in"):
        config_edits.apply_edits(content, [config_edits.Edit(
            op="remove_list_item", path=("expected_stores", "tiktok"), value="Nope")])
    with pytest.raises(ConfigEditError, match="no entry"):
        config_edits.apply_edits(content, [config_edits.Edit(
            op="remove_map_entry", path=("store_aliases", "tiktok"), key="nope")])


def test_an_unknown_operation_is_refused():
    with pytest.raises(ConfigEditError, match="unknown edit operation"):
        config_edits.Edit.parse({"op": "delete_everything", "path": ["x"]})


def test_an_empty_path_is_refused():
    with pytest.raises(ConfigEditError, match="whole document"):
        config_edits.Edit.parse({"op": "set", "path": []})


def test_apply_edit_still_works_for_the_single_edit_canaries(content):
    """The M5 shim stays so the twelve existing canary tests keep testing the same
    thing through the same shape."""
    assert config_store.apply_edit(content, ["vat_factors", "default"], 1.10) \
        == config_edits.apply_edits(content, [config_edits.Edit(
            op="set", path=("vat_factors", "default"), value=1.10)])


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------

def test_the_schema_endpoint_carries_evidence_for_every_field(editor_client):
    body = editor_client("recon.viewer").get("/config/schema").json()
    assert body["sections"], "no sections"
    vat = next(f for s in body["sections"] for f in s["fields"]
               if f["dotted"] == "vat_factors.default")
    assert any("TEMPORARY tax concession" in line for line in vat["evidence"])
    assert vat["widget"] == "number" and vat["invalidates_goldens"] is True
    assert "order_id" in body["canonical_fields"]


def test_preview_shows_the_diff_without_committing(editor_client, sandbox_settings):
    client = editor_client("recon.user")
    body = client.post("/config/preview", json={
        "edits": [{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        "summary": "checking what this would do"}).json()
    assert body["changed"] is True
    assert "1.1" in body["diff"]
    assert body["invalidates_goldens"] == ["vat_factors.default"]
    # Nothing moved on disk.
    assert config_store.read_value(
        config_store.read_text(sandbox_settings.config_dir),
        ["vat_factors", "default"]) == 1.08


def test_a_multi_edit_proposal_records_its_operations(editor_client, repo):
    client = editor_client("recon.user")
    created = client.post("/config/proposals", json={
        "edits": [
            {"op": "append_list_item", "path": ["expected_stores", "tiktok"],
             "value": "Zed Test", "comment": "onboarded 2026-08"},
            {"op": "set_map_entry", "path": ["store_aliases", "tiktok"],
             "key": "zed", "value": "Zed Test"},
        ],
        "summary": "onboard the Zed Test storefront and its export spelling"}).json()

    row = repo.proposal(created["id"])
    assert len(row["edits"]) == 2
    assert row["edits"][0]["op"] == "append_list_item"
    assert "onboarded 2026-08" in row["content"]


def test_a_stale_proposal_can_be_replayed_rather_than_retyped(editor_client,
                                                              sandbox_settings, repo):
    """Not a merge. D38 refuses a three-way merge of a file whose comments are
    evidence; this re-runs the stated INTENT and produces a fresh diff."""
    user = editor_client("recon.user", subject="asker@ada")
    stale = user.post("/config/proposals", json={
        "edits": [{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        "summary": "VAT revert, proposed before the file moved"}).json()

    # Somebody else changes the file underneath it.
    path = sandbox_settings.config_dir / "settings.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# a later hand edit\n",
                    encoding="utf-8")

    admin = editor_client("recon.admin")
    admin.post(f"/config/proposals/{stale['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{stale['id']}/apply").status_code == 409

    rebased = user.post(f"/config/proposals/{stale['id']}/rebase")
    assert rebased.status_code == 201
    fresh = rebased.json()
    assert fresh["state"] == "pending"
    assert fresh["rebased_from"] == stale["id"]
    assert "1.1" in fresh["diff"]
    # The base is the CURRENT file, so applying it now works.
    admin.post(f"/config/proposals/{fresh['id']}/approve", json={})
    assert admin.post(f"/config/proposals/{fresh['id']}/apply").status_code == 200


def test_rebasing_someone_elses_proposal_needs_admin(editor_client, sandbox_settings):
    owner = editor_client("recon.user", subject="owner@ada")
    created = owner.post("/config/proposals", json={
        "edits": [{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        "summary": "mine, not yours"}).json()
    path = sandbox_settings.config_dir / "settings.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# moved\n", encoding="utf-8")

    other = editor_client("recon.user", subject="someone-else@ada")
    assert other.post(f"/config/proposals/{created['id']}/rebase").status_code == 403
    admin = editor_client("recon.admin", subject="boss@ada")
    assert admin.post(f"/config/proposals/{created['id']}/rebase").status_code == 201


def test_a_pre_m6_proposal_cannot_be_replayed_and_says_why(editor_client, repo,
                                                           sandbox_settings):
    """It recorded only the resulting file, not the intent. Guessing at what it
    meant would be inventing an audit trail."""
    before = config_store.read_text(sandbox_settings.config_dir)
    legacy = repo.create_proposal(
        base_sha256=repo.content_digest(before),
        content=config_store.apply_edit(before, ["vat_factors", "default"], 1.10),
        summary="written the M5 way", diff="(none)", proposed_by="admin@test")
    r = editor_client("recon.admin", subject="admin@test").post(
        f"/config/proposals/{legacy['id']}/rebase")
    assert r.status_code == 422
    assert "created before M6" in r.json()["detail"]
