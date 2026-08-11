"""E-commerce reconciliation pipeline — Phase 1.

Usage:
    python recon.py --period 2026-06_p1 --platform tiktok

Reads  input/<period>/<platform>/{orders,income}/
Writes output/<period>/<platform>/{finance_file.xlsx, exceptions.xlsx, run_log.txt}
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src import calculate, classify, config, export, ingest, stitch, tieout
from src.classify import STATUS_OK, STATUS_ZERO
from src.errors import ReconHardStop
from src.runlog import RunLog

ROOT = Path(__file__).resolve().parent


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="E-commerce reconciliation pipeline (Phase 1)")
    p.add_argument("--period", required=True, help="Reconciliation period folder, e.g. 2026-06_p1")
    p.add_argument("--platform", required=True, choices=["tiktok", "shopee"],
                   help="Platform to reconcile (Lazada arrives after TikTok + Shopee are verified)")
    p.add_argument("--input-root", default=str(ROOT / "input"))
    p.add_argument("--output-root", default=str(ROOT / "output"))
    p.add_argument("--config-dir", default=str(ROOT / "config"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = RunLog()

    input_dir = Path(args.input_root) / args.period / args.platform
    output_dir = Path(args.output_root) / args.period / args.platform
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        config_dir = Path(args.config_dir)
        settings = config.load_settings(config_dir)
        brand_rules = config.load_brand_rules(config_dir)
        sku_master = config.load_sku_master(config_dir, log)

        log.section(f"STAGE 1 - INGEST - {args.platform} - {args.period}")
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, args.platform, "orders"),
                                   "orders", settings, log, args.platform)
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, args.platform, "income"),
                                   "income", settings, log, args.platform)
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        ingest.check_stores(orders, "orders", args.platform, settings, log)
        ingest.check_stores(income, "income", args.platform, settings, log)

        log.section("STAGE 2 - CROSS-PERIOD STITCH")
        income, unmatched = stitch.stitch(income, orders, log)

        log.section("STAGE 3 - CLASSIFY")
        income = classify.classify(income, brand_rules, log)

        log.section("STAGE 4 - CALCULATE")
        sku_level, unknown_skus = calculate.explode_to_sku(income, orders, sku_master, log)
        sku_level = calculate.compute_sku_columns(sku_level, float(settings.get("vat_rate", 0.08)), log)
        returns = calculate.build_return_lines(income, log)

        log.section("STAGE 6 - EXPORT (finance file first, so Check 3 ties against it)")
        finance_income_total, finance_return_total = export.write_finance_file(
            output_dir / "finance_file.xlsx", args.platform, sku_level, returns, log)

        log.section("STAGE 5 - TIE-OUT CHECKS")
        income_ok = income[income["status"] == STATUS_OK]
        checks = tieout.run_checks(income_ok, sku_level, finance_income_total, finance_return_total,
                                   settings, log)
        breaches = checks[checks["result"] == "BREACH"]

        exceptions = {
            "unmatched_orders": unmatched,
            "unknown_skus": unknown_skus,
            "tieout_breaches": breaches,
            "zero_revenue": income[income["status"] == STATUS_ZERO],
        }
        log.section("EXCEPTIONS")
        total_exceptions = export.write_exceptions_file(output_dir / "exceptions.xlsx", exceptions, log)

        log.section("SUMMARY")
        log.add(f"  finance file : {output_dir / 'finance_file.xlsx'}")
        log.add(f"  exceptions   : {output_dir / 'exceptions.xlsx'} ({total_exceptions} row(s))")
        log.add(f"  tie-out      : {len(checks) - len(breaches)}/{len(checks)} checks passed")
        log.write(output_dir / "run_log.txt")
        return 0

    except ReconHardStop as stop:
        log.section("HARD STOP — no finance file produced")
        log.add(str(stop))
        log.write(output_dir / "run_log.txt")
        return 1


if __name__ == "__main__":
    sys.exit(main())
