"""The upload boundary — where raw exports arrive and PII stops.

Closes [defect 2.3](docs/08-KNOWN-DEFECTS.md): until M5 the api could only run a
window somebody had already staged by hand, so the manual-download step survived
and the service removed no error class at all.

**The strip is an allowlist, and it is the pipeline's own.** `ingest.read_parts`
keeps exactly the columns named in that platform's column map and discards the
rest — which is how customer names, phone numbers and delivery addresses never
reach a DataFrame (`drop_unmapped_columns`, `src/ingest.py:224`). This module
applies the *same* rule at the door, from the *same* config, so there is no
second list of PII column names to maintain and go stale. A column that is not in
the contract does not get onto the server.

**Why sanitize at upload at all, when ingest already strips?** Because ingest
strips what it *reads into memory*; the file itself would sit on disk, in a
backup, and in whatever the host does with volumes. The roadmap's phrasing is
exact — *PII stripped at the upload boundary; raw uploads on short retention*.

**The risk this creates, and how it is answered.** Rewriting an export before the
verified pipeline reads it inserts a transformation into the path that produced
every verified number. That is the one thing this repo does not do on trust
(docs/06-DECISIONS.md#d12). So `tests/service/test_upload_sanitizer.py` runs a
real golden window through the sanitizer and asserts the resulting workbook
matches the committed digest cell for cell. If that test fails, the sanitizer is
wrong and the answer is to fix it — never to widen the comparison.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

# Documented PII columns, used ONLY to label what was dropped so the audit record
# can say "of 41 columns dropped, 6 are known personal data". The *control* is
# the allowlist above; this list is advisory and may be incomplete by design —
# nothing depends on it being exhaustive (docs/04-DATA-FLOW.md).
KNOWN_PII = frozenset({
    "Recipient", "Phone #", "Detail Address", "Buyer Username",
    "Tên Người nhận", "Số điện thoại", "Địa chỉ nhận hàng", "Người Mua",
})

SAFE_FILENAME = re.compile(r"^[A-Za-z0-9 ()._+\-À-ỹ]{1,180}$")

# The kinds whose settlement dates DEFINE which window a file belongs to.
# `orders` is deliberately absent: an order created in June can settle in July, so
# every healthy TikTok folder carries order files that predate its window — and
# TikTok re-ships each store's prior-month pull in *every* weekly folder because the
# cross-period stitch needs it. Date-checking an order export would flag the healthy
# case. `tools/stage_exports.py` draws the same line with its own spelling of these
# names (`("income", "Weekly", "Daily")`); the service's kind vocabulary is
# lowercase (`service/naming.py:KINDS_BY_PLATFORM`), and neither module may import
# the other, so the two lists are stated twice on purpose and this comment is the
# link between them.
WINDOW_DEFINING = ("income", "weekly", "daily")


class UploadRejected(ValueError):
    """The file cannot be accepted. Always names the reason — a rejected upload
    at month end is an operator's problem to solve, not a mystery."""


def month_of(period: str) -> str:
    """The `YYYY-MM` a window label belongs to. `2026-07_w2` -> `2026-07`.

    The same split `M5Repository.month_windows` keys the month-end master on, so a
    window that the master counts in July cannot be a window this door reads as
    belonging to a different month.
    """
    return period.split("_", 1)[0]


def _month_bounds(month: str) -> "tuple[date, date] | None":
    try:
        year, mon = (int(part) for part in month.split("-", 1))
        first = date(year, mon, 1)
    except (ValueError, TypeError):
        return None
    last_day = calendar.monthrange(year, mon)[1]
    return first, date(year, mon, last_day)


def check_span(period: str, kind: str, *, settles_from: "date | None",
               settles_to: "date | None",
               sibling_starts: "Sequence[date]" = ()) -> tuple[str | None, str | None]:
    """`(refusal, warning)` for one file's settlement span. At most one is set.

    The api validated `period` for **character safety only** — a file could be
    uploaded to any window and nothing looked at what it settled.
    `tools/stage_exports.py` has derived the window from settlement dates since
    M2.5, and the api had none of that ([defect 2.3](docs/08-KNOWN-DEFECTS.md)'s
    residual). July's two mis-pulls are what that costs: an order export labelled
    for the later window, byte-identical to the earlier one, worth
    4,527,401,608 VND of understatement across the month once its knock-on reached
    the tie.

    Three rules, and the differences between them are the design:

    * **A window-defining file that does not INTERSECT its window's month is
      refused.** Intersect, not contain: a Lazada weekly legitimately laps into the
      next month (the 25th-to-month-end Daily week is a permanent fixture), so
      `29 Jul – 4 Aug` in `2026-07_l5` is healthy and must not be refused.
    * **The mis-pull shape WARNS and never refuses.** A file starting earlier than
      its siblings is `find_outliers`' signal, but the first upload into a window
      has no siblings, [D9](docs/06-DECISIONS.md#d9)'s
      `window_settlement_bounds` owns the hard control at run time, and a false
      refusal at month end teaches operators to fight the door rather than read it.
    * **An unknown span is not checked**, and the caller says so rather than
      guessing — the same posture `ingest.date_format` takes when no format is
      configured. A file kind with no date column (a Lazada ledger has no
      `statement_date`) is a legitimate silence, not a failure.
    """
    if kind not in WINDOW_DEFINING or settles_from is None or settles_to is None:
        return None, None

    bounds = _month_bounds(month_of(period))
    if bounds is None:
        return None, None
    month_start, month_end = bounds

    if settles_from > month_end or settles_to < month_start:
        return (f"this file settles {settles_from}..{settles_to}, which does not "
                f"overlap {month_of(period)} at all — the month {period!r} belongs "
                f"to. Either it is labelled for the wrong window, or it is a "
                f"mis-pull carrying another period's settlement block. Check the "
                f"export before staging it; if the span is genuinely right, the "
                f"window label is what needs correcting."), None

    known = [s for s in sibling_starts if s is not None]
    # Earlier than EVERY sibling, not merely earlier than the modal start.
    # `find_outliers` compares against the mode, which needs an arbitrary tie-break
    # when starts are evenly split; at the door there is no reviewer watching a plan,
    # so the stricter unanimous test is used and an ambiguous case stays silent
    # rather than crying wolf on the first two files of a window.
    if known and all(settles_from < s for s in known):
        return None, (
            f"settles from {settles_from} while all {len(known)} of its "
            f"{kind} sibling(s) in {period} start at {min(known)} or later — the shape of "
            f"a mis-pull carrying an earlier settlement block. This is a warning, not "
            f"a refusal: verify the export, and if it is confirmed, declare the "
            f"window's real range under window_settlement_bounds (D9) so the run "
            f"drops the rows that belong to the earlier window.")
    return None, None


@dataclass
class SanitizeResult:
    sheet: str
    rows: int
    kept_columns: list[str]
    dropped_columns: list[str] = field(default_factory=list)
    # How many source sheets were read. >1 for Shopee income, whose export splits
    # data across "Doanh thu", "Doanh thu - 1", … and whose sanitized form is the
    # concatenation under one matching sheet name.
    sheets_read: int = 1
    # Where the leaf header row ends up in the written file (1-based). Preserved
    # from the source, not normalised — see `sanitize`.
    header_row: int = 1
    # Distinct order ids in this file, and the span of settlement dates it covers.
    # Both come out of the pass `sanitize` already makes over the frame, so neither
    # costs a second read (defect 2.12 / 2.3's residual). Empty when the file has no
    # such column: the api then reports "not checked" rather than guessing, the same
    # posture `ingest.date_format` takes when no format is configured.
    order_ids: list[str] = field(default_factory=list)
    settles_from: "date | None" = None
    settles_to: "date | None" = None

    @property
    def dropped_known_pii(self) -> list[str]:
        return sorted(c for c in self.dropped_columns if c in KNOWN_PII)

    @property
    def unrecognised_headers(self) -> list[str]:
        """Dropped headers that are not PII — the drift evidence (register D5).

        A dropped column is one of two very different things: a customer-PII column
        the contract deliberately does not name, or a header the export renamed. The
        first is the system working; the second is the monthly cost. Subtracting
        `KNOWN_PII` is what stops the report crying wolf on every healthy file — an
        untriaged list including `Recipient` and `Phone #` is a list an operator
        learns to ignore, which is the failure mode the whole findings design exists
        to avoid.
        """
        return sorted(c for c in self.dropped_columns if c not in KNOWN_PII)


def check_filename(name: str) -> str:
    """The filename is data, not decoration.

    Store identity is derived from it (`docs/06-DECISIONS.md#d6`), so it must
    survive intact — and it becomes a path, so it must not contain one.
    """
    cleaned = unicodedata.normalize("NFC", name).strip()
    if not cleaned:
        raise UploadRejected("empty filename")
    if "/" in cleaned or "\\" in cleaned or cleaned.startswith("."):
        raise UploadRejected(f"unsafe filename: {cleaned!r}")
    if not cleaned.lower().endswith((".xlsx", ".xls", ".csv")):
        raise UploadRejected(
            f"{cleaned!r}: expected a marketplace export (.xlsx, .xls or .csv)")
    if not SAFE_FILENAME.match(cleaned):
        raise UploadRejected(f"unsafe filename: {cleaned!r}")
    return cleaned


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Canonical fields the PIPELINE supplies rather than reading from a column, so a
# file must not be judged for lacking them. `store` comes from the filename
# (docs/06-DECISIONS.md#d6) and `read_files` adds it before `read_parts` checks its
# required set — checking the file's own headers for it would fail every export ever
# written.
PIPELINE_SUPPLIED_FIELDS = frozenset({"store"})


def required_fields(kind: str) -> frozenset[str]:
    """Canonical fields a window of this kind must supply, or the run hard-stops.

    The same set `ingest.read_parts` checks, minus what the pipeline supplies
    itself — one definition, read from `src/`, because a copy here would be a second
    opinion about what the money math needs.

    **Empty for Lazada's kinds, and that is not an oversight.** `REQUIRED_COLUMNS`
    has entries for `orders` and `income` only; Lazada is a fee-event ledger read by
    `lazada.read_ledger`, which has no equivalent required set. Returning an empty
    set means "nothing to check here", which is the honest answer and the same
    posture the roster check takes on a platform with no roster.
    """
    from src.ingest import REQUIRED_COLUMNS

    return frozenset(REQUIRED_COLUMNS.get(kind, ())) - PIPELINE_SUPPLIED_FIELDS


def fields_of(headers, colmap: dict[str, str]) -> set[str]:
    """Which canonical fields a set of raw headers supplies, per this colmap.

    Mapped at READ time rather than stored, because the mapping belongs to the
    contract — which is versioned and pinned per window — while the headers belong
    to the file. A stored mapping would freeze one reading of the file and go stale
    the moment a column map is edited (migration `023`).
    """
    return {colmap[h] for h in headers if h in colmap}


def missing_fields(header_sets, colmap: dict[str, str], kind: str) -> list[str]:
    """Required fields that NO file of this kind supplies.

    **The union across a kind's files, and that is the whole design.**
    `ingest.read_parts` concatenates every part of a kind and checks its required
    columns against the result, so a "part 2" export carrying fewer columns is
    legitimate — July produced nine of them. Judging each file on its own would
    refuse a healthy window to catch a fault the concatenation does not have, which
    is why this check lives on the window plan rather than at the upload door.

    An empty result means either "nothing missing" or "nothing to check" — Lazada's
    kinds have no required set at all. The caller distinguishes those; see the
    `checked` flag on the plan's drift payload, because "clean" and "unmeasured"
    must not render the same.
    """
    required = required_fields(kind)
    if not required:
        return []
    present: set[str] = set()
    for headers in header_sets:
        present |= fields_of(headers, colmap)
    return sorted(required - present)


def column_map_for(settings: dict, platform: str, kind: str) -> dict[str, str]:
    """The allowlist for this platform and file kind.

    Reached through the pipeline's own accessor, never a copy. Lazada used to be
    the exception — its maps were constants in `src/lazada.py` and this module had
    to import them — until M8/1.7 moved them into `column_maps.lazada` (docs/14
    D4). All three platforms now answer the same question the same way.
    """
    if platform == "lazada":
        from src import lazada
        return lazada.column_map(settings, "weekly" if kind == "weekly" else "daily")

    from src import config as src_config
    return dict(src_config.column_map(settings, platform, kind))


def sheet_for(platform: str, kind: str, settings: dict) -> Any:
    if platform == "lazada":
        # Through the pipeline's accessor rather than the config key directly, so
        # a missing entry raises `lazada.read_ledger`'s own refusal — the sanitizer
        # and the reader cannot disagree about which sheet the ledger is on.
        from src import lazada
        return lazada.sheet_name(settings, "weekly" if kind == "weekly" else "daily")
    sheets = (settings.get("sheet_names") or {}).get(platform) or {}
    return sheets.get(kind, 0)


def _shape(settings: dict, platform: str, kind: str) -> dict:
    """The four config facts that describe this file's SHAPE, not its columns.

    Read here for the same reason the column map is: `read_parts` will apply every
    one of them to the sanitized copy, so the copy has to still satisfy them.
    """
    if platform == "lazada":
        # Lazada's reader takes none of these: `lazada.read_ledger` reads the named
        # sheet with a header on row 1, no skip and the default engine. Its column
        # map and sheet name are in the contract since M8/1.7; these three are not,
        # because there is no code path that would read them.
        return {"header_row": 1, "sheet_regex": None, "engine": None}
    return {
        "header_row": int(((settings.get("header_rows") or {})
                           .get(platform) or {}).get(kind, 1)),
        "sheet_regex": ((settings.get("sheet_patterns") or {})
                        .get(platform) or {}).get(kind),
        "engine": ((settings.get("reader_engine") or {})
                   .get(platform) or {}).get(kind),
    }


def read_source(source: Path, *, settings: dict, platform: str,
                kind: str) -> tuple[Any, list[str], int]:
    """Read an incoming export the way the pipeline will read it.

    Returns `(frame, sheet_names_used, header_row)`.

    **Through `ingest.read_excel_sheet`, never a local copy.** That function
    carries the broken-`<dimension>` fallback — the difference between reading 63
    columns and reading one on a June TikTok order file — and a sanitizer that
    read the file differently from the pipeline would strip against a column list
    the pipeline never sees.

    `.csv` and `.xls` are dispatched here, which is why the caller can promise the
    written file is always `.xlsx`.
    """
    import pandas as pd

    from src.ingest import read_excel_sheet, rights_protected

    # Checked at the door, where a person is still looking at the screen. A
    # rights-protected file otherwise fails deep in a reader with "File is not a
    # zip file", which says nothing about the encryption or what to do about it.
    protection = rights_protected(source)
    if protection:
        raise UploadRejected(protection)

    shape = _shape(settings, platform, kind)
    header_row = shape["header_row"]

    if source.suffix.lower() == ".csv":
        # utf-8-sig for the BOM every Excel-exported CSV carries; a BOM left on
        # the first header makes it byte-unequal to the config key.
        return pd.read_csv(source, dtype=str, encoding="utf-8-sig"), ["Sheet1"], header_row

    regex = shape["sheet_regex"]
    if regex:
        # Shopee income arrives split across "Doanh thu", "Doanh thu - 1", … and
        # `read_parts` concatenates every matching sheet in workbook order. The
        # sanitized copy is that concatenation under ONE matching name: reading N
        # sheets and concatenating equals reading their concatenation, and it keeps
        # the sanitized file a single-sheet artifact anyone can open and check.
        book = pd.ExcelFile(source, engine="calamine" if shape["engine"] == "calamine" else None)
        matches = [s for s in book.sheet_names if re.search(regex, s)]
        if not matches:
            raise UploadRejected(
                f"{source.name}: no sheet matching /{regex}/ (sheets: "
                f"{book.sheet_names}). This is probably the wrong file kind.")
        frames = [read_excel_sheet(source, s, header_row, shape["engine"]) for s in matches]
        return (pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0],
                matches, header_row)

    sheet = sheet_for(platform, kind, settings)
    frame = read_excel_sheet(source, sheet if sheet else 0, header_row, shape["engine"])
    return frame, [str(sheet) if isinstance(sheet, str) else "Sheet1"], header_row


def sanitize(source: Path, target: Path, *, settings: dict, platform: str,
             kind: str) -> SanitizeResult:
    """Write `target` holding only the columns the contract names.

    Values are copied cell for cell — this drops columns and does nothing else.
    It deliberately does not coerce types, reformat dates or trim strings: every
    one of those is a decision the verified pipeline already makes, and making it
    twice, differently, is how two implementations of the money math appear.

    **It also does not normalise the file's shape, and that is the M6 fix.** Until
    now this wrote the header on row 1 with `pd.read_excel`'s defaults, which works
    for Lazada and for nothing else: Shopee income's leaf header is on row 3 under
    two band rows (`header_rows: 3`), and TikTok orders carry one junk row directly
    under the header (`skip_rows_after_header: 1`). `read_parts` applies both to
    whatever this writes, so flattening them here would shift every row by two and
    silently delete the first real order. The written file therefore reproduces the
    source's shape: `header_row - 1` blank rows above the header, and the junk row
    left in place as ordinary data for `read_parts` to drop as it always has.

    That is what makes the golden gate extensible to all three platforms — and the
    gate, not this docstring, is the evidence
    (`tests/service/test_uploads.py::test_a_sanitized_window_produces_the_committed_golden`).
    """
    import pandas as pd

    colmap = column_map_for(settings, platform, kind)
    frame, sheets, header_row = read_source(source, settings=settings,
                                            platform=platform, kind=kind)

    original = [str(c) for c in frame.columns]
    frame.columns = [unicodedata.normalize("NFC", str(c)).strip() for c in frame.columns]

    keep = [c for c in frame.columns if c in colmap]
    if not keep:
        raise UploadRejected(
            f"{source.name}: none of the {len(colmap)} configured {platform}/{kind} "
            f"columns are present. Either this is the wrong file kind, or the "
            f"export's headers drifted — add the new spelling as a parallel entry "
            f"in column_maps rather than replacing the old one.")

    dropped = [c for c in frame.columns if c not in colmap]
    target.parent.mkdir(parents=True, exist_ok=True)
    # The sheet name must be one the pipeline will still find: the literal name for
    # Lazada, a name matching the regex for Shopee income, anything for a
    # positional lookup.
    sheet_name = sheets[0] if sheets and sheets[0] else "Sheet1"
    with pd.ExcelWriter(target, engine="openpyxl") as writer:
        frame[keep].to_excel(writer, sheet_name=sheet_name, index=False,
                             startrow=header_row - 1)

    order_ids, first, last = _identify(frame[keep], colmap, settings=settings,
                                       platform=platform, kind=kind)
    return SanitizeResult(sheet=str(sheet_name), rows=len(frame),
                          kept_columns=keep, dropped_columns=dropped or original[:0],
                          sheets_read=len(sheets), header_row=header_row,
                          order_ids=order_ids, settles_from=first, settles_to=last)


def _identify(frame, colmap: dict, *, settings: dict, platform: str,
              kind: str) -> tuple[list[str], "date | None", "date | None"]:
    """Distinct order ids and the settlement-date span, from the frame already in hand.

    Two questions the api could not answer before 2026-08-19, both of which cost real
    money in July:

    * *Does this file belong to the window it was uploaded to?* `POST /uploads` took
      `period` as a form field validated for character safety only. `stage_exports.py`
      has derived the window from settlement dates since M2.5 and the api had none of
      that (2.3's residual). The July mis-pulls are the bill.
    * *Which uploaded file holds store S's order X?* Needed to see defect 2.12 at all,
      because the answer lives across windows.

    **Dates are parsed with the pipeline's own accessor**, `ingest.date_format`, never
    a second spelling — that is the whole point of D54, and `stage_exports._read_dates`
    already goes through it. A column that is absent yields an empty answer rather than
    a guess.
    """
    import pandas as pd

    from src.ingest import date_format

    canon = {raw: canonical for raw, canonical in colmap.items()}
    order_ids: list[str] = []
    for raw, canonical in canon.items():
        if canonical == "order_id" and raw in frame.columns:
            ids = frame[raw].astype(str).str.strip()
            order_ids = sorted({i for i in ids if i and i.lower() not in
                                ("nan", "none", "<na>")})
            break

    first = last = None
    date_cols = [raw for raw, canonical in canon.items()
                 if canonical in ("statement_date", "transaction_date")
                 and raw in frame.columns]
    if date_cols:
        fmt = date_format(settings, platform, kind)
        parsed = pd.to_datetime(frame[date_cols[0]], errors="coerce",
                                format=fmt) if fmt else pd.to_datetime(
                                    frame[date_cols[0]], errors="coerce")
        good = parsed.dropna()
        if len(good):
            first, last = good.min().date(), good.max().date()
    return order_ids, first, last


def identify_file(source: Path, colmap: dict, *, settings: dict, platform: str,
                  kind: str) -> tuple[list[str], "date | None", "date | None"]:
    """Order ids and settlement span from a file already in the object store.

    The backfill path (`service/order_index.py`), for uploads that arrived before the
    door started indexing. It reads through `read_source` — the same function the door
    reads an *arriving* export with — so a backfilled file and a freshly uploaded one
    are identified by one code path rather than two that can drift apart. That works
    because the stored copy is the sanitized rewrite, which deliberately preserves the
    source's shape (band rows, junk rows, sheet naming) so `read_parts` still applies
    to it; anything that can read the original can read the copy.
    """
    frame, _sheets, _header_row = read_source(
        source, settings=settings, platform=platform, kind=kind)
    frame.columns = [unicodedata.normalize("NFC", str(c)).strip()
                     for c in frame.columns]
    return _identify(frame, colmap, settings=settings, platform=platform, kind=kind)


# The pre-M6 name. Kept so a reader grepping either spelling lands in one place;
# the name became a lie when the function learned to read .csv and .xls.
sanitize_excel = sanitize


def sanitized_name(filename: str) -> str:
    """The name the sanitized bytes are stored under: always `.xlsx`.

    Closes a latent bug rather than a stylistic one. The sanitizer writes openpyxl
    bytes; before M6 a `.csv` upload was written under its original `.csv` name,
    and `read_parts` dispatches on the suffix — so the pipeline would have handed
    a zip archive to `pd.read_csv`.
    """
    return f"{Path(filename).stem}.xlsx"
