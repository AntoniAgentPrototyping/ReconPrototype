"""Stage 5 — Tie-out checks that can actually fail.

## What was wrong

The previous implementation ported the team's three Excel tolerance checks
faithfully and was, as automated controls, worthless. Each computed **both
sides from the same frame**, so every one reduced to an identity: a real run
reported `variance 0.00` exactly on 11.9B VND, and a falsification harness
showed that zeroing 100% of revenue still reported ALL PASS.

The formulas were not wrong. In the manual process a human copy-pastes between
each pivot, and the check catches a row lost in *that* step. Porting the
arithmetic without the manual step it policed left the shape of a control with
none of the substance. See docs/08-KNOWN-DEFECTS.md#11.

## What replaces it

**A check must cross a boundary.** Every check here compares the frame under
test against a `SourceReference` captured *upstream, from a different file*,
before the money math ran. If a check's inputs all come from the frame it is
checking, it can only restate that frame.

Three verdicts, not two:

    PASS    the relationship holds within tolerance
    BREACH  it does not — the run is not trustworthy
    INFO    a named reconciling item: real, expected, quantified, not an error

`INFO` exists because of something the rebuild surfaced. On a real TikTok
window, **11,765 of 55,894 classified-GOOD income orders (21%, 3.45B VND) have
no matching rows in the order export** and are dropped by the inner join in
`explode_to_sku_tiktok`. That is not new behaviour and it is not believed to be
a defect — the team's own VLOOKUP does the same, and June's totals tied exactly
against their files — but nothing anywhere reported it. A control that stays
silent about a fifth of settlement is not doing its job even when the number is
correct. It is now named, quantified and written to the run log every time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from .lazada import REVENUE_BUCKET
from .runlog import RunLog

PASS, BREACH, INFO = "PASS", "BREACH", "INFO"


@dataclass(frozen=True)
class SourceReference:
    """Totals captured upstream of the transformation being checked.

    This is the whole design. The reference is built from the **income** file;
    the checks run against the **SKU-level** frame rebuilt from the *order*
    file. The two are different exports from the platform, so agreement between
    them is evidence rather than arithmetic.

    Capture it immediately after the explode and before `compute_sku_columns`,
    so everything the money math does afterwards is inside the checked span.
    """

    label: str
    order_ids: frozenset
    settlement: float
    per_store: Mapping[str, float]
    orders: int

    @classmethod
    def from_income(cls, income: pd.DataFrame, *, money_col: str,
                    label: str = "income") -> "SourceReference":
        if money_col not in income.columns:
            raise KeyError(f"reference money column {money_col!r} not in the income frame")
        money = income[money_col].fillna(0)
        per_store = (income.assign(_m=money).groupby("store")["_m"].sum().to_dict()
                     if "store" in income.columns else {})
        return cls(
            label=label,
            order_ids=frozenset(income["order_id"].astype(str)),
            settlement=float(money.sum()),
            per_store={str(k): float(v) for k, v in per_store.items()},
            orders=int(income["order_id"].nunique()),
        )

@dataclass(frozen=True)
class RevenueCrossing:
    """Per-order revenue captured from the INCOME export, to be compared against
    the ORDER-file rebuild — Shopee's money crossing.

    Shopee had none until 2026-08-13. Applying TikTok's relation (rebuilt
    with-VAT total vs a settlement column) breached on correct data, because
    Shopee's settlement is net of platform fees and its invoice chain carries a
    proportional discount allocation. Rather than widen a tolerance until it
    passed, the gap was left open and logged (docs/06-DECISIONS.md#d1).

    What closed it is the team's OWN June reconciliation formula, read out of
    their consolidated file's `Net revenue` cell:

        Net revenue = Giá sản phẩm
                    + Sản phẩm được trợ giá từ Shopee
                    + Mã ưu đãi do Người Bán chịu
                    + Mã hoàn xu do Người Bán chịu
                    + Mã ưu đãi Đồng Tài Trợ do Người Bán chịu
                    + Mã hoàn xu Đồng Tài Trợ do Người Bán chịu

    The last four are the seller-borne discount pool. Move them to the other
    side and what remains is a pure order-file-vs-income-file statement:

        SUM(unit_price*qty - seller_subsidy)  ==  gross_revenue + shopee_product_subsidy
        └────────── order export ──────────┘      └──────── income export ────────┘

    The left side is evaluated **row-additively**, as
    `SUM(amount_with_vat - discount_allocated)` over the SKU lines, which is the
    same quantity: the invoice chain's `amount_with_vat` is `T + Z` (the VAT
    divide and multiply cancel) and `Z` sums back to the discount pool per
    order. Both forms were measured against real data — identical to 0.0000 VND
    on all three May windows — and the row-additive one is the better control,
    because a SKU line dropped after the per-order transform still reduces it,
    whereas reading the pre-computed `order_gross_sale` column would not.

    Measured before being asserted, on four independent windows: **zero**
    deviating orders among 48,535 / 14,831 / 16,873 non-refund orders (May
    s1/s2/s3) and 1 of 81,232 (their June file). Hence a 1 VND tolerance rather
    than a nominal one.

    **Refund orders are held out, not tolerated.** Every non-tying order in all
    four windows carries a refund, and the deviation is always positive: the
    order export still holds the full ordered quantity while the income side has
    been reduced for the returned units. They are reported as a named
    reconciling item with their count and amount, the same treatment as TikTok's
    unmatched-settlement class — folding them into a tolerance would be the
    original mistake all over again.
    """

    label: str
    per_order: "pd.Series"      # (store, order_id) -> revenue, refunds removed
    excluded: "pd.Series"       # (store, order_id) -> revenue, the refund class


def revenue_crossing_shopee(income: pd.DataFrame, log: RunLog,
                            *, label: str = "income:net-revenue") -> RevenueCrossing | None:
    """Build Shopee's crossing from the income frame, upstream of the money math.

    Returns None (and says why) if the export lacks a component, because a
    reference missing `shopee_product_subsidy` would deviate on precisely the
    orders that carry a Shopee-funded product subsidy — a check that fires on
    correct data is worse than no check.
    """
    keys = ["store", "order_id"]
    missing = [c for c in (*keys, "gross_revenue") if c not in income.columns]
    if missing:
        log.warn(f"Shopee revenue crossing NOT built: income frame lacks {missing}")
        return None
    if "shopee_product_subsidy" not in income.columns:
        log.warn("Shopee revenue crossing NOT built: 'shopee_product_subsidy' is not "
                 "mapped for this export, and the team's Net revenue formula includes "
                 "it — asserting the crossing without it would breach on every "
                 "Shopee-subsidised order")
        return None

    rows = income
    if "income_type" in rows.columns:
        rows = rows[rows["income_type"].astype(str).str.strip() == "Order"]
    revenue = rows["gross_revenue"].fillna(0) + rows["shopee_product_subsidy"].fillna(0)
    per_order = rows.assign(_rev=revenue).groupby(keys)["_rev"].sum()

    refund = (rows.assign(_r=rows["actual_refund"].fillna(0)).groupby(keys)["_r"].sum()
              if "actual_refund" in rows.columns else None)
    held = refund[refund != 0].index if refund is not None else per_order.index[:0]
    return RevenueCrossing(label=label, per_order=per_order.drop(held, errors="ignore"),
                           excluded=per_order.reindex(held).dropna())


REBUILT = ("amount_with_vat", "discount_allocated", "quantity")


def _crossing_rows(sku_level: pd.DataFrame, x: RevenueCrossing, tolerance: float) -> list[dict]:
    keys = ["store", "order_id"]
    missing = [c for c in (*keys, *REBUILT) if c not in sku_level.columns]
    if missing or not len(sku_level):
        return [_info("Revenue crossing skipped", 0.0,
                      f"the SKU frame lacks {missing}" if missing else "no SKU rows")]

    # A zero-quantity SKU line breaks the identity by construction: the pre-VAT
    # unit price divides by quantity and falls back to 0, so the line
    # contributes nothing to amount_with_vat while the order still owes
    # net + allocated discount. None occur in any window measured, but the
    # calculation explicitly guards against qty 0, so the case is real. Held out
    # and named rather than left to fire on correct data one day.
    zero_qty = sku_level.loc[sku_level["quantity"].fillna(0) == 0, keys]
    zero_qty_orders = pd.MultiIndex.from_frame(zero_qty).unique() if len(zero_qty) else None

    rebuilt = sku_level["amount_with_vat"].fillna(0) - sku_level["discount_allocated"].fillna(0)
    mine = sku_level.assign(_rebuilt=rebuilt).groupby(keys)["_rebuilt"].sum()
    reference = x.per_order
    if zero_qty_orders is not None:
        mine = mine.drop(zero_qty_orders, errors="ignore")
        reference = reference.drop(zero_qty_orders, errors="ignore")

    # The REFERENCE defines the population, and an order the SKU frame no longer
    # carries counts as a shortfall of its whole value — not as a row to skip.
    # An inner join here let the falsification harness delete an entire store
    # undetected: the orders simply left the comparison and everything that
    # remained still tied. Shopee order coverage is measured at 100% on every
    # window (49,138 / 14,989 / 17,080), so nothing legitimate is being punished;
    # if that ever stops being true the coverage check above breaches too.
    missing = int(len(reference.index.difference(mine.index)))
    both = pd.concat([reference.rename("ref"),
                      mine.reindex(reference.index).fillna(0.0).rename("mine")], axis=1)
    deviation = (both["mine"] - both["ref"]).abs()
    worst = float(deviation.max()) if len(deviation) else 0.0
    offenders = int((deviation > tolerance).sum())

    rows = [_row(
        "Revenue conservation: order-file rebuild == income Net revenue (per order)",
        expected=0.0, actual=worst, tolerance=tolerance,
        detail=(f"{offenders:,} of {len(both):,} orders deviate"
                + (f", {missing:,} absent from the SKU frame entirely" if missing else "")
                if offenders else f"{len(both):,} orders exact"),
        kind="conservation")]
    rows.append(_row(
        "Revenue conservation: rebuilt total == income Net revenue total",
        expected=float(both["ref"].sum()), actual=float(both["mine"].sum()),
        tolerance=max(tolerance, float(len(both))),
        detail=f"over {len(both):,} orders, refund orders held out", kind="conservation"))
    if len(x.excluded):
        rows.append(_info(
            "Orders with a refund, held out of the revenue crossing",
            float(x.excluded.sum()),
            f"{len(x.excluded):,} order(s); the order export carries the full ordered "
            f"quantity while income is reduced for returned units, so these do not tie "
            f"by construction and are reported rather than absorbed into a tolerance"))
    if zero_qty_orders is not None:
        rows.append(_info(
            "Orders with a zero-quantity SKU line, held out of the revenue crossing",
            0.0, f"{len(zero_qty_orders):,} order(s); the pre-VAT unit price divides by "
                 f"quantity, so such a line cannot carry its own revenue"))
    return rows


def partition(income: pd.DataFrame, *, money_col: str, present_order_ids,
              label: str = "income") -> tuple[SourceReference, float, int]:
    """Split the income frame into (matched reference, unmatched money, unmatched orders).

    The unmatched remainder becomes a *reconciling item*, not a variance, so
    the conservation check below stays exact. Folding a 21% silent-drop
    allowance into a tolerance instead would repeat the original mistake: a
    threshold wide enough never to fire.
    """
    present = set(map(str, present_order_ids))
    ids = income["order_id"].astype(str)
    matched, unmatched = income[ids.isin(present)], income[~ids.isin(present)]
    return (
        SourceReference.from_income(matched, money_col=money_col, label=f"{label}:matched"),
        float(unmatched[money_col].fillna(0).sum()) if len(unmatched) else 0.0,
        int(unmatched["order_id"].nunique()) if len(unmatched) else 0,
    )


def _row(name: str, expected: float, actual: float, tolerance: float,
         detail: str = "", kind: str = "") -> dict:
    variance = actual - expected
    return {
        "check": name,
        "kind": kind or "tieout",
        "expected": round(float(expected), 2),
        "actual": round(float(actual), 2),
        "variance": round(float(variance), 2),
        "tolerance": float(tolerance),
        "result": PASS if abs(variance) <= tolerance else BREACH,
        "detail": detail,
    }


def _info(name: str, amount: float, detail: str) -> dict:
    return {"check": name, "kind": "reconciling", "expected": 0.0,
            "actual": round(float(amount), 2), "variance": round(float(amount), 2),
            "tolerance": float("inf"), "result": INFO, "detail": detail}


def _tolerances(settings: dict, platform: str) -> dict:
    return (settings.get("tolerances") or {}).get(platform) or {}


def run_checks(sku_level: pd.DataFrame, reference: SourceReference, settings: dict,
               log: RunLog, *, platform: str, money_col: str | None = None,
               unmatched_money: float = 0.0, unmatched_orders: int = 0,
               crossing: RevenueCrossing | None = None) -> pd.DataFrame:
    """Verify the SKU-level frame against an independently-captured reference.

    Four checks, each crossing a boundary the previous implementation did not:

    1. **Order coverage** — every referenced order must still be present.
       Catches rows or whole orders lost after the reference was taken.
    2. **Store coverage** — a store with settlement must appear in the output.
       Catches an entire storefront disappearing while totals still look sane.
    3. **Settlement conservation** — revenue rebuilt from the *order* file must
       equal the settlement figure from the *income* file, per order. Measured
       exact (max deviation 0.0000 VND across 44,129 real orders), so the
       tolerance is tight rather than nominal.
    4. **Per-store conservation** — the same, per store, so an error that nets
       to zero across stores still fires.
    """
    tol = _tolerances(settings, platform)
    money_tol = float(tol.get("conservation_vnd", 1.0))
    results: list[dict] = []

    sku_orders = set(sku_level["order_id"].astype(str)) if len(sku_level) else set()

    # 1 — order coverage
    missing = reference.order_ids - sku_orders
    results.append(_row(
        "Order coverage: every referenced order reaches the SKU level",
        expected=0.0, actual=float(len(missing)), tolerance=0.0,
        detail=(f"{len(missing):,} of {len(reference.order_ids):,} referenced orders absent"
                if missing else f"all {len(reference.order_ids):,} orders present"),
        kind="coverage"))

    # 2 — store coverage
    ref_stores = {s for s, v in reference.per_store.items() if v != 0}
    out_stores = set(sku_level["store"].astype(str)) if "store" in sku_level.columns else set()
    lost_stores = sorted(ref_stores - out_stores)
    results.append(_row(
        "Store coverage: every store with settlement appears in the output",
        expected=0.0, actual=float(len(lost_stores)), tolerance=0.0,
        detail=(f"{len(lost_stores)} store(s) missing" if lost_stores
                else f"all {len(ref_stores)} store(s) present"),
        kind="coverage"))

    # 3 — settlement conservation, order file vs income file.
    #
    # Only run where the crossing has been MEASURED to hold. On TikTok the
    # order-file rebuild equals the income settlement exactly (max deviation
    # 0.0000 VND across 44,129 orders). On Shopee no such relation exists:
    # `amount_with_vat` carries a proportional allocation of order-level
    # discounts, and against the two income columns the closest fit still
    # deviates on 14,104 of 36,162 orders. Asserting a relation there would
    # produce a check that fires on correct data — which is how a control gets
    # switched off. It is named as an open gap instead (see below).
    if money_col and money_col in sku_level.columns and len(sku_level):
        per_order = sku_level.groupby("order_id").agg(
            rebuilt=("amount_with_vat", "sum"), settled=(money_col, "first"))
        deviation = (per_order["rebuilt"] - per_order["settled"]).abs()
        worst = float(deviation.max()) if len(deviation) else 0.0
        offenders = int((deviation > money_tol).sum())
        results.append(_row(
            "Settlement conservation: order-file rebuild == income settlement (per order)",
            expected=0.0, actual=worst, tolerance=money_tol,
            detail=(f"{offenders:,} of {len(per_order):,} orders deviate"
                    if offenders else f"{len(per_order):,} orders exact"),
            kind="conservation"))

        rebuilt_total = float(per_order["rebuilt"].sum())
        results.append(_row(
            "Settlement conservation: rebuilt total == referenced total",
            expected=reference.settlement, actual=rebuilt_total,
            tolerance=max(money_tol, float(tol.get("grand_vnd", 1.0))),
            detail=f"over {len(per_order):,} matched orders", kind="conservation"))

        # 4 — per store, so offsetting errors cannot cancel
        if "store" in sku_level.columns and reference.per_store:
            by_store = sku_level.groupby("store")["amount_with_vat"].sum()
            worst_store, worst_gap = "", 0.0
            for store, expected in reference.per_store.items():
                gap = abs(float(by_store.get(store, 0.0)) - float(expected))
                if gap > worst_gap:
                    worst_store, worst_gap = store, gap
            results.append(_row(
                "Per-store conservation: no store's total drifts from the reference",
                expected=0.0, actual=worst_gap,
                tolerance=max(money_tol, float(tol.get("per_store_vnd", 1.0))),
                detail=(f"worst store deviates {worst_gap:,.2f} VND" if worst_gap
                        else f"{len(reference.per_store)} store(s) exact"),
                kind="conservation"))

    # Shopee's crossing is a different pair of columns, not a different idea:
    # the order-file rebuild against revenue derived from the income file by the
    # team's own June formula. See RevenueCrossing.
    if crossing is not None:
        results.extend(_crossing_rows(sku_level, crossing, money_tol))

    if not money_col and crossing is None:
        results.append(_info(
            "Money conservation NOT verified for this platform",
            0.0,
            "coverage is checked; the order-file-vs-income-file money crossing "
            "has no measured exact relation here, so no tolerance is asserted"))

    # Reconciling item — real, expected, and previously invisible.
    if unmatched_orders or unmatched_money:
        results.append(_info(
            "Settlement with no matching order lines (excluded from invoicing)",
            unmatched_money,
            f"{unmatched_orders:,} order(s); matches the team's VLOOKUP behaviour, "
            f"but is reported rather than silent"))

    frame = pd.DataFrame(results)
    _log(frame, log)
    return frame


def _log(results: pd.DataFrame, log: RunLog) -> None:
    for _, row in results.iterrows():
        if row["result"] == INFO:
            log.add(f"  RECONCILING {row['check']}: {row['actual']:,.2f} VND — {row['detail']}")
            continue
        line = (f"  Check {row['check']}: {row['result']} "
                f"(expected {row['expected']:,.2f}, actual {row['actual']:,.2f}, "
                f"variance {row['variance']:,.2f}, tol {row['tolerance']:,.2f})")
        log.warn(line.strip()) if row["result"] == BREACH else log.add(line)
        if row["detail"]:
            log.add(f"      {row['detail']}")


def run_checks_tiktok(sku_level: pd.DataFrame, reference: SourceReference, settings: dict,
                      log: RunLog, **kw) -> pd.DataFrame:
    # TikTok is the platform where the order-file-vs-income-file crossing was
    # measured exact, so it gets the money check by default. `money_col` is
    # None everywhere else on purpose (see run_checks).
    kw.setdefault("money_col", "subtotal_after_seller_discounts")
    return run_checks(sku_level, reference, settings, log, platform="tiktok", **kw)


def run_checks_shopee(sku_level: pd.DataFrame, reference: SourceReference, settings: dict,
                      log: RunLog, **kw) -> pd.DataFrame:
    return run_checks(sku_level, reference, settings, log, platform="shopee", **kw)


def run_checks_lazada(revenue: pd.DataFrame, classified: pd.DataFrame, settings: dict,
                      log: RunLog) -> pd.DataFrame:
    """Lazada has no order/income split, so its boundary is a different one.

    The ledger is a fee-event stream: `classify_ledger` buckets every row by fee
    name, and `revenue_lines` expands only the revenue bucket into order × SKU
    lines. The crossing is therefore **ledger → revenue lines**: the money the
    classifier assigned to the revenue bucket must survive the expansion.

    Previously Lazada called `tieout` not at all — the module was never even
    imported on this path.
    """
    tol = _tolerances(settings, "lazada")
    money_tol = float(tol.get("conservation_vnd", 1.0))
    results: list[dict] = []

    revenue_rows = classified[classified["fee_bucket"] == REVENUE_BUCKET]
    credits = float(revenue_rows["amount_incl_vat"].fillna(0).sum())
    matched_promo = float(revenue["promo"].fillna(0).sum()) if "promo" in revenue.columns else 0.0
    lines_total = float(revenue["check_with_vat"].fillna(0).sum()) \
        if "check_with_vat" in revenue.columns else float("nan")

    # The invoiced figure is credits NET OF PROMO — promotional charges sit in
    # their own buckets and are paired back onto the revenue line they discount
    # (lazada.py:204-208). Comparing against credits alone was wrong: it made a
    # correct run report a 72M VND breach while the template's own control
    # blocks read 0.00, which is how the error was caught.
    #
    # The residual is Price KA rounding — a per-unit round to whole VND, then
    # multiplied back by quantity — so the tolerance is the team's own for that
    # same comparison, not an invented one.
    results.append(_row(
        "Revenue conservation: ledger credits + promo == expanded revenue lines",
        expected=credits + matched_promo, actual=lines_total,
        tolerance=max(money_tol, float(tol.get("price_ka_rounding_vnd", 1000.0))),
        detail=f"{len(revenue_rows):,} ledger rows -> {len(revenue):,} order x SKU lines; "
               f"promo netted {matched_promo:,.0f} VND",
        kind="conservation"))

    # Store coverage: a store with revenue in the ledger must reach the lines.
    ledger_stores = {str(s) for s, v in
                     revenue_rows.groupby("store")["amount_incl_vat"].sum().items() if v}
    line_stores = set(revenue["store"].astype(str)) if "store" in revenue.columns else set()
    lost = sorted(ledger_stores - line_stores)
    results.append(_row(
        "Store coverage: every store with ledger revenue reaches the lines",
        expected=0.0, actual=float(len(lost)), tolerance=0.0,
        detail=(f"{len(lost)} store(s) missing" if lost
                else f"all {len(ledger_stores)} store(s) present"),
        kind="coverage"))

    # Order-less revenue is a real Lazada category (compensation for lost or
    # damaged inventory). It is kept in the sale-report figure deliberately —
    # an earlier exporter silently zeroed it — so it is named, not breached.
    if "order_id" in revenue_rows.columns:
        orderless = revenue_rows[revenue_rows["order_id"].astype(str).str.strip().isin(("", "nan"))]
        if len(orderless):
            results.append(_info(
                "Revenue rows with no order (platform compensation)",
                float(orderless["amount_incl_vat"].fillna(0).sum()),
                f"{len(orderless):,} ledger row(s) kept in the sale report"))

    frame = pd.DataFrame(results)
    _log(frame, log)
    return frame
