"""Stage-1-only probe: ingest + validate real files, print totals for manual
tie-out against the team's Total file. Runs NO other pipeline stage.

Usage:
    python tools/stage1_probe.py --period 2026-05_p1 --platform tiktok --expect-stores "U food"

--expect-stores overrides settings.expected_stores for subset runs (a partial
input set would otherwise hard-stop the full-platform store check).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, ingest  # noqa: E402
from src.errors import ReconHardStop  # noqa: E402
from src.runlog import RunLog  # noqa: E402


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stage 1 ingest probe (no downstream stages)")
    p.add_argument("--period", required=True)
    p.add_argument("--platform", required=True, choices=["tiktok", "shopee"])
    p.add_argument("--config-dir", default=str(ROOT / "config"))
    p.add_argument("--expect-stores", default=None,
                   help="Comma-separated store list for subset runs (overrides settings)")
    args = p.parse_args(argv)

    log = RunLog()
    settings = config.load_settings(Path(args.config_dir))
    if args.expect_stores is not None:
        stores = [s.strip() for s in args.expect_stores.split(",") if s.strip()]
        settings.setdefault("expected_stores", {})[args.platform] = stores
        log.add(f"(store check overridden for this probe: {stores})")

    input_dir = ROOT / "input" / args.period / args.platform
    try:
        log.section(f"STAGE 1 PROBE - {args.platform} - {args.period}")
        orders = ingest.read_parts(input_dir / "orders",
                                   config.column_map(settings, args.platform, "orders"),
                                   "orders", settings, log, args.platform)
        income = ingest.read_parts(input_dir / "income",
                                   config.column_map(settings, args.platform, "income"),
                                   "income", settings, log, args.platform)
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        ingest.check_stores(orders, "orders", args.platform, settings, log)
        ingest.check_stores(income, "income", args.platform, settings, log)
    except ReconHardStop as stop:
        log.section("HARD STOP")
        log.add(str(stop))
        return 1

    log.section("ORDERS")
    log.add(f"  rows (SKU lines): {len(orders)}")
    log.add(f"  distinct orders : {orders['order_id'].nunique()}")
    created = orders["order_created_at"]
    log.add(f"  created range   : {created.min()} .. {created.max()}  (unparseable: {int(created.isna().sum())})")
    if "order_status" in orders.columns:
        for status, n in orders["order_status"].value_counts().items():
            log.add(f"    status {status}: {n}")

    log.section("INCOME")
    log.add(f"  rows: {len(income)}")
    if "income_type" in income.columns:
        for t, n in income["income_type"].value_counts().items():
            log.add(f"    type {t}: {n}")
    for col, label in [("gross_revenue", "Total Revenue (gross_revenue)"),
                       ("net_revenue", "Total settlement (net_revenue)"),
                       ("actual_refund", "Customer refund (actual_refund)")]:
        s = income[col]
        log.add(f"  {label}: sum {fmt(s.fillna(0).sum())} | non-zero rows {int((s.fillna(0) != 0).sum())} | unparseable {int(s.isna().sum())}")
    st = income["statement_date"]
    log.add(f"  settled range: {st.min()} .. {st.max()}  (unparseable: {int(st.isna().sum())})")
    if "income_order_created_at" in income.columns:
        oc = income["income_order_created_at"]
        log.add(f"  order-created range (from income file): {oc.min()} .. {oc.max()}  (unparseable: {int(oc.isna().sum())})")

    ids_in_orders = set(orders["order_id"])
    matched = income["order_id"].isin(ids_in_orders)
    log.add(f"  income lines with matching order file line: {int(matched.sum())}/{len(income)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
