"""Can the tie-out checks detect lost or altered revenue?

**Since M2: yes.** These were `xfail(strict)` from M0 through M1 — the original
checks computed both sides of every comparison from the same frame, so each
reduced to an identity and all six revenue-loss mutations below passed
undetected, including zeroing 100% of revenue.

The markers came off only after the rebuilt checks made them XPASS, in a
separate commit from the fix (docs/06-DECISIONS.md#d22). They are now ordinary
regression tests and must stay green.

What makes them able to fail is `reference_for()`: the reference is captured
from the CLEAN frame before mutation, exactly as production captures it from
the income export before the money math runs on the order export. A check whose
inputs all come from the frame under test can only restate that frame.
"""

from __future__ import annotations

import pytest

# Vestigial: this guarded collection under a second, pandas-free runtime that
# existed for a since-cancelled engine port (see docs/06-DECISIONS.md#d25).
# There is one venv now and pandas is a hard runtime dependency, so this can be
# dropped in M1 — kept for the moment only to avoid churn in a green suite.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

from helpers import breaches, make_shopee_frames, make_sku_level  # noqa: E402
from src import tieout  # noqa: E402
from src.tieout import SourceReference  # noqa: E402

MONEY = "subtotal_after_seller_discounts"


def reference_for(clean):
    """Capture the reference from the CLEAN frame, before any mutation.

    This mirrors production, where the reference is built from the income
    export while the frame under test is rebuilt from the order export. Taking
    it *after* the mutation would recreate the original defect exactly — both
    sides derived from the same data, and a check that cannot fail.
    """
    income = clean.groupby("order_id", as_index=False).agg(
        store=("store", "first"), **{MONEY: (MONEY, "first")})
    return SourceReference.from_income(income, money_col=MONEY)

CORRUPTIONS = [
    pytest.param(lambda d: d.sample(frac=0.70, random_state=1), "dropped 30% of SKU rows", id="drop-30pct"),
    pytest.param(lambda d: d.sample(frac=0.50, random_state=1), "dropped 50% of SKU rows", id="drop-50pct"),
    pytest.param(lambda d: d.sample(frac=0.10, random_state=1), "dropped 90% of SKU rows", id="drop-90pct"),
    pytest.param(lambda d: d[d["store"] != "KAO"], "dropped an entire store", id="drop-store"),
    pytest.param(lambda d: _scale(d, 0.5), "halved every revenue amount", id="halve-revenue"),
    pytest.param(lambda d: _scale(d, 0.0), "zeroed all revenue", id="zero-revenue"),
]


def _scale(df, factor: float):
    """Scale revenue while preserving the pre_vat -> with_vat relation, so the
    mutation is pure data loss rather than an internal inconsistency."""
    out = df.copy()
    out["amount_pre_vat"] = out["amount_pre_vat"] * factor
    out["amount_with_vat"] = out["amount_pre_vat"] * out["vat_factor"]
    return out


# --------------------------------------------------------------------------
# Controls — these pass today and prove the harness itself works.
# --------------------------------------------------------------------------

def test_clean_data_passes_all_checks(sku_level, settings, log):
    results = tieout.run_checks_tiktok(sku_level, reference_for(sku_level), settings, log)
    assert len(results) >= 4
    assert breaches(results).empty


def test_broken_with_vat_relation_is_detected(sku_level, settings, log):
    """A distorted with-VAT column no longer reconciles to the settlement the
    income file reported for the same order."""
    reference = reference_for(sku_level)
    corrupted = sku_level.copy()
    corrupted["amount_with_vat"] = corrupted["amount_pre_vat"] * 1.10

    breached = breaches(tieout.run_checks_tiktok(corrupted, reference, settings, log))

    assert not breached.empty
    assert any("conservation" in k for k in breached["kind"])


def test_reconciling_items_are_reported_but_are_not_breaches(sku_level, settings, log):
    """Settlement with no matching order lines is real and expected — 21% of a
    live TikTok window. It must be visible and must NOT fail the run, or the
    control becomes noise that operators learn to ignore."""
    results = tieout.run_checks_tiktok(
        sku_level, reference_for(sku_level), settings, log,
        unmatched_money=3_453_805_299.0, unmatched_orders=11_765)

    info = results[results["result"] == "INFO"]
    assert len(info) == 1
    assert info.iloc[0]["actual"] == 3_453_805_299.0
    assert breaches(results).empty, "a reconciling item must not fail the run"


# --------------------------------------------------------------------------
# The gate — every one of these is real revenue loss that goes undetected.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("corrupt,description", CORRUPTIONS)
def test_revenue_loss_is_detected(sku_level, settings, log, corrupt, description):
    baseline = float(sku_level["amount_pre_vat"].sum())
    corrupted = corrupt(sku_level)
    removed = baseline - float(corrupted["amount_pre_vat"].sum())
    assert removed > 0, "fixture error: the mutation removed no revenue"

    results = tieout.run_checks_tiktok(corrupted, reference_for(sku_level), settings, log)

    assert not breaches(results).empty, (
        f"{description}: {removed:,.0f} VND of revenue disappeared and all "
        f"{len(results)} checks still reported PASS"
    )


def test_checks_compare_against_an_independent_source(settings, log):
    """The structural version of the gap above.

    A tie-out has to cross a boundary. If every input to a check is derived
    from the same frame, the check can only ever restate that frame. Here two
    entirely different datasets are checked; at least one must be able to
    fail, or no check has an external reference at all.
    """
    small = make_sku_level(n=100, seed=1)
    large = make_sku_level(n=5000, seed=2)

    # Each frame is checked against the OTHER's reference. If a check reads
    # only the frame it is handed, both still pass.
    small_breaches = breaches(tieout.run_checks_tiktok(small, reference_for(large), settings, log))
    large_breaches = breaches(tieout.run_checks_tiktok(large, reference_for(small), settings, log))

    assert not (small_breaches.empty and large_breaches.empty), (
        "two unrelated datasets both passed every check — the checks do not "
        "reference anything outside the frame they are handed"
    )


# --------------------------------------------------------------------------
# Shopee's money crossing — added 2026-08-13, the platform that had none.
#
# Shopee ran coverage checks only, because applying TikTok's relation breached
# on correct data and no tolerance was going to be honest (D1). What closed it
# is the team's OWN June "Net revenue" formula, read out of their consolidated
# file. That makes these tests the whole warrant for the new check: a control
# derived from a formula is worth exactly as much as the attempt to fool it.
# --------------------------------------------------------------------------

SHOPEE_LOSS = [
    pytest.param(lambda d: d.sample(frac=0.70, random_state=1), "dropped 30% of SKU rows",
                 id="shopee-drop-30pct"),
    pytest.param(lambda d: d.sample(frac=0.10, random_state=1), "dropped 90% of SKU rows",
                 id="shopee-drop-90pct"),
    pytest.param(lambda d: d[d["store"] != "Masan"], "dropped an entire store",
                 id="shopee-drop-store"),
    pytest.param(lambda d: d.assign(amount_with_vat=d["amount_with_vat"] * 0.5),
                 "halved every with-VAT amount", id="shopee-halve-revenue"),
    pytest.param(lambda d: d.assign(amount_with_vat=0.0), "zeroed all revenue",
                 id="shopee-zero-revenue"),
    pytest.param(lambda d: d.assign(discount_allocated=d["discount_allocated"] * 3),
                 "inflated the discount allocation", id="shopee-inflate-discount"),
    # The one a per-order check would MISS: one SKU line removed from orders
    # that still have siblings. This is why the crossing is row-additive rather
    # than reading the pre-computed order_gross_sale column.
    pytest.param(lambda d: d.drop(d[d.duplicated(["store", "order_id"], keep="first")].index[:50]),
                 "removed one SKU line from 50 multi-line orders",
                 id="shopee-drop-sibling-lines"),
]


def _shopee_checks(sku, income, settings, log):
    crossing = tieout.revenue_crossing_shopee(income, log)
    assert crossing is not None, "the fixture should support building a crossing"
    reference, unmatched_money, unmatched_orders = tieout.partition(
        income, money_col="net_revenue", present_order_ids=sku["order_id"])
    return tieout.run_checks_shopee(
        sku, reference, settings, log, money_col=None, crossing=crossing,
        unmatched_money=unmatched_money, unmatched_orders=unmatched_orders)


def test_shopee_clean_data_passes_the_crossing(settings, log):
    income, sku = make_shopee_frames()

    results = _shopee_checks(sku, income, settings, log)

    conservation = results[results["kind"] == "conservation"]
    assert len(conservation) == 2, "the crossing must contribute both its rows"
    assert breaches(results).empty, breaches(results)[["check", "variance"]].to_dict("records")


def test_shopee_no_longer_reports_an_unverified_money_crossing(settings, log):
    """The INFO row that stood in for the missing control must be gone once the
    control exists — otherwise the run log tells an operator two things."""
    income, sku = make_shopee_frames()

    results = _shopee_checks(sku, income, settings, log)

    assert not any("NOT verified" in c for c in results["check"])


@pytest.mark.parametrize("corrupt,description", SHOPEE_LOSS)
def test_shopee_revenue_loss_is_detected(settings, log, corrupt, description):
    income, sku = make_shopee_frames()
    baseline = float((sku["amount_with_vat"] - sku["discount_allocated"]).sum())
    corrupted = corrupt(sku)
    moved = baseline - float((corrupted["amount_with_vat"] - corrupted["discount_allocated"]).sum())
    assert abs(moved) > 0, "fixture error: the mutation moved no revenue"

    results = _shopee_checks(corrupted, income, settings, log)

    assert not breaches(results).empty, (
        f"{description}: {moved:,.0f} VND moved and all {len(results)} checks "
        f"still reported PASS"
    )


def test_shopee_refund_orders_are_held_out_and_named(settings, log):
    """Refund orders cannot tie: the order export keeps the full ordered
    quantity while income is reduced for returned units. Every non-tying order
    in all four measured windows was one. They must be reported, not tolerated
    and not silently dropped."""
    income, sku = make_shopee_frames(refund_frac=0.2)

    results = _shopee_checks(sku, income, settings, log)

    held = [r for _, r in results.iterrows()
            if r["result"] == "INFO" and "refund" in r["check"]]
    assert len(held) == 1, "the held-out refund class must be reported once"
    assert held[0]["actual"] != 0
    assert breaches(results).empty, "holding them out must not itself breach"


def test_shopee_crossing_refuses_to_run_without_the_subsidy_column(settings, log):
    """A reference missing `shopee_product_subsidy` would deviate on exactly the
    orders that carry one. Better to build no check than a check that fires on
    correct data — that is how the original tie-outs became worthless."""
    income, _ = make_shopee_frames()

    crossing = tieout.revenue_crossing_shopee(
        income.drop(columns="shopee_product_subsidy"), log)

    assert crossing is None
    assert any("shopee_product_subsidy" in w for w in log.warnings)
