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

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
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


class UploadRejected(ValueError):
    """The file cannot be accepted. Always names the reason — a rejected upload
    at month end is an operator's problem to solve, not a mystery."""


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

    @property
    def dropped_known_pii(self) -> list[str]:
        return sorted(c for c in self.dropped_columns if c in KNOWN_PII)


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


def column_map_for(settings: dict, platform: str, kind: str) -> dict[str, str]:
    """The allowlist for this platform and file kind.

    Lazada's maps are hardcoded in `src/lazada.py` rather than in YAML — a real
    asymmetry in the codebase, noted in docs/08-KNOWN-DEFECTS.md 1.10 — so this
    reaches into the module rather than pretending the config covers it.
    """
    if platform == "lazada":
        from src import lazada
        return dict(lazada.WEEKLY_MAP if kind == "weekly" else lazada.DAILY_MAP)

    from src import config as src_config
    return dict(src_config.column_map(settings, platform, kind))


def sheet_for(platform: str, kind: str, settings: dict) -> Any:
    if platform == "lazada":
        from src import lazada
        return lazada.SHEETS["weekly" if kind == "weekly" else "daily"]
    sheets = (settings.get("sheet_names") or {}).get(platform) or {}
    return sheets.get(kind, 0)


def _shape(settings: dict, platform: str, kind: str) -> dict:
    """The four config facts that describe this file's SHAPE, not its columns.

    Read here for the same reason the column map is: `read_parts` will apply every
    one of them to the sanitized copy, so the copy has to still satisfy them.
    """
    if platform == "lazada":
        # Lazada's reader takes none of these — `lazada.read_ledger` reads the
        # named sheet with a header on row 1 and no skip. Hardcoded there, so
        # hardcoded here rather than invented from an absent config key.
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

    from src.ingest import read_excel_sheet

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

    return SanitizeResult(sheet=str(sheet_name), rows=len(frame),
                          kept_columns=keep, dropped_columns=dropped or original[:0],
                          sheets_read=len(sheets), header_row=header_row)


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
