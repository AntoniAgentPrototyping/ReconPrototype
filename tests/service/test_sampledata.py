"""The demo window: deterministic, believable, and it must not touch real config.

Its own gate is the determinism test — two generations compared by **cellset
digest**, never by file bytes, because openpyxl stamps timestamps into
`docProps/core.xml` ([D16](docs/06-DECISIONS.md#d16)).

The other tests here pin the per-platform traps. Each one, if broken, produces a
window that runs green and means nothing — which is worse than a window that fails.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT, ROOT / "tests" / "goldens"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("pandas")

from service import sampledata                                  # noqa: E402

CONFIG = ROOT / "config"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    target = tmp_path_factory.mktemp("demo")
    return target, sampledata.generate(target, config_dir=CONFIG)


# ---------------------------------------------------------------------------
# Determinism — the generator's own gate
# ---------------------------------------------------------------------------

def test_two_generations_are_identical_by_cellset_digest(tmp_path):
    """Compared by digest, not by file bytes: openpyxl stamps a timestamp into every
    workbook, so identical numbers produce different bytes."""
    from cellset import load_cellset, manifest

    runs = []
    for attempt in ("a", "b"):
        target = tmp_path / attempt
        written = sampledata.generate(target, config_dir=CONFIG)
        runs.append({str(p.relative_to(target)): manifest(load_cellset(p))
                     for p in written})

    first, second = runs
    assert first.keys() == second.keys()
    assert first == second, "the generator is not deterministic"
    assert len(first) == 10, f"expected 10 files, got {len(first)}"


# There is deliberately NO test asserting that two generations produce DIFFERENT
# file bytes, even though they usually do. It was written, and it failed: openpyxl's
# timestamp has one-second granularity, so two fast generations inside the same
# second are byte-identical and the assertion is decided by the clock. A test whose
# outcome depends on how long the previous test took is worse than the comment it
# was trying to enforce — so this is the comment. **Never compare workbook file
# bytes** ([D16](docs/06-DECISIONS.md#d16)); compare cellset digests, as above.


# ---------------------------------------------------------------------------
# Filenames carry the store, not a column
# ---------------------------------------------------------------------------

def test_every_generated_name_is_already_uniform_and_parses(generated):
    """The legacy generator emitted `part_1.csv` with a `Shop Name` COLUMN, which
    exercises a path production never takes — store identity comes from the filename
    (D6). These names are the uniform scheme and resolve through the pipeline's own
    parser."""
    from service import naming
    from src import config as src_config

    _target, written = generated
    settings = src_config.load_settings(CONFIG)
    for path in written:
        platform = path.parent.parent.name
        store = naming.store_of(path.name, platform, settings)
        assert store in sampledata.STORES, f"{path.name} resolved to {store!r}"
        naming.validate_roundtrip(path.name, platform, store, settings)


def test_one_store_is_vietnamese(generated):
    """So the demo exercises `SAFE_FILENAME`'s `À-ỹ` class, NFC normalisation and the
    Excel sheet-name path. A demo of an entirely ASCII world would not."""
    import unicodedata

    from service.uploads import check_filename

    _target, written = generated
    assert any(any(ord(ch) > 127 for ch in p.name) for p in written)
    for path in written:
        assert check_filename(path.name) == unicodedata.normalize("NFC", path.name)


def test_no_generated_store_name_appears_in_the_real_config():
    """A demo store leaking into the real roster would make every real window expect
    a storefront that does not exist."""
    text = (CONFIG / "settings.yaml").read_text(encoding="utf-8")
    for store in sampledata.STORES:
        assert store not in text, f"{store!r} is in the real settings.yaml"


# ---------------------------------------------------------------------------
# The per-platform traps
# ---------------------------------------------------------------------------

def test_tiktok_orders_carry_the_junk_row_the_config_drops(generated):
    """`skip_rows_after_header.tiktok.orders: 1` drops the first data row. Omit it
    and every order shifts by one — silently."""
    import pandas as pd

    target, _written = generated
    path = next((target / sampledata.PERIOD / "tiktok" / "orders").iterdir())
    raw = pd.read_excel(path, sheet_name="OrderSKUList", dtype=str)
    assert "do not read this row" in str(raw.iloc[0]["Order ID"])


def test_shopee_income_has_band_rows_and_two_matching_sheets(generated):
    """`header_rows.shopee.income: 3` means two band rows above the leaf header, and
    `sheet_patterns` means every sheet matching /Doanh thu/ is concatenated."""
    import pandas as pd

    target, _written = generated
    path = next((target / sampledata.PERIOD / "shopee" / "income").iterdir())
    with pd.ExcelFile(path) as book:
        sheets = list(book.sheet_names)
    matching = [s for s in sheets if "Doanh thu" in s]
    assert len(matching) == 2, f"expected two matching sheets, got {sheets}"

    # Read with header on row 3, the way the pipeline will.
    frame = pd.read_excel(path, sheet_name=matching[0], header=2, dtype=str)
    assert "Mã đơn hàng" in frame.columns, (
        "the leaf header is not on row 3, so read_parts would read band rows as "
        "headers and map nothing")


def test_one_shopee_order_export_has_nfd_headers(generated):
    """Real Shopee ORDER exports deliver Vietnamese headers decomposed — 9 of 63 in a
    real file — and NFD is byte-unequal to the visually identical config key. This is
    the bug `ingest.read_parts` normalises for, so the demo has to contain it."""
    import unicodedata

    import pandas as pd

    target, _written = generated
    folder = target / sampledata.PERIOD / "shopee" / "orders"
    forms = set()
    for path in sorted(folder.iterdir()):
        columns = list(pd.read_excel(path, sheet_name="orders", dtype=str).columns)
        forms.add(all(unicodedata.is_normalized("NFC", str(c)) for c in columns))
    assert forms == {True, False}, (
        "expected one NFC export and one NFD export, so the demo exercises both")


def test_lazada_fee_names_come_from_the_master(generated):
    """A fee name absent from the master lands in the unmapped frame and leaves every
    revenue tab empty — a green run that proved nothing."""
    import pandas as pd

    from src import lazada
    from src.runlog import RunLog

    target, _written = generated
    path = next((target / sampledata.PERIOD / "lazada" / "Weekly").iterdir())
    frame = pd.read_excel(path, sheet_name="Transaction Overview", dtype=str)
    fee_map = lazada.load_fee_type_map(CONFIG, RunLog())

    names = set(frame["Fee Name"].dropna())
    mapped = {n for n in names if n in fee_map}
    assert len(mapped) >= 2, f"only {mapped} of {names} are in the master"
    # And exactly one deliberate unmapped fee, so the exception sheet is populated.
    assert names - mapped == {"Demo Fee Nobody Has Mapped"}


# ---------------------------------------------------------------------------
# The demo's config version
# ---------------------------------------------------------------------------

def test_the_demo_config_keeps_every_comment():
    """It is a real `config_version` and has to be as defensible as any other."""
    from service import config_store

    real = config_store.read_text(CONFIG)
    demo = sampledata.demo_settings_text(CONFIG)
    assert demo != real
    # Comments describing the REAL stores go with them; the rest survive.
    assert sum(1 for l in demo.splitlines() if l.lstrip().startswith("#")) > 150


def test_the_demo_config_replaces_the_roster_rather_than_adding_to_it():
    """If the demo stores were merely appended, every real window would expect two
    storefronts that do not exist and would hard-stop."""
    from src import config as src_config

    demo = src_config.parse_settings(sampledata.demo_settings_text(CONFIG))
    for platform in ("tiktok", "shopee", "lazada"):
        assert (demo["expected_stores"] or {})[platform] == list(sampledata.STORES)


def test_generating_the_demo_config_does_not_touch_the_file_on_disk():
    before = (CONFIG / "settings.yaml").read_bytes()
    sampledata.demo_settings_text(CONFIG)
    assert (CONFIG / "settings.yaml").read_bytes() == before


# ---------------------------------------------------------------------------
# It has to actually run, and it has to say something
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform", ["lazada", "tiktok", "shopee"])
def test_the_demo_window_runs_through_the_verified_pipeline(generated, tmp_path,
                                                            platform):
    """Under the demo's own config, which is what the pin mechanism supplies at run
    time. A demo that hard-stops teaches the wrong lesson on the first click."""
    from src import pipeline
    from src.runlog import RunLog

    target, _written = generated
    ctx = pipeline.build_context(
        platform, sampledata.PERIOD, config_dir=CONFIG, input_root=target,
        output_root=tmp_path / "out", log=RunLog(),
        settings_text=sampledata.demo_settings_text(CONFIG))
    result = pipeline.run(ctx)
    assert result.error is None, result.error
    assert result.workbook is not None
    assert len(result.workbook.sheetnames) >= 6
    # Written, not just built: `run()` returns an UNSAVED openpyxl write-only
    # Workbook, and letting it be collected without saving leaves openpyxl's
    # per-sheet XML generators to be finalised against closed files. The CLI and the
    # worker always call this, so a test that does not is the unfaithful one.
    pipeline.write_artifacts(result)


def test_the_exception_queue_is_populated_across_the_demo(generated, tmp_path):
    """**An empty exception queue teaches nothing.** The demo carries ghost income
    lines with no order (TikTok's real ~21% class) and one unmapped fee, so an
    operator opening the queue for the first time sees the two shapes that matter.
    """
    from src import pipeline
    from src.runlog import RunLog

    target, _written = generated
    settings_text = sampledata.demo_settings_text(CONFIG)
    found: dict[str, int] = {}
    for platform in ("lazada", "tiktok", "shopee"):
        ctx = pipeline.build_context(
            platform, sampledata.PERIOD, config_dir=CONFIG, input_root=target,
            output_root=tmp_path / platform, log=RunLog(), settings_text=settings_text)
        result = pipeline.run(ctx)
        assert result.error is None, result.error
        pipeline.write_artifacts(result)
        for sheet, frame in (result.exceptions or {}).items():
            if frame is not None and len(frame):
                found[sheet] = found.get(sheet, 0) + len(frame)

    assert "unmapped_fees" in found, "the deliberate unmapped Lazada fee is missing"
    assert "unmatched_orders" in found, "the ghost TikTok income lines are missing"
    assert sum(found.values()) >= 5, found


def test_the_revenue_crossing_ties_rather_than_breaching(generated, tmp_path):
    """The crossing is DERIVED, and this is why.

    `revenue_crossing_shopee` compares income `gross_revenue + shopee_product_subsidy`
    against the order-file rebuild `(price x qty) - seller_subsidy`. Writing the order
    file's gross straight into income breaches by exactly the two subsidies — which
    this generator did on its first run, at 59,640 VND per order, indistinguishable
    from a real regression. A demo whose tie-out breaches for a manufactured reason
    teaches an operator to ignore breaches.
    """
    from src import pipeline
    from src.runlog import RunLog

    target, _written = generated
    ctx = pipeline.build_context(
        "shopee", sampledata.PERIOD, config_dir=CONFIG, input_root=target,
        output_root=tmp_path / "out", log=RunLog(),
        settings_text=sampledata.demo_settings_text(CONFIG))
    result = pipeline.run(ctx)
    assert result.error is None, result.error
    pipeline.write_artifacts(result)

    crossing = [row for _, row in result.tieout.iterrows()
                if "Revenue crossing" in row["check"] or "Net revenue" in row["check"]]
    assert crossing, "the crossing was not built at all — check the column map"
    breaches = [row["check"] for row in crossing if row["result"] == "BREACH"]
    assert not breaches, f"the derived crossing breached: {breaches}"
