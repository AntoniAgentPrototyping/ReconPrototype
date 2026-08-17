"""exceptions.xlsx — one tab per exception type.

Reduced to this in M1. The finance-file writer that used to live here was the
scaffold-era layout, superseded by `src/finance_template.py` (which emits the
team's own invoicing-template shape) and reachable only from the deleted
`recon.py`.

**Not yet wired into production.** `tools/full_run.py` never calls this, so
exception rows are computed and dropped — see docs/08-KNOWN-DEFECTS.md. The
seam now carries them on `RunResult.exceptions`; connecting that to this writer
is an M2 task, because it adds an output file and therefore changes what a run
produces.
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
]


def _tab(df: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    out = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out



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
