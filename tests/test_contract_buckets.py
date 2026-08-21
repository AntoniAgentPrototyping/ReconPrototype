"""A14 — the bucket lists and the VAT-rate list live in the contract.

Until 2026-08-20 these were module constants: `finance_template.VAT_RATES`,
`TIKTOK_BUCKETS`/`SHOPEE_BUCKETS`/`LAZADA_BUCKETS`, and `lazada.REVENUE_BUCKET`/
`PROMO_BUCKETS`. They decide what a workbook CELL holds, and they were the last
part of the domain contract only editable as Python (docs/14 A14, the tail of
M8/1.7's move). The values did not change — the money gate re-ran all eight
golden windows at zero tolerance after the move.

Two things these tests pin:

* **Absence is a hard stop that names the key** — never a fallback to a code
  copy, because two definitions of the same rule is exactly what the move
  removed (the `lazada.column_map` posture).
* **The workbook TAB layout stays code, and disagreement is loud.** The contract
  decides which stores land in which bucket; the template's tabs and control-row
  geometry are pinned to the team's own files. A configured bucket the template
  cannot lay out — or a rate list the Shopee/Lazada geometry does not match —
  stops the run with a sentence instead of leaking money into a drift breach.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

from src import finance_template, lazada  # noqa: E402
from src.errors import ReconHardStop  # noqa: E402
from src.runlog import RunLog  # noqa: E402

# The committed contract's values, verbatim (config/settings.yaml).
SETTINGS = {
    "vat_factors": {"default": 1.08, "rates": [1.05, 1.08, 1.10]},
    "invoice_buckets": {
        "tiktok": {"match": {"kao": "KAO 8", "merries": "Merries 8"},
                   "default": "Others 8"},
        "shopee": {"match": {"curel": "Curel", "kao": "KAO",
                             "merries": "Merries", "kate": "Kate"},
                   "default": "Others"},
        "lazada": {"match": {"curel": "Curel.xlsx", "kao": "KAO.xlsx",
                             "merries": "Merries.xlsx"},
                   "default": "Others"},
    },
    "fee_buckets": {
        "lazada": {"revenue": "1.Doanh Thu",
                   "promo": ["2.Promotional Charges Flexi-Combo"]},
    },
}


# ---------------------------------------------------------------------------
# Absence hard-stops, naming the key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("read", "key"), [
    (lambda: finance_template.vat_rates({}), "vat_factors.rates"),
    (lambda: finance_template.invoice_buckets({}, "tiktok"), "invoice_buckets.tiktok"),
    (lambda: lazada.revenue_bucket({}), "fee_buckets.lazada.revenue"),
    (lambda: lazada.promo_buckets({}), "fee_buckets.lazada.promo"),
], ids=["vat_rates", "invoice_buckets", "revenue_bucket", "promo_buckets"])
def test_an_absent_key_is_a_hard_stop_that_names_it(read, key):
    with pytest.raises(ReconHardStop) as err:
        read()
    assert key in str(err.value), (
        f"the refusal must name {key} so an operator can fix the contract "
        f"rather than read the traceback")


def test_a_bucket_config_missing_its_default_is_incomplete():
    """`match` without `default` would make the catch-all an invented value."""
    with pytest.raises(ReconHardStop, match="invoice_buckets.tiktok"):
        finance_template.invoice_buckets(
            {"invoice_buckets": {"tiktok": {"match": {"kao": "KAO 8"}}}}, "tiktok")


# ---------------------------------------------------------------------------
# The values round-trip exactly
# ---------------------------------------------------------------------------

def test_the_accessors_return_the_committed_values():
    assert finance_template.vat_rates(SETTINGS) == (1.05, 1.08, 1.10)
    match, default = finance_template.invoice_buckets(SETTINGS, "tiktok")
    assert match == [("kao", "KAO 8"), ("merries", "Merries 8")]
    assert default == "Others 8"
    assert lazada.revenue_bucket(SETTINGS) == "1.Doanh Thu"
    assert lazada.promo_buckets(SETTINGS) == ["2.Promotional Charges Flexi-Combo"]


def test_needles_are_lowercased_so_an_edited_case_cannot_silently_match_nothing():
    """`_bucket` lowercases the store name, so a mixed-case needle typed into the
    editor would otherwise never match — a silent reroute of every affected store
    into the catch-all."""
    settings = {"invoice_buckets": {"tiktok": {"match": {"KAO": "KAO 8"},
                                               "default": "Others 8"}}}
    match, default = finance_template.invoice_buckets(settings, "tiktok")
    assert finance_template._bucket("KAO Official Store", match, default) == "KAO 8"


def test_match_order_is_walk_order_first_hit_wins():
    match = [("curel", "Curel"), ("kao", "KAO")]
    assert finance_template._bucket("kao curel combined", match, "Others") == "Curel"


# ---------------------------------------------------------------------------
# Template geometry stays code, and disagreement is loud
# ---------------------------------------------------------------------------

def test_a_bucket_the_template_has_no_tab_for_stops_the_run(sku_level):
    settings = {**SETTINGS, "invoice_buckets": {
        "tiktok": {"match": {"kao": "KAO 8", "biore": "Biore 8"},
                   "default": "Others 8"}}}
    with pytest.raises(ReconHardStop, match="Biore 8"):
        finance_template.build_tiktok(sku_level, settings, {}, RunLog())


@pytest.mark.parametrize("build", [
    finance_template.build_shopee, finance_template.build_lazada,
], ids=["shopee", "lazada"])
def test_a_rate_list_the_template_geometry_cannot_lay_out_stops_the_run(build):
    """Shopee's PV-sum side block and Lazada's Summary rows hard-wire the trio;
    a fourth rate would need new control rows, not a config edit. TikTok's layout
    is fully enumerated from the list and needs no such guard."""
    import pandas as pd

    settings = {**SETTINGS,
                "vat_factors": {"default": 1.08, "rates": [1.05, 1.08, 1.10, 1.12]}}
    with pytest.raises(ReconHardStop, match="template"):
        build(pd.DataFrame(), settings, {}, RunLog())
