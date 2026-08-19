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
from .ingest import date_format as ingest_date_format
from .ingest import parse_dates, report_undated, report_unparseable, to_number
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

VARIANTS = ("weekly", "daily")


def column_map(settings: dict, variant: str) -> dict[str, str]:
    """The export's header spellings for one variant, from the contract.

    These were module constants (`WEEKLY_MAP` / `DAILY_MAP`) until 2026-08-18.
    They moved into `column_maps.lazada` so Lazada's rules are visible, versioned
    and editable like every other platform's — Lazada was the last part of the
    domain contract that could only be changed by editing Python
    (`docs/14-PRODUCTION-READINESS.md` D4). The spellings did not change.

    Absent is a hard stop, not a fallback to a copy kept here: two definitions of
    the same header map is exactly what this move removed, and a Lazada run under
    a contract that does not describe Lazada should say so rather than quietly use
    something else.
    """
    configured = ((settings.get("column_maps") or {}).get("lazada") or {}).get(variant)
    if not configured:
        raise ReconHardStop(
            f"column_maps.lazada.{variant} is not configured. Lazada's header map "
            f"moved out of src/lazada.py into the contract on 2026-08-18; a config "
            f"without it cannot read a {variant} ledger export.")
    return {str(k): str(v) for k, v in configured.items()}


def sheet_name(settings: dict, variant: str) -> str:
    """Which sheet holds the ledger. Weekly and Daily are different schemas, and
    the Daily-format week (25th-end) is a PERMANENT monthly fixture."""
    configured = ((settings.get("sheet_names") or {}).get("lazada") or {}).get(variant)
    if not configured:
        raise ReconHardStop(
            f"sheet_names.lazada.{variant} is not configured, so there is no sheet "
            f"to read a {variant} ledger export from.")
    return str(configured)


def store_pattern(settings: dict) -> str:
    """The regex whose group 1 is the storefront.

    Lazada exports carry no store column either, so this is the highest-consequence
    string in its half of the contract: a wrong capture reassigns a storefront's
    revenue.
    """
    configured = (settings.get("store_from_filename") or {}).get("lazada")
    if not configured:
        raise ReconHardStop(
            "store_from_filename.lazada is not configured, so no storefront can be "
            "derived from a Lazada export's file name.")
    return str(configured)


def _read_ledger_file(f: Path, variant: str, settings: dict, log: RunLog) -> pd.DataFrame:
    cmap = column_map(settings, variant)
    df = pd.read_excel(f, dtype=str, sheet_name=sheet_name(settings, variant))
    df.columns = [str(c).strip() for c in df.columns]
    missing = [src for src in cmap if src not in df.columns]
    df = df.rename(columns={s: d for s, d in cmap.items() if s in df.columns})
    m = re.match(store_pattern(settings), f.name, flags=re.IGNORECASE)
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
    for variant in VARIANTS:
        folder = period_dir / variant.capitalize()
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.xlsx")):
            frames.append(_read_ledger_file(f, variant, settings, log))
    if not frames:
        raise ReconHardStop(f"No Weekly/ or Daily/ ledger files under {period_dir}")
    df = pd.concat(frames, ignore_index=True)

    missing = [c for c in LEDGER_REQUIRED if c not in df.columns]
    if missing:
        raise ReconHardStop(f"Lazada ledger missing canonical columns: {missing}")
    style = settings.get("number_style", "standard")
    unparseable: dict[str, int] = {}
    for col in ("amount_incl_vat", "vat_amount"):
        df[col] = to_number(df[col], style)
        n_bad = int(df[col].isna().sum())
        if n_bad:
            unparseable[col] = n_bad
    # Same posture as TikTok/Shopee: an amount that cannot be read stops the
    # run rather than becoming 0 VND (docs/08-KNOWN-DEFECTS.md#16).
    report_unparseable(unparseable, "lazada/ledger", style, settings, log)
    # Lazada consumes no `dayfirst` (migration 009 records why: `read_ledger` never
    # passed one), so its format was decided by inference alone — the least defended
    # of the three platforms. Since 2026-08-19 it is stated: `date_formats.lazada`.
    #
    # **Parsed PER VARIANT, because the two do not agree.** Weekly spells it
    # `03-Jul-2026` and Daily spells it `29 Jul 2026` — measured over 66 weekly and
    # 14 daily files across May and July. One format applied to the concatenated
    # frame would silently NaT every row of whichever variant lost, and a window
    # that is entirely Daily (July's l5) is a real, recurring shape rather than a
    # corner case: the 25th-to-month-end week is Daily every month.
    parsed = df["transaction_date"].copy()
    undated: dict[str, tuple[int, str | None]] = {}
    for variant in sorted(set(df["ledger_variant"])):
        rows = df["ledger_variant"] == variant
        fmt = ingest_date_format(settings, "lazada", variant)
        raw = df.loc[rows, "transaction_date"]
        got = parse_dates(raw, False, f"lazada/{variant}",
                          "transaction_date", log, fmt=fmt)
        # Went in with text, came out NaT. A cell that was already blank is not an
        # unreadable date, which is why this is not a plain isna() count.
        n_bad = int((got.isna() & raw.notna()).sum())
        if n_bad:
            undated[variant] = (n_bad, fmt)
        parsed.loc[rows] = got
    df["transaction_date"] = pd.to_datetime(parsed, errors="coerce")
    # Reported PER VARIANT, under the format that actually parsed it. One collapsed
    # `lazada/ledger` line naming `dayfirst=False` sent the reader to a setting this
    # platform does not consume (migration 009 deliberately emits no `dayfirst.lazada`),
    # and it could not say which of the two disagreeing variants failed.
    for variant, (n_bad, fmt) in undated.items():
        report_undated({"transaction_date": n_bad}, f"lazada/{variant}", False,
                       settings, log, fmt=fmt)
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

    # Same default-plus-exceptions model as TikTok/Shopee, and the same
    # reporting: a SKU the master does not list is a fall-through, not a
    # confirmed standard rate (docs/08-KNOWN-DEFECTS.md#14).
    from .masters import resolve_vat_factors
    df["vat_rate"], _ = resolve_vat_factors(df["sku_id"], settings, vat_sku, log,
                                            label=" (lazada ledger)")
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
    # `dropna=False` matches the revenue side above. Without it a promo row with a
    # null product name (or SKU) left the pool entirely while its revenue-side
    # counterpart stayed — and because promo is a CREDIT that reduces the invoiced
    # unit price below, losing one makes `price_ka` too HIGH. An over-statement, in
    # the direction nothing else here can produce (docs/08-KNOWN-DEFECTS.md#110).
    #
    # Measured before the flag was added: **0 null `sku_id` and 0 null
    # `product_name` promo rows** across all nine staged Lazada windows (May
    # l1-l4, July l1-l5), so this moved no cell — the four Lazada goldens
    # regenerate unchanged. The divergence was latent, not active, and this closes
    # it while the direction of the error is known rather than after an export
    # arrives with a blank product name.
    promo = (promo_rows
             .groupby(["store", "order_id", "sku_id", "product_name"], as_index=False,
                      dropna=False)["amount_incl_vat"].sum()
             .rename(columns={"amount_incl_vat": "promo"}))
    grouped = grouped.merge(promo, on=["store", "order_id", "sku_id", "product_name"], how="left")
    grouped["promo"] = grouped["promo"].fillna(0)
    matched_total = float(grouped["promo"].sum())
    promo_total = float(promo_rows["amount_incl_vat"].fillna(0).sum())
    if abs(promo_total - matched_total) > 0.5:
        # Now that both sides handle null keys identically, this remainder is
        # genuinely un-paired promo — a charge against an (order, SKU, product) that
        # has no revenue line at all — and not a key the grouping threw away. The
        # message used to say "as the team's pairing does" for BOTH causes, which
        # made a dropped group and a real orphan read the same. Measured on real
        # data: July l2 -30,845 VND and l3 -22,486 VND with zero null keys, so the
        # orphan class is real and this is the wording for it.
        log.warn(f"promo charges paired to no revenue line: "
                 f"{promo_total - matched_total:,.0f} VND over "
                 f"{len(promo_rows):,} promo row(s) — left un-netted, as the team's "
                 f"pairing does. Not a null group key: those now group and merge "
                 f"like the revenue side.")

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
