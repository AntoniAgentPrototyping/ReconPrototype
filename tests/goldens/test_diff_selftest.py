"""Does the parity gate actually have teeth?

This runs with NO real data, and it must pass before any golden is trusted.
A differ that silently reports OK is worse than no differ: it would launder a
broken polars port as verified. Every defect class the gate claims to catch is
pinned here, and the tolerance boundary is pinned to the VND rather than
left to whatever the constant happens to be.

Same philosophy as tests/test_tieout_blindness.py — prove the check can fail.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
from openpyxl import Workbook

from cellset import dump_jsonl, load_cellset, load_jsonl, manifest
from diff import DEFAULT_POLICY, DiffKind, TolerancePolicy, compare_workbooks

BASE: list[tuple[str, list[list[object]]]] = [
    ("PV sum", [
        ["store", "amount_pre_vat", "verdict"],
        ["Store A", 1_464_000.0, "OK"],
        ["Store B", 2_035_000.5, "OK"],
    ]),
    ("Xuat HD bt", [
        ["sku", "qty", "amount"],
        ["SKU-1", 3, 244_000.0],
    ]),
]


def build(path: Path, sheets=BASE, *, created: dt.datetime | None = None,
          formats: dict[tuple[str, str], str] | None = None) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    for (sheet, ref), fmt in (formats or {}).items():
        wb[sheet][ref].number_format = fmt
    if created is not None:
        wb.properties.created = created
        wb.properties.modified = created
    wb.save(path)
    return path


def mutate(sheet: str, row: int, col: int, value: object):
    """Copy BASE with one cell replaced. row/col are 0-based into the rows."""
    out = []
    for name, rows in BASE:
        if name != sheet:
            out.append((name, [list(r) for r in rows]))
            continue
        copied = [list(r) for r in rows]
        copied[row][col] = value
        out.append((name, copied))
    return out


@pytest.fixture
def golden(tmp_path: Path) -> Path:
    return build(tmp_path / "golden.xlsx", created=dt.datetime(2026, 1, 1))


# --------------------------------------------------------------------------
# The gate must NOT fire on things that don't matter
# --------------------------------------------------------------------------

def test_byte_different_but_content_identical_compares_equal(tmp_path, golden):
    """The reason we compare content, not hashes: openpyxl stamps timestamps
    into docProps/core.xml, so identical data yields different bytes."""
    other = build(tmp_path / "other.xlsx", created=dt.datetime(2030, 6, 15))

    assert golden.read_bytes() != other.read_bytes(), "fixture should differ in bytes"
    diff = compare_workbooks(golden, other)
    assert diff.ok, diff.report()
    assert diff.compared > 0, "a vacuous comparison is not a pass"


def test_sub_epsilon_float_noise_is_tolerated(tmp_path, golden):
    """Cross-engine reduction order differs; 0.4 VND is below the 0.5 bound."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, 1_464_000.4))
    assert compare_workbooks(golden, cand).ok


# --------------------------------------------------------------------------
# The gate MUST fire on everything below
# --------------------------------------------------------------------------

def test_one_vnd_change_is_caught(tmp_path, golden):
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, 1_464_001.0))
    diff = compare_workbooks(golden, cand)

    assert not diff.ok
    assert DiffKind.VALUE in diff.kinds()
    (cd,) = [c for c in diff.cells if c.kind is DiffKind.VALUE]
    assert cd.ref == "B2" and cd.delta == pytest.approx(1.0)


def test_tolerance_boundary_is_pinned_to_half_a_vnd(tmp_path, golden):
    """0.6 VND must fail. Pins the constant, so loosening it breaks a test
    rather than quietly widening the gate."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, 1_464_000.6))
    assert not compare_workbooks(golden, cand).ok


def test_number_stored_as_text_is_caught(tmp_path, golden):
    """'1464000' where 1464000 belongs — a defect number formats disguise."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, "1464000"))
    diff = compare_workbooks(golden, cand)

    assert DiffKind.TYPE in diff.kinds(), diff.report()


def test_verdict_string_change_is_caught(tmp_path, golden):
    """A control block flipping from OK to a failure verdict is the single
    most important string in the workbook."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 2, "check lai sai roi"))
    diff = compare_workbooks(golden, cand)

    assert DiffKind.VALUE in diff.kinds(), diff.report()


def test_blank_where_a_zero_belongs_is_caught(tmp_path, golden):
    """None and 0.0 are different answers to 'was there revenue here'."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, None))
    diff = compare_workbooks(golden, cand)

    assert DiffKind.NULLNESS in diff.kinds(), diff.report()


def test_number_format_change_is_caught(tmp_path, golden):
    """Accounting vs plain changes how negatives read to finance; no value
    comparison catches it."""
    cand = build(tmp_path / "c.xlsx", formats={("PV sum", "B2"): "#,##0.00"})
    diff = compare_workbooks(golden, cand)

    assert DiffKind.FORMAT in diff.kinds(), diff.report()
    assert compare_workbooks(golden, cand,
                             policy=TolerancePolicy(exact_number_formats=False)).ok


def test_missing_and_extra_sheets_are_caught(tmp_path, golden):
    fewer = build(tmp_path / "fewer.xlsx", BASE[:1])
    assert DiffKind.MISSING_SHEET in compare_workbooks(golden, fewer).kinds()
    assert DiffKind.EXTRA_SHEET in compare_workbooks(fewer, golden).kinds()


def test_sheet_reorder_is_caught(tmp_path, golden):
    """Lazada's per-VAT tabs are read positionally by build_master_summary.py,
    so order is correctness, not cosmetics."""
    cand = build(tmp_path / "c.xlsx", list(reversed(BASE)))
    diff = compare_workbooks(golden, cand)

    assert DiffKind.SHEET_ORDER in diff.kinds(), diff.report()


def test_extra_row_is_caught(tmp_path, golden):
    extended = [(n, [list(r) for r in rows]) for n, rows in BASE]
    extended[0][1].append(["Store C", 999_000.0, "OK"])
    cand = build(tmp_path / "c.xlsx", extended)
    diff = compare_workbooks(golden, cand)

    kinds = diff.kinds()
    assert DiffKind.DIM in kinds and DiffKind.NULLNESS in kinds, diff.report()


# --------------------------------------------------------------------------
# Knife-edge allowlist, golden round-trip, manifest digests
# --------------------------------------------------------------------------

def test_knife_edge_allowlist_reclassifies_but_still_reports(tmp_path, golden):
    """A rounding knife edge is triaged by hand, never absorbed into epsilon —
    so it changes the diff's KIND, not whether the run is clean."""
    cand = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, 1_467_000.0))
    policy = TolerancePolicy(knife_edge=frozenset({"PV sum!B2"}))
    diff = compare_workbooks(golden, cand, policy=policy)

    assert DiffKind.KNIFE_EDGE in diff.kinds()
    assert not diff.ok, "an allowlisted knife edge must still fail the gate"


def test_golden_jsonl_round_trips_bit_exactly(tmp_path):
    """Goldens store floats via float.hex(), so a re-read cannot lose a bit."""
    tricky = [("s", [["v"], [1234567.8912345678], [0.1 + 0.2], [-2_035_000.5]])]
    src = build(tmp_path / "t.xlsx", tricky)
    original = load_cellset(src)

    dump_jsonl(original, tmp_path / "g.jsonl")
    restored = load_jsonl(tmp_path / "g.jsonl")

    assert compare_workbooks(original, restored,
                             policy=TolerancePolicy(abs_vnd=0.0, rel=0.0)).ok
    for s_o, s_r in zip(original.sheets, restored.sheets):
        for key, cell in s_o.cells.items():
            if isinstance(cell.value, float):
                assert cell.value.hex() == s_r.cells[key].value.hex()


def test_a_stored_golden_compares_against_a_live_workbook(tmp_path, golden):
    """The real usage: golden on disk as .jsonl, candidate as live .xlsx."""
    dump_jsonl(load_cellset(golden), tmp_path / "g.jsonl")

    assert compare_workbooks(tmp_path / "g.jsonl", golden).ok
    changed = build(tmp_path / "c.xlsx", mutate("PV sum", 1, 1, 1_464_050.0))
    assert not compare_workbooks(tmp_path / "g.jsonl", changed).ok


def test_manifest_digests_are_stable_and_sensitive(tmp_path, golden):
    """What gets committed: digests only. Identical content must hash equal
    despite differing bytes; a 1-VND change must alter the digest."""
    same = build(tmp_path / "same.xlsx", created=dt.datetime(2031, 2, 2))
    changed = build(tmp_path / "changed.xlsx", mutate("PV sum", 1, 1, 1_464_001.0))

    m_golden = manifest(load_cellset(golden))
    m_same = manifest(load_cellset(same))
    m_changed = manifest(load_cellset(changed))

    assert m_golden == m_same
    assert m_golden["sheets"][0]["digest"] != m_changed["sheets"][0]["digest"]
    # The untouched sheet's digest must NOT move — that is what localizes a
    # divergence to one tab instead of the whole workbook.
    assert m_golden["sheets"][1]["digest"] == m_changed["sheets"][1]["digest"]


def test_manifest_carries_no_cell_values(golden):
    """Committed manifests must be safe for git: hashes and shape only, never
    values or store names (config/settings.yaml:285 — exports carry PII)."""
    blob = repr(manifest(load_cellset(golden)))

    for leaked in ("Store A", "Store B", "1464000", "OK", "SKU-1"):
        assert leaked not in blob, f"manifest leaked {leaked!r}"
