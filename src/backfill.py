"""Borrowing an order's SKU lines from the window that actually exported them.

[Defect 2.12](../docs/08-KNOWN-DEFECTS.md). `explode_to_sku_*` joins a window's income
to the order files staged in *that window's* folder. An order settled in `w2` may have
been created days earlier, so its lines live in `w1`'s export; the income row matches
nothing and the revenue leaves the invoice through the documented "~21% unmatched" door,
quietly, because that door is expected to have traffic. July's first external month-end
comparison put **4,527,401,608 VND** through it.

**One mechanism, three modes**, read from `cross_window_order_backfill`:

* `off` — nothing here runs. Byte-for-byte the behaviour that produced every committed
  golden.
* `report` — measure it and say so. The tie-out gains an INFO row, the exceptions
  workbook gains a sheet, the log names the windows. **No number changes**: the orders
  frame handed to the explode is untouched.
* `apply` — the same computation, with the borrowed lines concatenated onto the orders
  frame before the explode. This moves cells and is gated accordingly.

Detection and fix are the *same code path*, exercised in production under `report`
before it is ever allowed to move money.

## The rule, and why each half of it is that way

For each `(store, order_id)` the income settles but this window's order files do not
contain, take that order's SKU lines from the **nearest same-month predecessor window**
that has them — exactly **one** window per order.

*One window per order, nearest first.* A re-pulled order file appears in several
windows (TikTok re-ships each store's prior-month pull in every weekly folder,
deliberately, because the cross-period stitch needs it). Summing every window that
holds a copy is the pooling anti-fix, and it is **measured**: pooling July's TikTok
order exports into `w2` takes that window to 183,102,704,362 VND against a reference of
40,060,544,029 — a 4.5× over-count. The explode sums quantity per
`(store, order_id, sku_id, sku_name, unit_price_gross)` bucket, so a second copy
inflates quantity *inside* one SKU line rather than adding a visible row.

*Within one window, every file's lines are kept.* That is the same rule the window's own
part-files already follow — `dedupe_rows: false`, because an order legitimately contains
two byte-identical SKU lines (a normal unit and a gift variant), and row content cannot
tell that apart from a re-pull ([D5](../docs/06-DECISIONS.md#d5)). The discriminator is
file-level provenance, never row identity.

*Predecessors only.* Borrowing from a *later* window would make a run's output depend on
what has arrived since, so re-running `w2` after `w5` was staged could produce a
different invoice from the same inputs. Reproducibility over recall.

*Income is never re-read.* Which settlement rows belong to a window is already decided,
declared and verified — `window_settlement_bounds` ([D9](../docs/06-DECISIONS.md#d9)).
Measured on the trap this could have walked into: of the 24,555 rows the `2026-07_w2`
bound drops, **24,546 of 24,546 distinct order ids are already in w1** — zero unique.

## The safety property this buys

Borrowed orders enter the tie-out's matched population, so TikTok's per-order settlement
conservation (1 VND tolerance) and Shopee's revenue crossing run **over the borrowed
lines**. A predecessor re-export whose quantities drifted therefore *breaches a check*
rather than silently mis-invoicing. That is the difference between this and pooling: the
lines are pulled through the same controls the window's own lines pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import config, ingest
from .errors import ReconHardStop
from .runlog import RunLog

# `2026-07_w2` -> ("2026-07", "w", "2")  ·  `2026-05_s2x` -> ("2026-05", "s", "2x")
_WINDOW = re.compile(r"^(?P<month>\d{4}-\d{2})_(?P<letter>[a-zA-Z]+)(?P<ordinal>.*)$")

MODES = ("off", "report", "apply")


@dataclass(frozen=True)
class BorrowReport:
    """What one predecessor window supplied. Counts and identities only."""
    window: str
    orders: int
    lines: int
    files: tuple[str, ...]


@dataclass(frozen=True)
class CrossWindowResult:
    """What this window found across its predecessors, and whether it used it.

    `orders` is the frame the explode should consume — the window's own orders under
    `off` and `report`, those plus the borrowed lines under `apply`. Keeping the
    decision in one object is what stops a runner from reporting one thing and
    exploding another.
    """
    mode: str
    orders: pd.DataFrame
    borrowed: pd.DataFrame
    reports: list[BorrowReport]
    money: float
    applied: bool

    @property
    def orders_found(self) -> int:
        return sum(r.orders for r in self.reports)

    @property
    def lines(self) -> int:
        return sum(r.lines for r in self.reports)


def mode_of(settings: dict) -> str:
    """The configured mode, refusing an unknown value rather than guessing.

    A typo must not silently mean `off`: that is the fail-quiet direction, and this
    setting's whole purpose is to make a measured gap visible.

    Neither may ABSENCE mean `off`, and that half was wrong here until 2026-08-21
    (A9). The default was written on 2026-08-20 while the mode still was `off`, so
    it was true and safe; the flip to `apply` later the same day made it a silent
    revert of defect 2.12's fix worth 2.33B VND of July recovery. The typo case was
    guarded and the missing-key case was not, which is the more likely of the two.
    """
    value = str(config.require(settings, "cross_window_order_backfill")).strip()
    if value not in MODES:
        raise ReconHardStop(
            f"cross_window_order_backfill is {value!r}, which is not one of "
            f"{list(MODES)}. 'off' is today's behaviour, 'report' measures the gap "
            f"without changing a number, and 'apply' borrows the lines and moves "
            f"cells (defect 2.12).")
    return value


def predecessor_labels(period: str, candidates) -> list[str]:
    """Which of `candidates` are earlier windows of the same series, nearest first.

    `2026-07_w4` against the month's labels -> `['2026-07_w3', '2026-07_w2',
    '2026-07_w1']`.

    **The rule, in one place, over any source of candidates.** The CLI's candidates are
    directories under the input root; the service's are window labels out of Postgres.
    Two spellings of "which window is earlier" is precisely the class of drift this
    whole defect is made of, so there is one.

    Same month *and* same series letter: a TikTok weekly (`w`) and a Shopee batch (`s`)
    are never each other's predecessors. Ordinals compare as **strings**, which puts
    `s2` before `s2x` — the intended order, since a sub-batch extends the batch it is
    named after and is staged later. Candidates that are not window-shaped are ignored
    rather than refused; a stray folder is not this run's problem.
    """
    own = _WINDOW.match(period)
    if own is None:
        return []
    month, letter, ordinal = own.group("month"), own.group("letter"), own.group("ordinal")

    earlier: list[tuple[str, str]] = []
    for name in candidates:
        name = str(name)
        if name == period:
            continue
        other = _WINDOW.match(name)
        if other is None:
            continue
        if other.group("month") != month or other.group("letter") != letter:
            continue
        if other.group("ordinal") >= ordinal:
            continue
        earlier.append((other.group("ordinal"), name))

    # Nearest first: the highest ordinal below our own.
    return [name for _, name in sorted(earlier, reverse=True)]


def predecessor_windows(input_root: Path, period: str, platform: str) -> list[str]:
    """`predecessor_labels` over the input root, keeping only windows with orders."""
    if not input_root.is_dir():
        return []
    names = [e.name for e in input_root.iterdir() if e.is_dir()]
    return [name for name in predecessor_labels(period, names)
            if (input_root / name / platform / "orders").is_dir()]


def borrow_order_lines(
    input_root: Path, period: str, platform: str, needed: frozenset,
    colmap: dict[str, str], settings: dict, log: RunLog, *, strict: bool = False,
) -> tuple[pd.DataFrame, list[BorrowReport]]:
    """SKU lines for `needed` orders, from the nearest predecessor window holding each.

    `needed` is a set of `(store, order_id)` pairs — the identity everything keys on
    since M2.5 (`tieout.pairs`). Returns the borrowed rows and one report per window
    that supplied any.

    Deterministic, because a run must be reproducible from its inputs: windows are
    visited nearest-first, files within a window in `read_parts`' own order, and the
    output is sorted on a fixed key. Reads through `ingest.read_files` **and**
    `ingest.normalize_parts`, which together are the reading path every verified
    number was produced under — see the note on the prefilter below for what it cost
    to have only the first half of that.

    `strict` decides what a broken predecessor costs. Under `apply` (strict) a
    predecessor that cannot be read is a refusal: those rows are about to become
    invoice lines. Under `report` it is a warning and that window is skipped — the
    mode's whole contract is that it changes nothing, and killing a settlement run
    over a *sibling* window's bad file is a control firing on the wrong window (the
    same reasoning `_store_of` already applies to an unparseable filename).
    """
    if not needed:
        return _empty(colmap), []

    remaining = set(needed)
    stores_wanted = {store for store, _ in remaining}
    borrowed: list[pd.DataFrame] = []
    reports: list[BorrowReport] = []
    store_pattern = (settings.get("store_from_filename") or {}).get(platform)

    for window in predecessor_windows(input_root, period, platform):
        if not remaining:
            break
        folder = input_root / window / platform / "orders"
        files = ingest.export_files(folder, settings)
        # Prefilter by store: a window holds every storefront's export and we may want
        # one. Only possible because store identity is in the FILENAME (D6). Without a
        # pattern the platform has a store column and every file has to be read.
        #
        # Through `canonical_store`, and that is load-bearing: `needed` comes from
        # frames that went through `read_parts`, so it holds ALIASED store names,
        # while a filename holds whatever the platform exported. Comparing the two
        # raw skipped every aliased store's files before anything could read them —
        # `settings.yaml` maps "Pediasure" to "Abbott Pediasure" because the order
        # files drop the "Abbott", so July's 941,081,056 VND Abbott recovery reported
        # as zero until 2026-08-20 (defect 2.12, found by review).
        if store_pattern:
            files = [f for f in files
                     if ingest.canonical_store(
                         settings, platform, _store_of(f.name, store_pattern))
                     in stores_wanted]
        if not files:
            continue

        log.add(f"  cross-window: reading {len(files)} order file(s) from {window} "
                f"for {len(remaining):,} unmatched order(s)")
        try:
            frames = ingest.read_files(files, colmap, "orders", settings, log, platform)
            if not frames:
                continue
            found = pd.concat(frames, ignore_index=True)
            # The SAME post-read rules the window's own orders got. Without this the
            # borrowed rows carried un-aliased stores and uncoerced text where the
            # explode expects numbers — it groups on `unit_price_gross` and sums
            # `quantity`, so raw text is both a different bucket and an unsummable
            # column. Labelled with the source window so a coercion warning names the
            # file it came from rather than impersonating this window's own read.
            found = ingest.normalize_parts(
                found, "orders", settings, log, platform,
                where=f"{platform}/orders borrowed from {window}")
        except Exception as exc:                    # noqa: BLE001 — see below
            # Deliberately broad. A predecessor can fail to read in more ways than one
            # type: `ReconHardStop` for an unparseable amount, pandas' own `ValueError`
            # for a missing sheet, `BadZipFile` for a truncated upload. Every one of
            # them is the same thing to a report — a sibling window that cannot be
            # checked — and the same precedent `_store_of` sets a few lines down. The
            # type is named either way so a genuine bug in this module stays visible
            # rather than reading as bad input.
            if strict:
                # `apply`: these rows were about to become invoice lines, so refuse —
                # but refuse in this project's own voice. A bare pandas ValueError
                # reaching a finance operator names a worksheet and no remedy. An
                # existing ReconHardStop is already written for this reader and passes
                # through untouched (the `_OWN_MESSAGE` rule service/failures.py uses).
                if isinstance(exc, ReconHardStop):
                    raise
                raise ReconHardStop(
                    f"cross-window borrow is set to `apply`, so {window}'s order files "
                    f"would supply invoice lines for {period} — and they cannot be read "
                    f"({type(exc).__name__}: {exc}). Fix or re-upload that window's "
                    f"order export, or set cross_window_order_backfill: report to "
                    f"measure without invoicing from it.") from exc
            log.warn(f"cross-window: {window}'s order files could not be read, so that "
                     f"window cannot be checked for borrowed lines "
                     f"({type(exc).__name__}: {exc})")
            continue

        keys = pd.Series(list(zip(found["store"], found["order_id"])),
                         index=found.index, dtype=object)
        take = found[keys.isin(remaining)].copy()
        if not len(take):
            continue

        take["source_window"] = window
        borrowed.append(take)
        supplied = frozenset(zip(take["store"], take["order_id"]))
        reports.append(BorrowReport(
            window=window, orders=len(supplied), lines=len(take),
            files=tuple(sorted(take["source_file"].unique()))))
        # Taken care of by THIS window — so a nearer window always wins and no order
        # is ever supplied twice. This is the fan-out guard.
        remaining -= supplied

    if not borrowed:
        return _empty(colmap), []

    out = pd.concat(borrowed, ignore_index=True)
    sort_on = [c for c in ("store", "order_id", "sku_id", "source_window", "source_file")
               if c in out.columns]
    out = out.sort_values(sort_on, kind="mergesort").reset_index(drop=True)

    # A structural guard rather than a comment: an order this window already covers
    # must never be borrowed, or its quantity doubles inside one SKU line where no new
    # row appears to show it. `needed` is built from what the window does NOT have, so
    # a violation means the caller passed the wrong set.
    #
    # A refusal, not an `assert`: this guards the ONE failure mode that is invisible in
    # the workbook (the explode inflates an existing SKU line rather than adding a
    # row — the measured 4.5x pooling over-count), and `python -O` deletes asserts. The
    # single statement standing between a re-pull and a doubled invoice must not be the
    # one an optimisation flag removes.
    #
    # Unreachable today, and that is the honest state: `take` is masked by
    # `keys.isin(remaining)` and `remaining` only ever shrinks, so no input can violate
    # this. It exists so that a future change to THAT mask fails loudly here instead of
    # quietly doubling money. No test drives it — reaching it needs the internals
    # mocked, which would assert the mock rather than the guard.
    extra = frozenset(zip(out["store"], out["order_id"])) - frozenset(needed)
    if extra:
        raise ReconHardStop(
            f"cross-window borrow returned {len(extra):,} order(s) that were not asked "
            f"for, so the caller's `needed` set is wrong. Applying these would double "
            f"quantity INSIDE existing SKU lines, where no new row would reveal it "
            f"(defect 2.12's pooling anti-fix, measured at 4.5x). Refusing.")
    return out, reports


def _empty(colmap: dict[str, str]) -> pd.DataFrame:
    """An empty frame shaped like the borrowed one, so callers need no special case."""
    columns = list(dict.fromkeys(list(colmap.values())
                                 + ["store", "source_file", "source_window"]))
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def _store_of(filename: str, pattern: str) -> str:
    """The store a file belongs to, or `""` when the name does not parse.

    Through the pipeline's own parser. A name this cannot read is skipped rather than
    refused: this is a *reporting* path over a window nobody asked to run, and hard
    stopping the current window because a sibling folder holds an oddly named file
    would be a control firing on the wrong window.
    """
    try:
        return ingest.store_from_filename(filename, pattern)
    except Exception:                                    # noqa: BLE001 — see above
        return ""


def resolve(
    *, input_root: Path, period: str, platform: str, orders: pd.DataFrame,
    settled: pd.DataFrame, money_col: str | None, colmap: dict[str, str],
    settings: dict, log: RunLog,
) -> CrossWindowResult:
    """The whole mechanism, for one window, in the mode the contract asks for.

    Called by both runners so the decision cannot be made two slightly different
    ways — the drift this project has watched happen before (`_vat_sku`, the
    sanitizer's per-platform shape handling). `settled` is the classified income
    the explode is about to join; `money_col` names its settlement column and may be
    `None`, in which case the recoverable figure is reported as 0 rather than guessed.

    In `off` mode this returns before reading anything, so a deployment that has not
    opted in pays nothing — not even a directory listing.
    """
    mode = mode_of(settings)
    unchanged = CrossWindowResult(mode=mode, orders=orders, borrowed=_empty(colmap),
                                 reports=[], money=0.0, applied=False)
    if mode == "off":
        return unchanged

    from . import tieout                      # local: tieout imports this module
    needed = tieout.pairs(settled) - tieout.pairs(orders)
    if not needed:
        log.add("  cross-window: every settled order has lines in this window")
        return unchanged

    # Strict only where the rows become money: under `apply` an unreadable predecessor
    # is a refusal, under `report` it is a warning against a window nobody asked to run.
    borrowed, reports = borrow_order_lines(
        input_root, period, platform, needed, colmap, settings, log,
        strict=(mode == "apply"))
    if not reports:
        log.add(f"  cross-window: {len(needed):,} settled order(s) have no lines in "
                f"this window and none in an earlier one either — the ordinary "
                f"reconciling class, reported as unmatched")
        return unchanged

    # What the borrowed orders are worth, from the INCOME side — the settlement
    # figure, not anything rebuilt from the borrowed lines. Reporting the rebuilt
    # value would be reporting our own arithmetic back to ourselves.
    money = 0.0
    if money_col and money_col in settled.columns:
        supplied = frozenset(zip(borrowed["store"].astype(str),
                                 borrowed["order_id"].astype(str)))
        keys = tieout.pair_series(settled)
        money = float(settled.loc[keys.isin(supplied), money_col].fillna(0).sum())

    applied = mode == "apply"
    log.add(f"  cross-window: {summarize(reports)}"
            + (f" — APPLIED, {money:,.0f} VND of settlement now has lines"
               if applied else
               f" — NOT applied (report mode); {money:,.0f} VND of settlement stays "
               f"outside the invoice"))

    # No `derive_brand` on the borrowed rows, deliberately. The runners brand `orders`
    # before this stage, so the concatenated frame carries a null brand on borrowed
    # rows — and that is harmless because the explode's groupby keeps only
    # (store, order_id, sku_id, sku_name, unit_price_gross) and the SKU frame takes its
    # brand from the INCOME side of the merge. Branding here would be a call in the
    # money path that provably changes nothing; checked 2026-08-20 rather than assumed.
    frame = (pd.concat([orders, borrowed], ignore_index=True) if applied else orders)
    return CrossWindowResult(mode=mode, orders=frame, borrowed=borrowed,
                            reports=reports, money=money, applied=applied)


def exception_rows(result: CrossWindowResult) -> pd.DataFrame:
    """One row per borrowed order, for the exceptions workbook.

    Identities and provenance — which order, which window exported it, which file,
    and whether this run used it. No money per row: the recoverable total is a
    tie-out figure, and repeating it per row would invite someone to sum a column
    that is not a settlement.
    """
    if result.borrowed.empty:
        return pd.DataFrame(columns=["store", "order_id", "source_window",
                                     "source_file", "applied"])
    rows = result.borrowed[["store", "order_id", "source_window", "source_file"]].copy()
    rows = rows.drop_duplicates(ignore_index=True)
    rows["applied"] = result.applied
    return rows


def summarize(reports: list[BorrowReport]) -> str:
    """One line for the log and the tie-out detail."""
    if not reports:
        return "none"
    return "; ".join(f"{r.orders:,} order(s)/{r.lines:,} line(s) from {r.window}"
                     for r in reports)
