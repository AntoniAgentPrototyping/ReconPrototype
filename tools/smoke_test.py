"""Does this machine work? End-to-end on synthetic data, no client data needed.

    python tools/smoke_test.py

Exercises the real production path — `pipeline.run()` then `write_artifacts()`
— on a Lazada window generated here in a temp directory. Lazada is the pilot
because it is self-contained: a single workbook per store, no separate order
file, so a believable input is a few dozen rows rather than a fixture library.

What a pass actually proves: the config parses, Excel reading works (this is
where a missing calamine or a broken openpyxl shows up), classification and the
money math run, a 12-tab workbook is built and written, and the metrics
instrumentation reports a non-zero memory reading.

Rewritten in M1. It used to drive `recon.py`, the legacy sample path, whose
calculations were unverified placeholders — so the old smoke test proved the
machine could run code that production never called. This one runs the code
production runs.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src import pipeline  # noqa: E402
from src.pipeline import RunContext, RunStatus  # noqa: E402
from src.runlog import RunLog  # noqa: E402

PERIOD = "2026-05_smoke"
STORE = "SmokeStore"

# Column names below are WEEKLY_MAP's (src/lazada.py) — the "Transaction
# Overview" sheet. The Daily schema spells the same fields differently.


def _fee_names() -> tuple[str, str]:
    """A revenue fee and a cost fee, read from the committed CSV snapshot.

    Not hardcoded: a fee name absent from the master lands in the unmapped
    exception frame and leaves the revenue tabs empty — a green run that proved
    nothing. Sourcing them from the master means the smoke test cannot drift
    away from it.
    """
    import csv
    path = ROOT / "config" / "lazada_fee_types.csv"
    revenue = cost = None
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            bucket = row["bucket"].strip()
            if bucket.startswith("1.") and revenue is None:
                revenue = row["fee_name"].strip()
            elif bucket.startswith("6.") and cost is None:
                cost = row["fee_name"].strip()
    if not revenue:
        raise SystemExit("no revenue-bucket fee in config/lazada_fee_types.csv")
    return revenue, cost or revenue


def build_window(root: Path) -> None:
    """Write one synthetic Lazada weekly ledger in the real export shape."""
    revenue_fee, cost_fee = _fee_names()
    folder = root / "input" / PERIOD / "lazada" / "Weekly"
    folder.mkdir(parents=True, exist_ok=True)

    rows = []
    for i in range(1, 13):
        order = f"SMOKE-{i:04d}"
        # Two SKU lines of revenue per order, plus a platform fee.
        for sku in ("SKU-A", "SKU-B"):
            rows.append({
                "Transaction Date": f"2026-05-{(i % 28) + 1:02d}",
                "Fee Name": revenue_fee,
                "Details": f"Smoke product {sku}",
                "Seller SKU": sku,
                "Lazada SKU": f"LZD-{sku}",
                "Amount": f"{108000 + i * 1000}",
                "VAT in Amount": f"{8000 + i * 74}",
                "Order No.": order,
                "Order Item No.": f"{order}-{sku}",
                "Paid Quantity": "1",
            })
        rows.append({
            "Transaction Date": f"2026-05-{(i % 28) + 1:02d}",
            "Fee Name": cost_fee,
            "Details": "",
            "Seller SKU": "",
            "Lazada SKU": "",
            "Amount": f"-{5000 + i * 100}",
            "VAT in Amount": f"-{370 + i * 7}",
            "Order No.": order,
            "Order Item No.": "",
            "Paid Quantity": "",
        })

    # Filename carries the store identity — "<n>_<store>.xlsx" (lazada.py:99).
    target = folder / f"1_{STORE}.xlsx"
    with pd.ExcelWriter(target, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="Transaction Overview", index=False)


def check(label: str, ok: bool) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="recon-smoke-"))
    print(f"scratch: {tmp}")
    try:
        build_window(tmp)

        from tools.devrun import build_context  # noqa: PLC0415
        log = RunLog()
        base = build_context("lazada", PERIOD, log=log, root=ROOT)
        ctx = RunContext(platform="lazada", period=PERIOD,
                         input_root=tmp / "input", output_root=tmp / "output",
                         config_dir=ROOT / "config", settings=base.settings,
                         log=log, refs={})

        result = pipeline.run(ctx)
        written = pipeline.write_artifacts(result) if result.error is None else []

        results = [
            check("run() completed without an exception", result.error is None),
            check("run() returned a workbook in memory", result.workbook is not None),
            check("run() itself wrote nothing", not (tmp / "output").exists()
                  or result.workbook_path in written),
            check("finance_file.xlsx written by write_artifacts",
                  result.workbook_path.is_file()),
            check("run_log.txt written", (ctx.output_dir / "run_log.txt").is_file()),
            check("run_metrics.json written", (ctx.output_dir / "run_metrics.json").is_file()),
            check("12 tabs in the workbook",
                  result.workbook is not None and len(result.workbook.sheetnames) == 12),
            check("template control blocks produced verdicts", len(result.checks) > 0),
            check("revenue lines reached the SKU level",
                  len(result.frames.get("revenue", ())) > 0),
            check("no unmapped fee names", len(result.frames.get("unmapped", ())) == 0),
            check("status is UNVERIFIED (no team refs supplied)",
                  result.status is RunStatus.UNVERIFIED),
            check("peak RSS was actually measured", result.metrics.peak_rss_mb > 0),
            check("io and compute were both accounted",
                  result.metrics.io_s > 0 and result.metrics.compute_s > 0),
        ]
        if result.error is not None:
            print(f"\n  error was: {type(result.error).__name__}: {result.error}")

        print(f"\n{sum(results)}/{len(results)} checks passed")
        return 0 if all(results) else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
