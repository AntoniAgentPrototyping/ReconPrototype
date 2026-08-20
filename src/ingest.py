"""Stage 1 — Ingest & validate.

Reads all file parts per platform (.xlsx and .csv), maps raw headers to
canonical names via settings.yaml column maps, dedupes across parts, and
enforces the team's store-count sanity check as a hard stop.
"""

from __future__ import annotations

import re
import unicodedata
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


# Cells that mean "no amount" rather than "a number I could not read". Blank
# and the accounting-format dash are the whole set: Excel's accounting format
# renders zero as a dash, and a real Shopee income file writes it in 46,972 of
# 83,134 rows of seller_ship_support — a column that feeds the discount
# allocation and therefore revenue. Under the old `errors="coerce"` those
# landed on NaN, indistinguishable from garbage, and `.fillna(0)` downstream
# made both 0 VND with no count anywhere (docs/08-KNOWN-DEFECTS.md#16).
ZERO_TOKENS = ("", "nan", "None", "-", "‐", "–", "—")


def to_number(series: pd.Series, style: str) -> pd.Series:
    """Parse amount strings. 'standard' = 1,234,567.89 · 'vietnamese' = 1.234.567,89

    A blank or accounting-dash cell returns **0.0**; only a value that could not
    be parsed at all returns NaN. That is what makes the two distinguishable to
    the caller — `read_parts` counts the NaNs and stops the run, because a
    settlement export never legitimately contains an unparseable amount.
    """
    t = series.astype(str).str.strip()
    zeros = t.isin(ZERO_TOKENS)
    t = t.mask(zeros)
    if style == "vietnamese":
        t = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        t = t.str.replace(",", "", regex=False)
    return pd.to_numeric(t, errors="coerce").mask(zeros, 0.0)


def report_unparseable(unparseable: dict[str, int], where: str, style: str,
                       settings: dict, log: RunLog) -> None:
    """A money cell that could not be parsed stops the run by default.

    The old behaviour was to coerce it to NaN and let a downstream `.fillna(0)`
    turn it into zero revenue — uncounted and unwarned. Hard-stopping matches
    the pipeline's stated posture ([D3](../docs/06-DECISIONS.md#d3)): a wrong
    invoice costs more than a late one, and the likeliest cause is a monthly
    format change (a Vietnamese-styled column, a currency suffix), which is a
    config fix rather than a data problem. `numeric_coercion: warn` restores
    the old behaviour for an operator who has looked and decided.
    """
    if not unparseable:
        return
    detail = ", ".join(f"{c}: {n:,} row(s)" for c, n in sorted(unparseable.items()))
    message = (
        f"{where}: {sum(unparseable.values()):,} amount cell(s) could not be parsed "
        f"under number_style='{style}' — {detail}. A settlement export never "
        f"legitimately contains an unparseable amount, and every one of these would "
        f"become 0 VND of revenue with no other signal. Check number_style and the "
        f"column map against the real export, then re-run. To continue anyway (the "
        f"old behaviour), set numeric_coercion: warn in settings.yaml."
    )
    if str(settings.get("numeric_coercion", "hard_stop")).lower() == "warn":
        log.warn(message)
        return
    raise ReconHardStop(message)


def parse_dates(values: "pd.Series", dayfirst: bool, where: str, column: str,
                log: RunLog, fmt: str | None = None) -> "pd.Series":
    """Text to timestamps. With `fmt`, exactly; without it, by inference.

    pandas infers a format from the first non-null element and coerces everything
    that does not match it to `NaT`. Which format it infers is therefore
    DATA-DEPENDENT, and `dayfirst` only decides the ambiguous case: measured on
    pandas 2.3.3, `["2026/05/01", "2026/05/13"]` under `dayfirst=True` yields
    `2026-01-05` and `NaT` — the first silently transposed, the second dropped —
    while the same column whose first value is unambiguous parses correctly and
    pandas emits `Parsing dates in %Y/%m/%d format when dayfirst=True was specified`.

    That warning is the exact signal that the contract and the file disagree, and it
    went to stderr and died there. It is captured and logged here instead.

    **`fmt` is why that inference is no longer load-bearing (2026-08-19).** The
    warning above fired on real TikTok income for months without costing anything,
    because May's first `Order settled time` value was unambiguous and inference
    quietly overrode the flag. July's first value is `2026/07/07` — ambiguous — so
    `dayfirst=True` won and the whole column parsed as `%Y/%d/%m`: a window covering
    1-7 July came out spanning January to September, and staging could not derive a
    window at all. An explicit format removes the data dependence entirely; see
    `date_formats` in settings.yaml for the measurements.

    Passing `fmt` is STRICTER, and deliberately so. A cell that does not match
    becomes `NaT` instead of being rescued by a second guess, and the caller counts
    and names those through `report_undated`.
    """
    import warnings

    if fmt:
        return pd.to_datetime(values, errors="coerce", format=fmt)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        parsed = pd.to_datetime(values, errors="coerce", dayfirst=dayfirst)
    for w in caught:
        text = str(w.message)
        if "dayfirst" in text or "Could not infer format" in text:
            log.warn(f"{where}/{column}: {text.strip()} "
                     f"(dayfirst={dayfirst} comes from the contract; the file's own "
                     f"format wins where pandas can tell, so the two disagree. Set "
                     f"date_formats.{where.replace('/', '.')} to parse it exactly.)")
    return parsed


def date_format(settings: dict, platform: str, kind: str) -> str | None:
    """The measured strptime format for one platform/kind, or None to infer.

    One accessor so `src/`, `service/` and `tools/stage_exports.py` cannot end up
    with three ideas of how a settlement date is spelled — which is precisely the
    class of bug this setting exists to close.
    """
    return ((settings.get("date_formats") or {}).get(platform) or {}).get(kind)


def report_undated(undated: dict[str, int], where: str, dayfirst: bool,
                   settings: dict, log: RunLog, fmt: str | None = None) -> None:
    """A date that could not be read is COUNTED and named, not silently dropped.

    The mirror of `report_unparseable`, and deliberately at a lower setting. An
    amount that will not parse hard-stops by default because a settlement export
    never legitimately contains one. A *date* can legitimately be blank —
    `apply_settlement_bounds` already keeps and reports undated income rows — so
    the default here is `warn`, and `date_coercion: hard_stop` is available to an
    operator who has decided otherwise.

    What was wrong until M8 was not the leniency, it was the silence: money went
    through `report_unparseable` while dates went through a bare
    `errors="coerce"` with no counter (docs/08-KNOWN-DEFECTS.md#16, the date half).
    An unreadable date does not produce a wrong number — it produces a MISSING one,
    because `finance_template` groups on `.dt.month` and pandas drops a NaN group
    key by default. Quieter than a wrong number, and worse.
    """
    if not undated:
        return
    detail = ", ".join(f"{c}: {n:,} row(s)" for c, n in sorted(undated.items()))
    # Name the rule that actually parsed, not the one that did not. An explicit
    # format makes dayfirst irrelevant, and reporting it would send whoever reads
    # this to the wrong setting.
    under = f"date format {fmt}" if fmt else f"dayfirst={dayfirst}"
    message = (
        f"{where}: {sum(undated.values()):,} date cell(s) could not be read under "
        f"{under} — {detail}. Rows with no date are kept, but they drop "
        f"out of any month grouping in the finance workbook, so they leave the "
        f"invoice quietly rather than loudly. The usual cause is an export whose "
        f"date format changed. To stop the run on this instead, set "
        f"date_coercion: hard_stop in settings.yaml."
    )
    if str(settings.get("date_coercion", "warn")).lower() == "hard_stop":
        raise ReconHardStop(message)
    log.warn(message)


# A rights-protected Office file is an OLE2 compound document wrapping the real,
# encrypted .xlsx. Detected from bytes rather than with a library, because the whole
# point is to say something useful in a deployment that has no extra dependency and
# on a machine that cannot open the file anyway.
_OLE2_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
_ENCRYPTED_PACKAGE = "EncryptedPackage".encode("utf-16-le")
_LABEL_INFO = "LabelInfo".encode("utf-16-le")


def rights_protected(path: Path) -> str | None:
    """Why this file cannot be opened, if the reason is encryption. Else None.

    **Found the hard way, 2026-08-19.** Two files in this tree refuse to open, and
    both were recorded for months as something they are not — the month-end master as
    "a legacy .xls with the wrong extension", one Lazada weekly export as
    "password-protected". Neither is true. Both are genuine `.xlsx` files wrapped in
    an OLE2 container by a Microsoft Purview **sensitivity label with encryption**,
    carrying the same label id and the same tenant, applied deliberately
    (`method="Privileged"`).

    That distinction decides what anyone does next. A password is something you ask a
    colleague for. A sensitivity label is org policy: the file opens only for an
    identity the label grants rights to, and **no** amount of re-saving, renaming or
    reader-swapping changes that. It is also not a per-file accident — it will apply
    to every labelled file the team ever sends, which makes it a constraint on
    hosting this system at all (`docs/13-ENTRA-SETUP.md`).

    Cheap by construction: a healthy export is a ZIP, so the signature check rejects
    every good file on 8 bytes and only a file that is already broken is scanned.
    """
    try:
        with open(path, "rb") as fh:
            if not fh.read(8).startswith(_OLE2_SIGNATURE):
                return None
            fh.seek(0)
            # Chunked with overlap: the stream directory can sit anywhere, and a
            # marker must not be missed because it straddles a boundary.
            tail = b""
            found = set()
            while chunk := fh.read(1 << 20):
                window = tail + chunk
                for marker, name in ((_ENCRYPTED_PACKAGE, "encrypted"),
                                     (_LABEL_INFO, "labelled")):
                    if marker in window:
                        found.add(name)
                if len(found) == 2:
                    break
                tail = window[-64:]
    except OSError:                                             # pragma: no cover
        return None

    if "encrypted" not in found:
        # OLE2 without an encrypted package: a genuine legacy .xls, or another
        # Office format renamed. Different problem, different answer.
        return (f"{path.name} is not a modern Excel file despite its name — it is an "
                f"OLE2 document (the legacy .xls/.doc container). Re-save it from "
                f"Excel as 'Excel Workbook (*.xlsx)'; renaming it is not enough.")
    if "labelled" in found:
        return (f"{path.name} is encrypted by a Microsoft sensitivity label, so "
                f"nothing can open it without rights to that label — not this "
                f"system, and not a colleague the label does not cover. Re-saving "
                f"or renaming will not help. Either supply a copy with the label "
                f"removed, or have the label grant rights to the identity this "
                f"service runs as.")
    return (f"{path.name} is an encrypted Office file (password or rights "
            f"protection). Supply an unprotected copy.")


def read_excel_sheet(path: Path, sheet, header_row: int, engine: str | None = None) -> pd.DataFrame:
    """Read one xlsx sheet as strings, header on `header_row` (1-based).

    Public since M6: the upload sanitizer rewrites an export before the pipeline
    reads it, and it must do its read through THIS function. A private copy in
    `service/` would mean the sanitizer's idea of the file and the pipeline's idea
    of the file could diverge — including on the broken-`<dimension>` fallback
    below, which is the difference between reading 63 columns and reading one.

    Some exports (June 2026 TikTok order files) ship a broken `<dimension>`
    tag that makes the default openpyxl streaming reader — even after
    reset_dimensions — see only column A. `calamine` ignores the dimension
    and reads the real cells. Set reader_engine.<platform>.<kind>: calamine
    in settings for a source known to be broken (skips a wasted openpyxl
    read); otherwise the openpyxl fast path runs and calamine is a
    single-column safety-net fallback. Well-formed sources keep the exact
    openpyxl path, so verified May behaviour is untouched."""
    # Checked before any reader sees it: openpyxl says "File is not a zip file" and
    # calamine says "Cannot detect file format", and neither tells a person that the
    # file is encrypted or what to do about it.
    protection = rights_protected(path)
    if protection:
        raise ReconHardStop(protection)
    if engine == "calamine":
        return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                             engine="calamine")
    df = pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1)
    if df.shape[1] > 1:
        return df
    return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                         engine="calamine")


def sheet_names(path: Path, engine: str | None = None) -> list[str]:
    """The workbook's sheet names, through the same boundary as its cells.

    `read_excel_sheet` needs to be told WHICH sheet, and a caller that must first
    ask "which tabs are in here" would otherwise open the file itself — putting a
    second `pd.ExcelFile` outside the boundary this module exists to be. Same
    rights-protection check first, for the same reason: openpyxl's "File is not a
    zip file" tells nobody that the file is encrypted.
    """
    protection = rights_protected(path)
    if protection:
        raise ReconHardStop(protection)
    return list(pd.ExcelFile(path, engine="calamine" if engine == "calamine"
                             else None).sheet_names)


def store_from_filename(filename: str, pattern: str) -> str:
    """Store identity, derived from the file name (docs/06-DECISIONS.md#d6).

    Public because `service/naming.py` must use *this* function to check that a
    renamed upload still resolves to the same store. A second copy of the rule
    living in the service is the failure mode to avoid: the rename would then be
    validated against a parser that is not the one the pipeline actually runs,
    which is worse than not checking at all.
    """
    m = re.match(pattern, filename, flags=re.IGNORECASE)
    if not m or not (m.group(1) or "").strip():
        raise ReconHardStop(
            f"Could not derive the store name from file name '{filename}' "
            f"(store_from_filename pattern: {pattern}). The export has no store "
            f"column, so the file name must identify the store."
        )
    return m.group(1).strip()


# The private name predates M6 and is kept as an alias so a reader grepping for
# either spelling lands in one place.
_store_from_filename = store_from_filename


def export_files(folder: Path, settings: dict) -> list[Path]:
    """The export files in one folder, in the order a run reads them.

    One place for the `file_formats` rule and the sort, so a second caller cannot
    end up reading a different set of files — or the same files in a different
    order, which for `read_parts` decides row order and therefore every downstream
    digest. Returns empty for a folder that is not there; callers that require one
    raise their own refusal (`read_parts` does).
    """
    if not folder.is_dir():
        return []
    suffixes = tuple(s.lower() for s in settings.get("file_formats", [".xlsx", ".csv"]))
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in suffixes)


def read_files(
    files: list[Path], colmap: dict[str, str], kind: str, settings: dict,
    log: RunLog, platform: str
) -> list[pd.DataFrame]:
    """Read a named list of export files into one frame each, canonically named.

    Extracted from `read_parts` (whose signature and behaviour are unchanged) so
    that a second caller can read *some* files out of a folder rather than all of
    them: `src/backfill.py` needs one store's order files from a predecessor window
    and must read them through **this** code path, not a copy of it. The reading
    rules here are the ones every verified number was produced under — the broken
    `<dimension>` fallback, the NFC header normalisation, the sheet regex, the junk
    row skip, the PII drop — and a second implementation of any of them is how the
    upload sanitizer silently worked for one platform out of three for a milestone.
    """
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
                [read_excel_sheet(f, s, header_row, engine) for s in matches], ignore_index=True)
        else:
            df = read_excel_sheet(f, sheet if sheet else 0, header_row, engine)
        if skip:
            df = df.iloc[skip:]
        # NFC-normalize before matching. Shopee ORDER exports deliver Vietnamese
        # headers in NFD (decomposed) while settings.yaml keys are NFC, so
        # 'Được Shopee trợ giá' is byte-unequal to the visually identical config
        # key and silently fails to map — 9 of 63 headers in a real file are
        # non-NFC. full_run's norm_store had always done this for store names;
        # ingest never got the same treatment (docs/08-KNOWN-DEFECTS.md#12).
        df.columns = [unicodedata.normalize("NFC", str(c)).strip() for c in df.columns]
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
        # Default TRUE since M8/2.4: a settings dict that forgets to say now
        # STRIPS rather than retains. The old False meant the fail-open direction
        # was the PII-leaking one — a caller that built its own settings (a test,
        # a script, a future entry point) kept `Recipient`, `Phone #` and
        # `Detail Address` in the frame and in anything that frame reached.
        if settings.get("drop_unmapped_columns", True):
            keep = set(colmap.values()) | {"store", "source_file"}
            df = df[[c for c in df.columns if c in keep]]
        log.add(f"  {f.name}: {len(df)} rows" + (f" (headers not found: {missing})" if missing else ""))
        frames.append(df)
    return frames


def read_parts(
    folder: Path, colmap: dict[str, str], kind: str, settings: dict, log: RunLog, platform: str
) -> pd.DataFrame:
    """Read every file part in `folder`, rename to canonical columns, dedupe."""
    if not folder.is_dir():
        raise ReconHardStop(f"Input folder not found: {folder}")

    suffixes = tuple(s.lower() for s in settings.get("file_formats", [".xlsx", ".csv"]))
    files = export_files(folder, settings)
    if not files:
        raise ReconHardStop(f"No {kind} files found in {folder} (expected {suffixes})")

    frames = read_files(files, colmap, kind, settings, log, platform)

    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)
    # Row dedupe is OFF for the real platforms: an order can legitimately
    # contain two byte-identical SKU lines (e.g. duplicated gift items —
    # Sanofi Shopee May), and the team's Power Query never dedupes; their
    # per-window folder discipline is the overlap protection. The synthetic
    # the legacy synthetic sample path set dedupe_rows: true because
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

    return normalize_parts(combined, kind, settings, log, platform)


def canonical_store(settings: dict, platform: str, store: str) -> str:
    """One store name through `store_aliases`, or itself if unmapped.

    The single-value form of what `normalize_parts` applies to a whole column, for
    the callers that must decide from a FILENAME before any frame exists — the
    roster preview (`service/materialize`) and the cross-window borrow's file
    prefilter (`src/backfill`). Both used to carry their own copy of this
    arithmetic; the borrow's copy did not exist at all, which is how an aliased
    store's predecessor files came to be skipped (defect 2.12's report-mode gap,
    2026-08-20).

    `TODO-HUMAN` is an unresolved alias and must resolve to itself, not to the
    literal string — `normalize_parts` skips those too, and mapping a real store
    onto a placeholder would put "TODO-HUMAN" in the roster comparison.
    """
    aliases = (settings.get("store_aliases") or {}).get(platform) or {}
    mapped = aliases.get(store)
    return mapped if mapped and mapped != "TODO-HUMAN" else store


def normalize_parts(
    combined: pd.DataFrame, kind: str, settings: dict, log: RunLog, platform: str,
    *, where: str | None = None,
) -> pd.DataFrame:
    """The post-read rules every verified number was produced under.

    Required columns, numeric coercion, date parsing, store/order_id stripping and
    `store_aliases` — everything `read_parts` used to do inline after `read_files`.

    Extracted 2026-08-20 because it turned out to have a second caller. The
    cross-window borrow (`src/backfill.py`) read predecessor files through
    `read_files` and then hand-rolled two `.strip()` calls, so borrowed frames got
    **no aliases and no numeric coercion**. The alias half was live and silent: the
    borrow's `needed` set holds canonical stores while a filename says `Pediasure`,
    so July's 941,081,056 VND Abbott recovery reported as zero. One spelling, two
    callers — the same lesson as the `_vat_sku` back-channel ([D28]).

    Deliberately NOT included, because they are per-*window* policies rather than
    per-frame rules: the part-file concatenation, `dedupe_rows` (borrowing must never
    dedupe — an order legitimately holds two identical SKU lines, D5), the
    "N part(s), M rows" log line, and the `REQUIRED_COLUMNS` check. That last one is
    `read_parts`' own contract about a *window's* export being complete enough to
    invoice from; a predecessor read for comparison is held to the narrower bar
    below — the two columns this function itself touches. `where` overrides the label
    the coercion reports are filed under, so a borrowed frame's warnings name the
    window they came from instead of impersonating this window's own read.
    """
    where = where or f"{platform}/{kind}"
    # Narrower than REQUIRED_COLUMNS on purpose (see above). For `read_parts` this can
    # never fire — REQUIRED_COLUMNS is a superset and is checked first — so this is a
    # contract for the second caller, not a second check for the first.
    missing_identity = [c for c in ("store", "order_id") if c not in combined.columns]
    if missing_identity:
        raise ReconHardStop(
            f"{where}: no {missing_identity} column after header mapping, so these rows "
            f"cannot be identified. Check column_maps.{kind} and store_from_filename "
            f"against the real export headers.")

    style = settings.get("number_style", "standard")
    dayfirst = bool((settings.get("dayfirst") or {}).get(platform, False))
    unparseable: dict[str, int] = {}
    for col in NUMERIC_COLUMNS[kind]:
        if col in combined.columns:
            combined[col] = to_number(combined[col], style)
            n_bad = int(combined[col].isna().sum())
            if n_bad:
                unparseable[col] = n_bad
    report_unparseable(unparseable, where, style, settings, log)
    undated: dict[str, int] = {}
    fmt = date_format(settings, platform, kind)
    for col in DATE_COLUMNS[kind]:
        if col in combined.columns:
            before_bad = int(combined[col].isna().sum())
            combined[col] = parse_dates(combined[col], dayfirst, where,
                                        col, log, fmt=fmt)
            n_bad = int(combined[col].isna().sum()) - before_bad
            if n_bad:
                undated[col] = n_bad
    report_undated(undated, where, dayfirst, settings, log, fmt=fmt)
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
