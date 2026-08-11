"""Stage 3 — Classify.

Tags each income line OK / WRITTEN (returned) / ZERO_REVENUE per the team's
rules, and attaches the invoice grouping from brand_rules.yaml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import invoice_grouping
from .runlog import RunLog

STATUS_OK = "OK"
STATUS_WRITTEN = "WRITTEN"
STATUS_ZERO = "ZERO_REVENUE"

# TikTok statuses ported from the team's Power Query (Income_Final query,
# Thanh_recon V1/V2 M code, steps "Added Custom3"/"Grouped Rows"):
CHECK_GOOD = "Good"
CHECK_PARTIAL_RETURN = "Partial Return"
CHECK_TOTAL_RETURN = "Total Return"
CHECK_PAYBACK = "Payback_Order"
FINAL_OK = "OK"
FINAL_TAKE_OUT = "take out"
# Lines that fall through every M-code branch: Logistics/Platform
# reimbursements, commission adjustments, and rare Order lines with negative
# settlement but no refund. The team's pivots include these in NEITHER the
# invoice (Good) NOR take-out — they must surface in exceptions, not vanish.
FINAL_UNCLASSIFIED = "unclassified"

# Shopee statuses, exactly as they appear in the intermediary "Xuat HĐ"
# column A (XLOOKUP against the curated "return + 0dong" sheet, default ok):
SHOPEE_OK = "ok"
SHOPEE_RETURN = "Return"
SHOPEE_ZERO_DONG = "0 dong"

# Income columns the M code sums when collapsing settlement lines per order.
TIKTOK_GROUP_SUM_COLUMNS = [
    "gross_revenue", "net_revenue", "actual_refund",
    "subtotal_after_seller_discounts", "subtotal_before_discounts",
    "refund_subtotal_after_sd", "refund_subtotal_before_sd",
]


def classify(income: pd.DataFrame, brand_rules: dict, log: RunLog) -> pd.DataFrame:
    df = income.copy()
    refund = df["actual_refund"].fillna(0)
    net = df["net_revenue"].fillna(0)
    df["status"] = np.where(refund != 0, STATUS_WRITTEN, np.where(net == 0, STATUS_ZERO, STATUS_OK))

    # Invoice grouping is config, not code: separate-invoice brands split out,
    # everything else lands in the combined group.
    df["invoice_group"] = [
        brand if invoice_grouping(brand, brand_rules) == "separate" else "combined"
        for brand in df["brand"]
    ]

    counts = df["status"].value_counts()
    for status in (STATUS_OK, STATUS_WRITTEN, STATUS_ZERO):
        log.add(f"  {status}: {int(counts.get(status, 0))}")
    return df


def classify_tiktok_income(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Line-by-line port of the team's TikTok income classification.

    Evidence: Power Query "Income_Final" embedded in Thanh_recon V1/V2
    (customXml DataMashup -> Formulas/Section1.m):
      - step "Grouped Rows": income collapsed per (Name, Order/adjustment ID,
        Type, Order created time), amount columns summed
      - step "Added Custom3":
          _OrderID_Pass           = subtotal_before + refund_before <> 0
                                    and settlement >= 0 and Type = "Order"
          _OrderID_Partial_Return = subtotal_before + refund_before <> 0
                                    and refund_before <> 0 and Type = "Order"
          _OrderID_Return_Total   = subtotal_before + refund_before = 0
                                    and Type = "Order"
          _OrderID_Refund_PayBack = not Pass and Total Revenue < 0
          _Check_Status: Payback_Order > Total Return > Partial Return > Good
      - step "Filtered Rows1": rows with null Type dropped

    Final_Status (OK / "take out") is a Power Pivot model column whose DAX is
    not extractable from the workbook. Empirically determined rule, verified
    exactly against the Total files' pivots for U food (Good/Total Return
    only) and Unilever Homecare (all four statuses, both windows):
      OK        = _Check_Status "Good" (the only lines that get invoiced)
      take out  = Total Return, Payback_Order AND Partial Return
                  (W1: -55,421,041 TR - 9,692,678 PB + 8,035,197 Partial
                   = -57,078,522 = V1 "Pivot Income" Unilever row, exact)
      unclassified = null _Check_Status (reimbursements/adjustments +
                  negative-settlement no-refund Orders; ~150/window/store,
                  +44-51M VND settlement for Unilever) — present in NEITHER
                  team pivot; route to exceptions, ask Hoang where they book.
    Partial Return orders are fully EXCLUDED from the window's invoicing
    (not partially adjusted) — the For KA file has a manual "Return 1 phan"
    section for them (broken #REF! formula in both windows' files).
    """
    df = income.copy()
    df = df[df["income_type"].notna()]

    group_keys = ["store", "brand", "order_id", "income_type", "income_order_created_at"]
    sum_cols = [c for c in TIKTOK_GROUP_SUM_COLUMNS if c in df.columns]
    before = len(df)
    df = df.groupby(group_keys, as_index=False, dropna=False).agg(
        {**{c: "sum" for c in sum_cols}, "statement_date": "min"})
    if len(df) != before:
        log.add(f"  income settlement lines collapsed per order: {before} -> {len(df)}")

    subtotal_net = df["subtotal_before_discounts"].fillna(0) + df["refund_subtotal_before_sd"].fillna(0)
    is_order = df["income_type"] == "Order"
    is_pass = (subtotal_net != 0) & (df["net_revenue"].fillna(0) >= 0) & is_order
    is_partial = (subtotal_net != 0) & (df["refund_subtotal_before_sd"].fillna(0) != 0) & is_order
    is_total_return = (subtotal_net == 0) & is_order
    is_payback = ~is_pass & (df["gross_revenue"].fillna(0) < 0)

    df["check_status"] = np.select(
        [is_payback, is_total_return, is_partial, is_pass],
        [CHECK_PAYBACK, CHECK_TOTAL_RETURN, CHECK_PARTIAL_RETURN, CHECK_GOOD],
        default=None,
    )
    df["final_status"] = np.select(
        [df["check_status"] == CHECK_GOOD, df["check_status"].notna()],
        [FINAL_OK, FINAL_TAKE_OUT],
        default=FINAL_UNCLASSIFIED,
    )

    for status, n in df["check_status"].value_counts(dropna=False).items():
        log.add(f"  check_status {status}: {n}")
    for status, n in df["final_status"].value_counts().items():
        log.add(f"  final_status {status}: {n}")
    return df


def classify_shopee_income(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee order classification.

    The team's process tags each order via XLOOKUP into a manually curated
    "return + 0dong" sheet ('Xuat HĐ' col A, default "ok"). The membership
    rules were DERIVED from raw income and verified exactly:
      Return : order's refund ("Số tiền hoàn lại") sum != 0
               (Kate W1 11/11; Sanofi W1 178/178 — 0 missing, 0 extra)
      0 dong : order's settlement ("Tổng tiền đã thanh toán") sum <= 0
               AND refund == 0 — fully promo-covered orders where the
               remaining settlement is just fees, so nothing is invoiced
               (Sanofi W1 40/40 — 0 missing, 0 extra)
      ok     : everything else (invoiced)

    Only rows typed "Order" in "Đơn hàng / Sản phẩm" count (the team's M code
    filters the product-level "Sku" rows the same way).
    """
    df = income[income["income_type"] == "Order"].copy()
    dropped = len(income) - len(df)
    if dropped:
        log.add(f"  non-Order income lines dropped (Sku/product rows): {dropped}")

    group_keys = ["store", "brand", "order_id"]
    df = df.groupby(group_keys, as_index=False, dropna=False).agg(
        net_revenue=("net_revenue", "sum"),
        actual_refund=("actual_refund", "sum"),
        gross_revenue=("gross_revenue", "sum"),
        cofund_voucher=("cofund_voucher", "sum"),
        seller_voucher=("seller_voucher", "sum"),
        seller_coin_cashback=("seller_coin_cashback", "sum"),
        seller_ship_support=("seller_ship_support", "sum"),
        income_order_created_at=("income_order_created_at", "min"),
        statement_date=("statement_date", "min"),
    )
    refund = df["actual_refund"].fillna(0)
    net = df["net_revenue"].fillna(0)
    df["check_status"] = np.select(
        [refund != 0, net <= 0],
        [SHOPEE_RETURN, SHOPEE_ZERO_DONG],
        default=SHOPEE_OK,
    )
    df["final_status"] = np.where(df["check_status"] == SHOPEE_OK, FINAL_OK, FINAL_TAKE_OUT)
    for status, n in df["check_status"].value_counts().items():
        log.add(f"  check_status {status}: {n}")
    return df
