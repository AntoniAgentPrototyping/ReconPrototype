"""Generate deterministic synthetic input files for a smoke run.

Writes input/2026-06_p1/{tiktok,shopee}/{orders,income}/ using the SOURCE
headers from config/settings.yaml column maps, so the run exercises header
mapping exactly as real files will. Bakes in the anomalies the pipeline must
surface: duplicate rows across overlapping file parts, income lines with no
matching order, an unknown SKU, refunds, zero-revenue lines, and prior-month
orders (the re-pull) for cross-period stitching.

Usage: python tools/make_sample_data.py
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PERIOD = "2026-06_p1"

STORES = {"Keoh Official Store": "KEOH", "Meko Official Store": "MEKO"}
SKUS = {
    "KEOH": [("KEOH-001", "Keoh Lip Balm 4g", 89000), ("KEOH-002", "Keoh Face Serum 30ml", 249000),
             ("KEOH-003", "Keoh Sunscreen SPF50 50ml", 189000)],
    "MEKO": [("MEKO-001", "Meko Shampoo 500ml", 129000), ("MEKO-002", "Meko Conditioner 500ml", 119000)],
}
UNKNOWN_SKU = ("MYST-999", "Mystery Gift Item", 15000)  # deliberately NOT in sku_master.csv


def build_platform(platform: str, rng: random.Random) -> tuple[list[dict], list[dict]]:
    orders, income = [], []
    for i in range(60):
        store = rng.choice(list(STORES))
        prefix = STORES[store]
        order_id = f"{platform.upper()}-{prefix}-{1000 + i}"
        # ~15% of orders are the prior-month re-pull (created in May, settled in June)
        created = (f"2026-05-{rng.randint(20, 31):02d} {rng.randint(8, 22):02d}:15:00"
                   if rng.random() < 0.15 else
                   f"2026-06-{rng.randint(1, 28):02d} {rng.randint(8, 22):02d}:15:00")

        n_lines = rng.choice([1, 1, 1, 2, 3])
        chosen = rng.sample(SKUS[prefix], k=min(n_lines, len(SKUS[prefix])))
        if i == 7:  # one order carries the unknown SKU
            chosen = chosen + [UNKNOWN_SKU]
        gross = 0
        for sku_id, sku_name, price in chosen:
            qty = rng.randint(1, 3)
            gross += qty * price
            orders.append({"order_id": order_id, "sku_id": sku_id, "sku_name": sku_name,
                           "quantity": qty, "unit_price_gross": price,
                           "order_created_at": created, "store": store})

        roll = rng.random()
        if i == 7:           # unknown-SKU order must settle normally so it reaches SKU explode
            roll = 0.99
        if roll < 0.08:      # returned
            refund, net = gross, 0
        elif roll < 0.13:    # zero revenue (fully voucher-covered / cancelled-settled)
            refund, net = 0, 0
        else:                # normal
            refund, net = 0, round(gross * rng.uniform(0.82, 0.95))
        income.append({"order_id": order_id, "store": store, "gross_revenue": gross,
                       "actual_refund": refund, "net_revenue": net,
                       "statement_date": f"2026-07-0{rng.randint(1, 5)}"})

    # two income lines that reference orders missing from the order files
    for j in (1, 2):
        income.append({"order_id": f"{platform.upper()}-GHOST-{j}", "store": "Keoh Official Store",
                       "gross_revenue": 99000, "actual_refund": 0, "net_revenue": 91000,
                       "statement_date": "2026-07-03"})
    return orders, income


def write_parts(rows: list[dict], folder: Path, colmap: dict[str, str], n_parts: int) -> None:
    """Split rows into parts; part 2 re-includes the tail of part 1 to
    simulate the overlapping exports that happen in real downloads."""
    folder.mkdir(parents=True, exist_ok=True)
    canonical_to_source = {v: k for k, v in colmap.items()}
    headers = list(colmap.keys())
    chunk = max(1, len(rows) // n_parts + 1)
    parts = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]
    if len(parts) > 1:
        parts[1] = parts[0][-5:] + parts[1]  # overlap → duplicates to dedupe
    for idx, part in enumerate(parts, 1):
        with (folder / f"part_{idx}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            for row in part:
                writer.writerow({canonical_to_source[k]: v for k, v in row.items()})


def main() -> int:
    settings = yaml.safe_load(
        (ROOT / "tools" / "sample_config" / "settings.yaml").read_text(encoding="utf-8"))
    rng = random.Random(42)
    for platform in ("tiktok", "shopee"):
        orders, income = build_platform(platform, rng)
        base = ROOT / "input" / PERIOD / platform
        maps = settings["column_maps"][platform]
        write_parts(orders, base / "orders", maps["orders"], n_parts=3)
        write_parts(income, base / "income", maps["income"], n_parts=2)
        print(f"{platform}: {len(orders)} order lines, {len(income)} income lines -> {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
