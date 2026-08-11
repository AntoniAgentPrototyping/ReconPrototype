"""Stage 2 — Cross-period stitch.

Matches income-report lines back to their true order-creation date using the
order files (which include the prior-month re-pull in the same folder).
Income lines with no matching order line go to the exception report — never
silently dropped.
"""

from __future__ import annotations

import pandas as pd

from .runlog import RunLog


def stitch(income: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (income with order_created_at attached, unmatched income lines)."""
    order_dates = (
        orders.groupby("order_id", as_index=False)["order_created_at"].min()
    )
    merged = income.merge(order_dates, on="order_id", how="left")

    unmatched = merged[merged["order_created_at"].isna()].copy()
    matched = merged[merged["order_created_at"].notna()].copy()

    if len(matched):
        by_month = matched["order_created_at"].dt.to_period("M").value_counts().sort_index()
        for period, count in by_month.items():
            log.add(f"  income lines attributed to orders created in {period}: {count}")
    log.add(f"  matched: {len(matched)}; unmatched (-> exceptions): {len(unmatched)}")
    return matched, unmatched
