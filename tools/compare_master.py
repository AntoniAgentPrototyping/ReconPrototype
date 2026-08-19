"""Compare our month-end master against the team's, tab by tab (task 3.7).

    python tools/compare_master.py --month 2026-07 --reference "input/master"

The team's file is `ADA marketplace MASTER July 2026.xlsx`, encrypted by a
Microsoft Purview sensitivity label (`ingest.rights_protected`), so nothing here
can open it. What it reads instead is the per-tab CSV export of that file, one
CSV per tab, named `<anything> - <tab>.csv`.

**Read `docs/07-VERIFICATION.md` before quoting a result from this.** Two things
about it are not obvious:

1. **A difference is not automatically our error.** Five defects have already been
   found in the team's own workbooks (`docs/08-KNOWN-DEFECTS.md` Part 2) by exactly
   this kind of comparison. A difference starts a conversation; it does not settle
   one.
2. **Window labels are matched by ORDER, not by name.** The team's tabs head their
   columns with day ranges (`01-07`) and ours with window ids (`w1`), and there is
   no lookup between them that is not the hardcoded table this phase deleted. Both
   sides list a platform's windows in settlement order, so the nth column is
   compared with the nth column — and the tool refuses if the two sides do not
   have the same NUMBER of windows for a platform, because then the alignment
   would be silently wrong rather than obviously wrong.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import master_summary as ms                              # noqa: E402

# VND, and the finance files carry sub-dong floats that the team's Excel rounds
# for display. A dong is the smallest unit that can be meaningfully different.
TOL = 1.0


def _num(cell: str) -> float | None:
    """'  1,234,567 ' -> 1234567.0;  ' -   ' -> 0.0;  a label -> None."""
    s = (cell or "").strip().replace(",", "")
    if s in ("", "-", "–", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None


def read_reference(folder: Path) -> dict[str, list[list[str]]]:
    """{tab name: rows}. The tab is the part of the filename after the last ' - '.

    **These CSVs are cp1252, and Vietnamese did not survive the export.** Excel's
    "CSV (Comma delimited)" writes the system ANSI codepage, so a storefront like
    `Unilever Chăm Sóc Vẻ Đẹp` came out as `Unilever Ch?m S�c V? ??p`. Nothing here
    can recover those characters — they were lost when the file was written, not
    when it is read. Decoding as cp1252 at least keeps the row and its NUMBER,
    which is what the comparison is actually about; `_skeleton` then matches the
    mangled label against our intact one.
    """
    tabs: dict[str, list[list[str]]] = {}
    for path in sorted(folder.glob("*.csv")):
        stem = path.stem
        tab = stem.rsplit(" - ", 1)[-1].strip().lower() if " - " in stem else stem.lower()
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = path.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:                                                   # pragma: no cover
            raise SystemExit(f"cannot decode {path.name}")
        tabs[tab] = list(csv.reader(text.splitlines()))
    return tabs


def _skeleton(name: str) -> str:
    """A key that survives the reference export's lost Vietnamese.

    Every non-ASCII character becomes '?', because that is what the cp1252 export
    did to ours. `thuận phát` and `thu?n ph�t` both reduce to `thu?n?ph?t`, so the
    row can still be compared. Deliberately NOT a diacritic strip: that would make
    `thuan phat` and `thuận phát` equal, and those are two different rows in the
    team's own file — merging them would invent agreement.
    """
    s = ms.norm_store(name)
    return re.sub(r"[^a-z0-9]+", "?",
                  "".join(c if c.isascii() else "?" for c in s)).strip("?")


def summary_rows(rows: list[list[str]]) -> dict[tuple[str, int], tuple[float, float]]:
    """The team's dashboard tab -> {(platform, nth window): (pre, with_vat)}.

    Ignores the '<platform> total' and 'ALL PLATFORMS' lines: they are sums of
    the rows above, so comparing them would double-count agreement.
    """
    out: dict[tuple[str, int], tuple[float, float]] = {}
    counters: dict[str, int] = {}
    for row in rows:
        if len(row) < 4:
            continue
        label = (row[0] or "").strip()
        if not label or label.lower().startswith("all platforms") or "total" in label.lower():
            continue
        pre, wv = _num(row[2]), _num(row[3])
        if pre is None or wv is None:
            continue
        n = counters.get(label, 0)
        counters[label] = n + 1
        out[(label, n)] = (pre, wv)
    return out


def grid(rows: list[list[str]], *, key_col: int = 0) -> tuple[list[str], dict[str, list[float]]]:
    """A `_grid`-shaped tab -> (column headers, {row label: values})."""
    header_i = next((i for i, r in enumerate(rows)
                     if len(r) > 1 and any((c or "").strip() for c in r[1:])
                     and _num(r[1]) is None), None)
    if header_i is None:
        return [], {}
    cols = [(c or "").strip() for c in rows[header_i][1:] if (c or "").strip()]
    out: dict[str, list[float]] = {}
    for r in rows[header_i + 1:]:
        if len(r) < 2:
            continue
        label = (r[key_col] or "").strip()
        if not label or label.lower() == "total":
            continue
        vals = [_num(c) for c in r[1:1 + len(cols)]]
        if any(v is None for v in vals):
            continue
        out[label] = [v for v in vals if v is not None]
    return cols, out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True)
    ap.add_argument("--reference", default=str(ROOT / "input" / "master"))
    ap.add_argument("--output-root", default=str(ROOT / "output"))
    args = ap.parse_args(argv)

    from tools.build_master_summary import discover

    ours = discover(Path(args.output_root), args.month)
    if not ours:
        raise SystemExit(f"no finance files under {args.output_root}/{args.month}_*/")
    grouped = ms.order_windows(ours)
    theirs = read_reference(Path(args.reference))
    if not theirs:
        raise SystemExit(f"no reference CSVs under {args.reference}")
    print(f"reference tabs: {sorted(theirs)}\n")

    problems = 0

    # ---- the dashboard / summary -----------------------------------------
    dash = next((t for t in ("dashboard", "summary") if t in theirs), None)
    if dash is None:
        print("!! no dashboard/summary tab in the reference")
        problems += 1
    else:
        ref = summary_rows(theirs[dash])
        print(f"WINDOW TOTALS  (from the '{dash}' tab)")
        print(f"  {'platform':<9}{'window':<8}{'ours (with VAT)':>20}"
              f"{'theirs':>20}{'diff':>16}")
        for platform, windows in grouped.items():
            n_ref = sum(1 for (p, _) in ref if p == platform)
            if n_ref != len(windows):
                print(f"  !! {platform}: we have {len(windows)} window(s), the "
                      f"reference has {n_ref} — NOT comparing by position")
                problems += 1
                continue
            for i, w in enumerate(windows):
                r_pre, r_wv = ref[(platform, i)]
                o_pre = float(w.totals["pre"].sum())
                o_wv = float(w.totals["wv"].sum())
                d_pre, d_wv = o_pre - r_pre, o_wv - r_wv
                flag = "" if abs(d_pre) < TOL and abs(d_wv) < TOL else "   <-- DIFF"
                if flag:
                    problems += 1
                print(f"  {platform:<9}{w.label:<8}{o_wv:>20,.0f}{r_wv:>20,.0f}"
                      f"{d_wv:>16,.0f}{flag}")
                if flag and abs(d_pre) >= TOL:
                    print(f"  {'':<17}pre-VAT{o_pre:>20,.0f}{r_pre:>20,.0f}"
                          f"{d_pre:>16,.0f}")

    # ---- per-platform storefront tabs -------------------------------------
    for platform, windows in grouped.items():
        tab = platform.lower()
        if tab not in theirs:
            continue
        cols, ref_rows = grid(theirs[tab])
        # Their last column is 'Sum of Amount before VAT'; the rest are windows.
        n_windows = len(cols) - 1 if cols and "before vat" in cols[-1].lower() else len(cols)
        print(f"\n{platform.upper()} BY STOREFRONT  ({len(ref_rows)} row(s) in the "
              f"reference, {n_windows} window column(s))")
        ours_by_store: dict[str, float] = {}
        names: dict[str, str] = {}
        for w in windows:
            for s, v in zip(w.totals["store"], w.totals["wv"]):
                k = _skeleton(s)
                ours_by_store[k] = ours_by_store.get(k, 0.0) + float(v)
                names.setdefault(k, s)
        ref_by_store: dict[str, float] = {}
        for label, vals in ref_rows.items():
            k = _skeleton(label)
            ref_by_store[k] = ref_by_store.get(k, 0.0) + sum(vals[:n_windows])
            names.setdefault(k, label)

        only_ours = sorted(set(ours_by_store) - set(ref_by_store))
        only_theirs = sorted(set(ref_by_store) - set(ours_by_store))
        for k in only_ours:
            print(f"  ONLY OURS    {names[k]:<52}{ours_by_store[k]:>18,.0f}")
            problems += 1
        for k in only_theirs:
            print(f"  ONLY THEIRS  {names[k]:<52}{ref_by_store[k]:>18,.0f}")
            problems += 1
        both = sorted(set(ours_by_store) & set(ref_by_store))
        diffs = 0
        for k in both:
            d = ours_by_store[k] - ref_by_store[k]
            if abs(d) >= TOL:
                print(f"  DIFF         {names[k]:<40}{ours_by_store[k]:>18,.0f}"
                      f"{ref_by_store[k]:>18,.0f}{d:>16,.0f}")
                problems += 1
                diffs += 1
        if not only_ours and not only_theirs and not diffs:
            print(f"  all {len(both)} storefront(s) match to within {TOL:.0f} VND")

    print(f"\n{'MATCHES' if problems == 0 else f'{problems} DIFFERENCE(S)'} — "
          f"a difference is a finding to investigate, not proof either side is wrong "
          f"(docs/08-KNOWN-DEFECTS.md Part 2).")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
