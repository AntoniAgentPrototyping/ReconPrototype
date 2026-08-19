"""Lazada promo pairing — the null group key, and what it costs.

`revenue_lines` pairs promotional charges to revenue lines on
`(store, order_id, sku_id, product_name)`. Until 2026-08-19 the revenue side
passed `dropna=False` and the promo side did not, so a promo row with a null
product name left the pool while its revenue counterpart stayed.

**The direction matters.** Promo amounts are CREDITS that reduce the invoiced unit
price (`price_ka = (credits + promo) / units / VAT`), so a dropped promo row makes
`price_ka` too HIGH — an over-statement of invoiced revenue. Every other open
defect in this area under-states; this one bills the client too much.

It was latent rather than active: **0 null `sku_id` and 0 null `product_name`
promo rows** across all nine staged Lazada windows (May `l1`-`l4`, July
`l1`-`l5`), which is why the fix moved no golden cell. These tests are what stop
the divergence coming back before an export arrives with a blank product name.

`revenue_lines` has no other unit coverage — it was reachable only through the
workbook goldens, which cover four windows of one platform and cannot say *why* a
cell moved.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

import pandas as pd  # noqa: E402
from src import lazada  # noqa: E402

VAT = 1.08


def _ledger(product_name, promo_product_name, *, credits=540_000.0, promo=-108_000.0):
    """One revenue line and one promo charge against the same order and SKU.

    `product_name` / `promo_product_name` are passed separately so a test can make
    the two sides agree on a null as easily as on a string.
    """
    return pd.DataFrame({
        "store": ["KAO", "KAO"],
        "order_id": ["ORD-1", "ORD-1"],
        "order_line_id": ["L1", "L2"],
        "sku_id": ["SKU-1", "SKU-1"],
        "product_name": [product_name, promo_product_name],
        "fee_bucket": [lazada.REVENUE_BUCKET, lazada.PROMO_BUCKETS[0]],
        "amount_incl_vat": [credits, promo],
        "vat_rate": [VAT, VAT],
    })


def test_a_named_product_nets_its_promo(log):
    """Control: the ordinary path, so a failure below is about the null key."""
    out = lazada.revenue_lines(_ledger("CHIN-SU 35g", "CHIN-SU 35g"), log)

    assert len(out) == 1
    assert out.iloc[0]["promo"] == -108_000.0
    # (540,000 - 108,000) / 1 unit / 1.08
    assert out.iloc[0]["price_ka"] == 400_000.0


def test_a_null_product_name_still_nets_its_promo(log):
    """The regression. Both sides carry a null product name and must still pair.

    Dropped, the promo credit vanishes and `price_ka` becomes 500,000 — 25% high.
    """
    out = lazada.revenue_lines(_ledger(None, None), log)

    assert len(out) == 1, "the revenue line itself must survive a null product name"
    assert out.iloc[0]["promo"] == -108_000.0, (
        "the promo charge was dropped from the pool, so the invoiced unit price is "
        "over-stated — see docs/08-KNOWN-DEFECTS.md#110"
    )
    assert out.iloc[0]["price_ka"] == 400_000.0

    # The discriminator: the pre-fix promo grouping, spelled out. If this stops
    # losing the row, the test above no longer proves anything.
    promo_rows = _ledger(None, None)
    promo_rows = promo_rows[promo_rows["fee_bucket"].isin(lazada.PROMO_BUCKETS)]
    dropped = promo_rows.groupby(
        ["store", "order_id", "sku_id", "product_name"], as_index=False,
    )["amount_incl_vat"].sum()
    assert dropped.empty, (
        "fixture no longer reproduces the defect: the default (dropna=True) "
        "grouping already keeps the null-product promo row"
    )


def test_pairing_a_null_product_does_not_fire_the_unpaired_warning(log):
    """A dropped key and a genuine orphan used to produce the same sentence.

    Now that both sides handle nulls identically, a null-keyed pair is silent and
    the warning means only what it says: promo charged against something with no
    revenue line at all.
    """
    lazada.revenue_lines(_ledger(None, None), log)

    assert not any("paired to no revenue line" in w for w in log.warnings), log.warnings


def test_a_genuinely_unpaired_promo_is_still_reported(log):
    """The orphan class is real — measured at -30,845 VND on July `l2` and
    -22,486 VND on `l3`, both with zero null keys — so it must stay visible."""
    ledger = _ledger("CHIN-SU 35g", "A PRODUCT WITH NO REVENUE LINE")

    out = lazada.revenue_lines(ledger, log)

    assert out.iloc[0]["promo"] == 0.0
    assert any("paired to no revenue line" in w for w in log.warnings), log.warnings


def test_the_gift_variant_pairing_still_keys_on_product_name(log):
    """Guard on the rule the null handling must not erode.

    The same SKU can appear in one order as a normal unit and as a gift variant
    (Masan order 524944050276659, a 35,000 unit alongside a 1,000,000 gift). They
    are separate groups, and the gift's Flexi-Combo chargeback must net against the
    gift alone — pairing on (order, SKU) without the name double-applies the pool.
    """
    ledger = pd.concat([
        _ledger("CHIN-SU 35g", "CHIN-SU 35g", credits=35_000.0, promo=0.0),
        _ledger("CHIN-SU gift", "CHIN-SU gift", credits=1_000_000.0, promo=-1_000_000.0),
    ], ignore_index=True)

    out = lazada.revenue_lines(ledger, log).set_index("product_name")

    assert out.loc["CHIN-SU gift", "price_ka"] == 0.0, "the gift group must zero out"
    assert out.loc["CHIN-SU 35g", "price_ka"] > 0.0, (
        "the gift's chargeback leaked onto the real product line")
