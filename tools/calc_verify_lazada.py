"""Row-level verification of the Lazada ledger port against the team's
Total files: 'PV used' per-line pivot (Price KA / Quantity / VAT checks)
and 'SUM CP' fee-bucket totals.

Usage:
    python tools/calc_verify_lazada.py --team-dir <dir> --store "Curel" --slug curel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, lazada  # noqa: E402
from src.runlog import RunLog  # noqa: E402

WINDOW_PERIODS = {"L3": "2026-05_l3", "L5": "2026-05_l5"}

# 'SUM CP' rows for the verified stores, read from the Total files
# (Total Lazada 26_11T5 to 17T5 / 26_25T5 to 31T5, sheet "SUM CP"):
# buckets: 1.Doanh Thu, 2.Flexi-Combo, 3.Vouchers, 4.1 LazCoins, 7.CP no Inv, 6.CP co Inv
EXPECTED_SUM_CP = {
    ("L3", "curel"): {"1.Doanh Thu": 3_541_000, "3.Promotional Charges Vouchers": -23_250,
                      "7.CP no Invoice": -14_500, "6.CP co Invoice": -804_071},
    ("L3", "unilever2"): {"1.Doanh Thu": 397_616_030, "2.Promotional Charges Flexi-Combo": -304_000_000,
                          "3.Promotional Charges Vouchers": -3_580_870, "4.1 LazCoints Discount": -708_370,
                          "7.CP no Invoice": 1_033_800, "6.CP co Invoice": -23_995_064},
    ("L5", "curel"): {"1.Doanh Thu": 3_265_000, "3.Promotional Charges Vouchers": -19_590,
                      "6.CP co Invoice": -835_812},
    ("L5", "unilever2"): {"1.Doanh Thu": 468_549_490, "2.Promotional Charges Flexi-Combo": -344_000_000,
                          "3.Promotional Charges Vouchers": -6_373_146, "4.1 LazCoints Discount": -921_956,
                          "7.CP no Invoice": 2_212_284, "6.CP co Invoice": -42_888_498},
}


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    return pd.DataFrame({
        "order_id": df[cols[1]].astype(str).str.strip(),
        "sku_id": df[cols[2]].astype(str).str.strip(),
        "product_name": df[cols[3]].astype(str).str.strip(),
        "team_price_ka": pd.to_numeric(df[cols[4]], errors="coerce"),
        "team_quantity": pd.to_numeric(df[cols[5]], errors="coerce"),
        "team_check_with_vat": pd.to_numeric(df[cols[6]], errors="coerce"),
        "team_check_no_vat": pd.to_numeric(df[cols[7]], errors="coerce"),
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", required=True, help="store token as derived from file names")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    fee_types = lazada.load_fee_type_map(ROOT / "config", log)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log)
    all_ok = True

    for window, period in WINDOW_PERIODS.items():
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
        ledger = ledger[ledger["store"].str.contains(args.store, case=False, regex=False)]
        print(f"  scoped: {len(ledger)} ledger rows")
        classified, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)

        exp = EXPECTED_SUM_CP.get((window, args.slug), {})
        ok_buckets = True
        for bucket, expected in exp.items():
            mine = float(classified.loc[classified["fee_bucket"] == bucket, "amount_incl_vat"].fillna(0).sum())
            match = abs(mine - expected) < 1
            ok_buckets &= match
            print(f"  bucket {bucket}: mine {mine:,.0f} vs team {expected:,.0f} "
                  f"{'MATCH' if match else 'MISMATCH'}")
        all_ok &= ok_buckets

        mine = lazada.revenue_lines(classified, settings, log)
        team = load_team(team_dir / f"laz_{window.lower()}_{args.slug}.csv")
        keys = ["order_id", "sku_id", "product_name"]
        merged = team.merge(mine, on=keys, how="outer", indicator=True)
        both = merged[merged["_merge"] == "both"]
        only_t, only_m = merged[merged["_merge"] == "left_only"], merged[merged["_merge"] == "right_only"]
        print(f"  row alignment: {len(both)} matched, {len(only_t)} only-team, {len(only_m)} only-mine")
        for _, r in pd.concat([only_t.head(4), only_m.head(4)]).iterrows():
            print(f"    unaligned ({r['_merge']}): {r['order_id']} / {r['sku_id']}")
        all_ok &= (len(only_t) == 0) and (len(only_m) == 0) and len(both) > 0

        for mine_col, team_col in [("price_ka", "team_price_ka"), ("quantity", "team_quantity"),
                                   ("check_with_vat", "team_check_with_vat"),
                                   ("check_no_vat", "team_check_no_vat")]:
            ok = ((both[team_col] - both[mine_col]).abs() < 0.01)
            print(f"  {mine_col}: {int(ok.sum())}/{len(both)} rows {'MATCH' if ok.all() else 'MISMATCH'}")
            for _, r in both[~ok].head(3).iterrows():
                print(f"      {r['order_id']}/{r['sku_id']}: team={r[team_col]} mine={r[mine_col]}")
            all_ok &= bool(ok.all())

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
