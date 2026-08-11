"""Evidence check for the July 29-31 window's renamed stores: the order
exports span the whole month, so if 'Reckitt' (w5) is the same store as
'Veet & Reckitt Personal Care' (w1-w4), their order-ID sets overlap heavily;
if it's a different store, overlap is ~zero. Read-only."""

from pathlib import Path

import pandas as pd

IN = Path(__file__).resolve().parents[1] / "input"

PAIRS = [
    ("Reckitt (w5)", "2026-07_w5", "23. Order Reckitt 07.xlsx",
     "Veet & Reckitt (w4)", "2026-07_w4", None, "Veet & Reckitt"),
    ("Nutifood Grow (w5)", "2026-07_w5", "22. Order Nutifood Grow 07.xlsx",
     "Nutifood Nutrition Store (w4)", "2026-07_w4", None, "Nutifood Nutrition"),
    ("Curel (w5)", "2026-07_w5", "16. Order Curel 07.xlsx",
     "any w4 store?", "2026-07_w4", None, None),
]


def order_ids(path: Path) -> set[str]:
    df = pd.read_excel(path, sheet_name="OrderSKUList", dtype=str, engine="calamine")
    col = next(c for c in df.columns if str(c).strip() == "Order ID")
    return set(df[col].dropna().astype(str).str.strip())


for name_a, win_a, file_a, name_b, win_b, _, needle in PAIRS:
    a = order_ids(IN / win_a / "tiktok" / "orders" / file_a)
    print(f"{name_a}: {len(a)} order IDs")
    if needle:
        files_b = [f for f in (IN / win_b / "tiktok" / "orders").glob("*.xlsx")
                   if needle.lower() in f.name.lower()]
        b = set()
        for f in files_b:
            b |= order_ids(f)
        ov = len(a & b)
        print(f"  vs {name_b} ({len(b)} IDs from {len(files_b)} file(s)): "
              f"overlap {ov} ({100 * ov / max(len(a), 1):.1f}% of w5 set)")
    else:
        best = ("", 0)
        for f in sorted((IN / win_b / "tiktok" / "orders").glob("*.xlsx")):
            ov = len(a & order_ids(f))
            if ov > best[1]:
                best = (f.name, ov)
        print(f"  best overlap in {win_b}: {best[0]} ({best[1]} IDs)"
              if best[1] else "  no overlap with any w4 store -> genuinely new store")
print("ALIAS CHECK DONE", flush=True)
