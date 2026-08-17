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
    # Keyed on (store, order_id), not order_id alone. The left side is keyed by
    # store, so a plain order_id group takes min() ACROSS stores — one store's
    # income could then be dated by another store's order, which decides the
    # period the revenue lands in (docs/08-KNOWN-DEFECTS.md#15). Per-store input
    # folders hide this today; multi-tenant API pulls would remove that accident.
    # Measured on both May windows: 0 order_ids appear in more than one store,
    # so this is output-identical here and only bites the case it exists for.
    keys = ["store", "order_id"]
    order_dates = orders.groupby(keys, as_index=False)["order_created_at"].min()
    merged = income.merge(order_dates, on=keys, how="left")

    unmatched = merged[merged["order_created_at"].isna()].copy()
    matched = merged[merged["order_created_at"].notna()].copy()

    if len(matched):
        by_month = matched["order_created_at"].dt.to_period("M").value_counts().sort_index()
        for period, count in by_month.items():
            log.add(f"  income lines attributed to orders created in {period}: {count}")
    log.add(f"  matched: {len(matched)}; unmatched (-> exceptions): {len(unmatched)}")
    return matched, unmatched
