"""Stage 5 — Tie-out checks.

Three automated checks. A breach is named in exceptions.xlsx with the
variance amount; the pipeline still completes — it flags, it doesn't hide.
"""

from __future__ import annotations

import pandas as pd

from .runlog import RunLog


def _check(name: str, expected: float, actual: float, tolerance: float) -> dict:
    variance = actual - expected
    return {
        "check": name,
        "expected": round(expected, 2),
        "actual": round(actual, 2),
        "variance": round(variance, 2),
        "tolerance": tolerance,
        "result": "PASS" if abs(variance) <= tolerance else "BREACH",
    }


def run_checks(
    income_ok: pd.DataFrame,
    sku_level: pd.DataFrame,
    finance_income_total: float,
    finance_return_total: float,
    settings: dict,
    log: RunLog,
) -> pd.DataFrame:
    tol = settings.get("tolerances") or {}
    exact = float(tol.get("exact_check_vnd", 1))
    split_tol = float(tol.get("split_rounding_vnd", 10000))

    pivot_total = float(income_ok["net_revenue"].fillna(0).sum())
    calc_total = float(sku_level["net_revenue_sku"].sum())

    # Check 2: each invoice split is rounded to whole VND (as the manual
    # brand-split files are) — the recombined sum may drift from the grand
    # total by rounding; the existing rule tolerates ≤ 10,000 VND.
    split_totals = sku_level.groupby("invoice_group")["net_revenue_sku"].sum().round(0)
    splits_recombined = float(split_totals.sum())

    finance_total = finance_income_total + finance_return_total
    return_total = float(finance_return_total)

    checks = [
        _check("1: Pivot-Income total == calculated total", pivot_total, calc_total, exact),
        _check("2: sum of brand splits == grand total", calc_total, splits_recombined, split_tol),
        _check("3: finance-file total == calculated total", calc_total + return_total, finance_total, exact),
    ]
    results = pd.DataFrame(checks)

    for _, row in results.iterrows():
        log.add(
            f"  Check {row['check']}: {row['result']} "
            f"(expected {row['expected']:,.2f}, actual {row['actual']:,.2f}, variance {row['variance']:,.2f})"
        )
    for group, total in split_totals.items():
        log.add(f"    split '{group}': {total:,.0f} VND")
    return results


def run_checks_tiktok(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The team's OWN three tolerance checks, ported from their TikTok
    invoicing workbook (formulas + tolerances in cell evidence below;
    tolerances configurable under tolerances.tiktok):

    - "PV sum"     ('PV sum'!E2/E3, tol 12,000): total pre-VAT amount summed
      per store equals the same amount summed per VAT bucket.
    - "Xuat HD bt" ('Xuat HD bt'!O5/P5, tol 2,000): line-level with-VAT total
      equals the VAT-bucket recombination (bucket pre-VAT total x factor).
    - "PV xuat HD" ('PV xuat HD'!H1/I1, tol 1,000): line-level pre-VAT total
      equals the SKU-pivot recombination.

    In their process each check compares a pivot against source rows after
    manual steps; a breach means a row was dropped/edited on one side.
    """
    tol = (settings.get("tolerances") or {}).get("tiktok") or {}

    by_store = float(sku_level.groupby("store")["amount_pre_vat"].sum().sum())
    by_vat_pre = sku_level.groupby("vat_factor")["amount_pre_vat"].sum()
    by_vat_total = float(by_vat_pre.sum())

    with_vat_lines = float(sku_level["amount_with_vat"].sum())
    with_vat_buckets = float((by_vat_pre * by_vat_pre.index).sum())

    pre_vat_lines = float(sku_level["amount_pre_vat"].sum())
    pre_vat_sku_pivot = float(
        sku_level.groupby(["store", "sku_id", "sku_name"], dropna=False)["amount_pre_vat"].sum().sum())

    checks = [
        _check("PV sum: pre-VAT per store == per VAT bucket",
               by_store, by_vat_total, float(tol.get("pv_sum_vnd", 12000))),
        _check("Xuat HD bt: with-VAT lines == VAT-bucket recombination",
               with_vat_lines, with_vat_buckets, float(tol.get("xuat_hd_vnd", 2000))),
        _check("PV xuat HD: pre-VAT lines == SKU pivot",
               pre_vat_lines, pre_vat_sku_pivot, float(tol.get("pv_xuat_hd_vnd", 1000))),
    ]
    results = pd.DataFrame(checks)
    for _, row in results.iterrows():
        log.add(
            f"  Check {row['check']}: {row['result']} "
            f"(expected {row['expected']:,.2f}, actual {row['actual']:,.2f}, variance {row['variance']:,.2f})"
        )
    return results
