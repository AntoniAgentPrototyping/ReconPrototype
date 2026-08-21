"""Row-level verification of the ported Shopee chain against the team's
intermediary "Xuat HĐ" rows (extracted per store/window to CSV).

Usage:
    python tools/calc_verify_shopee.py --team-dir <dir> --store "Sanofi" --slug sanofi
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, ingest  # noqa: E402
from src.runlog import RunLog  # noqa: E402

WINDOW_PERIODS = {"S1": "2026-05_s1", "S2": "2026-05_s2", "S3": "2026-05_s3"}

# 0-based column indexes in the extracted 'Xuat HĐ' CSVs (header row 3):
TEAM_COLS = {
    "quantity": 17,            # R "Sum of Số lượng"
    "gross_rev": 18,           # S "Gross Rev"
    "net_after_discount": 19,  # T "net rev after discount"
    "seller_subsidy": 20,      # U "Sum of Người bán trợ giá"
    "total_discount": 22,      # W "Total discount" (first row per order)
    "order_gross_sale": 23,    # X "order gross sale per order"
    "discount_per_order": 24,  # Y "discount per order"
    "discount_allocated": 25,  # Z "Discount by product"
    "vat_factor": 26,          # AA "VAT KA sử dụng"
    "unit_price_pre_vat": 27,  # AB "Đơn giá KA sử dụng trước VAT"
    "amount_pre_vat": 29,      # AD "Cộng tiền hàng KA sử dụng trước VAT"
    "amount_with_vat": 30,     # AE (with VAT)
}
ORDER_LEVEL = {"total_discount"}  # blank on repeat rows in the team sheet


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    out = pd.DataFrame({
        "team_tag": df[cols[0]].astype(str).str.strip(),
        "order_id": df[cols[2]].astype(str).str.strip(),
        "sku_id": df[cols[14]].astype(str).str.strip(),
        "sku_name": df[cols[15]].astype(str).str.strip(),
    })
    for name, idx in TEAM_COLS.items():
        out[f"team_{name}"] = pd.to_numeric(df[cols[idx]], errors="coerce")
    return out


def compare(mine: pd.DataFrame, team: pd.DataFrame, window: str) -> bool:
    keys = ["order_id", "sku_id", "sku_name"]
    merged = team.merge(mine, on=keys, how="outer", indicator=True, suffixes=("", "_mine"))
    both = merged[merged["_merge"] == "both"]
    only_t, only_m = merged[merged["_merge"] == "left_only"], merged[merged["_merge"] == "right_only"]
    print(f"  [{window}] row alignment: {len(both)} matched, {len(only_t)} only-team, {len(only_m)} only-mine")
    for _, r in pd.concat([only_t.head(4), only_m.head(4)]).iterrows():
        print(f"    unaligned ({r['_merge']}): {r['order_id']} / {r['sku_id']}")

    ok_all = (len(only_t) == 0) and (len(only_m) == 0) and len(both) > 0

    tag_ok = (both["team_tag"] == both["check_status"])
    print(f"  [{window}] status tag: {int(tag_ok.sum())}/{len(both)} rows "
          f"{'MATCH' if tag_ok.all() else 'MISMATCH'}")
    for _, r in both[~tag_ok].head(4).iterrows():
        print(f"      {r['order_id']}: team={r['team_tag']} mine={r['check_status']}")
    ok_all &= bool(tag_ok.all())

    for name in TEAM_COLS:
        t, m = both[f"team_{name}"], both[name]
        if name in ORDER_LEVEL:
            t = both.groupby("order_id")[f"team_{name}"].transform("sum")
        ok = ((t - m).abs() < 0.01) | (t.isna() & m.isna()) | (t.isna() & (m == 0))
        print(f"  [{window}] {name}: {int(ok.sum())}/{len(both)} rows {'MATCH' if ok.all() else 'MISMATCH'}")
        for _, r in both[~ok].head(3).iterrows():
            tv = r[f"team_{name}"] if name not in ORDER_LEVEL else "(order-sum)"
            print(f"      {r['order_id']}/{r['sku_id']}: team={tv} mine={r[name]}")
        ok_all &= bool(ok.all())
    return ok_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--windows", default="S1,S2,S3")
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    log = RunLog()

    settings = config.load_settings(ROOT / "config")
    settings.setdefault("expected_stores", {})["shopee"] = []
    all_ok = True

    for window in [w.strip().upper() for w in args.windows.split(",") if w.strip()]:
        period = WINDOW_PERIODS[window]
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        input_dir = ROOT / "input" / period / "shopee"
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, "shopee", "orders"),
                                   "orders", settings, log, "shopee")
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, "shopee", "income"),
                                   "income", settings, log, "shopee")
        orders = ingest.derive_brand(orders, settings, log, "shopee")
        income = ingest.derive_brand(income, settings, log, "shopee")
        orders = orders[orders["store"] == args.store]
        income = income[income["store"] == args.store]
        print(f"  scoped to '{args.store}': {len(orders)} order rows, {len(income)} income rows")

        print("--- classification (derived Return / 0 dong / ok rules) ---")
        classified = classify.classify_shopee_income(income, log)

        print("--- SKU explode + yellow columns ---")
        sku_level = calculate.explode_to_sku_shopee(classified, orders, log)
        sku_level = calculate.compute_sku_columns_shopee(sku_level, settings, log)

        team = load_team(team_dir / f"int_{window.lower()}_{args.slug}.csv")
        all_ok &= compare(sku_level, team, window)

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
