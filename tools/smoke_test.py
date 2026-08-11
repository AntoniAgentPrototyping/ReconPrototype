"""End-to-end smoke test on synthetic data.

Generates sample input, runs the pipeline for both platforms, and asserts
the outputs exist and contain what the baked-in anomalies predict.

Usage: python tools/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import make_sample_data  # noqa: E402
import recon  # noqa: E402

PERIOD = "2026-06_p1"


def check(label: str, ok: bool) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    make_sample_data.main()
    results: list[bool] = []

    for platform in ("tiktok", "shopee"):
        print(f"\n--- smoke: {platform} ---")
        rc = recon.main(["--period", PERIOD, "--platform", platform,
                         "--config-dir", str(ROOT / "tools" / "sample_config")])
        out = ROOT / "output" / PERIOD / platform
        results.append(check("pipeline exits 0", rc == 0))
        results.append(check("finance_file.xlsx written", (out / "finance_file.xlsx").exists()))
        results.append(check("run_log.txt written", (out / "run_log.txt").exists()))

        exc = pd.read_excel(out / "exceptions.xlsx", sheet_name=None)
        results.append(check("2 unmatched income lines flagged", len(exc["Unmatched Orders"]) == 2))
        results.append(check("unknown SKU flagged", (exc["Unknown SKUs"].get("sku_id") == "MYST-999").any()))
        results.append(check("zero-revenue lines flagged", len(exc["Zero Revenue"]) > 0))
        results.append(check("no tie-out breaches on clean data", len(exc["Tie-out Breaches"]) == 0))

        fin = pd.read_excel(out / "finance_file.xlsx", sheet_name=None)
        expected_tabs = ["Income"] + (["Return"] if platform == "shopee" else [])
        results.append(check(f"finance tabs are {expected_tabs}", list(fin) == expected_tabs))
        if platform == "shopee":
            results.append(check("Return tab has rows", len(fin["Return"]) > 0))
            neg = pd.to_numeric(fin["Return"]["Return Amount (negative adjustment)"], errors="coerce")
            results.append(check("return amounts are negative", bool((neg < 0).all())))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
