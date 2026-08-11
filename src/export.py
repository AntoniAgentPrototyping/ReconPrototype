"""Stage 6 — Export.

finance_file.xlsx in the layout finance books from (TikTok: one Income tab;
Shopee: Income + Return tabs), exceptions.xlsx with one tab per exception
type, run_log.txt as the audit trail.

PORT ZONE (layout): the exact column order/labels of the finance file must be
matched to the team's real finance file once a sample arrives. The canonical
layout below is a stand-in.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .runlog import RunLog

FINANCE_INCOME_COLUMNS = {
    "order_id": "Order ID",
    "order_created_at": "Order Created",
    "store": "Store",
    "brand": "Brand",
    "invoice_group": "Invoice Group",
    "sku_id": "SKU ID",
    "sku_name": "SKU Name",
    "quantity": "Quantity",
    "gross_revenue_sku": "Gross Revenue",
    "discount_sku": "Discount",
    "net_pre_vat_sku": "Net Revenue (pre-VAT)",
    "vat_sku": "VAT",
    "net_revenue_sku": "Net Revenue",
}

FINANCE_RETURN_COLUMNS = {
    "order_id": "Order ID",
    "order_created_at": "Order Created",
    "store": "Store",
    "brand": "Brand",
    "invoice_group": "Invoice Group",
    "actual_refund": "Actual Refund",
    "return_amount": "Return Amount (negative adjustment)",
}

EXCEPTION_TABS = [
    ("unmatched_orders", "Unmatched Orders"),
    ("unknown_skus", "Unknown SKUs"),
    ("tieout_breaches", "Tie-out Breaches"),
    ("zero_revenue", "Zero Revenue"),
]


def _tab(df: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    out = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


def write_finance_file(
    path: Path, platform: str, sku_level: pd.DataFrame, returns: pd.DataFrame, log: RunLog
) -> tuple[float, float]:
    """Returns (income tab total, return tab total) for tie-out Check 3."""
    income_tab = _tab(sku_level, FINANCE_INCOME_COLUMNS)
    income_total = float(sku_level["net_revenue_sku"].sum()) if len(sku_level) else 0.0
    return_total = 0.0

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        income_tab.to_excel(xw, sheet_name="Income", index=False)
        if platform == "shopee":
            _tab(returns, FINANCE_RETURN_COLUMNS).to_excel(xw, sheet_name="Return", index=False)
            return_total = float(returns["return_amount"].sum()) if len(returns) else 0.0

    log.add(f"  {path.name}: Income tab {len(income_tab)} rows (total {income_total:,.2f})"
            + (f", Return tab {len(returns)} rows (total {return_total:,.2f})" if platform == "shopee" else ""))
    return income_total, return_total


def write_exceptions_file(path: Path, exceptions: dict[str, pd.DataFrame], log: RunLog) -> int:
    """One tab per exception type, always all four tabs (empty = nothing to look at)."""
    total = 0
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for key, sheet in EXCEPTION_TABS:
            df = exceptions.get(key, pd.DataFrame())
            if df.empty and not len(df.columns):
                df = pd.DataFrame({"(no rows)": []})
            out = df.copy()
            for col in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            out.to_excel(xw, sheet_name=sheet, index=False)
            total += len(df)
            log.add(f"  exceptions - {sheet}: {len(df)} row(s)")
    return total
