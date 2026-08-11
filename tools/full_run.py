"""Full-platform run: all stores in a window through the verified chain,
finance-file export, and grand/per-store total ties against team references.

Usage:
    python tools/full_run.py --platform tiktok --period 2026-05_w1 \
        --refs <refs json>            # from tools/extract refs scripts

Refs JSON shape:
    {"per_store": {"<normalized store>": {"metric": value, ...}},
     "grand": {"pre_vat": x, "with_vat": y, ...},
     "grand_tolerance": 2000}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, finance_template, ingest, lazada, tieout  # noqa: E402
from src.runlog import RunLog  # noqa: E402


def window_meta(dates: pd.Series) -> dict:
    """Team-style window labels derived from the data itself, e.g.
    settlements 2026-07-08..2026-07-14 -> label '08 to 14T07',
    period stamp '26_08 to 14T07', month stamp 'Laz 26T7'."""
    d = pd.to_datetime(dates, errors="coerce").dropna()
    if d.empty:
        return {"label": "", "period_label": "", "month_label": ""}
    lo, hi = d.min(), d.max()
    label = f"{lo:%d} to {hi:%d}T{hi:%m}"
    return {"label": label,
            "period_label": f"{hi:%y}_{label}",
            "month_label": f"Laz {hi:%y}T{hi.month}"}


def norm_store(name: str) -> str:
    """Shared normalization so team file labels ('income U food.xlsx',
    'Income.Masan part 1.xlsx') and pipeline store names compare equal."""
    s = unicodedata.normalize("NFC", str(name)).lower().strip()
    s = re.sub(r"^\s*\d+[._ ]*", "", s)
    s = re.sub(r"^(income|order)\b[. ]*", "", s)
    s = re.sub(r"\s+part\s*\d+", "", s)
    s = s.replace(".xlsx", "")
    s = re.sub(r"[._]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tie(per_store_mine: dict, refs: dict, log: RunLog) -> list[str]:
    variances = []
    # Team labels can split one store across several source files
    # ("Income.Masan part 1/2.xlsx") — SUM metrics when normalized keys collide.
    per_ref: dict[str, dict] = {}
    for k, v in (refs.get("per_store") or {}).items():
        acc = per_ref.setdefault(norm_store(k), {})
        for m, val in v.items():
            acc[m] = acc.get(m, 0.0) + float(val)
    for store, metrics in sorted(per_store_mine.items()):
        key = norm_store(store)
        ref = per_ref.get(key)
        if ref is None:
            # Team labels sometimes keep only the middle segment of long
            # underscore names ("Unilever Chăm Sóc Vẻ Đẹp" for
            # "..._Unilever 2.xlsx") — fall back to prefix matching.
            cands = [r for r in per_ref if len(r) >= 8 and (key.startswith(r) or r.startswith(key))]
            ref = per_ref[cands[0]] if len(cands) == 1 else None
        if ref is None:
            variances.append(f"{store}: no team reference found")
            continue
        for metric, mine in metrics.items():
            expected = ref.get(metric)
            if expected is None:
                continue
            diff = mine - float(expected)
            status = "TIES" if abs(diff) < 1 else f"VARIANCE {diff:+,.0f}"
            log.add(f"  {store} · {metric}: mine {mine:,.0f} vs team {float(expected):,.0f} -> {status}")
            if abs(diff) >= 1:
                variances.append(f"{store} {metric}: {diff:+,.0f}")
    return variances


def run_tiktok(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    d = ROOT / "input" / period / "tiktok"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "tiktok", "orders"),
                               "orders", settings, log, "tiktok")
    income = ingest.read_parts(d / "income", config.column_map(settings, "tiktok", "income"),
                               "income", settings, log, "tiktok")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    income = ingest.apply_settlement_bounds(income, period, settings, log)
    ingest.check_stores(income, "income", "tiktok", settings, log)
    cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    sku = calculate.explode_to_sku_tiktok(good, orders, log)
    sku = calculate.compute_sku_columns_tiktok(sku, settings, log)

    wb, checks = finance_template.build_tiktok(sku, settings, window_meta(sku["statement_date"]), log)
    finance_template.write_workbook(wb, ROOT / "output" / period / "tiktok" / "finance_file.xlsx",
                                    checks, log)
    tieout.run_checks_tiktok(sku, settings, log)

    per_store = {
        s: {"ok_good_settlement": float(g["net_revenue"].fillna(0).sum()),
            "ok_good_revenue": float(g["gross_revenue"].fillna(0).sum())}
        for s, g in good.groupby("store")
    }
    for s, g in cl[cl["final_status"] == classify.FINAL_TAKE_OUT].groupby("store"):
        per_store.setdefault(s, {})["takeout_settlement"] = float(g["net_revenue"].fillna(0).sum())
    # V1-style Total files pivot with Final_Status = All -> raw sums:
    for s, g in cl.groupby("store"):
        per_store.setdefault(s, {})["raw_settlement"] = float(g["net_revenue"].fillna(0).sum())
        per_store[s]["raw_revenue"] = float(g["gross_revenue"].fillna(0).sum())
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 1))
    for metric, mine in [("pre_vat", float(sku["amount_pre_vat"].sum())),
                         ("with_vat", float(sku["amount_with_vat"].sum()))]:
        if metric in grand:
            diff = mine - float(grand[metric])
            log.add(f"  GRAND {metric}: mine {mine:,.2f} vs team {float(grand[metric]):,.2f} "
                    f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
            if abs(diff) > tol:
                variances.append(f"GRAND {metric}: {diff:+,.0f}")
    return variances


def run_shopee(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    d = ROOT / "input" / period / "shopee"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "shopee", "orders"),
                               "orders", settings, log, "shopee")
    income = ingest.read_parts(d / "income", config.column_map(settings, "shopee", "income"),
                               "income", settings, log, "shopee")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    income = ingest.apply_settlement_bounds(income, period, settings, log)
    cl = classify.classify_shopee_income(income, log)
    sku = calculate.explode_to_sku_shopee(cl, orders, log)
    sku = calculate.compute_sku_columns_shopee(sku, settings, log)

    wb, checks = finance_template.build_shopee(sku, settings, window_meta(sku["statement_date"]), log)
    finance_template.write_workbook(wb, ROOT / "output" / period / "shopee" / "finance_file.xlsx",
                                    checks, log)

    ok = sku[sku["check_status"] == classify.SHOPEE_OK]
    per_store = {
        s: {"ok_pre_vat": float(g["amount_pre_vat"].sum()),
            "ok_with_vat": float(g["amount_with_vat"].sum())}
        for s, g in ok.groupby("store")
    }
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 2000))
    for metric, mine in [("pre_vat", float(ok["amount_pre_vat"].sum())),
                         ("with_vat", float(ok["amount_with_vat"].sum()))]:
        if metric in grand:
            diff = mine - float(grand[metric])
            log.add(f"  GRAND {metric}: mine {mine:,.2f} vs team {float(grand[metric]):,.2f} "
                    f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
            if abs(diff) > tol:
                variances.append(f"GRAND {metric}: {diff:+,.0f}")
    return variances


def run_lazada(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    fee_types = lazada.load_fee_type_map(ROOT / "config", log)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log)
    ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
    cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    rev = lazada.revenue_lines(cl, log)

    wb, checks = finance_template.build_lazada(rev, settings, window_meta(cl["transaction_date"]),
                                               log, classified=cl)
    finance_template.write_workbook(wb, ROOT / "output" / period / "lazada" / "finance_file.xlsx",
                                    checks, log)

    per_store = {}
    for (s, b), g in cl.groupby(["store", "fee_bucket"]):
        per_store.setdefault(s, {})[b] = float(g["amount_incl_vat"].fillna(0).sum())
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 1000))
    # The team's KA line sheets are per VAT rate — compare like for like.
    for rate_key, rate in [("pre_vat_105", 1.05), ("pre_vat", 1.08), ("pre_vat_110", 1.10)]:
        if rate_key not in grand or grand[rate_key] is None:
            continue
        mine = float(rev.loc[rev["vat_rate"] == rate, "check_no_vat"].sum())
        diff = mine - float(grand[rate_key])
        log.add(f"  GRAND pre_vat @{rate}: mine {mine:,.2f} vs team {float(grand[rate_key]):,.2f} "
                f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
        if abs(diff) > tol:
            variances.append(f"GRAND pre_vat @{rate}: {diff:+,.0f}")
    if len(unmapped):
        variances.append(f"{len(unmapped)} unmapped fee rows")
    return variances


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--period", required=True)
    ap.add_argument("--refs", default=None)
    args = ap.parse_args()
    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    from src.masters import load_masters
    settings["_vat_sku"] = load_masters(ROOT / "config", settings, log)["vat_sku"]
    refs = json.loads(Path(args.refs).read_text(encoding="utf-8")) if args.refs else {}

    log.section(f"FULL RUN {args.platform} {args.period}")
    fn = {"tiktok": run_tiktok, "shopee": run_shopee, "lazada": run_lazada}[args.platform]
    variances = fn(args.period, settings, refs, log)

    log.section("RESULT")
    if variances:
        log.add(f"  {len(variances)} variance(s):")
        for v in variances:
            log.add(f"    - {v}")
    else:
        log.add("  ALL TIES")
    (ROOT / "output" / args.period / args.platform).mkdir(parents=True, exist_ok=True)
    log.write(ROOT / "output" / args.period / args.platform / "run_log.txt")
    return 0 if not variances else 1


if __name__ == "__main__":
    sys.exit(main())
