"""Per-store order-file coverage — defect 2.12's detection half.

`explode_to_sku_*` joins a window's income to the order files staged in *that
window's folder*. An order settled in `w2` may have been created days earlier, so
its SKU lines live in `w1`'s export; the income row matches nothing and the revenue
leaves the invoice through the documented "~21% unmatched" door. July's month-end
comparison found **4,527,401,608 VND** of understatement this way.

**Why per store, and why not a threshold.** The reconciling total has been reported
since M2, but only for the whole window. Measured on the golden windows, the entire
~21% on `2026-05_w1` belongs to ONE storefront (Unilever Homecare at 21.2%; Mars at
0.0%) — a window that reproduces the team's figures and has a committed golden. So
the *level* separates nothing: a store far above its siblings is the legitimate case
there. What these numbers give an operator is an identity and a month-over-month
comparison, which is what `service/exceptions.py` keys on `("store",)` for.

The check with no legitimate traffic — "these lines exist in ANOTHER window's
export" — needs the upload index to see, and lives in `service/`.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

import pandas as pd  # noqa: E402
from src import tieout  # noqa: E402

MONEY = "net_revenue"


def _income(rows):
    return pd.DataFrame(rows, columns=["store", "order_id", MONEY])


def test_a_store_whose_orders_are_all_present_reports_no_shortfall():
    income = _income([("KAO", "A", 100.0), ("KAO", "B", 200.0)])
    present = frozenset({("KAO", "A"), ("KAO", "B")})

    out = tieout.coverage_by_store(income, money_col=MONEY, present_keys=present)

    assert len(out) == 1
    assert out.iloc[0]["unmatched_money"] == 0.0
    assert out.iloc[0]["unmatched_share"] == 0.0
    assert out.iloc[0]["orders"] == 2


def test_the_shortfall_is_attributed_to_the_right_store():
    """The whole point: a total cannot say which storefront lost its lines."""
    income = _income([
        ("KAO", "A", 1_000.0), ("KAO", "B", 1_000.0),      # KAO fully covered
        ("Purite", "C", 3_000.0), ("Purite", "D", 1_000.0),  # Purite lost C
    ])
    present = frozenset({("KAO", "A"), ("KAO", "B"), ("Purite", "D")})

    out = tieout.coverage_by_store(income, money_col=MONEY,
                                   present_keys=present).set_index("store")

    assert out.loc["KAO", "unmatched_money"] == 0.0
    assert out.loc["Purite", "unmatched_money"] == 3_000.0
    assert out.loc["Purite", "unmatched_share"] == 75.0
    assert out.loc["Purite", "unmatched_orders"] == 1


def test_rows_are_ordered_by_money_at_risk():
    """The operator reads the top of this sheet, so the biggest gap must be there."""
    income = _income([
        ("Small", "A", 10.0), ("Big", "B", 5_000.0), ("Mid", "C", 500.0)])

    out = tieout.coverage_by_store(income, money_col=MONEY, present_keys=frozenset())

    assert out["store"].tolist() == ["Big", "Mid", "Small"]


def test_coverage_keys_on_store_and_order_id_not_the_id_alone():
    """The 2.9 lesson applied here from the start.

    `Purite/SHARED` has no lines; `KAO/SHARED` does. Keyed on the bare id, Purite's
    order would count as covered because another storefront carries that id — the
    exact blindness that made the tie-out's coverage check useless.
    """
    income = _income([("KAO", "SHARED", 100.0), ("Purite", "SHARED", 900.0)])
    present = frozenset({("KAO", "SHARED")})

    out = tieout.coverage_by_store(income, money_col=MONEY,
                                   present_keys=present).set_index("store")

    assert out.loc["Purite", "unmatched_money"] == 900.0
    assert out.loc["KAO", "unmatched_money"] == 0.0

    # The discriminator: bare-id membership would have found nothing missing.
    assert not (set(income["order_id"]) - {o for _, o in present}), (
        "fixture no longer exercises the composite key")


def test_an_empty_or_columnless_frame_is_not_an_error():
    """A hard-stopped or empty window must not crash the reporting path."""
    empty = tieout.coverage_by_store(_income([]), money_col=MONEY,
                                     present_keys=frozenset())
    assert list(empty.columns) == ["store", "orders", "unmatched_orders", "money",
                                   "unmatched_money", "unmatched_share"]
    assert len(empty) == 0

    no_money = tieout.coverage_by_store(
        pd.DataFrame({"store": ["KAO"], "order_id": ["A"]}),
        money_col=MONEY, present_keys=frozenset())
    assert len(no_money) == 0


def test_the_coverage_row_reaches_the_tieout_results_as_INFO(settings, log):
    """It must be visible in the run log and must NOT fail the run.

    A legitimate 21% share would otherwise turn every TikTok run red, which is how a
    control becomes noise operators learn to ignore.
    """
    from helpers import make_sku_level

    sku = make_sku_level(n=50, seed=3)
    income = sku[["store", "order_id"]].copy()
    income[MONEY] = sku["amount_with_vat"].values
    reference = tieout.SourceReference.from_income(income, money_col=MONEY)
    coverage = tieout.coverage_by_store(
        income, money_col=MONEY, present_keys=frozenset({("nobody", "nothing")}))

    results = tieout.run_checks_tiktok(sku, reference, settings, log,
                                       money_col=None, coverage=coverage)

    rows = results[results["check"].str.startswith("Order-file coverage per store")]
    assert len(rows) == 1, results["check"].tolist()
    assert rows.iloc[0]["result"] == "INFO"
    assert (results["result"] == "BREACH").sum() == 0, "coverage must not fail a run"
    assert "worst" in rows.iloc[0]["detail"]


def test_the_coverage_row_names_stores_so_the_log_is_actionable(settings, log):
    income = _income([("Purite", "C", 3_000.0), ("KAO", "A", 1_000.0)])
    reference = tieout.SourceReference.from_income(income, money_col=MONEY)
    coverage = tieout.coverage_by_store(income, money_col=MONEY,
                                        present_keys=frozenset({("KAO", "A")}))

    results = tieout.run_checks_tiktok(
        pd.DataFrame(columns=["store", "order_id", "amount_with_vat"]),
        reference, settings, log, money_col=None, coverage=coverage)

    detail = results[results["check"].str.startswith("Order-file coverage")].iloc[0]["detail"]
    assert "Purite" in detail, detail
