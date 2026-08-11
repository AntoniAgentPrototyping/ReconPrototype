"""Stage 4 — Calculate.

Explodes order-level income lines to SKU level via the order file (the
VLOOKUP the Power Query does today), then applies the yellow-column logic
from the team's calculation file.

=============================================================================
PORT ZONE
Everything between compute_sku_columns() and the end of this file must be
translated LINE-BY-LINE from the team's actual calculation file and verified
against its output — not reinvented. The bodies below are structural
placeholders (proportional allocation, flat VAT back-out) so the pipeline
runs end-to-end before the real file arrives. While PLACEHOLDER_FORMULAS is
True, every run stamps a warning into run_log.txt.
=============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .classify import STATUS_OK, STATUS_WRITTEN
from .runlog import RunLog

PLACEHOLDER_FORMULAS = True

# Per-formula verification state for the TikTok chain ported from the team's
# intermediary file "Tiktok result Sample T5 - 1 to 17T5.xlsx", sheet
# "Xuat HĐ" (header row 3, formulas read from row 4). Flip an entry to
# "verified" ONLY after tools/calc_verify.py shows a row-by-row match against
# the team's file for that column. PLACEHOLDER_FORMULAS above stays True
# until every entry is verified AND the 1.05/1.10 VAT buckets are resolved.
TIKTOK_FORMULA_STATUS = {
    # Verified 2026-07-16 by tools/calc_verify.py: U food, May 2026, both
    # windows — 25/25 and 124/124 rows matched the team's intermediary file
    # ("Xuat HĐ") exactly; classification aggregates matched the Total files'
    # pivots to the VND. Verified means: for U food, one month, one store.
    # join income->order SKU lines on Order ID; SKU identity = Seller SKU
    # (evidence: "Xuat HĐ" col H header "Seller SKU"; col C join key)
    "sku_explode_join": "verified",
    "gross_rev": "verified",              # L4 = J4*K4  (unit original price x qty)
    "net_after_seller_discount": "verified",  # M4 = (J4*K4)-N4  (minus SKU Seller Discount)
    "order_gross_sale": "verified",       # P4 = SUMIF($C:$C, C4, $M:$M)
    "unit_price_pre_vat": "verified",     # R4 = (M4/K4)/Q4  (Q = VAT factor, 1.08)
    "amount_pre_vat": "verified",         # T4 = R4*S4  (S = qty)
    "amount_with_vat": "verified",        # U4 = T4*Q4
    "order_revenue_check": "verified",    # V4 = SUMIF(C:C, E4, U:U), W4 = V4-F4 (per-order semantics)
    # Classification (M code port, classify.py): all four branches exercised
    # and verified — U food (Good, Total Return) row-level; Unilever Homecare
    # (adds Partial Return + Payback_Order) via exact take-out pivot equality
    # in both windows. Final_Status rule: take out = not Good.
    "check_status": "verified",
    "final_status_take_out": "verified",
}


def explode_to_sku(
    income: pd.DataFrame, orders: pd.DataFrame, sku_master: dict[str, str], log: RunLog
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Income has one line per order; orders have one line per SKU.
    Returns (sku-level frame for OK lines, unknown-SKU exception frame)."""
    lines = orders[["order_id", "sku_id", "sku_name", "quantity", "unit_price_gross"]].copy()
    lines["quantity"] = lines["quantity"].fillna(0)
    lines["line_gross"] = lines["quantity"] * lines["unit_price_gross"].fillna(0)

    order_total = lines.groupby("order_id")["line_gross"].transform("sum")
    line_count = lines.groupby("order_id")["order_id"].transform("size")
    # Allocation weight per SKU line: share of order gross; equal split when
    # the order gross is zero (free items / vouchers covering full price).
    lines["weight"] = np.where(order_total > 0, lines["line_gross"] / order_total, 1.0 / line_count)

    ok = income[income["status"] == STATUS_OK]
    sku_level = ok.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(ok)} OK income lines exploded to {len(sku_level)} SKU lines")

    known = sku_level["sku_id"].astype(str).str.strip().isin(sku_master.keys())
    unknown = sku_level.loc[~known, ["order_id", "store", "brand", "sku_id", "sku_name"]].drop_duplicates()
    if len(unknown):
        log.warn(f"{len(unknown)} SKU line(s) not in sku_master.csv (-> exceptions, run continues)")
    return sku_level, unknown


def compute_sku_columns(sku_level: pd.DataFrame, vat_rate: float, log: RunLog) -> pd.DataFrame:
    """PORT ZONE — the yellow columns. Placeholder formulas:

    gross_revenue_sku   = order gross revenue × allocation weight
    discount_sku        = (order gross − order net) × allocation weight
    net_revenue_sku     = order net revenue × allocation weight
    net_pre_vat_sku     = net_revenue_sku ÷ (1 + vat_rate)
    vat_sku             = net_revenue_sku − net_pre_vat_sku

    Each of these must be replaced by the exact formula from the team's
    calculation file and ticked off against the real file's output.
    """
    df = sku_level.copy()
    df["gross_revenue_sku"] = df["gross_revenue"].fillna(0) * df["weight"]
    df["discount_sku"] = (df["gross_revenue"].fillna(0) - df["net_revenue"].fillna(0)) * df["weight"]
    df["net_revenue_sku"] = df["net_revenue"].fillna(0) * df["weight"]
    df["net_pre_vat_sku"] = df["net_revenue_sku"] / (1 + vat_rate)
    df["vat_sku"] = df["net_revenue_sku"] - df["net_pre_vat_sku"]

    if PLACEHOLDER_FORMULAS:
        log.warn(
            "Calculation formulas are PLACEHOLDERS (src/calculate.py PORT ZONE). "
            "Numbers are NOT finance-grade until ported from the real calculation file."
        )
    return df


def explode_to_sku_tiktok(income_ok: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """TikTok SKU explode, ported from 'Xuat HĐ': order SKU lines grouped by
    (Order ID, Seller SKU, Product Name, unit price) with Quantity and
    SKU Seller Discount summed, then joined to OK income orders on Order ID.
    Order-level income amounts repeat on every SKU line (no allocation —
    the team rebuilds revenue from the order side instead)."""
    lines = orders.groupby(
        ["order_id", "sku_id", "sku_name", "unit_price_gross"], as_index=False, dropna=False
    ).agg(quantity=("quantity", "sum"), sku_seller_discount=("sku_seller_discount", "sum"))

    sku_level = income_ok.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(income_ok)} OK income orders exploded to {len(sku_level)} SKU lines")
    return sku_level


def compute_sku_columns_tiktok(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The yellow columns, ported formula-by-formula from 'Xuat HĐ' row 4
    (see TIKTOK_FORMULA_STATUS for the cell evidence per column)."""
    df = sku_level.copy()
    # Default-plus-exceptions VAT (confirmed model): one default factor plus
    # per-SKU exceptions from the team's master file. Reverting the 8% tax
    # concession to 10% is the single vat_factors.default config line.
    from .masters import vat_factor_for
    df["vat_factor"] = vat_factor_for(df["sku_id"], settings, settings.get("_vat_sku") or {})  # Q

    qty = df["quantity"].fillna(0)
    df["gross_rev"] = df["unit_price_gross"].fillna(0) * qty                        # L = J*K
    df["net_after_seller_discount"] = df["gross_rev"] - df["sku_seller_discount"].fillna(0)  # M = (J*K)-N
    df["order_gross_sale"] = df.groupby("order_id")["net_after_seller_discount"].transform("sum")  # P
    df["unit_price_pre_vat"] = (df["net_after_seller_discount"] / qty.replace(0, pd.NA)) / df["vat_factor"]  # R
    df["amount_pre_vat"] = df["unit_price_pre_vat"] * qty                           # T = R*S
    df["amount_with_vat"] = df["amount_pre_vat"] * df["vat_factor"]                 # U = T*Q
    # The team computes V/W only on the FIRST row of each order (their SUMIF
    # keys on the blank-on-repeat "non repeat" order-id column E; D/E/F are
    # blank on 2nd+ SKU rows of an order — intermediary rows 38/39 evidence).
    # The pipeline carries the order-level value on every row instead;
    # per-order semantics are identical and verified by calc_verify.py.
    df["order_revenue_check"] = df.groupby("order_id")["amount_with_vat"].transform("sum")  # V
    df["order_check_diff"] = df["order_revenue_check"] - df["subtotal_after_seller_discounts"].fillna(0)  # W = V-F

    pending = [k for k, v in TIKTOK_FORMULA_STATUS.items() if v != "verified"]
    if pending:
        log.warn(f"TikTok formulas not yet row-verified: {pending}")
    return df


# =============================================================================
# SHOPEE PORT ZONE — chain ported from the Shopee intermediary "Xuat HĐ"
# (shopee result sample 01 to 10T05.xlsx, header row 3, formulas row 4).
# Each entry flips to "verified" only after tools/calc_verify_shopee.py shows
# a row-by-row match against the team's file for that column.
# =============================================================================
# Verified 2026-07-17 by tools/calc_verify_shopee.py, May 2026 all three
# windows: nutifoodgpddvietnam (cleanest, 55 rows) and Sanofi (messiest,
# 51,159 rows — the only store with 0-dong orders) — every column and the
# status tag row-exact against the team's "Xuat HĐ". Caveats: 0-dong only
# occurs at Sanofi; the 1.05/1.10 VAT buckets were EMPTY in May (all rows
# 1.08) so non-default VAT is still unexercised, as are the Xmen/Kao
# sub-batch files.
SHOPEE_FORMULA_STATUS = {
    "classification_return_0dong": "verified",  # derived rules (see classify.py)
    "sku_explode_join": "verified",     # join on Mã đơn hàng; SKU = "SKU phân loại hàng"
    "gross_rev": "verified",            # S4 = Q4*R4 (Giá gốc x Số lượng)
    "net_after_discount": "verified",   # T4 = (Q4*R4)-U4 (minus Người bán trợ giá)
    "total_discount": "verified",       # W4 = K4+L4+M4+H4 (voucher+ship support+coins+co-fund)
    "order_gross_sale": "verified",     # X4 = SUMIF($C:$C,C4,$T:$T)
    "discount_per_order": "verified",   # Y4 = SUMIF($C:$C,C4,$W:$W)
    "discount_allocated": "verified",   # Z4 = IFERROR((T4/X4)*Y4, 0)  — proportional!
    "unit_price_pre_vat": "verified",   # AB4 = ((T4+Z4)/R4)/AA4  (AA = VAT factor)
    "amount_pre_vat": "verified",       # AD4 = AB4*AC4
    "amount_with_vat": "verified",      # AE = AD*AA
}


def explode_to_sku_shopee(income_orders: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee SKU explode: order SKU lines grouped by (order, SKU, name,
    unit price) with quantity and both subsidies summed, joined to the
    classified income orders on order id. ALL classified orders join (the
    intermediary carries Return/0-dong rows too — invoicing filters later)."""
    orders = orders.copy()
    # Shopee order exports drift between stores/versions — some lack the
    # subsidy columns entirely. seller_subsidy feeds the T formula, so its
    # absence is worth a loud warning; shopee_subsidy is informational.
    for col in ("seller_subsidy", "shopee_subsidy"):
        if col not in orders.columns:
            log.warn(f"orders have no '{col}' column (export version drift) — treating as 0")
            orders[col] = 0.0
    lines = orders.groupby(
        ["order_id", "sku_id", "sku_name", "unit_price_gross"], as_index=False, dropna=False
    ).agg(quantity=("quantity", "sum"), seller_subsidy=("seller_subsidy", "sum"),
          shopee_subsidy=("shopee_subsidy", "sum"))
    sku_level = income_orders.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(income_orders)} income orders exploded to {len(sku_level)} SKU lines")
    return sku_level


def compute_sku_columns_shopee(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The Shopee yellow columns (cell evidence in SHOPEE_FORMULA_STATUS)."""
    df = sku_level.copy()
    # Default-plus-exceptions VAT, same model as TikTok/Lazada (masters.py).
    from .masters import vat_factor_for
    df["vat_factor"] = vat_factor_for(df["sku_id"], settings, settings.get("_vat_sku") or {})  # AA

    qty = df["quantity"].fillna(0)
    df["gross_rev"] = df["unit_price_gross"].fillna(0) * qty                       # S = Q*R
    df["net_after_discount"] = df["gross_rev"] - df["seller_subsidy"].fillna(0)    # T = (Q*R)-U
    df["total_discount"] = (df["seller_voucher"].fillna(0) + df["seller_ship_support"].fillna(0)
                            + df["seller_coin_cashback"].fillna(0) + df["cofund_voucher"].fillna(0))  # W
    df["order_gross_sale"] = df.groupby("order_id")["net_after_discount"].transform("sum")   # X
    # W lives on order level (first row per order in their sheet); the SUMIF
    # over C picks it up once per order — groupby "max" of the constant works
    # because our merge repeats the order-level value on every SKU row.
    df["discount_per_order"] = df.groupby("order_id")["total_discount"].transform("max")     # Y
    ratio = (df["net_after_discount"] / df["order_gross_sale"].replace(0, pd.NA)).fillna(0)
    df["discount_allocated"] = ratio * df["discount_per_order"]                    # Z = IFERROR((T/X)*Y,0)
    df["unit_price_pre_vat"] = ((df["net_after_discount"] + df["discount_allocated"])
                                / qty.replace(0, pd.NA)).fillna(0) / df["vat_factor"]        # AB
    df["amount_pre_vat"] = df["unit_price_pre_vat"] * qty                          # AD = AB*AC
    df["amount_with_vat"] = df["amount_pre_vat"] * df["vat_factor"]                # AE

    pending = [k for k, v in SHOPEE_FORMULA_STATUS.items() if v != "verified"]
    if pending:
        log.warn(f"Shopee formulas not yet row-verified: {pending}")
    return df


def build_return_lines(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee Return tab: WRITTEN (returned) lines with the negative-discount
    adjustment. PORT ZONE — placeholder posts the refund as a negative amount;
    the exact adjustment must come from the team's file."""
    written = income[income["status"] == STATUS_WRITTEN].copy()
    written["return_amount"] = -written["actual_refund"].fillna(0).abs()
    log.add(f"  return lines: {len(written)}")
    return written
