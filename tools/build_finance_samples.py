"""Build template-shaped finance workbooks (src/finance_template.py) from a
window's staged inputs, for the team's review. Writes to
output/samples_for_nu/ with team-style file names. Does not replace the
finance_file.xlsx exports — wiring into the run happens after sign-off.

Usage:
    python tools/build_finance_samples.py --platform tiktok --period 2026-05_w1 \
        --label "01 to 17T5"
    python tools/build_finance_samples.py --platform lazada --period 2026-05_l2 \
        --label "26_04T5 to 10T5" --month "Laz 26T5"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, finance_template, ingest, lazada  # noqa: E402
from src.runlog import RunLog  # noqa: E402

OUT = ROOT / "output" / "samples_for_nu"


def sample_tiktok(period: str, settings: dict, meta: dict, log: RunLog,
                  vat_sku: dict | None = None) -> Path:
    d = ROOT / "input" / period / "tiktok"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "tiktok", "orders"),
                               "orders", settings, log, "tiktok")
    income = ingest.read_parts(d / "income", config.column_map(settings, "tiktok", "income"),
                               "income", settings, log, "tiktok")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    sku = calculate.explode_to_sku_tiktok(good, orders, log)
    sku = calculate.compute_sku_columns_tiktok(sku, settings, log, vat_sku)
    wb, checks = finance_template.build_tiktok(sku, settings, meta, log)
    path = OUT / f"Tiktok result {meta['label']} For KA.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def sample_shopee(period: str, settings: dict, meta: dict, log: RunLog,
                  vat_sku: dict | None = None) -> Path:
    d = ROOT / "input" / period / "shopee"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "shopee", "orders"),
                               "orders", settings, log, "shopee")
    income = ingest.read_parts(d / "income", config.column_map(settings, "shopee", "income"),
                               "income", settings, log, "shopee")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    cl = classify.classify_shopee_income(income, log)
    sku = calculate.explode_to_sku_shopee(cl, orders, log)
    sku = calculate.compute_sku_columns_shopee(sku, settings, log, vat_sku)
    wb, checks = finance_template.build_shopee(sku, settings, meta, log)
    path = OUT / f"shopee result For KA {meta['label']}.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def sample_lazada(period: str, settings: dict, meta: dict, log: RunLog,
                  vat_sku: dict | None = None) -> Path:
    # `vat_sku` is accepted and ignored: Lazada loads its own map below, and a
    # uniform signature keeps the dispatcher in main() from special-casing one
    # platform.
    fee_types = lazada.load_fee_type_map(ROOT / "config", log, settings)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log, settings)
    ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
    cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    if len(unmapped):
        log.warn(f"{len(unmapped)} unmapped fee rows are NOT in this sample")
    rev = lazada.revenue_lines(cl, settings, log)
    wb, checks = finance_template.build_lazada(rev, settings, meta, log)
    path = OUT / f"Laz result KA used {meta['label']}.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--period", required=True)
    ap.add_argument("--label", required=True, help="window label for the file name / stamp")
    ap.add_argument("--month", default="", help="Lazada month stamp, e.g. 'Laz 26T5'")
    args = ap.parse_args()

    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    from src.masters import load_masters
    vat_sku = load_masters(ROOT / "config", settings, log)["vat_sku"]
    meta = {"label": args.label, "period_label": args.label, "month_label": args.month}

    log.section(f"FINANCE TEMPLATE SAMPLE {args.platform} {args.period}")
    fn = {"tiktok": sample_tiktok, "shopee": sample_shopee, "lazada": sample_lazada}[args.platform]
    path = fn(args.period, settings, meta, log, vat_sku)
    log.add(f"  sample ready: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
