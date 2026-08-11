"""Lazada reconciliation path — a transaction LEDGER, not order+income.

Evidence (all from the team's own files, May 2026):
- Source = per-store ledger exports: Weekly files (sheet "Transaction
  Overview") or Daily files (sheet "Income Overview", different schema) —
  one row per (order item x fee event). There are NO order files.
  The Daily-format week (25th-end) is a PERMANENT monthly fixture, so
  dual-schema handling is standard, not an exception.
- The Total files' Power Query (DataMashup "Weekly"/"Daily"/"FR_Total")
  normalizes both variants to one table; FR_Total = Daily UNION Weekly.
- Per-row computed columns (FR_Total sheet, row 2 evidence):
    Fee Type   = Lib.xlsx fee-name -> bucket ("Item Price Credit" ->
                 "1.Doanh Thu", "Commission" -> "6.CP co Invoice", ...)
    Xuat HD    = bt/exp status via Lib
    VAT rate   = VAT_SKU.xlsx lookup by Seller SKU, default 1.08
                 (May master: 664 SKUs @ 1.08, 4 @ 1.05)
    Amount no VAT = Amount(Include Tax) / VAT rate
- Revenue = gross "Item Price Credit" lines (even free gifts credit full
  price); promotional charges are SEPARATE ledger lines in their own
  buckets — no discount allocation ever touches revenue lines.
- Refunds: NO credit notes (confirmed by the team) — refund/reversal fee
  lines net into final sales through the Lib bucket mapping, which is
  exactly what this module does. Nothing further to build there.
- Invoicing pivots group revenue lines per (store, order, Seller SKU):
  Price KA (pre-VAT) + quantity, split by VAT bucket, cross-checked against
  the sale report with |diff| < 1000 (1.05/1.08) or < 2000 (1.10).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .errors import ReconHardStop
from .ingest import to_number
from .runlog import RunLog

# Verified 2026-07-17 by tools/calc_verify_lazada.py against the Total files'
# "PV used" pivots and "SUM CP" bucket totals — Curel (cleanest) and
# "Unilever 2" Chăm Sóc Vẻ Đẹp (refund-heaviest), windows 11-17T5 (Weekly
# schema) AND 25-31T5 (Daily schema): 1,306 revenue lines + all fee buckets
# exact. Caveats: the 4 SKUs at VAT 1.05 did not trade in the verified
# windows (non-1.08 unexercised); Lib/VAT_SKU config CSVs are point-in-time
# ports of the team's masters and must be refreshed when those change.
LAZADA_FORMULA_STATUS = {
    "ledger_union_weekly_daily": "verified",
    "fee_bucket_classification": "verified",   # Lib.xlsx port, 0 unmapped in May
    "vat_rate_per_sku": "verified-1.08-only",  # VAT_SKU port; 1.05 SKUs didn't trade
    "amount_no_vat": "verified",               # amount / rate
    "price_ka_unit_promo_netted": "verified",  # round((credits+promo)/units/VAT)
    "check_totals": "verified",                # E*F and E*F*VAT vs 'PV used'
}

REVENUE_BUCKET = "1.Doanh Thu"
# Promotional buckets whose per-(order, SKU) charges net INTO the invoiced
# unit price (evidence: gift lines credit full price, their Flexi-Combo
# chargeback zeroes them in "PV used"; voucher/coin lines reduce the paired
# product's Price KA by exactly their allocated amount / VAT).
PROMO_BUCKETS = [
    "2.Promotional Charges Flexi-Combo",
    "3.Promotional Charges Vouchers",
    "3.1 Seller Funded Marketing Voucher",
    "4.1 LazCoints Discount",
    "5. Lazcoin discount",
]

# Canonical ledger columns (superset both export variants map into):
LEDGER_REQUIRED = ["store", "transaction_date", "fee_name", "amount_incl_vat",
                   "vat_amount", "order_id", "order_line_id", "sku_id"]

WEEKLY_MAP = {  # sheet "Transaction Overview", header row 1
    "Transaction Date": "transaction_date",
    "Fee Name": "fee_name",
    "Details": "product_name",
    "Seller SKU": "sku_id",
    "Lazada SKU": "lazada_sku",
    "Amount": "amount_incl_vat",
    "VAT in Amount": "vat_amount",
    "Order No.": "order_id",
    "Order Item No.": "order_line_id",
}
DAILY_MAP = {  # sheet "Income Overview" (the 25-31T05 window uses these)
    "Transaction Date": "transaction_date",
    "Fee Name": "fee_name",
    "Product Name": "product_name",
    "Seller SKU": "sku_id",
    "Lazada SKU": "lazada_sku",
    "Amount(Include Tax)": "amount_incl_vat",
    "VAT Amount": "vat_amount",
    "Order Number": "order_id",
    "Order Line ID": "order_line_id",
}
SHEETS = {"weekly": "Transaction Overview", "daily": "Income Overview"}

STORE_PATTERN = r"^\s*\d+_\s*(.+?)\s*\.xlsx$"  # "15_Masan.xlsx" -> "Masan"


def _read_ledger_file(f: Path, variant: str, log: RunLog) -> pd.DataFrame:
    cmap = WEEKLY_MAP if variant == "weekly" else DAILY_MAP
    df = pd.read_excel(f, dtype=str, sheet_name=SHEETS[variant])
    df.columns = [str(c).strip() for c in df.columns]
    missing = [src for src in cmap if src not in df.columns]
    df = df.rename(columns={s: d for s, d in cmap.items() if s in df.columns})
    m = re.match(STORE_PATTERN, f.name, flags=re.IGNORECASE)
    if not m:
        raise ReconHardStop(f"Cannot derive store from Lazada file name '{f.name}'")
    df["store"] = m.group(1).strip()
    df["source_file"] = f.name
    df["ledger_variant"] = variant
    log.add(f"  {f.name} [{variant}]: {len(df)} rows"
            + (f" (headers not found: {missing})" if missing else ""))
    return df


def read_ledger(period_dir: Path, settings: dict, log: RunLog) -> pd.DataFrame:
    """Read a window's Weekly/ and Daily/ folders (either may be empty) —
    the union mirrors the team's FR_Total query."""
    frames = []
    for variant in ("weekly", "daily"):
        folder = period_dir / variant.capitalize()
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.xlsx")):
            frames.append(_read_ledger_file(f, variant, log))
    if not frames:
        raise ReconHardStop(f"No Weekly/ or Daily/ ledger files under {period_dir}")
    df = pd.concat(frames, ignore_index=True)

    missing = [c for c in LEDGER_REQUIRED if c not in df.columns]
    if missing:
        raise ReconHardStop(f"Lazada ledger missing canonical columns: {missing}")
    style = settings.get("number_style", "standard")
    for col in ("amount_incl_vat", "vat_amount"):
        df[col] = to_number(df[col], style)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    for col in ("order_id", "order_line_id", "sku_id", "fee_name", "store"):
        df[col] = df[col].astype(str).str.strip()
    log.add(f"  ledger: {len(df)} rows, {df['store'].nunique()} stores, "
            f"{df['order_id'].nunique()} orders")
    return df


def load_fee_type_map(config_dir: Path, log: RunLog, settings: dict | None = None) -> dict[str, dict]:
    """Fee name -> {bucket, status}. Reads the team-owned live master
    ("Lib & VAT rate.xlsb") when present, CSV snapshot otherwise — see
    src/masters.py. Unmapped fee names go to exceptions, never dropped."""
    from .masters import load_masters
    m = load_masters(config_dir, settings or {}, log)
    if not m["fee_types"]:
        raise ReconHardStop("No fee-type mapping available (master and snapshot both missing)")
    return m["fee_types"]


def load_vat_sku(config_dir: Path, log: RunLog, settings: dict | None = None) -> dict[str, float]:
    """SKU -> VAT factor from the live master / CSV snapshot (masters.py)."""
    from .masters import load_masters
    m = load_masters(config_dir, settings or {}, log)
    if not m["vat_sku"]:
        raise ReconHardStop("No VAT_SKU mapping available (master and snapshot both missing)")
    return m["vat_sku"]


def classify_ledger(df: pd.DataFrame, fee_types: dict, vat_sku: dict[str, float],
                    settings: dict, log: RunLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-row computed columns exactly as FR_Total: fee bucket + status via
    the Lib port, VAT rate via the VAT_SKU port, amount_no_vat = amount/rate.
    Returns (ledger with columns, unmapped-fee exception rows)."""
    df = df.copy()
    df["fee_bucket"] = df["fee_name"].map(lambda f: (fee_types.get(f) or {}).get("bucket"))
    df["xuat_hd"] = df["fee_name"].map(lambda f: (fee_types.get(f) or {}).get("status"))
    unmapped = df[df["fee_bucket"].isna()]
    if len(unmapped):
        log.warn(f"{len(unmapped)} ledger rows with fee names missing from the "
                 f"Lib port ({sorted(unmapped['fee_name'].unique())[:5]}) -> exceptions")

    default = float((settings.get("vat_factors") or {}).get("default", 1.08))
    df["vat_rate"] = df["sku_id"].map(vat_sku).fillna(default)
    df["amount_no_vat"] = df["amount_incl_vat"].fillna(0) / df["vat_rate"]

    for bucket, n in df["fee_bucket"].value_counts(dropna=False).head(8).items():
        log.add(f"  bucket {bucket}: {n} rows")
    return df, unmapped


def revenue_lines(df: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Invoicing base: revenue-bucket rows grouped per (store, order, SKU,
    product) — Price KA = summed pre-VAT amount, quantity = row count
    (one Item Price Credit row per unit; FR_Total Quantity = 1 per row)."""
    rev = df[df["fee_bucket"] == REVENUE_BUCKET]
    grouped = rev.groupby(["store", "order_id", "sku_id", "product_name"], as_index=False, dropna=False).agg(
        quantity=("order_line_id", "count"),
        credits=("amount_incl_vat", "sum"),
        vat_rate=("vat_rate", "max"),
    )
    # Promo pairing includes PRODUCT NAME: the same SKU can appear in one
    # order under two Details (e.g. a 35,000 CHIN-SU unit AND a 1,000,000
    # gift variant, Masan order 524944050276659) — pairing by (order, SKU)
    # alone double-applies the promo pool to both name-groups. Matching by
    # name reproduces the team's KA values exactly (gift groups zero out).
    promo_rows = df[df["fee_bucket"].isin(PROMO_BUCKETS)]
    promo = (promo_rows
             .groupby(["store", "order_id", "sku_id", "product_name"], as_index=False)["amount_incl_vat"].sum()
             .rename(columns={"amount_incl_vat": "promo"}))
    grouped = grouped.merge(promo, on=["store", "order_id", "sku_id", "product_name"], how="left")
    grouped["promo"] = grouped["promo"].fillna(0)
    matched_total = float(grouped["promo"].sum())
    promo_total = float(promo_rows["amount_incl_vat"].fillna(0).sum())
    if abs(promo_total - matched_total) > 0.5:
        log.warn(f"promo charges not matched to any revenue line: "
                 f"{promo_total - matched_total:,.0f} VND (left un-netted, as the team's pairing does)")

    # Team's "Price KA" = per-UNIT net price, rounded to whole VND (Excel
    # ROUND, half away from zero): (credits + promo) / units / VAT.
    # Evidence: gift line 1,000,000 credit - 1,000,000 Flexi-Combo -> 0;
    # Curel 2-unit line 500,000/2/1.08 -> 231,481; Unilever real product
    # (514,000 - 29,970)/1.08 -> 448,176 — all exact vs "PV used".
    x = ((grouped["credits"] + grouped["promo"])
         / grouped["quantity"].replace(0, pd.NA)).fillna(0) / grouped["vat_rate"]
    grouped["price_ka"] = np.where(x >= 0, np.floor(x + 0.5), np.ceil(x - 0.5))
    grouped["check_no_vat"] = grouped["price_ka"] * grouped["quantity"]
    grouped["check_with_vat"] = grouped["check_no_vat"] * grouped["vat_rate"]
    log.add(f"  revenue lines: {len(rev)} ledger rows -> {len(grouped)} (order x SKU) lines")
    return grouped
