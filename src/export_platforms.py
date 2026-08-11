"""SUPERSEDED — NOT A DELIVERABLE FORMAT.

src/finance_template.py is the only export path (wired in tools/full_run.py
since Aug 2026): it produces the team's full invoicing-template shape
(PV sum / Summary / brand tabs / control block). This module's plain
Income-only workbooks were shipped for July by mistake and the team asked
for the template shape; keep this module only as reference for the original
column-layout evidence below. Do not wire it back into any run path.

Original docstring:
Finance-file exports matched to the team's real May invoicing files.

Layout evidence:
- TikTok : "Tiktok result * For KA.xlsx" sheet "Xuat HD bt" data region
           (header row 6) — single Income tab.
- Shopee : "shopee result Sample For KA *" — "Xuat HD bt" (Income) +
           "return" (Return tab: negative refunds, full-vs-partial split at
           |order total + refund| < 10 VND, 'return'!R7 evidence).
- Lazada : "Laz result KA used *" — one line tab per VAT rate (cols A..H of
           sheet "1.08") with whole-VND Price KA, plus a fee-bucket summary
           tab (Total file "SUM CP" layout).

Values come from the row-verified chains; the "non repeat" columns blank
order-level fields on 2nd+ SKU rows exactly like the team's sheets.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .runlog import RunLog


def _blank_repeats(df: pd.DataFrame, order_col: str, cols: list[str]) -> pd.DataFrame:
    """Blank order-level columns on repeated rows of the same order —
    the team's 'non repeat' convention."""
    dup = df.duplicated(subset=[order_col])
    for c in cols:
        df.loc[dup, c] = None
    return df


def finance_tiktok(sku_level: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Good rows -> Income tab in the 'Xuat HD bt' column layout."""
    df = sku_level.sort_values(["store", "order_id"]).copy()
    out = pd.DataFrame({
        "Month": pd.to_datetime(df["income_order_created_at"]).dt.month,
        "Source.Name": df["store"],
        "Mã đơn hàng": df["order_id"],
        "Source.Name non repeat": df["store"],
        "Mã đơn hàng non repeat": df["order_id"],
        "SKU phân loại hàng": df["sku_id"],
        "Tên sản phẩm": df["sku_name"],
        "VAT KA sử dụng": df["vat_factor"],
        "Đơn giá KA sử dụng trước VAT": df["unit_price_pre_vat"],
        "số lượng KA sử dụng trước VAT": df["quantity"],
        "Amount before VAT": df["amount_pre_vat"],
        "Check total": df["amount_with_vat"],
        "Số tiền hoàn trả cho Người mua (₫)": df["actual_refund"],
    })
    out = _blank_repeats(out, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])
    return {"Income": out}


def finance_shopee(sku_level: pd.DataFrame, return_tol_vnd: float = 10.0) -> dict[str, pd.DataFrame]:
    """ok rows -> Income tab; Return rows -> Return tab with negative refund
    and the 10-VND full/partial split ('return'!R7)."""
    df = sku_level.sort_values(["store", "order_id"]).copy()

    def base(sub: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "Diff": sub["check_status"],
            "Source.Name": sub["store"],
            "Mã đơn hàng": sub["order_id"],
            "Source.Name non repeat": sub["store"],
            "Mã đơn hàng non repeat": sub["order_id"],
            "Create_Order_Month": pd.to_datetime(sub["income_order_created_at"]).dt.month,
            "Finance_Month": pd.to_datetime(sub["statement_date"]).dt.month,
            "SKU phân loại hàng": sub["sku_id"],
            "Tên sản phẩm": sub["sku_name"],
            "VAT KA sử dụng": sub["vat_factor"],
            "Đơn giá KA sử dụng trước VAT": sub["unit_price_pre_vat"],
            "số lượng KA sử dụng trước VAT": sub["quantity"],
            "Cộng tiền hàng KA sử dụng trước VAT": sub["amount_pre_vat"],
            "Cộng tiền hàng KA sử dụng có VAT": sub["amount_with_vat"],
        })

    ok = df[df["check_status"] == "ok"]
    income = base(ok)
    income = _blank_repeats(income, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])

    ret = df[df["check_status"] == "Return"].copy()
    rtab = base(ret)
    rtab["Total by order"] = ret.groupby("order_id")["amount_with_vat"].transform("sum").values
    refund = ret["actual_refund"].fillna(0)
    rtab["Số tiền hoàn trả cho Người mua (₫)"] = np.where(refund > 0, -refund, refund)  # negative adjustment
    rtab["Check"] = rtab["Total by order"] + rtab["Số tiền hoàn trả cho Người mua (₫)"]
    rtab["Note"] = np.where(rtab["Check"].abs() < return_tol_vnd,
                            "Return full ko xuat HD", "Return 1 phan phai xuat HD")
    rtab = _blank_repeats(rtab, "Mã đơn hàng",
                          ["Source.Name non repeat", "Mã đơn hàng non repeat",
                           "Total by order", "Số tiền hoàn trả cho Người mua (₫)", "Check", "Note"])
    return {"Income": income, "Return": rtab}


def finance_lazada(revenue: pd.DataFrame, classified: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """One line tab per VAT rate ('1.08' layout, whole-VND Price KA) plus a
    'Fee buckets' tab in the Total file's 'SUM CP' shape."""
    tabs: dict[str, pd.DataFrame] = {}
    for rate, sub in revenue.sort_values(["store", "order_id"]).groupby("vat_rate"):
        tabs[f"{rate:.2f}".rstrip("0").rstrip(".")] = pd.DataFrame({
            "Source.Name": sub["store"],
            "Order No.": sub["order_id"],
            "Seller SKU": sub["sku_id"],
            "Details": sub["product_name"],
            "Sum of Price KA": sub["price_ka"],
            "Sum of Quantity": sub["quantity"],
            "Amount": sub["check_no_vat"],
            "Amount with VAT": sub["check_with_vat"],
        })
    buckets = (classified.pivot_table(index="store", columns="fee_bucket",
                                      values="amount_incl_vat", aggfunc="sum")
               .round(2).reset_index())
    tabs["Fee buckets"] = buckets
    return tabs


def write_finance(tabs: dict[str, pd.DataFrame], path: Path, log: RunLog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in tabs.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            log.add(f"  {path.name} · tab '{name}': {len(df)} rows")
