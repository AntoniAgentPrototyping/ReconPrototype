"""Build the month-end master from the finance files in `output/`.

    python tools/build_master_summary.py --month 2026-07 \
        --out "C:/Users/.../ADA marketplace master July 2026.xlsx"

**A thin CLI over `src/master_summary.py`, since M8 Phase 3.** It used to be the
whole implementation — ~300 lines of reading, aggregation and workbook building
that lived outside the verified pipeline, which is what
`docs/06-DECISIONS.md#d24` forbids. The compute moved to `src/`; this keeps the
CLI first-class, which is the other half of D24.

**The window list is discovered, never declared.** This file used to carry

    WINDOWS = {"TikTok": [w1..w5], "Shopee": [s1..s4], "Lazada": [l1..l5]}

which omitted the sub-batch windows that really exist (`s2x`, `s3k`) while its own
tie check re-read the same dict — so it could not notice its own omission
(register A5). Here the windows are whatever `output/<month>_*/` holds; in the
service they are whatever the database says ran (`service/month_master.py`). Both
answers come from the world rather than from a list somebody has to remember to
update.

The tie check is no longer a printed line: it is `--check`, and it is the same
assertion `tests/test_master_summary.py` makes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `tools/` must never import `service/` (tests/test_io_boundary.py and
# tests/service/test_service_is_deletable.py): the container ships src/ + service/
# without tools/, and this script must keep working with service/ deleted. The
# shared rules live in src/master_summary.
from src import master_summary as ms                         # noqa: E402


def brand_map(config_dir: Path) -> dict[tuple[str, str], tuple[str, str, str]]:
    """`config/brand_map.csv`, parsed by the shared rule in `src/`."""
    path = Path(config_dir) / "brand_map.csv"
    if not path.is_file():
        return {}
    return ms.parse_brand_map(path.read_text(encoding="utf-8-sig"))


def discover(output_root: Path, month: str) -> list[ms.Window]:
    """Every window of `month` with a finance file under `output/`."""
    windows: list[ms.Window] = []
    for period_dir in sorted(output_root.glob(f"{month}_*")):
        if not period_dir.is_dir():
            continue
        for platform_dir, platform in sorted(ms.DIR_PLATFORM.items()):
            path = period_dir / platform_dir / "finance_file.xlsx"
            if not path.is_file():
                continue
            windows.append(ms.Window(
                platform=platform, period=period_dir.name,
                label=ms.window_label(period_dir.name),
                totals=ms.read_window(path, platform),
                source=path.relative_to(ROOT).as_posix()))
    return windows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="e.g. 2026-07")
    ap.add_argument("--out", required=True)
    ap.add_argument("--output-root", default=str(ROOT / "output"))
    ap.add_argument("--check", action="store_true",
                    help="Assert every column total ties to the window it came "
                         "from, and exit non-zero if one does not.")
    args = ap.parse_args(argv)

    windows = discover(Path(args.output_root), args.month)
    if not windows:
        raise SystemExit(
            f"no finance file found under {args.output_root}/{args.month}_*/ — "
            f"run the month's windows first")

    for w in windows:
        print(f"  {w.platform:<7} {w.period}: {len(w.totals):>3} storefront(s), "
              f"with-VAT {w.totals['wv'].sum():>18,.0f}")

    coverage = ms.Coverage(month=args.month,
                           included=[(w.platform, w.period) for w in windows],
                           built_by=f"tools/build_master_summary.py ({args.output_root})")
    # The CLI reads a directory, so it cannot know a window is missing — only the
    # service can, because only the database knows a window was expected. Saying
    # so on the face of the file is the point of task 3.5.
    coverage.missing = []
    wb = ms.build(coverage, windows, brand_map(ROOT / "config"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"\nwrote {out}")
    print(f"  {coverage.headline()}")
    print("  NOTE: built from a directory listing, so 'complete' here means "
          "'every window present in output/', not 'every window of the month'.")

    if not args.check:
        return 0

    print("\nTIE CHECK (each column total against the window it was read from):")
    ok = True
    for row in ms.tie_rows(windows):
        source = next(w for w in windows if w.period == row["period"]
                      and w.platform == row["platform"])
        fresh = ms.read_window(ROOT / source.source, row["platform"]) \
            if (ROOT / source.source).is_file() else source.totals
        good = abs(float(fresh["wv"].sum()) - row["wv"]) < 1
        ok &= good
        print(f"  {row['platform']:<7} {row['period']}: {row['wv']:>18,.0f}  "
              f"{'TIES' if good else 'MISMATCH'}")
    print("ALL COLUMNS TIE" if ok else "TIE FAILURE — do not send")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
