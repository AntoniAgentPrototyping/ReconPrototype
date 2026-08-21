"""Row-level verification of the ported TikTok calculation chain (U food).

Runs stages 2-4 (stitch semantics, classification, SKU explode + yellow
columns) per settlement window and compares every ported formula column
against the team's own computed rows, extracted from:
  - intermediary "Tiktok result Sample T5 - * .xlsx" sheet "Xuat HĐ"
  - Total files' take-out pivot aggregates (passed as constants below)

Usage:
    python tools/calc_verify.py --team-dir <dir with extracted team CSVs> \
        [--store "U food"] [--team-csv-w1 ...] [--team-csv-w2 ...] \
        [--expected-json expected.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, ingest, tieout  # noqa: E402
from src.runlog import RunLog  # noqa: E402

# One reconciliation window = one period folder = one Total file, mirroring
# the team's process (input/<window>/tiktok mirrors "26_01 to 17T05" etc.).
WINDOW_PERIODS = {"W1": "2026-05_w1", "W2": "2026-05_w2"}

# Default aggregates = U food, read from the Total files' pivots:
#   V1/V2 "Pivot Income" (Final_Status = take out) U food rows,
#   V2 "Cross check with recon file" (Final_Status=OK, _Check_Status=Good).
# Override per store with --expected-json.
DEFAULT_EXPECTED = {
    "W1": {"takeout_settlement": -158_602},
    "W2": {"takeout_settlement": -471_486, "ok_good_settlement": 10_170_577,
           "ok_good_revenue": 12_540_000},
}

TEAM_COLS = {
    "quantity": 10,               # "Sum of Quantity" (K)
    "gross_rev": 11,              # "Gross Rev" (L)
    "net_after_seller_discount": 12,  # "net rev after discount" (M)
    "sku_seller_discount": 13,    # "Sum of SKU Seller Discount" (N)
    "order_gross_sale": 15,       # "order gross sale per order" (P)
    "vat_factor": 16,             # "VAT KA sử dụng" (Q)
    "unit_price_pre_vat": 17,     # "Đơn giá KA sử dụng trước VAT" (R)
    "amount_pre_vat": 19,         # "Cộng tiền hàng KA sử dụng trước VAT" (T)
    "amount_with_vat": 20,        # "Cộng tiền hàng KA sử dụng có VAT" (U)
    "order_revenue_check": 21,    # "Doanh thu by order" (V)
    "order_check_diff": 22,       # "check" (W)
}


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    out = pd.DataFrame({
        "order_id": df[cols[2]].astype(str).str.strip(),
        "sku_id": df[cols[7]].astype(str).str.strip(),
        "sku_name": df[cols[8]].astype(str).str.strip(),
        "unit_price_gross": pd.to_numeric(df[cols[9]], errors="coerce"),
    })
    for name, idx in TEAM_COLS.items():
        out[f"team_{name}"] = pd.to_numeric(df[cols[idx]], errors="coerce")
    return out


def compare(mine: pd.DataFrame, team: pd.DataFrame, window: str) -> dict[str, tuple[int, int]]:
    keys = ["order_id", "sku_id", "sku_name"]
    merged = team.merge(mine, on=keys, how="outer", indicator=True, suffixes=("", "_mine"))
    only_team = merged[merged["_merge"] == "left_only"]
    only_mine = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"]
    print(f"  [{window}] row alignment: {len(both)} matched keys, "
          f"{len(only_team)} only in team file, {len(only_mine)} only in pipeline")
    for _, r in only_team.head(5).iterrows():
        print(f"    only-team: {r['order_id']} / {r['sku_id']}")
    for _, r in only_mine.head(5).iterrows():
        print(f"    only-mine: {r['order_id']} / {r['sku_id']}")

    results: dict[str, tuple[int, int]] = {}
    for name in TEAM_COLS:
        t, m = both[f"team_{name}"], both[name]
        if name == "order_revenue_check":
            # The team writes V only on the first row per order (repeat rows
            # get 0 from SUMIF over the blank "non repeat" id column); the
            # pipeline carries it on every row. Compare per order: team rows
            # sum to the single real value, pipeline rows are constant.
            t = both.groupby("order_id")[f"team_{name}"].transform("sum")
        ok = ((t - m).abs() < 0.01) | (t.isna() & m.isna())
        results[name] = (int(ok.sum()), len(both))
        bad = both[~ok]
        flag = "MATCH" if ok.all() and len(both) else "MISMATCH"
        print(f"  [{window}] {name}: {int(ok.sum())}/{len(both)} rows {flag}")
        for _, r in bad.head(3).iterrows():
            print(f"      {r['order_id']}/{r['sku_id']}: team={r[f'team_{name}']} mine={r[name]}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", default="U food")
    ap.add_argument("--team-csv-w1", default="int_1_17_ufood.csv")
    ap.add_argument("--team-csv-w2", default="int_18_31_ufood.csv")
    ap.add_argument("--expected-json", default=None,
                    help="JSON {W1:{takeout_settlement,...}, W2:{...}} from the Total files' pivots")
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    team_csvs = {"W1": args.team_csv_w1, "W2": args.team_csv_w2}
    expected = DEFAULT_EXPECTED if args.expected_json is None else json.loads(
        Path(args.expected_json).read_text(encoding="utf-8"))
    log = RunLog()

    settings = config.load_settings(ROOT / "config")
    # Input folders may hold several stores' files; the store filter below
    # scopes the run, so skip the whole-platform store check here.
    settings.setdefault("expected_stores", {})["tiktok"] = []
    all_ok = True

    for window, period in WINDOW_PERIODS.items():
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        input_dir = ROOT / "input" / period / "tiktok"
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, "tiktok", "orders"),
                                   "orders", settings, log, "tiktok")
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, "tiktok", "income"),
                                   "income", settings, log, "tiktok")
        orders = ingest.derive_brand(orders, settings, log, "tiktok")
        income = ingest.derive_brand(income, settings, log, "tiktok")
        orders = orders[orders["store"] == args.store]
        income = income[income["store"] == args.store]
        print(f"  scoped to store '{args.store}': {len(orders)} order rows, {len(income)} income rows")

        print("--- stage 3: classification (M-code port) ---")
        classified = classify.classify_tiktok_income(income, log)

        takeout = classified[classified["final_status"] == classify.FINAL_TAKE_OUT]
        ok_good = classified[(classified["final_status"] == classify.FINAL_OK)
                             & (classified["check_status"] == classify.CHECK_GOOD)]
        uncl = classified[classified["final_status"] == classify.FINAL_UNCLASSIFIED]
        if len(uncl):
            print(f"  unclassified (reimbursements/adjustments, outside both team pivots): "
                  f"{len(uncl)} lines, settlement {uncl['net_revenue'].fillna(0).sum():,.0f}")
        exp = expected[window]
        to_sum = float(takeout["net_revenue"].fillna(0).sum())
        print(f"  take-out settlement: mine {to_sum:,.0f} vs team {exp['takeout_settlement']:,.0f} "
              f"{'MATCH' if abs(to_sum - exp['takeout_settlement']) < 1 else 'MISMATCH'}")
        all_ok &= abs(to_sum - exp["takeout_settlement"]) < 1
        if "ok_good_settlement" in exp:
            g_set = float(ok_good["net_revenue"].fillna(0).sum())
            g_rev = float(ok_good["gross_revenue"].fillna(0).sum())
            print(f"  OK/Good settlement: mine {g_set:,.0f} vs team {exp['ok_good_settlement']:,.0f} "
                  f"{'MATCH' if abs(g_set - exp['ok_good_settlement']) < 1 else 'MISMATCH'}")
            print(f"  OK/Good revenue   : mine {g_rev:,.0f} vs team {exp['ok_good_revenue']:,.0f} "
                  f"{'MATCH' if abs(g_rev - exp['ok_good_revenue']) < 1 else 'MISMATCH'}")
            all_ok &= abs(g_set - exp["ok_good_settlement"]) < 1
            all_ok &= abs(g_rev - exp["ok_good_revenue"]) < 1

        print("--- stage 4: SKU explode + yellow columns ---")
        sku_level = calculate.explode_to_sku_tiktok(ok_good, orders, log)
        sku_level = calculate.compute_sku_columns_tiktok(sku_level, settings, log)

        team = load_team(team_dir / team_csvs[window])
        results = compare(sku_level, team, window)
        all_ok &= all(n == total and total > 0 for n, total in results.values())

        print("--- stage 5 preview: team's own tie-out checks ---")
        breaches = tieout.run_checks_tiktok(sku_level, settings, log)
        all_ok &= bool((breaches["result"] == "PASS").all())

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
