"""Read run_metrics.json across a month and answer: is pandas running out of road?

    python tools/metrics_report.py --month 2026-05
    python tools/metrics_report.py --month 2026-05 --container-mb 4096

This is the dashboard for the engine-port trigger (docs/10-ROADMAP.md). The port
was descheduled on the argument that the data is small and the bottleneck is
Excel I/O; that argument expires the moment the numbers say otherwise, and this
is what reads the numbers.

Thresholds — any ONE firing is the signal to reconsider:

    peak RSS      > 50% of the worker container limit
    compute share > 25% of wall time   <- DataFrame math only; workbook
                                          materialization is `serialize` and is
                                          engine-independent, so it is in the
                                          denominator but not the numerator
    rows/window   > 2,000,000

Reporting only. It never changes a threshold and never edits anything.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RSS_FRACTION = 0.50
COMPUTE_SHARE = 0.25
MAX_ROWS = 2_000_000


def collect(output_root: Path, month: str) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(output_root.glob(f"{month}*/*/run_metrics.json")):
        window = f"{path.parent.parent.name}/{path.parent.name}"
        try:
            out.append((window, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  ! unreadable {path}: {exc}", file=sys.stderr)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="window prefix, e.g. 2026-05")
    ap.add_argument("--output-root", default=str(ROOT / "output"))
    ap.add_argument("--container-mb", type=float, default=None,
                    help="worker container memory limit; enables the RSS trigger")
    args = ap.parse_args(argv)

    runs = collect(Path(args.output_root), args.month)
    if not runs:
        print(f"no run_metrics.json under {args.output_root} for {args.month}*")
        print("(metrics are written by every run since M1 — re-run a window to populate)")
        return 1

    print(f"{'window':<24} {'wall':>8} {'io':>8} {'serial':>8} {'compute':>8} "
          f"{'cmp%':>6} {'peak MB':>8} {'max rows':>10}")
    print("-" * 86)
    worst_rss = worst_share = 0.0
    worst_rows = 0
    for window, d in runs:
        share = d.get("compute_share", 0.0)
        rss = d.get("peak_rss_mb", 0.0)
        rows = d.get("max_rows", 0)
        worst_rss, worst_share = max(worst_rss, rss), max(worst_share, share)
        worst_rows = max(worst_rows, rows)
        print(f"{window:<24} {d['wall_s']:>7.1f}s {d['io_s']:>7.1f}s "
              f"{d.get('serialize_s', 0.0):>7.1f}s {d['compute_s']:>7.1f}s "
              f"{share:>5.1%} {rss:>8,.0f} {rows:>10,}")

    print("-" * 86)
    print(f"{len(runs)} window(s)\n")
    print("Engine-port trigger (docs/10-ROADMAP.md) — any one firing is the signal:")

    fired = []

    def verdict(name: str, value: str, limit: str, hit: bool) -> None:
        print(f"  [{'FIRED' if hit else ' ok  '}] {name:<24} {value:>14}   limit {limit}")
        if hit:
            fired.append(name)

    if args.container_mb:
        verdict("peak RSS", f"{worst_rss:,.0f} MB",
                f"{args.container_mb * RSS_FRACTION:,.0f} MB "
                f"({RSS_FRACTION:.0%} of {args.container_mb:,.0f})",
                worst_rss > args.container_mb * RSS_FRACTION)
    else:
        print(f"  [ n/a ] {'peak RSS':<24} {worst_rss:>11,.0f} MB   "
              f"pass --container-mb to evaluate")
    verdict("compute share", f"{worst_share:.0%}", f"{COMPUTE_SHARE:.0%}",
            worst_share > COMPUTE_SHARE)
    verdict("rows per window", f"{worst_rows:,}", f"{MAX_ROWS:,}", worst_rows > MAX_ROWS)

    if fired:
        print(f"\n{len(fired)} trigger(s) fired: {', '.join(fired)}.")
        print("Before porting, try the cheaper fix first: string[pyarrow] / Categorical")
        print("dtypes at the ingest boundary (docs/06-DECISIONS.md#d25).")
    else:
        print("\nNo trigger fired — pandas is comfortably within its envelope.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
