"""Does the staging normalizer put each export in the right window?

Staging is where a whole store, or an entire extra settlement block, silently
enters a month — and it was the least defended step in the system: a
hand-written PowerShell script with another developer's absolute paths and a
folder-name -> window table rewritten monthly (`tools/stage_july.ps1`).

The normalizer derives the window from the exports' own settlement dates. These
tests cover the derivation logic without needing client data, plus one
acceptance test (skipped when `input/` is absent) asserting that deriving
reproduces the eight windows a human staged by hand — which is the only real
proof that "derived" and "correct" are the same thing here.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from stage_exports import (  # noqa: E402
    _is_window_member,
    Probe, build_plan, derive_windows, find_duplicates, find_outliers, group_key,
)

SOURCE = Path("/dump/shopee")


def income(folder: str, name: str, lo: str, hi: str, *, platform="shopee", rows=100) -> Probe:
    return Probe(path=SOURCE / folder / name if folder else SOURCE / name,
                 platform=platform, kind="income", store="Store", rows=rows,
                 first=date.fromisoformat(lo), last=date.fromisoformat(hi),
                 sha=f"sha-{name}")


def orders(folder: str, name: str, *, platform="shopee") -> Probe:
    return Probe(path=SOURCE / folder / name if folder else SOURCE / name,
                 platform=platform, kind="orders", store="Store", sha=f"sha-{name}")


def ledger(name: str, lo: str, hi: str) -> Probe:
    return Probe(path=Path("/dump/lazada") / name, platform="lazada", kind="Weekly",
                 store="Store", rows=50, first=date.fromisoformat(lo),
                 last=date.fromisoformat(hi), sha=f"sha-{name}")


# --------------------------------------------------------------------------
# Window derivation
# --------------------------------------------------------------------------

def test_windows_are_numbered_in_settlement_order():
    probes = [income("21_end", "i3.xlsx", "2026-05-21", "2026-05-31"),
              income("1_10", "i1.xlsx", "2026-05-01", "2026-05-10"),
              income("11_20", "i2.xlsx", "2026-05-11", "2026-05-20")]

    labels, _ = derive_windows(probes, SOURCE)

    assert labels == {"1_10": "2026-05_s1", "11_20": "2026-05_s2", "21_end": "2026-05_s3"}


def test_two_stores_of_the_same_window_get_ONE_label():
    """The bug this pins: one store's export sat at the dump root and another's
    in a `1_10/` folder, both settling 01-10. Folder-based numbering made them
    s1 and s2 — two windows where the platform paid out once."""
    probes = [income("", "Income.Masan.xlsx", "2026-05-01", "2026-05-10"),
              income("1_10", "Income.Xmen.xlsx", "2026-05-01", "2026-05-10"),
              income("11_20", "Income.Xmen.xlsx", "2026-05-11", "2026-05-20")]

    labels, _ = derive_windows(probes, SOURCE)

    assert labels["."] == labels["1_10"] == "2026-05_s1"
    assert labels["11_20"] == "2026-05_s2"


def test_partial_overlap_still_merges_into_one_window():
    """Stores rarely settle on identical days — one may have no sales on the
    10th. Overlapping spans are the same payout window."""
    probes = [income("", "a.xlsx", "2026-05-01", "2026-05-10"),
              income("1_10", "b.xlsx", "2026-05-02", "2026-05-09")]

    labels, spans = derive_windows(probes, SOURCE)

    assert len(set(labels.values())) == 1
    assert spans["."] == (date(2026, 5, 1), date(2026, 5, 10))


def test_consecutive_weeks_stay_separate():
    """The counterpart risk: Lazada's four weekly exports are adjacent but must
    NOT merge, or four windows collapse into one."""
    probes = [ledger("2_KAO.xlsx", "2026-05-01", "2026-05-03"),
              ledger("2_KAO (3).xlsx", "2026-05-04", "2026-05-10"),
              ledger("2_KAO (4).xlsx", "2026-05-11", "2026-05-17"),
              ledger("2_KAO (1).xlsx", "2026-05-18", "2026-05-24")]

    labels, _ = derive_windows(probes, Path("/dump/lazada"))

    assert sorted(set(labels.values())) == [
        "2026-05_l1", "2026-05_l2", "2026-05_l3", "2026-05_l4"]


def test_order_exports_inherit_their_groups_window():
    """An order export deliberately spans earlier months — the cross-period
    stitch needs the prior-month re-pull — so it can never define a window."""
    probes = [income("1_10", "i.xlsx", "2026-05-01", "2026-05-10"),
              orders("1_10", "o.xlsx")]

    plan = build_plan([], "shopee", {}, SOURCE)          # unused, keeps signature honest
    labels, _ = derive_windows(probes, SOURCE)

    assert group_key(probes[1], SOURCE) == "1_10"
    assert labels["1_10"] == "2026-05_s1"
    assert plan.assignment == []


def test_nested_kind_folders_group_with_their_window():
    """A real dump nests `11_20 - Xmen/Doanh Thu/` and `11_20 - Xmen/Order New/`.
    Keying on the immediate parent orphaned the order files: their group had no
    income export, so no window could be derived for them."""
    inc = income("11_20 - Xmen/Doanh Thu", "i.xlsx", "2026-05-11", "2026-05-20")
    ordr = orders("11_20 - Xmen/Order New", "o.xlsx")

    assert group_key(inc, SOURCE) == group_key(ordr, SOURCE) == "11_20 - Xmen"


def test_month_label_survives_a_window_lapping_into_the_next_month():
    probes = [income("a", "i1.xlsx", "2026-05-01", "2026-05-10"),
              income("b", "i2.xlsx", "2026-05-11", "2026-05-20"),
              income("c", "i3.xlsx", "2026-05-29", "2026-06-02")]

    labels, _ = derive_windows(probes, SOURCE)

    assert all(v.startswith("2026-05_") for v in labels.values()), labels


# --------------------------------------------------------------------------
# The mis-pull class
# --------------------------------------------------------------------------

def test_an_export_carrying_an_earlier_settlement_block_is_flagged():
    """The July catch, automated: one export also contained the whole previous
    settlement block — 18,352 orders / 5.97B VND of double-invoicing risk —
    found only by ad-hoc analysis afterwards."""
    probes = [income("w2", "clean1.xlsx", "2026-07-08", "2026-07-14"),
              income("w2", "clean2.xlsx", "2026-07-08", "2026-07-14"),
              income("w2", "mispull.xlsx", "2026-07-01", "2026-07-14")]

    notes = find_outliers(probes, SOURCE)

    assert len(notes) == 1
    assert "mispull.xlsx" in notes[0] and "2026-07-01" in notes[0]


def test_a_clean_group_raises_no_mispull_warning():
    probes = [income("w2", "a.xlsx", "2026-07-08", "2026-07-14"),
              income("w2", "b.xlsx", "2026-07-08", "2026-07-14")]

    assert find_outliers(probes, SOURCE) == []


# --------------------------------------------------------------------------
# The double-pull class, by digest
# --------------------------------------------------------------------------

def test_identical_content_headed_for_two_windows_is_refused(tmp_path):
    a = income("1_10", "x.xlsx", "2026-05-01", "2026-05-10")
    b = income("11_20", "y.xlsx", "2026-05-11", "2026-05-20")
    b.sha = a.sha                                     # same bytes, two windows

    problems = find_duplicates([(a, "2026-05_s1"), (b, "2026-05_s2")], tmp_path, "shopee")

    assert len(problems) == 1 and "more than once" in problems[0]


def test_content_already_staged_under_another_window_is_refused(tmp_path):
    staged = tmp_path / "2026-05_s1" / "shopee" / "income"
    staged.mkdir(parents=True)
    (staged / "already.xlsx").write_bytes(b"the same bytes")
    import hashlib
    p = income("11_20", "new-name.xlsx", "2026-05-11", "2026-05-20")
    p.sha = hashlib.sha256(b"the same bytes").hexdigest()

    problems = find_duplicates([(p, "2026-05_s2")], tmp_path, "shopee")

    assert len(problems) == 1 and "double-pull" in problems[0]


def test_restaging_the_same_file_into_the_same_window_is_fine(tmp_path):
    """Re-running staging must be a no-op, not a refusal — otherwise nobody can
    safely re-stage a window a golden was built from."""
    staged = tmp_path / "2026-05_s1" / "shopee" / "income"
    staged.mkdir(parents=True)
    (staged / "same.xlsx").write_bytes(b"bytes")
    import hashlib
    p = income("", "same.xlsx", "2026-05-01", "2026-05-10")
    p.sha = hashlib.sha256(b"bytes").hexdigest()

    assert find_duplicates([(p, "2026-05_s1")], tmp_path, "shopee") == []


# --------------------------------------------------------------------------
# Acceptance: does deriving reproduce what a human staged by hand?
# --------------------------------------------------------------------------

RAW = ROOT / "input" / "original exports"


@pytest.mark.slow
@pytest.mark.parametrize("platform,expected_windows", [
    ("tiktok", {"2026-05_w1"}),
    ("shopee", {"2026-05_s1", "2026-05_s2", "2026-05_s3"}),
    ("lazada", {"2026-05_l1", "2026-05_l2", "2026-05_l3", "2026-05_l4"}),
])
def test_derived_windows_match_the_hand_staged_tree(platform, expected_windows):
    """The eight staged windows were assigned by a human reading settlement
    dates out of each file. If deriving them from the same dates lands anywhere
    else, one of the two is wrong — and every committed golden is keyed to these
    labels.
    """
    source = RAW / platform
    if not source.is_dir():
        pytest.skip("raw exports absent (client data is not in the repo)")
    pytest.importorskip("pandas")
    from src import config

    settings = config.load_settings(ROOT / "config")
    files = [p for p in sorted(source.rglob("*"))
             if p.is_file() and p.suffix.lower() in (".xlsx", ".csv")]
    plan = build_plan(files, platform, settings, source)

    assert set(plan.labels.values()) == expected_windows, (
        f"{platform}: derived {sorted(set(plan.labels.values()))}")
    assert not plan.duplicates, plan.duplicates

    # Every planned file must already sit exactly where the plan would put it.
    for probe_, period in plan.assignment:
        target = ROOT / "input" / period / platform / probe_.subdir / probe_.path.name
        assert target.is_file(), f"{target} is planned but not staged"


# --------------------------------------------------------------------------
# Which failures hold a window back, and which are just skipped
# --------------------------------------------------------------------------

def test_an_unreadable_export_holds_its_window_back():
    p = income("1_10", "Income.Xmen.xlsx", "2026-05-01", "2026-05-10")
    p.problem = "unreadable sheet 'Doanh thu' (PasswordError)"

    assert _is_window_member(p) is True


def test_a_file_that_is_not_an_export_at_all_is_merely_skipped():
    """A team analysis workbook sitting in the dump folder is not a missing
    export. Blocking the window on it made staging refuse s1 outright."""
    p = Probe(path=SOURCE / "Shopee tong hop Thanh.xlsx", platform="shopee",
              kind=None, problem="file name says neither 'order' nor 'income'")

    assert _is_window_member(p) is False


def test_a_self_declared_empty_export_is_skipped_not_blocking():
    """Nine Shopee 'part 2' income exports in July had no revenue sheet and each
    declared total 0 in its own Summary. The team's conclusion was to leave them
    out of staging — and check_stores still fires if a store truly vanishes."""
    p = income("1_10", "Income.Xmen part 2.xlsx", "2026-05-01", "2026-05-10")
    p.kind, p.problem = "income", "no sheet matching /Doanh thu/ — an export with no data sheet"

    assert _is_window_member(p) is False


def test_a_blocked_window_is_held_back_while_the_others_stage():
    from stage_exports import Plan

    good = income("11_20", "ok.xlsx", "2026-05-11", "2026-05-20")
    bad = income("1_10", "broken.xlsx", "2026-05-01", "2026-05-10")
    plan = Plan(assignment=[(good, "2026-05_s2"), (bad, "2026-05_s1")],
                blocked={"2026-05_s1": ["broken.xlsx: unreadable"]})

    assert [period for _, period in plan.stageable] == ["2026-05_s2"]
    assert plan.ok is False
