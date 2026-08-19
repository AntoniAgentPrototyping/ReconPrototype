"""The uniform naming scheme, and the four properties it is built on.

None of these need a database. They need the real `config/settings.yaml`, because
the patterns being tested against are the ones that have changed three times in
four months — a fixture with a simplified regex would prove nothing about the file
the pipeline actually reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("pandas")

from service import naming                                          # noqa: E402
from service.naming import NamingError                              # noqa: E402


@pytest.fixture(scope="module")
def settings():
    from src import config as src_config
    return src_config.load_settings(ROOT / "config")


# ---------------------------------------------------------------------------
# Property 1: the store survives the rename
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform,kind,store,expected", [
    ("tiktok", "orders", "Curel", "001.order Curel.xlsx"),
    ("tiktok", "income", "Curel", "001.income Curel.xlsx"),
    ("shopee", "orders", "Kao", "001_order. Kao.xlsx"),
    ("shopee", "income", "Kao", "001_income. Kao.xlsx"),
    ("lazada", "weekly", "KAO", "001_KAO.xlsx"),
    ("lazada", "daily", "KAO", "001_KAO.xlsx"),
])
def test_the_generated_name_is_read_back_as_the_same_store(
        platform, kind, store, expected, settings):
    """`derive(uniform(store)) == store` — the fixed point the whole module rests
    on. Asserted through the pipeline's OWN parser, not a copy."""
    name = naming.uniform_name(platform, kind, 1, store)
    assert name == expected
    assert naming.store_of(name, platform, settings) == store
    naming.validate_roundtrip(name, platform, store, settings)


def test_nothing_is_appended_after_the_store_name(settings):
    """TikTok's pattern eats a trailing bare 1-2 digit token; Shopee's eats
    ` part N` and a ` 1-10` range. A suffix would be swallowed, so the ordinal goes
    in the prefix — which TikTok's pattern already required."""
    assert naming.uniform_name("tiktok", "orders", 7, "Curel").startswith("007.")
    assert naming.uniform_name("shopee", "income", 7, "Kao").startswith("007_")
    for platform, kind in (("tiktok", "orders"), ("shopee", "income"), ("lazada", "weekly")):
        name = naming.uniform_name(platform, kind, 7, "Curel")
        assert name.endswith("Curel.xlsx"), f"{platform}: something follows the store"


def test_a_store_the_pattern_would_truncate_is_refused(settings):
    """The live hazard, and it bites on a HUMAN-supplied store, not a derived one.

    TikTok's own regex strips a trailing bare 1-2 digit token, so a uniform name
    built from the store `Unilever 2` reads back as `Unilever`. Inside
    `plan_window` the store was itself derived by that same pattern, so it is
    already truncated and the round trip holds trivially — the check earns its
    keep where an operator CONFIRMS or CORRECTS the store at the upload door,
    which is where `POST /uploads` calls it.

    Better a refused upload naming the store than a window that quietly invoices
    two storefronts as one.
    """
    name = naming.uniform_name("tiktok", "orders", 1, "Unilever 2")
    assert name == "001.order Unilever 2.xlsx"
    assert naming.store_of(name, "tiktok", settings) == "Unilever", (
        "if this ever changes, TikTok's pattern stopped stripping the trailing "
        "token and the refusal below is no longer needed")
    with pytest.raises(NamingError, match="does not survive"):
        naming.validate_roundtrip(name, "tiktok", "Unilever 2", settings)

    # Lazada's pattern strips only a parenthesised marker, so the same store name
    # is perfectly safe there — the refusal is specific, not blanket.
    planned = naming.plan_window(["1_Unilever 2.xlsx"], "lazada", "weekly", settings)
    assert planned[0].store == "Unilever 2"
    assert planned[0].name == "001_Unilever 2.xlsx"
    naming.validate_roundtrip(planned[0].name, "lazada", "Unilever 2", settings)


def test_plan_window_rejects_a_pattern_that_stops_matching_the_generated_shape(settings):
    """What the `validate_roundtrip` call inside `plan_window` is actually for.

    `store_from_filename` has changed three times in four months. An edit that
    stops matching the shape this module GENERATES — a required separator, a
    different prefix — would break every future window. Here it breaks one plan,
    loudly, naming the pattern.
    """
    hostile = dict(settings)
    hostile["store_from_filename"] = dict(settings["store_from_filename"])
    # Requires a "wk" prefix the uniform scheme does not produce.
    hostile["store_from_filename"]["tiktok"] = r"^wk\d+\.order\s+(.+?)\s*\.xlsx$"
    with pytest.raises(NamingError):
        naming.plan_window(["wk1.order Curel.xlsx"], "tiktok", "orders", hostile)


# ---------------------------------------------------------------------------
# Property 2: sorted() stays numeric, so row order is unchanged
# ---------------------------------------------------------------------------

def test_the_ordinal_is_fixed_width_so_sorting_stays_numeric(settings):
    """The non-obvious property, and the one that makes the rename
    output-identical.

    `read_parts` and `read_ledger` read `sorted(folder.iterdir())`, concatenate in
    that order, and workbook row order follows the concatenation. Under
    lexicographic sorting `9` would come after `10`, reordering rows and moving
    cells in the file the team invoices from.
    """
    names = [naming.uniform_name("lazada", "weekly", n, "KAO") for n in (1, 2, 9, 10, 11)]
    assert names == sorted(names)
    assert names[2] == "009_KAO.xlsx" and names[3] == "010_KAO.xlsx"


def test_ordinals_follow_sorted_originals_not_arrival_order(settings):
    """Assign by arrival and two concurrent uploads decide workbook row order
    between them, which is how a byte-reproducible pipeline stops being one."""
    originals = ["3_KAO.xlsx", "1_KAO.xlsx", "2_KAO.xlsx"]
    planned = naming.plan_window(originals, "lazada", "weekly", settings)
    assert [p.original for p in planned] == ["1_KAO.xlsx", "2_KAO.xlsx", "3_KAO.xlsx"]
    assert [p.name for p in planned] == ["001_KAO.xlsx", "002_KAO.xlsx", "003_KAO.xlsx"]
    # Same set, different input order -> identical plan. That is the guarantee.
    assert naming.plan_window(list(reversed(originals)), "lazada", "weekly", settings) \
        == planned


def test_sorted_new_names_preserve_sorted_originals(settings):
    """Stated as a property over a realistic Lazada folder: five weekly exports of
    one store, downloaded in one browser session."""
    originals = ["2_KAO.xlsx", "2_KAO (1).xlsx", "2_KAO (2).xlsx",
                 "2_KAO (3).xlsx", "2_KAO (4).xlsx"]
    planned = naming.plan_window(originals, "lazada", "weekly", settings)
    assert [p.original for p in planned] == sorted(originals)
    assert [p.name for p in planned] == sorted(p.name for p in planned)


# ---------------------------------------------------------------------------
# Property 3: Lazada's (N) marker disappears
# ---------------------------------------------------------------------------

def test_the_browser_duplicate_marker_disappears(settings):
    """`2_KAO (3).xlsx` is a DIFFERENT settlement week, not a re-pull (verified Aug
    2026). Without stripping the marker the same storefront reads as five separate
    stores; the ordinal carries the distinctness instead."""
    planned = naming.plan_window(
        ["2_KAO.xlsx", "2_KAO (1).xlsx", "2_KAO (4).xlsx"], "lazada", "weekly", settings)
    assert {p.store for p in planned} == {"KAO"}, "one store, not three"
    assert [p.name for p in planned] == ["001_KAO.xlsx", "002_KAO.xlsx", "003_KAO.xlsx"]
    assert all(p.renamed for p in planned)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_a_window_too_large_for_the_ordinal_is_refused(settings):
    """Widening the width later would make '0100' sort before '999' for every name
    already stored, so this is a deliberate refusal rather than a rollover."""
    too_many = [f"{n}_KAO.xlsx" for n in range(naming.MAX_FILES + 1)]
    with pytest.raises(NamingError, match="deliberate refusal"):
        naming.plan_window(too_many, "lazada", "weekly", settings)


def test_an_unparseable_filename_is_refused_with_the_pattern_named(settings):
    with pytest.raises(NamingError, match="store_from_filename"):
        naming.store_of("no store at all.xlsx", "lazada", settings)


def test_a_kind_the_platform_does_not_have_is_refused(settings):
    with pytest.raises(NamingError):
        naming.uniform_name("lazada", "orders", 1, "KAO")
    with pytest.raises(NamingError):
        naming.uniform_name("tiktok", "weekly", 1, "Curel")


def test_the_preview_hides_an_ordinal_that_is_not_decided_yet():
    """The upload screen shows this greyed: the ordinal is a property of the whole
    window and is assigned per run, not per upload."""
    assert naming.preview_name("shopee", "income", "Kao") == "NNN_income. Kao.xlsx"
    assert naming.preview_name("lazada", "weekly", "KAO") == "NNN_KAO.xlsx"


def test_the_pipelines_own_pattern_is_used_for_every_platform(settings):
    """Not a copy. Lazada's pattern lives in `src/lazada.py` rather than in YAML —
    a real asymmetry — and this reaches it rather than pretending config covers it."""
    from _contract import lazada_store_pattern
    assert naming.pattern_for(settings, "lazada") == lazada_store_pattern(settings)
    assert naming.pattern_for(settings, "tiktok") == settings["store_from_filename"]["tiktok"]
    assert naming.pattern_for(settings, "shopee") == settings["store_from_filename"]["shopee"]


def test_names_are_nfc_normalised(settings):
    """Vietnamese store names arrive in both forms and NFD is byte-unequal to the
    visually identical NFC — the same class of bug as the Shopee headers."""
    import unicodedata
    nfd = unicodedata.normalize("NFD", "Curel Đông")
    assert nfd != "Curel Đông"
    name = naming.uniform_name("lazada", "weekly", 1, nfd)
    assert name == "001_Curel Đông.xlsx"
    assert unicodedata.is_normalized("NFC", name)
