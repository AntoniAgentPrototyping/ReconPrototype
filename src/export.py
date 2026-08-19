"""exceptions.xlsx — one tab per exception type.

Reduced to this in M1. The finance-file writer that used to live here was the
scaffold-era layout, superseded by `src/finance_template.py` (which emits the
team's own invoicing-template shape) and reachable only from the deleted
`recon.py`.

**Wired in since M2.** `pipeline.write_artifacts` calls this whenever any
exception sheet is populated, so the rows the seam carries on
`RunResult.exceptions` reach a file instead of being computed and dropped
(docs/08-KNOWN-DEFECTS.md#110). This docstring claimed otherwise until
2026-08-19, and cited `tools/full_run.py`, which became `tools/devrun.py` in M6.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .runlog import RunLog



EXCEPTION_TABS = [
    ("unmatched_orders", "Unmatched Orders"),
    ("unknown_skus", "Unknown SKUs"),
    ("tieout_breaches", "Tie-out Breaches"),
    ("zero_revenue", "Zero Revenue"),
    # Added 2026-08-19 for defect 2.12. One row per store: settled money, how much of
    # it reached no SKU line in this window, and the share. The reconciling total has
    # been reported since M2 but only per WINDOW, and on the TikTok golden window the
    # whole ~21% is a single store — so a total cannot distinguish that window's
    # ordinary traffic from a store whose order export does not cover what it settles.
    ("order_coverage", "Order Coverage"),
]


def _tab(df: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    out = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out



def write_exceptions_file(path: Path, exceptions: dict[str, pd.DataFrame], log: RunLog,
                          *, write_to: Path | None = None) -> int:
    """One tab per exception type, always all four tabs (empty = nothing to look at).

    `write_to` overrides where the bytes land without changing what `path` names, so
    `pipeline.write_artifacts` can stage an atomic write. See `write_workbook` for
    why the temp path belongs to the caller.
    """
    total = 0
    with pd.ExcelWriter(write_to or path, engine="openpyxl") as xw:
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
