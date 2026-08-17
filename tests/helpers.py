"""Pandas-backed test frame builders.

Separate module, not conftest.py: conftest is for fixtures pytest
auto-discovers, and importing helpers *from* it collides as soon as a
subdirectory has its own conftest (both import as `conftest`).

Importing this module requires pandas, so conftest imports it lazily —
the suite must stay collectable without pandas. RecordingLog therefore lives
in tests/recording_log.py instead of here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_sku_level(n: int = 2000, seed: int = 42, stores: list[str] | None = None) -> pd.DataFrame:
    """A TikTok sku_level frame built exactly as calculate.py:140-142 builds it.

    Deriving amount_with_vat as amount_pre_vat * vat_factor is essential: it
    is what makes the 'Xuat HD bt' check an identity in production, and a
    fixture that broke that relation would flatter the checks under test.
    """
    rng = np.random.default_rng(seed)
    stores = stores or ["Unilever Homecare", "KAO", "AHC", "U food"]
    df = pd.DataFrame(
        {
            "store": rng.choice(stores, n),
            "order_id": [f"ORD{i:07d}" for i in range(n)],
            "sku_id": rng.choice([f"SKU{i:04d}" for i in range(200)], n),
            "sku_name": "product",
            "quantity": rng.integers(1, 5, n),
        }
    )
    # VAT factors as masters.vat_factor_for yields them: 1.08 default + exceptions.
    df["vat_factor"] = np.where(rng.random(n) < 0.05, 1.05, 1.08)
    unit_gross = rng.integers(20_000, 900_000, n).astype(float)

    df["unit_price_pre_vat"] = unit_gross / df["vat_factor"]
    df["amount_pre_vat"] = df["unit_price_pre_vat"] * df["quantity"]
    df["amount_with_vat"] = df["amount_pre_vat"] * df["vat_factor"]
    # The settlement figure the INCOME export carries for this order. In real
    # data the order-file rebuild equals it exactly — measured at max deviation
    # 0.0000 VND across 44,129 orders of a real TikTok window — so the fixture
    # mirrors that rather than inventing a spread the checks would have to
    # tolerate.
    df["subtotal_after_seller_discounts"] = df["amount_with_vat"]
    return df


def breaches(results: pd.DataFrame) -> pd.DataFrame:
    """The BREACH rows from a tieout results frame."""
    return results[results["result"] == "BREACH"]


def make_shopee_frames(n: int = 800, seed: int = 7, refund_frac: float = 0.02):
    """A Shopee (income, sku_level) pair obeying the measured crossing.

    The relation asserted in production is
        SUM(amount_with_vat - discount_allocated)  ==  gross_revenue + shopee_product_subsidy
    with the left side rebuilt from the ORDER export and the right side read
    from the INCOME export. This builder reproduces the calculate.py chain
    exactly — T, X, Y, the proportional allocation Z, then the VAT round-trip —
    so the fixture cannot flatter the check by construction; a broken chain here
    would show up as the clean-data control failing.
    """
    rng = np.random.default_rng(seed)
    stores = ["Xmenforboss", "Masan"]
    lines_per_order = rng.integers(1, 4, n)
    order_ids = [f"SHP{i:07d}" for i in range(n)]
    rows = []
    for i, (oid, k) in enumerate(zip(order_ids, lines_per_order)):
        store = stores[i % len(stores)]
        for j in range(int(k)):
            rows.append({
                "store": store, "order_id": oid,
                "sku_id": f"SKU{rng.integers(0, 300):04d}", "sku_name": "product",
                "unit_price_gross": float(rng.integers(20_000, 900_000)),
                "quantity": int(rng.integers(1, 5)),
                "seller_subsidy": float(rng.integers(0, 20_000)),
            })
    sku = pd.DataFrame(rows)
    keys = ["store", "order_id"]

    # The order-level discount pool (income-side columns, constant per order).
    pool = pd.DataFrame({
        "store": [stores[i % len(stores)] for i in range(n)],
        "order_id": order_ids,
        "total_discount": rng.integers(0, 60_000, n).astype(float),
        "shopee_product_subsidy": np.where(rng.random(n) < 0.05,
                                           rng.integers(0, 40_000, n), 0).astype(float),
        "actual_refund": np.where(rng.random(n) < refund_frac,
                                  -rng.integers(10_000, 500_000, n), 0).astype(float),
    })
    sku = sku.merge(pool[[*keys, "total_discount"]], on=keys, how="left")

    sku["vat_factor"] = np.where(rng.random(len(sku)) < 0.05, 1.05, 1.08)
    sku["net_after_discount"] = sku["unit_price_gross"] * sku["quantity"] - sku["seller_subsidy"]
    sku["order_gross_sale"] = sku.groupby(keys)["net_after_discount"].transform("sum")
    sku["discount_per_order"] = sku.groupby(keys)["total_discount"].transform("max")
    ratio = (sku["net_after_discount"] / sku["order_gross_sale"].replace(0, pd.NA)).fillna(0)
    sku["discount_allocated"] = ratio * sku["discount_per_order"]
    sku["unit_price_pre_vat"] = ((sku["net_after_discount"] + sku["discount_allocated"])
                                 / sku["quantity"].replace(0, pd.NA)).fillna(0) / sku["vat_factor"]
    sku["amount_pre_vat"] = sku["unit_price_pre_vat"] * sku["quantity"]
    sku["amount_with_vat"] = sku["amount_pre_vat"] * sku["vat_factor"]
    sku["check_status"] = "ok"

    # The income export: gross_revenue is what the order side rebuilds to,
    # minus the Shopee-funded product subsidy the formula adds back.
    x = sku.groupby(keys)["net_after_discount"].sum().rename("X").reset_index()
    income = pool.merge(x, on=keys, how="left")
    income["gross_revenue"] = income["X"] - income["shopee_product_subsidy"]
    income["income_type"] = "Order"
    income["net_revenue"] = income["gross_revenue"]
    return income.drop(columns="X"), sku
