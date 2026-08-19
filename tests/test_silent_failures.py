"""Paths where wrong numbers are produced with no signal at all.

Each is safe-ish today because a human reads the output against a spreadsheet.
Under the web app that human is gone, so every one becomes a silently wrong
finance file.

**All four are closed as of 2026-08-13** (1.4 silent VAT default, 1.5 order_id
fan-out ×2, 1.6 silent coercion), which empties the strict-xfail list for the
first time since M0. Each was done as a pair — fix, XPASS as the evidence,
then the marker ([D22](../docs/06-DECISIONS.md#d22)) — and each was measured
against the golden gate first: 1.4 and 1.5 moved nothing at all, 1.6 moved
`fingerprint_digest` only, exactly as predicted in advance.

The tests stay. They are what stops the behaviour drifting back, and their
docstrings keep the shape of the original failure, which is the part worth
not re-deriving.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

import pandas as pd  # noqa: E402
from src import calculate, ingest, masters, stitch, tieout  # noqa: E402


# --------------------------------------------------------------------------
# Money parsing — ingest.to_number (src/ingest.py:73-81)
# --------------------------------------------------------------------------

def test_to_number_parses_both_configured_styles():
    standard = ingest.to_number(pd.Series(["1,234,567.89", "0", "-500"]), "standard")
    vietnamese = ingest.to_number(pd.Series(["1.234.567,89", "0", "-500"]), "vietnamese")

    assert standard.tolist() == [1234567.89, 0.0, -500.0]
    assert vietnamese.tolist() == [1234567.89, 0.0, -500.0]


def test_unparseable_money_is_distinguishable_from_absent():
    """`errors="coerce"` mapped both a blank cell and genuine garbage to NaN.
    Downstream `.fillna(0)` then turned both into zero revenue. A settlement
    export never legitimately contains an unparseable amount, so the two must
    not be the same value.

    Closed 2026-08-13: blank is 0.0, unparseable stays NaN and is counted.
    """
    absent = ingest.to_number(pd.Series([""]), "standard")
    garbage = ingest.to_number(pd.Series(["1.234,56 VND"]), "standard")

    assert not (absent.isna().all() and garbage.isna().all()), (
        "a blank cell and an unparseable amount are indistinguishable; both "
        "become 0 VND of revenue downstream with no warning"
    )
    assert absent.tolist() == [0.0]
    assert garbage.isna().all()


def test_accounting_dash_is_zero_not_garbage():
    """Excel's accounting format writes zero as a dash, and a real Shopee
    income file does it in 46,972 of 83,134 rows of a column that feeds the
    discount allocation. Treating it as unparseable would hard-stop every
    Shopee run; treating it as NaN was the old silent path."""
    parsed = ingest.to_number(pd.Series(["-", "–", "—", "1,000"]), "standard")

    assert parsed.tolist() == [0.0, 0.0, 0.0, 1000.0]


def test_unparseable_amount_stops_the_run(log):
    """The count has to do something. Default posture is a hard stop with the
    column named ([D3](../docs/06-DECISIONS.md#d3))."""
    from src.errors import ReconHardStop

    with pytest.raises(ReconHardStop) as exc:
        ingest.report_unparseable({"net_revenue": 3}, "shopee/income", "standard", {}, log)

    assert "net_revenue" in str(exc.value)
    assert "3" in str(exc.value)


def test_unparseable_amount_can_be_downgraded_to_a_warning(log):
    """An operator who has looked can continue — deliberately, in config."""
    ingest.report_unparseable({"net_revenue": 3}, "shopee/income", "standard",
                              {"numeric_coercion": "warn"}, log)

    assert any("net_revenue" in w for w in log.warnings)


# --------------------------------------------------------------------------
# VAT resolution — masters.vat_factor_for (src/masters.py:112-117)
# --------------------------------------------------------------------------

def test_vat_exceptions_override_the_default(settings, log):
    """Control: the team's rule is unchanged — master exception wins, and a SKU
    the master does not list still gets the default. What moved is that the
    fall-through is now returned as a mask instead of being indistinguishable
    from a confirmed rate."""
    factors, unmapped = masters.resolve_vat_factors(
        pd.Series(["SKU-STD", "SKU-LOW"]), settings, {"SKU-LOW": 1.05}, log
    )
    assert factors.tolist() == [1.08, 1.05]
    assert unmapped.tolist() == [True, False]


def test_vat_coverage_is_reported(settings, log):
    """Control: a fall-through that nobody can see is the defect. On real data
    coverage is 0% for all three platforms, so the count has to reach the log."""
    masters.resolve_vat_factors(pd.Series(["A", "B"]), settings, {}, log)

    assert any("VAT master coverage" in line for line in log.lines)
    assert any("NO SKU" in line for line in log.lines), (
        "zero master coverage must warn, not just count"
    )


def test_unmapped_sku_does_not_silently_get_default_vat():
    """`.fillna(default)` could not distinguish "this SKU is standard-rated"
    from "this SKU is absent from the master". The second is a wrong-tax
    path, and it is the one branch the row-level verification never
    exercised (the 1.05 SKUs did not trade in May).

    Closed 2026-08-13: `vat_factor_for` now returns NaN for a SKU the master
    does not list, and `resolve_vat_factors` applies the team's default
    fall-through where it can be counted. The invoiced numbers did not move —
    only the silence did."""
    settings = {"vat_factors": {"default": 1.08}}
    known = {"SKU-KNOWN": 1.05}

    factors = masters.vat_factor_for(pd.Series(["SKU-NEVER-SEEN"]), settings, known)

    assert not (factors == 1.08).all(), (
        "a SKU absent from the VAT master was assigned the 1.08 default with "
        "no warning — indistinguishable from a genuinely standard-rated SKU"
    )


# --------------------------------------------------------------------------
# Order-ID collisions across stores
# --------------------------------------------------------------------------

def _two_stores_sharing_an_order_id():
    income = pd.DataFrame(
        {
            "store": ["Store A", "Store B"],
            "order_id": ["SHARED-001", "SHARED-001"],
            "subtotal_after_seller_discounts": [100_000.0, 250_000.0],
        }
    )
    orders = pd.DataFrame(
        {
            "store": ["Store A", "Store B"],
            "order_id": ["SHARED-001", "SHARED-001"],
            "sku_id": ["SKU-A", "SKU-B"],
            "sku_name": ["product A", "product B"],
            "unit_price_gross": [100_000.0, 250_000.0],
            "quantity": [1, 1],
            "sku_seller_discount": [0.0, 0.0],
            "order_created_at": pd.to_datetime(["2026-05-01", "2026-06-01"]),
        }
    )
    return income, orders


def test_shared_order_id_does_not_fan_out_across_stores(log):
    """Per-store input folders hid this. Multi-tenant API pulls remove that
    accident, and the merge became many-to-many: each store's income joined
    every other store's SKU lines, inflating revenue.

    Closed 2026-08-13 — both explodes key on (store, order_id)."""
    income, orders = _two_stores_sharing_an_order_id()

    sku_level = calculate.explode_to_sku_tiktok(income, orders, log)

    pairs = set(zip(sku_level["store"], sku_level["sku_id"]))
    assert pairs == {("Store A", "SKU-A"), ("Store B", "SKU-B")}, (
        f"expected each store to keep only its own SKU lines, got {sorted(pairs)}"
    )


def test_shared_order_id_does_not_borrow_another_stores_date(log):
    """`orders.groupby("order_id")[...].min()` collapsed stores together, so one
    store's income could be attributed to another store's order-creation date —
    which decides the period the revenue lands in.

    Closed 2026-08-13 — stitch groups and merges on (store, order_id)."""
    income, orders = _two_stores_sharing_an_order_id()

    matched, _ = stitch.stitch(income, orders, log)

    store_b = matched[matched["store"] == "Store B"].iloc[0]
    assert store_b["order_created_at"] == pd.Timestamp("2026-06-01"), (
        f"Store B's income was dated {store_b['order_created_at'].date()}, "
        f"which is Store A's order date"
    )


# The same collision, one layer further on: the JOINS key on (store, order_id),
# but the TIE-OUT's coverage reference does not. Carried from M2.5 as a named
# residual and registered as defect 2.9. Pinned 2026-08-19.
#
# Why it matters more on Shopee than the wording suggests: `SHOPEE_MONEY` is
# None, so the per-order and per-store conservation checks never run there.
# Order coverage is Shopee's ONLY order-population control, and this is the
# defect in it.

def _tiktok_checks(sku, income, settings, log):
    """The production call sequence, so the pin exercises what runs."""
    reference, unmatched_money, unmatched_orders = tieout.partition(
        income, money_col="subtotal_after_seller_discounts",
        present_keys=tieout.pairs(sku))
    return tieout.run_checks_tiktok(
        sku, reference, settings, log,
        money_col="subtotal_after_seller_discounts",
        unmatched_money=unmatched_money, unmatched_orders=unmatched_orders)


def _collision_masking_a_missing_order():
    """Store A settles two orders; only one of them has SKU lines.

    The missing one shares its id with an order Store B *does* have lines for.
    Store A survives store coverage (its other order is present), so nothing
    backstops the order-coverage check — which is the point of the fixture.
    """
    income = pd.DataFrame({
        "store": ["Store A", "Store A", "Store B"],
        "order_id": ["A-ONLY-1", "SHARED-001", "SHARED-001"],
        "subtotal_after_seller_discounts": [100_000.0, 250_000.0, 400_000.0],
    })
    orders = pd.DataFrame({                     # NOTE: no Store A / SHARED-001 row
        "store": ["Store A", "Store B"],
        "order_id": ["A-ONLY-1", "SHARED-001"],
        "sku_id": ["SKU-A", "SKU-B"],
        "sku_name": ["product A", "product B"],
        "unit_price_gross": [100_000.0, 400_000.0],
        "quantity": [1, 1],
        "sku_seller_discount": [0.0, 0.0],
        "order_created_at": pd.to_datetime(["2026-05-01", "2026-05-02"]),
    })
    return income, orders


def test_order_coverage_notices_a_settled_order_with_no_lines_of_its_own(settings, log):
    """`Store A / SHARED-001` is in the reference and reaches no SKU line.

    Check 1 guards the span *after* the reference is captured — rows lost between
    capture and the checks — so the reference here is built from the full income
    frame rather than through `partition`, which by construction only ever
    references orders it already matched. (That `partition` builds its reference
    from the matched subset, so the ~21% door cannot alarm, is a *different*
    defect: 2.12.)

    Keyed on `order_id` alone this passed: Store B's row carries the same
    `SHARED-001`, so the id counted as present. The discriminator below asserts
    that directly, which is stronger than an xfail marker — it keeps the proof
    that the old keying was blind, permanently, in the test itself.

    **An accidental backstop exists and does not make this safe.** TikTok's
    `rebuilt total == referenced total` row also breaches here, and Shopee's
    crossing was composite from the start. But a total variance names no order and
    no store, which is exactly what this check is for — and on Shopee
    `SHOPEE_MONEY` is None, so conservation never runs at all and order coverage
    is the only order-population control there is.
    """
    income, orders = _collision_masking_a_missing_order()
    sku = calculate.explode_to_sku_tiktok(income, orders, log)
    sku = sku.assign(amount_with_vat=sku["subtotal_after_seller_discounts"])
    reference = tieout.SourceReference.from_income(
        income, money_col="subtotal_after_seller_discounts")

    results = tieout.run_checks_tiktok(
        sku, reference, settings, log, money_col="subtotal_after_seller_discounts")
    store_coverage = results[results["check"].str.startswith("Store coverage")]
    order_coverage = results[results["check"].str.startswith("Order coverage")]

    # Guard: the fixture must not let store coverage do this check's work.
    assert (store_coverage["result"] == "PASS").all(), (
        "fixture error: store coverage fired, so this would pass for the wrong reason")

    assert (order_coverage["result"] == "BREACH").any(), (
        "250,000 VND settled under an order with no SKU lines of its own, and "
        f"order coverage passed: {order_coverage[['check', 'detail']].to_dict('records')}"
    )
    assert "Store A" in order_coverage.iloc[0]["detail"], (
        "the breach must name the store that lost an order; a count alone sends "
        "an operator back to the raw exports")

    # The discriminator: the pre-fix comparison, spelled out. If this ever starts
    # breaching, the fixture has stopped exercising the collision and the test
    # above is passing for a different reason.
    id_only_missing = ({o for _, o in reference.order_keys}
                       - set(sku["order_id"].astype(str)))
    assert not id_only_missing, (
        "fixture no longer reproduces defect 2.9: differencing bare order ids "
        "already finds the missing order, so the composite key is not what is "
        "being tested")


def test_the_reconciling_item_counts_an_order_whose_lines_belong_to_another_store(settings, log):
    """The quieter half, and arguably the worse one.

    `partition` decides which settlement is "matched" using bare ids, so Store
    A's line-less order is filed as matched on the strength of Store B's id. Its
    250,000 VND is added to the reference total (which then cannot be rebuilt)
    and, more insidiously, is *removed* from the ~21%-unmatched reconciling item
    that a reviewer is told to watch for changes in. The money leaves the invoice
    through a door that reports less traffic than it carried.
    """
    income, orders = _collision_masking_a_missing_order()
    sku = calculate.explode_to_sku_tiktok(income, orders, log)

    _, unmatched_money, unmatched_orders = tieout.partition(
        income, money_col="subtotal_after_seller_discounts",
        present_keys=tieout.pairs(sku))

    assert unmatched_orders == 1 and unmatched_money == 250_000.0, (
        f"expected Store A's line-less order reported as 1 unmatched order worth "
        f"250,000 VND; got {unmatched_orders} order(s) / {unmatched_money:,.0f} VND"
    )

    # The discriminator: bare-id matching would have called every row matched and
    # reported an empty reconciling item.
    id_only_unmatched = income[~income["order_id"].astype(str).isin(
        sku["order_id"].astype(str))]
    assert id_only_unmatched.empty, (
        "fixture no longer reproduces defect 2.9: bare-id matching already reports "
        "this order as unmatched")


def test_a_shared_order_id_does_not_fake_a_conservation_variance(settings, log):
    """The other half, and it fails the opposite way — noisily.

    Check 3 does `groupby("order_id")` with `settled=(money, "first")`. Under a
    collision it sums BOTH stores' rebuilt revenue and compares it against ONE
    store's settlement, manufacturing a variance out of correct data. A control
    that fires on clean input is how the original tie-outs became worthless.
    """
    income, orders = _two_stores_sharing_an_order_id()
    sku = calculate.explode_to_sku_tiktok(income, orders, log)
    sku = sku.assign(amount_with_vat=sku["subtotal_after_seller_discounts"])

    results = _tiktok_checks(sku, income, settings, log)
    conservation = results[results["kind"] == "conservation"]

    assert not (conservation["result"] == "BREACH").any(), (
        "both stores' lines are present and correct, yet conservation reported a "
        f"variance: {conservation[['check', 'variance', 'detail']].to_dict('records')}"
    )

    # The discriminator: grouping on the id alone collapses two stores into one
    # row, so `first()` keeps one settlement against both stores' rebuild.
    collapsed = sku.groupby("order_id").agg(
        rebuilt=("amount_with_vat", "sum"),
        settled=("subtotal_after_seller_discounts", "first"))
    assert (collapsed["rebuilt"] - collapsed["settled"]).abs().max() > 1.0, (
        "fixture no longer reproduces defect 2.9: id-only grouping no longer "
        "manufactures a variance, so this test proves nothing")


def test_stitch_flags_unmatched_income_rather_than_dropping_it(log):
    """Control: the pipeline's stated posture — never silently drop."""
    income = pd.DataFrame({"store": ["A"], "order_id": ["NO-SUCH-ORDER"]})
    orders = pd.DataFrame(
        {
            "store": ["A"],
            "order_id": ["OTHER"],
            "order_created_at": pd.to_datetime(["2026-05-01"]),
        }
    )

    matched, unmatched = stitch.stitch(income, orders, log)

    assert len(matched) == 0
    assert len(unmatched) == 1


# --------------------------------------------------------------------------
# Interface stability the web app depends on
# --------------------------------------------------------------------------

def test_runlog_is_duck_typed(log):
    """The service (M4/M5) streams progress by substituting a QueueRunLog for
    RunLog. That works only because annotations are strings and nothing
    isinstance-checks the logger. Pin it: `log` here is a RecordingLog, not a
    RunLog. Retarget this at pipeline.run() once the M1 seam exists."""
    from src.runlog import RunLog

    assert not isinstance(log, RunLog)

    income = pd.DataFrame({"store": ["A"], "order_id": ["X"]})
    orders = pd.DataFrame(
        {"store": ["A"], "order_id": ["X"], "order_created_at": pd.to_datetime(["2026-05-01"])}
    )

    matched, _ = stitch.stitch(income, orders, log)

    assert len(matched) == 1
    assert log.lines, "the substituted logger received no output"
