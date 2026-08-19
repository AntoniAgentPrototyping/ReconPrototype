"""How much of a window's settled income has order lines in that window? Read-only.

    python tools/measure_order_coverage.py --month 2026-07
    python tools/measure_order_coverage.py --month 2026-05 --platform shopee

**Why this exists — defect 2.12.** `explode_to_sku_*` joins a window's classified
income to the order files staged in *that window's folder*. An order settled in `w2`
may have been created days earlier, so its SKU lines live in the `01-07` folder's
export and not in `08-14`'s. Those lines are simply absent, the income row matches
nothing, and the revenue leaves the invoice through the documented "~21% unmatched"
door — quietly, because that door is expected to have traffic. The first external
month-end comparison found 4,527,401,608 VND of July understatement this way.

This tool measures three things the run itself cannot currently tell you:

1. **Per store, how much settled money has no order lines in its own window.** The
   legitimate reconciling class sits around 21%, so the signal is not an absolute
   level — it is a store collapsing well below its siblings in the same window.
2. **The leave-one-out comparison**, which is what a threshold has to be built on. A
   store big enough to drag the window average up must not be able to hide inside
   it — `masan` is 2/3 of Shopee `s4`.
3. **How much of the shortfall an EARLIER window's order files would recover**, which
   is the size of the prize for the cross-window fix and the expected delta to state
   in advance before it moves a golden.

**Counts, percentages and aggregate VND only.** Store names appear (the accepted
exposure — the run log already names them) but never a cell value, never an order id.

Lazada is skipped: a fee-event ledger with no order files, and it reproduces the
team's master exactly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# `tools/` must never import `service/` (tests/test_io_boundary.py).
from src import calculate, classify, config, ingest, tieout  # noqa: E402
from src.runlog import RunLog  # noqa: E402

PLATFORMS = ("tiktok", "shopee")
# The income-side money column each platform's shortfall is measured in. TikTok's is
# the settlement figure the tie-out reconciles against; Shopee's is the classified
# `net_revenue` its own reference is built from (`pipeline._run_shopee`).
MONEY = {"tiktok": "subtotal_after_seller_discounts", "shopee": "net_revenue"}


def _read(folder: Path, kind: str, platform: str, settings: dict, log: RunLog):
    return ingest.read_parts(folder, config.column_map(settings, platform, kind),
                             kind, settings, log, platform)


def explode_side(platform: str, income, log: RunLog):
    """The income frame each platform's explode actually joins.

    Mirrors `pipeline._run_tiktok` (classified-GOOD only) and `_run_shopee` (every
    classified order). Measuring a different population than the join sees would make
    every number here quietly wrong.
    """
    if platform == "tiktok":
        cl = classify.classify_tiktok_income(income, log)
        return cl[cl["check_status"] == classify.CHECK_GOOD]
    return classify.classify_shopee_income(income, log)


def window_index(period: str) -> tuple[str, str, int]:
    """`2026-07_w2` -> (`2026-07`, `w`, 2). Sub-batches like `s2x` sort after `s2`."""
    month, _, tail = period.partition("_")
    letter = tail[:1]
    digits = "".join(c for c in tail[1:] if c.isdigit())
    return month, letter, int(digits or 0)


def predecessors(period: str, available: list[str]) -> list[str]:
    """Same month, same letter, lower ordinal — nearest first."""
    month, letter, idx = window_index(period)
    same = [p for p in available
            if window_index(p)[:2] == (month, letter) and window_index(p)[2] < idx]
    return sorted(same, key=lambda p: window_index(p)[2], reverse=True)


def measure(input_root: Path, month: str, platform: str, settings: dict,
            log: RunLog) -> list[dict]:
    periods = sorted(p.name for p in input_root.glob(f"{month}_*")
                     if (p / platform / "orders").is_dir())
    out: list[dict] = []

    # Order-side keys per window, read once. Predecessor lookups then cost nothing.
    order_keys: dict[str, frozenset] = {}
    for period in periods:
        try:
            orders = _read(input_root / period / platform / "orders", "orders",
                           platform, settings, log)
            order_keys[period] = tieout.pairs(orders)
        except Exception as exc:                       # a window we cannot read is a finding
            print(f"  !! {period}/{platform} orders unreadable: "
                  f"{type(exc).__name__}: {exc}")
            order_keys[period] = frozenset()

    for period in periods:
        try:
            income = _read(input_root / period / platform / "income", "income",
                           platform, settings, log)
            income = ingest.derive_brand(income, settings, log)
            income = ingest.apply_settlement_bounds(income, period, settings, log)
            side = explode_side(platform, income, log)
        except Exception as exc:
            print(f"  !! {period}/{platform} income unreadable: "
                  f"{type(exc).__name__}: {exc}")
            continue

        money_col = MONEY[platform]
        if money_col not in side.columns:
            print(f"  !! {period}/{platform}: no {money_col!r} column")
            continue

        own = order_keys.get(period, frozenset())
        earlier = predecessors(period, periods)
        keys = tieout.pair_series(side)
        matched_own = keys.isin(own)

        # Which of the unmatched would a predecessor window's orders supply, under the
        # one-nearest-window rule the fix would use?
        recoverable = {w: 0.0 for w in earlier}
        recovered_mask = matched_own.copy()
        for w in earlier:                              # nearest first; first wins
            todo = ~recovered_mask & keys.isin(order_keys.get(w, frozenset()))
            recoverable[w] = float(side.loc[todo, money_col].fillna(0).sum())
            recovered_mask |= todo

        money = side[money_col].fillna(0)
        for store, g in side.groupby(side["store"].astype(str)):
            gm = g[money_col].fillna(0)
            gk = tieout.pair_series(g)
            unmatched = ~gk.isin(own)
            total = float(gm.sum())
            short = float(gm[unmatched].sum())
            others = float(money.sum()) - total
            others_short = float(money[~keys.isin(own)].sum()) - short
            out.append({
                "period": period, "store": store,
                "orders": int(len(set(gk))),
                "unmatched_orders": int(len(set(gk[unmatched]))),
                "money": total, "short": short,
                "share": (short / total * 100) if total else 0.0,
                "sibling_share": (others_short / others * 100) if others else 0.0,
                "recoverable": float(g.loc[
                    (~gk.isin(own)) & gk.isin(
                        frozenset().union(*(order_keys.get(w, frozenset())
                                            for w in earlier)) if earlier else frozenset()),
                    money_col].fillna(0).sum()),
            })
        if earlier:
            named = ", ".join(f"{w}: {v:,.0f} VND" for w, v in recoverable.items() if v)
            print(f"  {period}: recoverable from predecessors — {named or 'nothing'}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, help="e.g. 2026-07")
    ap.add_argument("--platform", choices=PLATFORMS)
    ap.add_argument("--input-root", default=str(ROOT / "input"))
    ap.add_argument("--min-orders", type=int, default=100,
                    help="stores below this are noise for threshold purposes")
    args = ap.parse_args(argv)

    settings = config.load_settings(ROOT / "config")
    log = RunLog()
    root = Path(args.input_root)
    platforms = [args.platform] if args.platform else list(PLATFORMS)

    rows: list[dict] = []
    for platform in platforms:
        print(f"\n=== {platform} {args.month} ===")
        rows.extend([dict(r, platform=platform)
                     for r in measure(root, args.month, platform, settings, log)])

    if not rows:
        print("nothing measured")
        return 2

    print(f"\n{'window':>14} {'platform':>8} {'store':<24} {'orders':>8} "
          f"{'unmatched':>9} {'short VND':>18} {'share%':>7} {'sibs%':>7} "
          f"{'excess pp':>9} {'recoverable VND':>18}")
    worst: list[tuple[float, dict]] = []
    for r in sorted(rows, key=lambda r: (r["platform"], r["period"], -r["short"])):
        excess = r["share"] - r["sibling_share"]
        flag = ""
        if r["orders"] >= args.min_orders and excess > 10:
            flag = "  <-- 2.12 SHAPE"
            worst.append((excess, r))
        print(f"{r['period']:>14} {r['platform']:>8} {r['store'][:24]:<24} "
              f"{r['orders']:>8,} {r['unmatched_orders']:>9,} {r['short']:>18,.0f} "
              f"{r['share']:>7.1f} {r['sibling_share']:>7.1f} {excess:>9.1f} "
              f"{r['recoverable']:>18,.0f}{flag}")

    total_short = sum(r["short"] for r in rows)
    total_recov = sum(r["recoverable"] for r in rows)
    print(f"\nunmatched settlement across all measured windows: {total_short:,.0f} VND")
    print(f"of which an EARLIER window's order files hold the lines: "
          f"{total_recov:,.0f} VND ({total_recov / total_short * 100:.1f}%)"
          if total_short else "")
    print(f"stores at least {args.min_orders} orders and >10pp above their siblings: "
          f"{len(worst)}")
    if worst:
        print("\nThese are the threshold calibration points. A per-store check must "
              "fire on them and stay silent on every window that ties today — an "
              "absolute floor cannot do that, because ~21% unmatched is legitimate.")
        for excess, r in sorted(worst, reverse=True)[:12]:
            print(f"  {r['period']} {r['platform']:>7} {r['store'][:28]:<28} "
                  f"{excess:>6.1f}pp above siblings, {r['short']:>16,.0f} VND short")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
