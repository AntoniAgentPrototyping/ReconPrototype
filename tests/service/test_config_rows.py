"""The row editor: what each table permits, what it refuses, and what it renders.

**The predecessor and what survives it.** `test_config_sections.py` tested the
dotted-path editor and six ruamel canaries proving that editing YAML text in place
preserved its comments. That editor is gone (M8/1.6): the contract is edited as rows
and the file is rendered from them, so the canaries no longer guard the write path.
One of them is still load-bearing and lives in `test_config_editor.py` — a round
trip of the real contract must be byte-identical, because `service/sampledata.py`
round-trips it and because an unstable render would mint a config version per call.

Everything here runs against the **real** `config/settings.yaml`, imported into the
tables. A fixture with a simplified file would prove nothing about the 400-line
contract with Vietnamese header keys, 200 comment lines, and patterns that have
changed three times in four months — which is exactly the contract the editor has to
not damage.
"""

from __future__ import annotations

import pytest

from service import config_rows, config_store
from service.config_rows import RowEdit, RowEditError

pytest.importorskip("httpx")


@pytest.fixture
def conn(repo, seeded_config):
    """A connection with the real contract loaded into the config tables."""
    with repo._conn() as connection:                            # noqa: SLF001
        yield connection


@pytest.fixture
def content(service_settings) -> str:
    return config_store.read_text(service_settings.config_dir)


@pytest.fixture
def parsed(content: str):
    return config_store.parse(content)


def edit(**kwargs) -> RowEdit:
    return RowEdit.parse(kwargs)


# ---------------------------------------------------------------------------
# Evidence — the objection this design has to answer
# ---------------------------------------------------------------------------

def test_evidence_is_the_comment_block_a_human_would_attribute(content):
    """The whole answer to "a form would strip the evidence for every value".

    Still read from the file's TEXT rather than ruamel's `.ca` — measured, that
    attaches a block to the key *preceding* the one it documents, so `.ca` would
    caption nearly every field with the previous field's justification (D42). The
    importer lifts these into `evidence` columns.
    """
    block = config_store.evidence_for(content, ["vat_factors"])
    assert any("TEMPORARY tax concession" in line for line in block)


def test_a_key_with_no_comment_of_its_own_inherits_its_parents(content):
    """What a reader does: `vat_factors.default` is documented by the block above
    `vat_factors`, not by a comment of its own."""
    assert config_store.evidence_for(content, ["vat_factors", "default"]) \
        == config_store.evidence_for(content, ["vat_factors"])


def test_evidence_for_an_unknown_path_is_empty_not_an_error(content):
    assert config_store.evidence_for(content, ["nope", "not_here"]) == []


def test_every_row_carries_its_own_evidence_not_its_containers(conn):
    """**The difference a column makes.** Through M6 the editor read comment blocks
    out of the file, and a file can only caption a top-level key — so the roster's
    justification appeared against all 42 storefronts in it, and one alias's proof
    could not be shown at all. Each row now answers for itself.
    """
    aliases = config_rows.read_rows(conn, "config_store_aliases")
    assert aliases, "the real contract has aliases"
    distinct = {row["evidence"] for row in aliases}
    assert len(distinct) > 1, (
        "every alias carries the same evidence, which means it is still the "
        "container's comment block rather than the entry's own justification")


def test_the_evidence_of_a_removed_row_goes_with_it(conn):
    """`OrphanedEvidence` and `comment_disposition` are gone, and this is why.

    Removing a commented list item left its block captioning the item below it —
    measured, and the worst available outcome: evidence that looks authoritative and
    is attached to the wrong thing. A row deletes its own evidence.
    """
    before = config_rows.read_rows(conn, "config_store_aliases")
    target = next(r for r in before if r["evidence"])
    config_rows.apply(conn, [edit(
        table="config_store_aliases", op="delete",
        key={"platform": target["platform"], "raw": target["raw"]})], who="test")

    after = config_rows.read_rows(conn, "config_store_aliases")
    assert len(after) == len(before) - 1
    assert (target["platform"], target["raw"]) not in {
        (r["platform"], r["raw"]) for r in after}
    assert target["evidence"] not in [r["evidence"] for r in after], (
        "the justification for the removed alias is still attached to a row it "
        "does not describe — the exact failure OrphanedEvidence had to model")
    assert not hasattr(config_rows, "OrphanedEvidence")


# ---------------------------------------------------------------------------
# The tables, described
# ---------------------------------------------------------------------------

def test_every_editable_setting_names_the_module_that_reads_it(conn):
    """A setting whose reader cannot be named is a setting nobody should edit.

    Module only, no line number. The old schema carried "src/ingest.py:290
    check_stores" on every field and the numbers had rotted — `check_stores` was at
    309, `read_excel_sheet` at 131 not 148 — while being rendered to a finance user
    on every control (docs/14 B5). A module name is provenance; a line number is a
    maintenance liability nothing tested.
    """
    for table in ("config_scalars", "config_tolerances"):
        for row in config_rows.read_rows(conn, table):
            reader = row["reader"]
            assert reader, f"{table} {row} names no reader"
            assert ":" not in reader, (
                f"{reader!r} carries a line number, which rots silently and is "
                f"rendered to a finance user")


def test_no_structured_value_renders_as_a_bare_text_box(conn):
    """A bare text input is the wrong affordance for two thirds of this contract,
    and for a non-technical user it is worse than what it replaced. Normalising is
    what makes that true by construction: a roster is rows, not a text area."""
    for table in config_rows.payload(conn):
        for column in table["columns"] + table["key"]:
            if column["kind"] == config_rows.Kind.JSON:
                assert table["table"] == "config_scalars", (
                    f"{table['table']}.{column['name']} is unstructured; only the "
                    f"single-settings table may hold a bare json value")


def test_the_pii_control_is_locked_and_says_why(conn):
    """A privacy incident should not be two clicks. Its diff reads as an ordinary
    boolean flip rather than as "customer names now enter the pipeline".

    Locked is a COLUMN, not a hidden control: the API refuses the write. A field a
    form omits is indistinguishable from a field nobody thought about.
    """
    row = next(r for r in config_rows.read_rows(conn, "config_scalars")
               if r["key"] == "drop_unmapped_columns")
    assert row["locked"] is True
    assert "PII" in row["locked_reason"]

    with pytest.raises(RowEditError, match="not editable"):
        config_rows.apply(conn, [edit(
            table="config_scalars", op="upsert",
            key={"key": "drop_unmapped_columns"}, values={"value": False})],
            who="test")


def test_keys_that_nothing_reads_are_gone_from_the_contract(parsed, conn):
    """The successor to `test_dead_keys_are_shown_as_dead_rather_than_hidden`.

    Deleting a dead key is the stronger form of "so it cannot be forgotten", and
    this is what stops them coming back: in the normalized config a key that exists
    is a key something reads (docs/14 A12, D11).

    Removed 2026-08-18: `vat_rate`, `periods`, `tolerances.split_rounding_vnd`,
    `tolerances.exact_check_vnd`, `tolerances.shopee.pv_sum_vnd`. Note that
    `tolerances.TIKTOK.pv_sum_vnd` is genuinely read (finance_template.py:188) and
    must survive — the two are one keystroke apart.
    """
    assert "vat_rate" not in parsed
    assert "periods" not in parsed
    tolerances = parsed["tolerances"]
    assert "split_rounding_vnd" not in tolerances
    assert "exact_check_vnd" not in tolerances
    assert "pv_sum_vnd" not in tolerances["shopee"]
    assert tolerances["tiktok"]["pv_sum_vnd"] == 12000

    # And the controls went with them: a row is the only way to offer one.
    offered = {(r["platform"], r["name"])
               for r in config_rows.read_rows(conn, "config_tolerances")}
    assert ("shopee", "pv_sum_vnd") not in offered
    assert ("tiktok", "pv_sum_vnd") in offered
    keys = {r["key"] for r in config_rows.read_rows(conn, "config_scalars")}
    assert "vat_rate" not in keys and "periods.rolling_window_months" not in keys


def test_the_canonical_field_list_is_derived_not_hardcoded(conn, parsed):
    """The biggest usability win: the right-hand side of a column map becomes a
    dropdown, so mapping a drifted header stops requiring anyone to know the
    pipeline's internal vocabulary. Deliberately NOT a table — a table would be a
    second definition of what the pipeline understands, free to drift from the code
    that consumes it."""
    fields = config_rows.canonical_fields(conn)
    assert "order_id" in fields and "gross_revenue" in fields
    # Set by the pipeline itself, never by a map.
    assert "store" not in fields and "source_file" not in fields
    for platform_maps in parsed["column_maps"].values():
        for kind_map in platform_maps.values():
            for target in kind_map.values():
                assert str(target) in fields, f"{target} is not offerable"


def test_a_wire_name_is_never_part_of_the_rendered_payload(conn):
    """A table name, a column name and a dotted path exist only in the wire format.

    What a user reads is a `title`, a `blurb`, a `label` or a `help`, and none of
    them may leak the machine name of what is being changed. The old form of this
    rule exempted `reader`; it does not any more, because `reader` is rendered on
    every control and was where the rotted line numbers were showing (docs/14 B5).
    """
    for table in config_rows.payload(conn):
        human = [table["title"], table["blurb"], table["closed_reason"]]
        for column in table["key"] + table["columns"]:
            human += [column["label"], column["help"]]
            human += [option["label"] for option in column["options"]]
        for text in human:
            assert table["table"] not in text, (
                f"{table['table']} appears in text shown to a user: {text[:80]}")


def test_a_closed_table_says_why_rather_than_merely_refusing(conn):
    """The honesty rule `config_edits._check_may_add` used to state as
    `open_container`. A table that refuses new rows and cannot say why is a table
    nobody can argue with."""
    for name, spec in config_rows.TABLES.items():
        if not (spec.may_insert and spec.may_delete):
            assert len(spec.closed_reason) > 40, f"{name} is closed and does not say why"


# ---------------------------------------------------------------------------
# invalidates_goldens — the input to the verification run
# ---------------------------------------------------------------------------

def test_the_rows_that_can_move_a_cell_are_marked(conn):
    """Read off the row, never inferred from a path (migration 008)."""
    cases = [
        edit(table="config_column_maps", op="upsert",
             key={"platform": "tiktok", "kind": "orders",
                  "raw_header": "Order ID"}, values={"canonical": "order_id"}),
        edit(table="config_platforms", op="upsert", key={"platform": "tiktok"},
             values={"dayfirst": True}),
        edit(table="config_reading", op="upsert",
             key={"platform": "shopee", "kind": "income"}, values={"header_row": 3}),
        edit(table="config_scalars", op="upsert",
             key={"key": "vat_factors.default"}, values={"value": 1.10}),
        edit(table="config_scalars", op="upsert", key={"key": "dedupe_rows"},
             values={"value": True}),
        edit(table="config_scalars", op="upsert", key={"key": "number_style"},
             values={"value": "vietnamese"}),
    ]
    for one in cases:
        assert config_rows.invalidating(conn, [one]), (
            f"{one.describe()} can move a workbook cell and is not marked")


def test_a_harmless_change_is_not_marked(conn):
    """Saying so is the outcome `oracle_rev` could never report, because it could
    not tell "unchanged" from "unknown".

    A tolerance is read by `src/tieout.py`, which reports variances and writes no
    workbook cell. A roster addition decides whether a run STOPS, not what a cell
    holds — so it cannot move a golden that was generated without it.
    """
    harmless = [
        edit(table="config_tolerances", op="upsert",
             key={"platform": "tiktok", "name": "pv_sum_vnd"}, values={"vnd": 12000}),
        edit(table="config_stores", op="upsert",
             key={"platform": "tiktok", "store": "Something New"}, values={},
             evidence="onboarded in September"),
        edit(table="config_scalars", op="upsert", key={"key": "masters_file"},
             values={"value": "Lib & VAT rate.xlsb"}),
    ]
    assert config_rows.invalidating(conn, harmless) == []


def test_an_unknown_row_counts_as_invalidating(conn):
    """The whole lesson of `oracle_rev` (D26): a change no claim can be made about
    is exactly where defaulting to "harmless" turns a gate into a silent skip. A row
    that does not exist yet takes its table's declared default, and every table not
    argued down to false defaults to true."""
    unknown = edit(table="config_column_maps", op="upsert",
                   key={"platform": "tiktok", "kind": "orders",
                        "raw_header": "A Header Nobody Has Seen"},
                   values={"canonical": "order_id"},
                   evidence="appeared in the September export")
    assert config_rows.invalidating(conn, [unknown]) == ["Export column names"]


def test_the_two_tables_argued_down_to_false_are_the_only_ones(conn):
    """Tightening is free; loosening is not. Migration 008 argues both exceptions,
    and a third appearing without an argument is what this catches."""
    false_by_default = {name for name, spec in config_rows.TABLES.items()
                        if not spec.invalidates_goldens}
    assert false_by_default == {"config_stores", "config_tolerances"}


# ---------------------------------------------------------------------------
# Parsing and refusals
# ---------------------------------------------------------------------------

def test_an_unknown_operation_is_refused():
    with pytest.raises(RowEditError, match="unknown edit operation"):
        RowEdit.parse({"table": "config_stores", "op": "delete_everything",
                       "key": {"platform": "tiktok", "store": "x"}})


def test_an_unknown_table_is_refused():
    with pytest.raises(RowEditError, match="unknown config table"):
        RowEdit.parse({"table": "users", "op": "delete", "key": {"id": 1}})


def test_an_edit_with_no_key_is_refused():
    with pytest.raises(RowEditError, match="needs the key"):
        RowEdit.parse({"table": "config_stores", "op": "upsert", "key": {}})


def test_an_empty_or_absurd_proposal_is_refused():
    with pytest.raises(RowEditError, match="non-empty"):
        config_rows.parse_all([])
    with pytest.raises(RowEditError, match="not reviewable"):
        config_rows.parse_all([{"table": "config_stores", "op": "delete",
                                "key": {"platform": "tiktok", "store": "x"}}] * 201)


def test_a_column_the_form_does_not_own_is_refused(conn):
    """`changed_by`, `source` and `sort_order` come from the act of editing, never
    from the body — the same rule as `requested_by` and `uploaded_by`."""
    with pytest.raises(RowEditError, match="not something this form changes"):
        config_rows.apply(conn, [edit(
            table="config_stores", op="upsert",
            key={"platform": "tiktok", "store": "Somewhere"},
            values={"changed_by": "somebody-else"},
            evidence="trying to forge an author")], who="test")


def test_removing_a_row_that_is_not_there_is_refused(conn):
    with pytest.raises(RowEditError, match="there is no"):
        config_rows.apply(conn, [edit(
            table="config_stores", op="delete",
            key={"platform": "tiktok", "store": "Never Existed"})], who="test")


def test_an_edit_that_changes_nothing_is_refused(conn):
    row = config_rows.read_rows(conn, "config_stores")[0]
    with pytest.raises(RowEditError, match="nothing to change"):
        config_rows.apply(conn, [edit(
            table="config_stores", op="upsert",
            key={"platform": row["platform"], "store": row["store"]},
            values={})], who="test")


def test_a_reading_rule_cannot_name_a_sheet_and_a_pattern_at_once(conn):
    """`read_parts` prefers the pattern and would silently ignore the exact name —
    the 'configured and inert' state this table exists to make impossible."""
    with pytest.raises(RowEditError, match="not both"):
        config_rows.apply(conn, [edit(
            table="config_reading", op="upsert",
            key={"platform": "shopee", "kind": "income"},
            values={"sheet_name": "Doanh thu", "sheet_pattern": "^Doanh thu"})],
            who="test")


def test_a_retired_header_spelling_cannot_still_be_in_use(conn):
    row = config_rows.read_rows(conn, "config_column_maps")[0]
    with pytest.raises(RowEditError, match="still in use"):
        config_rows.apply(conn, [edit(
            table="config_column_maps", op="upsert",
            key={"platform": row["platform"], "kind": row["kind"],
                 "raw_header": row["raw_header"]},
            values={"active": True, "retired_at": "2026-06-01"})], who="test")


def test_an_alias_to_itself_is_refused(conn):
    """A no-op that reads as a decision."""
    store = config_rows.read_rows(conn, "config_stores")[0]["store"]
    with pytest.raises(RowEditError, match="no-op"):
        config_rows.apply(conn, [edit(
            table="config_store_aliases", op="upsert",
            key={"platform": "tiktok", "raw": store},
            values={"canonical": store},
            evidence="pointing a name at itself")], who="test")


# ---------------------------------------------------------------------------
# Rendering after an edit
# ---------------------------------------------------------------------------

def test_a_retired_column_spelling_stays_a_row_and_leaves_the_contract(conn):
    """June 2026 renamed "Order\\adjustment ID" to "Order/Adjustment ID" and both
    still resolve, because the pipeline may be pointed at an older export at any
    time. Deleting the old spelling would break a re-run of May; keeping it with no
    marker loses the fact that it is historical.
    """
    row = config_rows.read_rows(conn, "config_column_maps")[0]
    key = {"platform": row["platform"], "kind": row["kind"],
           "raw_header": row["raw_header"]}

    config_rows.apply(conn, [edit(
        table="config_column_maps", op="upsert", key=key,
        values={"active": False, "retired_at": "2026-06-30"})], who="test")

    from service import config_render
    rendered = config_store.parse(config_render.render(conn))
    maps = rendered["column_maps"][row["platform"]][row["kind"]]
    assert row["raw_header"] not in maps, "a retired spelling is not in the contract"
    kept = config_rows.read_rows(conn, "config_column_maps")
    assert any(r["raw_header"] == row["raw_header"] and r["active"] is False
               and r["retired_at"] is not None
               for r in kept), "and the row survives, with the date it retired"


def test_render_after_changes_nothing(conn):
    """The preview applies and rolls back — a simulation would be a second
    implementation of the write, free to disagree with the diff an operator
    approved."""
    from service import config_render

    before = config_render.render(conn)
    after = config_rows.render_after(conn, [edit(
        table="config_scalars", op="upsert", key={"key": "vat_factors.default"},
        values={"value": 1.10})], who="test")
    assert "1.1" in after and after != before
    assert config_render.render(conn) == before


def test_a_new_row_lands_at_the_end_and_rendering_stays_stable(conn):
    """`sort_order` is appended, never renumbered. Rendering has to be byte-stable
    for a given row set or `config_versions` mints a version per render and pin
    de-duplication stops working."""
    from service import config_render

    config_rows.apply(conn, [edit(
        table="config_stores", op="upsert",
        key={"platform": "tiktok", "store": "Zed Test"}, values={},
        evidence="onboarded 2026-08, appears from w3")], who="test")

    once = config_render.render(conn)
    assert config_render.render(conn) == once, "rendering is not deterministic"
    roster = config_store.parse(once)["expected_stores"]["tiktok"]
    assert roster[-1] == "Zed Test"
