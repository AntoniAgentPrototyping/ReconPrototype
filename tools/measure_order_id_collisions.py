"""Do any order ids appear under more than one store? Read-only.

    python tools/measure_order_id_collisions.py [--month 2026-05] [--platform tiktok]

**Why this exists.** `src/stitch.py` and both `explode_to_sku_*` moved to a
composite `(store, order_id)` key in M2.5, and `src/tieout.py` followed on
2026-08-19 (defect 2.9). Both changes are output-identical exactly as long as no
order id is shared between stores, and that claim was asserted in prose from an
ad-hoc script nobody kept — so re-deriving it meant re-writing the measurement.
This is that measurement, committed, so the next person to touch the key can
re-run it instead of trusting a sentence.

Reads through the pipeline's own readers (`ingest.read_parts`, the configured
column maps and aliases), because a measurement of a different frame than the one
the join sees would be worthless.

**Prints counts only** — never a store name next to a figure, never a cell value.
Order ids are printed for a colliding pair only, capped, because a collision is
the one case where the identifier is the finding.

Lazada is skipped: it is a fee-event ledger with no order files, and its own
grouping has been composite since M2.5.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `tools/` must never import `service/` (tests/test_io_boundary.py).
from src import config, ingest  # noqa: E402
from src.runlog import RunLog  # noqa: E402

PLATFORMS = ("tiktok", "shopee")
KINDS = {"tiktok": ("orders", "income"), "shopee": ("orders", "income")}


def collisions(frame, label: str) -> tuple[int, int, list[str]]:
    """(distinct ids, ids under >1 store, a few examples)."""
    if frame is None or not len(frame) or "store" not in frame.columns:
        return 0, 0, []
    pairs = frame[["store", "order_id"]].astype(str).drop_duplicates()
    per_id = pairs.groupby("order_id")["store"].nunique()
    shared = per_id[per_id > 1]
    return int(per_id.size), int(shared.size), [str(i) for i in shared.index[:5]]


def measure_window(period_dir: Path, platform: str, settings: dict, log: RunLog) -> dict:
    out = {"period": period_dir.name, "platform": platform, "kinds": {}}
    for kind in KINDS[platform]:
        folder = period_dir / platform / kind
        if not folder.is_dir():
            continue
        try:
            frame = ingest.read_parts(
                folder, config.column_map(settings, platform, kind),
                kind, settings, log, platform)
        except Exception as exc:                      # a window we cannot read is a finding
            out["kinds"][kind] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        total, shared, examples = collisions(frame, f"{period_dir.name}/{platform}/{kind}")
        out["kinds"][kind] = {"rows": len(frame), "ids": total,
                              "shared": shared, "examples": examples}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", help="restrict to windows of one month, e.g. 2026-05")
    ap.add_argument("--platform", choices=PLATFORMS, help="restrict to one platform")
    ap.add_argument("--input-root", default=str(ROOT / "input"))
    args = ap.parse_args(argv)

    settings = config.load_settings(ROOT / "config")
    log = RunLog()
    root = Path(args.input_root)
    if not root.is_dir():
        print(f"no input tree at {root}")
        return 2

    pattern = f"{args.month}_*" if args.month else "*"
    platforms = [args.platform] if args.platform else list(PLATFORMS)

    grand_ids = grand_shared = 0
    rows: list[dict] = []
    for period_dir in sorted(root.glob(pattern)):
        if not period_dir.is_dir():
            continue
        for platform in platforms:
            if not (period_dir / platform).is_dir():
                continue
            rows.append(measure_window(period_dir, platform, settings, log))

    print(f"{'window':>16}  {'platform':>8}  {'kind':>7}  {'rows':>9}  "
          f"{'distinct ids':>12}  {'ids in >1 store':>15}")
    for r in rows:
        for kind, m in r["kinds"].items():
            if "error" in m:
                print(f"{r['period']:>16}  {r['platform']:>8}  {kind:>7}  {m['error']}")
                continue
            grand_ids += m["ids"]
            grand_shared += m["shared"]
            flag = "  <-- COLLISION" if m["shared"] else ""
            print(f"{r['period']:>16}  {r['platform']:>8}  {kind:>7}  {m['rows']:>9,}  "
                  f"{m['ids']:>12,}  {m['shared']:>15,}{flag}")
            if m["examples"]:
                print(f"{'':>16}  colliding ids (first 5): {', '.join(m['examples'])}")

    print(f"\n{grand_ids:,} distinct order ids measured; "
          f"{grand_shared:,} appear under more than one store.")
    if grand_shared:
        print("A collision means the composite (store, order_id) key is now "
              "load-bearing rather than defensive. Re-read docs/08-KNOWN-DEFECTS.md "
              "1.5 and 2.9 before changing any join or tie-out key.")
    else:
        print("Zero collisions: the composite key is defensive on this data, so "
              "moving to it is output-identical here and only bites the case it "
              "exists for. This is the measurement docs/08-KNOWN-DEFECTS.md 1.5 "
              "and 2.9 cite, and src/tieout.pairs' docstring quotes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
