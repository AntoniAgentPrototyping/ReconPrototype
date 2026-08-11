"""Stage 1 — Ingest & validate.

Reads all file parts per platform (.xlsx and .csv), maps raw headers to
canonical names via settings.yaml column maps, dedupes across parts, and
enforces the team's store-count sanity check as a hard stop.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .errors import ReconHardStop
from .runlog import RunLog

REQUIRED_COLUMNS = {
    "orders": ["order_id", "sku_id", "sku_name", "quantity", "unit_price_gross", "order_created_at", "store"],
    "income": ["order_id", "store", "gross_revenue", "actual_refund", "net_revenue", "statement_date"],
}

NUMERIC_COLUMNS = {
    "orders": ["quantity", "unit_price_gross", "sku_seller_discount", "order_refund_amount",
               "sku_subtotal_after_discount", "seller_subsidy", "shopee_subsidy"],
    "income": ["gross_revenue", "actual_refund", "net_revenue", "subtotal_after_seller_discounts",
               "subtotal_before_discounts", "refund_subtotal_after_sd", "refund_subtotal_before_sd",
               "cofund_voucher", "seller_voucher", "seller_coin_cashback", "seller_ship_support",
               "shopee_product_subsidy"],
}

DATE_COLUMNS = {
    "orders": ["order_created_at"],
    "income": ["statement_date", "income_order_created_at"],
}


def apply_settlement_bounds(income: pd.DataFrame, period: str, settings: dict,
                            log: RunLog) -> pd.DataFrame:
    """Drop income rows settled outside a window's own labelled date range.

    Only windows listed under settings['window_settlement_bounds'] are
    affected — used where a raw export was pulled with the wrong start/end
    date and carries the adjacent window's settlements (see the evidence
    comment beside each entry in settings.yaml). Rows with no settlement
    date are KEPT and reported: they cannot be attributed to a window, and
    dropping them silently would hide data.
    """
    bounds = (settings.get("window_settlement_bounds") or {}).get(period)
    if not bounds or "statement_date" not in income.columns:
        return income

    d = income["statement_date"]
    keep = pd.Series(True, index=income.index)
    if bounds.get("from"):
        keep &= (d >= pd.Timestamp(bounds["from"])) | d.isna()
    if bounds.get("to"):
        keep &= (d <= pd.Timestamp(bounds["to"])) | d.isna()

    dropped = int((~keep).sum())
    undated = int(d.isna().sum())
    log.add(f"  settlement bounds for {period} ({bounds}): dropped {dropped} "
            f"out-of-window income row(s) of {len(income)}")
    if dropped:
        for day, n in d[~keep].dt.date.value_counts().sort_index().items():
            log.add(f"    dropped settled {day}: {n} row(s)")
    if undated:
        log.warn(f"{undated} income row(s) have no settlement date and were KEPT "
                 f"(cannot be attributed to a window)")
    return income[keep].copy()


def to_number(series: pd.Series, style: str) -> pd.Series:
    """Parse amount strings. 'standard' = 1,234,567.89 · 'vietnamese' = 1.234.567,89"""
    t = series.astype(str).str.strip()
    t = t.replace({"": None, "nan": None, "None": None})
    if style == "vietnamese":
        t = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        t = t.str.replace(",", "", regex=False)
    return pd.to_numeric(t, errors="coerce")


def _read_excel_sheet(path: Path, sheet, header_row: int, engine: str | None = None) -> pd.DataFrame:
    """Read one xlsx sheet as strings, header on `header_row` (1-based).

    Some exports (June 2026 TikTok order files) ship a broken `<dimension>`
    tag that makes the default openpyxl streaming reader — even after
    reset_dimensions — see only column A. `calamine` ignores the dimension
    and reads the real cells. Set reader_engine.<platform>.<kind>: calamine
    in settings for a source known to be broken (skips a wasted openpyxl
    read); otherwise the openpyxl fast path runs and calamine is a
    single-column safety-net fallback. Well-formed sources keep the exact
    openpyxl path, so verified May behaviour is untouched."""
    if engine == "calamine":
        return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                             engine="calamine")
    df = pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1)
    if df.shape[1] > 1:
        return df
    return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                         engine="calamine")


def _store_from_filename(filename: str, pattern: str) -> str:
    m = re.match(pattern, filename, flags=re.IGNORECASE)
    if not m or not (m.group(1) or "").strip():
        raise ReconHardStop(
            f"Could not derive the store name from file name '{filename}' "
            f"(store_from_filename pattern: {pattern}). The export has no store "
            f"column, so the file name must identify the store."
        )
    return m.group(1).strip()


def read_parts(
    folder: Path, colmap: dict[str, str], kind: str, settings: dict, log: RunLog, platform: str
) -> pd.DataFrame:
    """Read every file part in `folder`, rename to canonical columns, dedupe."""
    if not folder.is_dir():
        raise ReconHardStop(f"Input folder not found: {folder}")

    suffixes = tuple(s.lower() for s in settings.get("file_formats", [".xlsx", ".csv"]))
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in suffixes)
    if not files:
        raise ReconHardStop(f"No {kind} files found in {folder} (expected {suffixes})")

    sheet = ((settings.get("sheet_names") or {}).get(platform) or {}).get(kind)
    # Regex alternative to sheet_names: platforms that split data across
    # numbered sheets (Shopee income: "Doanh thu", "Doanh thu - 1", ...) get
    # every matching sheet read and concatenated — same as the team's M code
    # (Text.Contains([Name.1], "Doanh thu")).
    sheet_regex = ((settings.get("sheet_patterns") or {}).get(platform) or {}).get(kind)
    # 1-based row that holds the real (leaf) headers. Shopee income has two
    # band/group rows above the leaf header row (triple PromoteHeaders in the
    # team's M code -> header_row 3).
    header_row = int(((settings.get("header_rows") or {}).get(platform) or {}).get(kind, 1))
    store_pattern = (settings.get("store_from_filename") or {}).get(platform)
    skip = ((settings.get("skip_rows_after_header") or {}).get(platform) or {}).get(kind, 0)
    engine = ((settings.get("reader_engine") or {}).get(platform) or {}).get(kind)

    frames: list[pd.DataFrame] = []
    for f in files:
        if f.suffix.lower() == ".csv":
            df = pd.read_csv(f, dtype=str, encoding="utf-8-sig")
        elif sheet_regex:
            xf = pd.ExcelFile(f, engine="calamine" if engine == "calamine" else None)
            matches = [s for s in xf.sheet_names if re.search(sheet_regex, s)]
            if not matches:
                raise ReconHardStop(
                    f"{f.name}: no sheet matching /{sheet_regex}/ (sheets: {xf.sheet_names})")
            df = pd.concat(
                [_read_excel_sheet(f, s, header_row, engine) for s in matches], ignore_index=True)
        else:
            df = _read_excel_sheet(f, sheet if sheet else 0, header_row, engine)
        if skip:
            df = df.iloc[skip:]
        df.columns = [str(c).strip() for c in df.columns]
        present = {src: dst for src, dst in colmap.items() if src in df.columns}
        missing = [src for src in colmap if src not in df.columns]
        df = df.rename(columns=present)
        # TikTok/Shopee exports carry no store column — the per-store download
        # file name is the store identity.
        if "store" not in df.columns and store_pattern:
            df["store"] = _store_from_filename(f.name, store_pattern)
        df["source_file"] = f.name
        # Real config drops unmapped raw columns at read time: nothing
        # downstream uses them, it strips PII columns immediately (the
        # team's own Shopee M code does the same), and it keeps full-
        # platform runs within memory.
        if settings.get("drop_unmapped_columns", False):
            keep = set(colmap.values()) | {"store", "source_file"}
            df = df[[c for c in df.columns if c in keep]]
        log.add(f"  {f.name}: {len(df)} rows" + (f" (headers not found: {missing})" if missing else ""))
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)
    # Row dedupe is OFF for the real platforms: an order can legitimately
    # contain two byte-identical SKU lines (e.g. duplicated gift items —
    # Sanofi Shopee May), and the team's Power Query never dedupes; their
    # per-window folder discipline is the overlap protection. The synthetic
    # sample path (tools/sample_config) still sets dedupe_rows: true because
    # its generator bakes overlapping parts.
    if settings.get("dedupe_rows", True):
        data_cols = [c for c in combined.columns if c != "source_file"]
        combined = combined.drop_duplicates(subset=data_cols, ignore_index=True)
    dupes = before - len(combined)
    log.add(f"  {kind}: {len(files)} part(s), {before} rows, {dupes} duplicate rows dropped across parts")

    missing_required = [c for c in REQUIRED_COLUMNS[kind] if c not in combined.columns]
    if missing_required:
        raise ReconHardStop(
            f"{kind} data is missing required columns after header mapping: {missing_required}. "
            f"Update column_maps.{kind} in settings.yaml to match the real export headers."
        )

    style = settings.get("number_style", "standard")
    dayfirst = bool((settings.get("dayfirst") or {}).get(platform, False))
    for col in NUMERIC_COLUMNS[kind]:
        if col in combined.columns:
            combined[col] = to_number(combined[col], style)
    for col in DATE_COLUMNS[kind]:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors="coerce", dayfirst=dayfirst)
    combined["store"] = combined["store"].astype(str).str.strip()
    combined["order_id"] = combined["order_id"].astype(str).str.strip()

    aliases = (settings.get("store_aliases") or {}).get(platform) or {}
    if aliases:
        stores_found = set(combined["store"])
        unresolved = [s for s, canon in aliases.items() if canon == "TODO-HUMAN" and s in stores_found]
        if unresolved:
            log.warn(f"Stores with UNRESOLVED alias (TODO-HUMAN in store_aliases): {unresolved}")
        combined["store"] = combined["store"].replace(
            {s: canon for s, canon in aliases.items() if canon != "TODO-HUMAN"})
    return combined


def derive_brand(df: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """Brand comes from the store→brand map; unmapped stores keep the store name."""
    mapping = settings.get("store_to_brand") or {}
    df = df.copy()
    df["brand"] = df["store"].map(mapping)
    unmapped = sorted(df.loc[df["brand"].isna(), "store"].unique())
    if unmapped:
        log.warn(f"Stores with no store_to_brand mapping (brand falls back to store name): {unmapped}")
        df["brand"] = df["brand"].fillna(df["store"])
    return df


def check_stores(df: pd.DataFrame, kind: str, platform: str, settings: dict, log: RunLog) -> None:
    """The team's existing sanity check, codified: stores in data must equal
    the expected list. Mismatch → hard stop with named stores."""
    expected = set((settings.get("expected_stores") or {}).get(platform) or [])
    if not expected:
        log.warn(f"expected_stores.{platform} not configured — store-count check SKIPPED for {kind}")
        return
    # Stores that legitimately appear only in some windows (e.g. TikTok's
    # "Nutifood Nutrition Store" onboarded mid-May): warn when absent
    # instead of hard-stopping.
    optional = set((settings.get("stores_optional") or {}).get(platform) or [])
    found = set(df["store"].dropna().unique())
    missing = sorted(expected - found - optional)
    missing_optional = sorted((expected & optional) - found)
    unexpected = sorted(found - expected)
    if missing or unexpected:
        raise ReconHardStop(
            f"Store-count check FAILED for {platform}/{kind}. "
            f"Missing stores: {missing or 'none'}. Unexpected stores: {unexpected or 'none'}."
        )
    if missing_optional:
        log.warn(f"optional store(s) absent from {kind} (allowed): {missing_optional}")
    log.add(f"  store check {kind}: OK ({len(found)}/{len(expected)} expected stores present)")
