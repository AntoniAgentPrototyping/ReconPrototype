"""The opt-in Excel read cache, and the three properties that make it safe.

This cache sits under the two most valuable tests in the suite — the ones proving
the upload sanitizer can rewrite a real export and the pipeline still produces the
committed golden. A cache that served a stale or mis-keyed frame there would turn
that gate into a recording of its own output, which is the failure `oracle_rev` was
deleted over ([D26](../docs/06-DECISIONS.md#d26)).

So the properties are tested, not asserted in a docstring:

1. it is **off** unless asked for,
2. the key is the file's **content**, never its name or its path,
3. every input that changes what a decode returns changes the key.
"""

from __future__ import annotations

import pytest

import io_cache

pytest.importorskip("pandas")


@pytest.fixture
def sheet(tmp_path):
    """A tiny real .xlsx, written the way the exports are read: all strings."""
    import pandas as pd

    path = tmp_path / "1_Store.xlsx"
    frame = pd.DataFrame({"Order No.": ["A-1", "A-2"], "Amount": ["100", "200"]})
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Transaction Overview", index=False)
    return path


# ---------------------------------------------------------------------------
# 1. Off unless asked for
# ---------------------------------------------------------------------------

def test_the_cache_is_off_unless_the_environment_asks_for_it(monkeypatch):
    """**The property everything else rests on.**

    A run whose reads were served from a cache is a weaker run. If this defaulted
    on, the run that mattered — a golden re-baseline, a claim that a change moved
    nothing — would be the one nobody remembered to turn it off for.
    """
    monkeypatch.delenv(io_cache.ENV_FLAG, raising=False)
    assert io_cache.enabled() is False
    assert io_cache.install(monkeypatch) is False, (
        "install() engaged without the environment flag")


def test_installing_leaves_the_real_reader_in_place_when_disabled(monkeypatch):
    from src import ingest

    monkeypatch.delenv(io_cache.ENV_FLAG, raising=False)
    before = ingest.read_excel_sheet
    io_cache.install(monkeypatch)
    assert ingest.read_excel_sheet is before


# ---------------------------------------------------------------------------
# 2. Keyed on content, never on identity
# ---------------------------------------------------------------------------

def test_the_same_bytes_under_a_different_name_share_an_entry(tmp_path, sheet):
    """`test_uploads.py` copies and renames real exports into a tmp_path, so two
    names routinely hold identical bytes. Keying on the path would decode the same
    sheet twice and make the cache close to useless where it is needed most."""
    import shutil

    renamed = tmp_path / "3_Store renamed.xlsx"
    shutil.copy2(sheet, renamed)
    assert (io_cache.cache_key(sheet, 0, 1, None)
            == io_cache.cache_key(renamed, 0, 1, None))


def test_one_changed_byte_changes_the_key(tmp_path, sheet):
    """The property that makes a stale entry impossible to name, and therefore
    impossible to serve."""
    import pandas as pd

    before = io_cache.cache_key(sheet, 0, 1, None)
    with pd.ExcelWriter(sheet, engine="openpyxl") as writer:
        pd.DataFrame({"Order No.": ["A-1"], "Amount": ["999"]}).to_excel(
            writer, sheet_name="Transaction Overview", index=False)
    assert io_cache.cache_key(sheet, 0, 1, None) != before


# ---------------------------------------------------------------------------
# 3. Every input that changes the decode changes the key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("other", [
    pytest.param(("Doanh thu", 1, None), id="sheet"),
    pytest.param((0, 3, None), id="header_row"),
    pytest.param((0, 1, "calamine"), id="engine"),
])
def test_every_reading_rule_is_part_of_the_key(sheet, other):
    """A header row of 3 and a header row of 1 are different frames off the same
    bytes — Shopee income is exactly that case. An engine change is the
    broken-`<dimension>` fallback, the difference between 63 columns and one."""
    assert io_cache.cache_key(sheet, 0, 1, None) != io_cache.cache_key(sheet, *other)


# ---------------------------------------------------------------------------
# It has to actually return the same frame
# ---------------------------------------------------------------------------

def test_a_cached_read_returns_what_the_real_read_returned(monkeypatch, sheet, tmp_path):
    """Round trip through Parquet and back, compared to the real decode.

    All-string frames are what `read_excel_sheet` produces (`dtype=str`), which is
    why Parquet round-trips them exactly and why this can be an equality check
    rather than a tolerance.
    """
    import pandas as pd

    from src import ingest

    real = ingest.read_excel_sheet(sheet, 0, 1, None)

    monkeypatch.setenv(io_cache.ENV_FLAG, "1")
    monkeypatch.setattr(io_cache, "CACHE_ROOT", tmp_path / "cache")
    assert io_cache.install(monkeypatch) is True

    miss = ingest.read_excel_sheet(sheet, 0, 1, None)    # populates
    hit = ingest.read_excel_sheet(sheet, 0, 1, None)     # serves

    assert list((tmp_path / "cache").glob("*.parquet")), "nothing was cached"
    pd.testing.assert_frame_equal(miss, real)
    pd.testing.assert_frame_equal(hit, real)


def test_a_corrupt_entry_falls_back_to_a_real_read(monkeypatch, sheet, tmp_path):
    """The cache is an optimisation, never an oracle. A damaged entry must cost a
    decode, not a failed test somebody then has to diagnose as a real regression."""
    import pandas as pd

    from src import ingest

    real = ingest.read_excel_sheet(sheet, 0, 1, None)
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv(io_cache.ENV_FLAG, "1")
    monkeypatch.setattr(io_cache, "CACHE_ROOT", cache_dir)
    io_cache.install(monkeypatch)

    ingest.read_excel_sheet(sheet, 0, 1, None)
    entry = next(iter(cache_dir.glob("*.parquet")))
    entry.write_bytes(b"not parquet at all")

    pd.testing.assert_frame_equal(ingest.read_excel_sheet(sheet, 0, 1, None), real)
