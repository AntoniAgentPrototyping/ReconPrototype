"""D12 — one storefront->brand mapping, and the matching rule that makes it work.

The register said "two mappings that disagree". The disagreement that mattered was
not which file held the rows, it was the KEY: `config/brand_map.csv` was written in
`norm_store` spellings (`abbott grow`, `ufood store`) and the pipeline's stores are
roster spellings (`Abbott grow`, `ufood_store`), while `ingest.derive_brand` did an
exact `df["store"].map()`. Measured 2026-08-21: 2 of 42 TikTok+Shopee rows matched.

So the failure mode this file pins is **the populated contract that brands almost
nothing**. It is worse than the empty map it replaced, because the empty map warned
on every store and a near-miss map warns on the forty it silently missed while
looking configured ([D65](../docs/06-DECISIONS.md#d65)).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("pandas")
import pandas as pd                                                 # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import ingest                                              # noqa: E402
from src.errors import ReconHardStop                                # noqa: E402


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, message: str) -> None:
        self.lines.append(message)

    warn = add
    section = add


def _settings(**brands) -> dict:
    return {"store_to_brand": brands}


def _frame(*stores: str) -> pd.DataFrame:
    return pd.DataFrame({"store": list(stores), "amount": [1.0] * len(stores)})


# ---------------------------------------------------------------------------
# The matching rule
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("configured", ["ufood_store", "ufood store", "UFood_Store"])
def test_a_brand_resolves_through_norm_store_on_both_sides(configured):
    """The regression that WAS the defect.

    `ufood_store` is the roster spelling and `ufood store` is what the brand file
    said. An exact `.map()` matched neither against the other, so the storefront
    kept its own name and nobody was told the row existed. Any spelling that
    normalises the same must now resolve.
    """
    settings = _settings(shopee={configured: {"brand": "U Food",
                                              "confidence": "confirmed"}})
    log = _Log()
    out = ingest.derive_brand(_frame("ufood_store"), settings, log, "shopee")

    assert list(out["brand"]) == ["U Food"]
    assert not log.lines, f"a resolved brand should warn about nothing: {log.lines}"


def test_a_storefront_with_no_row_keeps_its_name_and_is_named():
    """The honest fallback, unchanged from before D12 — and it must stay loud.

    Three Shopee storefronts are deliberately in this state (the July onboardings,
    whose client brand nobody has told us), so this is the normal path for a real
    window and not an error condition.
    """
    settings = _settings(shopee={"Kate": {"brand": "Kate"}})
    log = _Log()
    out = ingest.derive_brand(_frame("Kate", "pepsicofoods"), settings, log, "shopee")

    assert dict(zip(out["store"], out["brand"])) == {"Kate": "Kate",
                                                    "pepsicofoods": "pepsicofoods"}
    assert any("pepsicofoods" in line for line in log.lines), log.lines


def test_the_mapping_is_per_platform():
    """Why the contract is nested rather than flat.

    A flat `store -> brand` map collapses a storefront name that trades under
    different brands on two platforms into one entry, and the table it renders
    from is keyed `(platform, store)`. Lossless on today's rows — measured, no
    conflicting flat keys — which is exactly the kind of property that stops being
    true without anyone noticing.
    """
    settings = _settings(tiktok={"Lashe": {"brand": "Lashe TikTok"}},
                         shopee={"Lashe": {"brand": "Lashe Shopee"}})
    log = _Log()

    tik = ingest.derive_brand(_frame("Lashe"), settings, log, "tiktok")
    shop = ingest.derive_brand(_frame("Lashe"), settings, log, "shopee")

    assert list(tik["brand"]) == ["Lashe TikTok"]
    assert list(shop["brand"]) == ["Lashe Shopee"]


def test_an_absent_platform_brands_nothing_rather_than_raising():
    """Lazada has brand rows and TikTok might not. A platform with no node is
    "nobody has said", which is the same state as an unmapped store."""
    log = _Log()
    out = ingest.derive_brand(_frame("KAO"), _settings(lazada={}), log, "tiktok")
    assert list(out["brand"]) == ["KAO"]


# ---------------------------------------------------------------------------
# The shape
# ---------------------------------------------------------------------------

def test_a_bare_brand_string_is_refused():
    """`store_to_brand.tiktok.Kao: "KAO"` is the natural thing to hand-write, and
    reading it would mean inventing a `confidence` — whose whole purpose is that
    nobody has to guess whether a mapping was reviewed."""
    with pytest.raises(ReconHardStop) as exc:
        ingest.store_brands(_settings(tiktok={"Kao": "KAO"}), "tiktok")
    assert "store_to_brand.tiktok.Kao" in str(exc.value)


def test_a_row_with_no_brand_is_refused():
    with pytest.raises(ReconHardStop, match="brand"):
        ingest.store_brands(_settings(tiktok={"Kao": {"confidence": "confirmed"}}),
                            "tiktok")


def test_confidence_defaults_to_confirmed_and_note_to_empty():
    """The master paints both. Absent metadata must not read as "needs review",
    which would flag the whole tab red and teach the team to ignore it."""
    resolved = ingest.store_brands(_settings(tiktok={"Kao": {"brand": "KAO"}}), "tiktok")
    assert resolved == {"kao": ("KAO", "confirmed", "")}


def test_brand_map_keys_every_platform_on_norm_store():
    """The shape `master_summary.build` takes — one function feeding the month-end
    master and a settlement run, which is the whole of D12."""
    settings = _settings(tiktok={"Abbott grow": {"brand": "Abbott"}},
                         lazada={"unilever ahc": {"brand": "Unilever AHC",
                                                  "confidence": "needs_confirmation",
                                                  "note": "verify with Hoang"}})
    assert ingest.brand_map(settings) == {
        ("tiktok", "abbott grow"): ("Abbott", "confirmed", ""),
        ("lazada", "unilever ahc"): ("Unilever AHC", "needs_confirmation",
                                     "verify with Hoang"),
    }


# ---------------------------------------------------------------------------
# The real contract
# ---------------------------------------------------------------------------

def test_the_committed_contract_resolves_a_brand_for_every_rostered_store_that_has_one():
    """Against `config/settings.yaml` itself, because the defect was invisible to
    every synthetic fixture: hand-written rows agree with themselves.

    The three named exceptions are the July Shopee onboardings. They are asserted
    BY NAME rather than tolerated as a count, so a fourth unmapped storefront fails
    this test instead of joining a permitted total.
    """
    from src import config as src_config

    settings = src_config.load_settings(ROOT / "config")
    expected = settings["expected_stores"]

    unmapped = {}
    for platform in ("tiktok", "shopee"):
        resolved = ingest.store_brands(settings, platform)
        unmapped[platform] = [s for s in expected[platform]
                              if ingest.norm_store(s) not in resolved]
        # No brand row may name a storefront the roster does not have: that is the
        # "configured and inert" state the whole change is about.
        assert set(resolved) <= {ingest.norm_store(s) for s in expected[platform]}, (
            f"{platform} brands a storefront the roster does not name")

    assert unmapped["tiktok"] == []
    assert unmapped["shopee"] == ["Tolpa", "pepsicofoods", "xa_kho_gia_tot"]
