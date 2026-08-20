"""Turning a run's exception frames into a queue a human can work.

`RunResult.exceptions` is a dict of DataFrames — unmatched orders, unmapped fee
names, tie-out breaches. Until M5 they were written to `exceptions.xlsx` and
that was the end of it: to know whether an exception was new, you opened this
week's workbook next to last week's.

**The fingerprint is the whole point.** It is a stable identity for one exception
across runs, so "this unmatched order has recurred for six weeks" becomes a
query rather than an archaeology exercise. M6 hangs *dispositions* off it — a
decision that survives a re-run — and getting the identity wrong now would be
expensive to correct then, because dispositions would already be attached to it.

So it is built from **identity columns only**. Including an amount would mean a
row's fingerprint changed whenever its value did, which is exactly when you most
want to recognise it as the same row.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any, Iterable

# What identifies a row, per exception sheet. Ordered — the fingerprint is
# order-sensitive, so these lists are part of the contract and reordering one
# silently orphans every existing fingerprint.
IDENTITY_COLUMNS: dict[str, tuple[str, ...]] = {
    # ~21% of TikTok GOOD settlement has no matching order lines. Expected, and
    # reported every run as a RECONCILING line — the thing worth watching is a
    # change in it, which is precisely what a per-row identity makes visible.
    "unmatched_orders": ("store", "order_id"),
    # A fee name absent from the team-owned master. The name recurring week after
    # week is the signal; the transaction it appeared on is not.
    "unmapped_fees": ("store", "fee_name"),
    # One row per failing check, so the check name IS the identity.
    "tieout_breaches": ("check",),
    # One row per store per run: how much of its settled money reached no SKU line
    # in this window. The store IS the identity, because the question this sheet
    # answers is month-over-month — "did this storefront's coverage change?" — and
    # that is the only thing that distinguishes ordinary traffic through the ~21%
    # door from defect 2.12. A window TOTAL cannot answer it: on the TikTok golden
    # window the entire 21% is one store, so the total looks the same whether the
    # cause is legitimate or an order export that does not cover its own window.
    "order_coverage": ("store",),
    # One row per order settled here whose lines were exported with an earlier
    # window. Keyed on the order, NOT on the window that holds it: the same order
    # recurring run after run means the export was never re-pulled, and that is the
    # thing worth chasing. Including `source_window` would make a re-pull that moved
    # the lines to a different sibling look like a brand-new exception.
    "cross_window_orders": ("store", "order_id"),
}

# Rows whose identity columns are all missing fall back to hashing the whole
# row. That is honest but weak — such a fingerprint changes when any value does —
# so it is counted and surfaced rather than passing silently.
FALLBACK_MARKER = "whole-row"


def fingerprint(sheet: str, row: dict) -> str:
    """Stable identity for one exception row.

    The sheet name is inside the hash so two sheets cannot collide on a shared
    key — `store` alone appears in most of them.
    """
    columns = IDENTITY_COLUMNS.get(sheet)
    if columns and any(_present(row.get(c)) for c in columns):
        parts = [sheet] + [_norm(row.get(c)) for c in columns]
    else:
        parts = [sheet, FALLBACK_MARKER] + [
            f"{k}={_norm(v)}" for k, v in sorted(row.items()) if _present(v)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def uses_fallback(sheet: str, row: dict) -> bool:
    columns = IDENTITY_COLUMNS.get(sheet)
    return not (columns and any(_present(row.get(c)) for c in columns))


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and value != value:     # NaN
        return False
    return str(value).strip() != ""


def _norm(value: Any) -> str:
    """Normalize for hashing.

    Floats that are whole numbers render as integers because pandas will hand
    back `1.0` where the export held `1` — a fingerprint that changes with a
    dtype is not an identity.

    **NFC since M6, and for the same reason.** A fingerprint that changes with a
    Unicode form is not an identity either: `store` and `fee_name` are Vietnamese
    values, and NFD is byte-unequal to the visually identical NFC. This is the same
    bug `ingest.py:211` fixes for headers and `pipeline.norm_store` has always fixed
    for store names — `_norm` never got the treatment, so an export arriving
    decomposed would have silently orphaned every stored fingerprint and detached its
    history, with nothing to see.

    **Measured before it was changed** (`service/nfc_audit.py`, 2026-08-17): **0**
    non-NFC identity values anywhere — 0 of 118 live `.xlsb` fee names, 0 of 118 in
    the CSV snapshot, 0 store names in `settings.yaml`, 0 stored `run_exceptions`
    rows. So this moves no existing fingerprint and `006_exception_nfc.sql` is a
    recorded no-op. It is done anyway: it costs one call, and the next Vietnamese
    value that arrives decomposed is otherwise a silent history break.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:
            return ""
        if value.is_integer():
            return str(int(value))
    return unicodedata.normalize("NFC", str(value).strip())


def frame_rows(frame: Any, *, cap: int) -> tuple[list[dict], int]:
    """(rows, total). Caps deliberately and reports the total separately.

    A capped queue is fine; a capped queue that looks complete is not. The caller
    stores both numbers and the api returns `truncated`, so nothing here has to
    guess whether the reader knows.
    """
    if frame is None:
        return [], 0
    total = int(len(frame))
    if total == 0:
        return [], 0
    head = frame.head(cap)
    rows = head.to_dict(orient="records")
    return rows, total


def summarize(rows: Iterable[dict], sheet: str) -> dict:
    """Counts a UI can show without re-deriving them, including how many rows
    fell back to whole-row identity."""
    rows = list(rows)
    fallback = sum(1 for r in rows if uses_fallback(sheet, r))
    return {"sheet": sheet, "rows": len(rows), "weak_identity": fallback}
