"""The config migration's safety net: rows must reproduce the contract.

The expensive half of this gate — every golden window re-run under DB-rendered
config, compared to its committed workbook digests — lives in
`test_config_render_produces_the_committed_goldens` and is marked `slow`, because it
re-runs the pipeline eight times (~11 minutes, mostly Shopee's Excel reads). The
cheap half runs always and catches the failure modes that do not need a workbook:
an unstable render, a key that stopped being emitted, and the one behaviour change
this migration is most likely to make by accident.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.config import REQUIRED_SETTINGS

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


@pytest.fixture
def imported(repo):
    """The real `config/` loaded into the test database."""
    from service import config_import
    with repo._conn() as conn:                                      # noqa: SLF001
        config_import.import_settings(conn, CONFIG, changed_by="test")
        conn.commit()
    return repo


def _rendered(repo) -> tuple[str, dict]:
    text = repo.render_config()
    assert text is not None
    return text, yaml.safe_load(text)


# ---------------------------------------------------------------------------
# The render itself
# ---------------------------------------------------------------------------

def test_rendering_twice_gives_identical_bytes(imported):
    """`config_versions` is content-addressed on sha256, so an unstable render would
    mint a new "version" every time anything asked for one — breaking pin
    de-duplication and making "did the config change?" unanswerable. Every query in
    `read_config` carries an explicit ORDER BY for this reason."""
    assert imported.render_config() == imported.render_config()


def test_the_rendered_config_parses_to_a_plain_dict(imported):
    """One parser for anything that reaches the money math (src/config.py:17-29).
    ruamel's round-trip loader would hand the pipeline `CommentedMap` and
    `ScalarFloat` where it has always been verified against `dict` and `float`."""
    text, parsed = _rendered(imported)
    assert type(parsed) is dict
    assert type(parsed["vat_factors"]["default"]) is float
    assert type(parsed["drop_unmapped_columns"]) is bool
    assert type(parsed["expected_stores"]["tiktok"]) is list


def test_the_render_reproduces_the_file_except_for_named_differences(imported):
    """Semantic equivalence with `config/settings.yaml`, and the exceptions are
    enumerated rather than tolerated.

    The only permitted differences are the seven tolerances that `src/tieout.py`
    reads and the file never configured. Importing them at the code literal each was
    already falling back to is behaviour-neutral by construction — but it is a
    difference, so it is named here and nowhere else.
    """
    _, rendered = _rendered(imported)
    disk = yaml.safe_load((CONFIG / "settings.yaml").read_text(encoding="utf-8"))

    expected_new = {
        ("tolerances", "tiktok", "conservation_vnd"): 1,
        ("tolerances", "tiktok", "grand_vnd"): 1,
        ("tolerances", "tiktok", "per_store_vnd"): 1,
        ("tolerances", "shopee", "grand_vnd"): 1,
        ("tolerances", "shopee", "per_store_vnd"): 1,
        ("tolerances", "lazada", "conservation_vnd"): 1,
        ("tolerances", "lazada", "price_ka_rounding_vnd"): 1000,
    }

    def walk(a, b, path=()):
        out = []
        for key in set(a) | set(b):
            here = path + (str(key),)
            left, right = a.get(key, _MISSING), b.get(key, _MISSING)
            # A whole sub-tree appearing on one side only still has to be compared
            # LEAF BY LEAF, or an entire new section reports as one difference and
            # the enumeration below cannot say which keys it holds. `tolerances.
            # lazada` is exactly that case: absent from the file, two keys in the
            # render.
            if isinstance(left, dict) and right is _MISSING:
                right = {}
            elif isinstance(right, dict) and left is _MISSING:
                left = {}
            if isinstance(left, dict) and isinstance(right, dict):
                out += walk(left, right, here)
            elif isinstance(left, list) and isinstance(right, list):
                # Order is not meaning: both roster keys are consumed as sets by
                # `ingest.check_stores` and `pipeline.apply_partial_roster`.
                if sorted(map(str, left)) != sorted(map(str, right)):
                    out.append((here, left, right))
            elif left != right:
                out.append((here, left, right))
        return out

    differences = walk(disk, rendered)
    unexpected = [(p, a, b) for p, a, b in differences
                  if expected_new.get(p, _MISSING) != b]
    assert not unexpected, f"the render changed something nobody asked it to: {unexpected}"
    assert len(differences) == len(expected_new), (
        f"expected exactly {len(expected_new)} new tolerance(s), got "
        f"{len(differences)}: {[p for p, _, _ in differences]}")


class _Missing:
    def __repr__(self) -> str:                                      # pragma: no cover
        return "<absent>"


_MISSING = _Missing()


def test_every_brand_row_is_rendered_and_resolves_for_a_roster_store(imported):
    """D12's fix, from the other end.

    **This test asserted the opposite until 2026-08-21**, and correctly so: while
    there were two mappings, `store_to_brand` had to stay `{}` or a config
    migration would have silently rebranded storefronts. There is one mapping now,
    so the assertion inverts — every row renders, per platform, with its review
    metadata.

    The second half is the part that would have caught the real defect. Rendering
    the rows was never the hard bit; making them RESOLVE was. brand_map.csv's keys
    were normalised spellings and the pipeline's stores are roster spellings, so
    the exact `.map()` this replaced hit 2 of 42 — a contract that looked populated
    and branded almost nothing. So this resolves through `ingest.store_brands`,
    which is the function the pipeline itself reads.
    """
    from src import ingest

    _, rendered = _rendered(imported)
    brands = rendered["store_to_brand"]
    assert set(brands) <= {"tiktok", "shopee", "lazada"}
    assert brands.get("tiktok"), "the contract renders no TikTok brands"

    with imported._conn() as conn, conn.cursor() as cur:            # noqa: SLF001
        cur.execute("select count(*) from config_store_brands")
        rows = cur.fetchone()[0]
    assert sum(len(v) for v in brands.values()) == rows, (
        "a brand row exists that the contract does not carry — the mapping the "
        "editor shows and the mapping a run reads must be the same rows")

    for platform in ("tiktok", "shopee"):
        resolved = ingest.store_brands(rendered, platform)
        roster = rendered["expected_stores"][platform]
        assert [s for s in roster if ingest.norm_store(s) in resolved], (
            f"no {platform} storefront resolves a brand")
        # A brand keyed on a spelling no run ever produces is the D12 defect.
        assert set(resolved) <= {ingest.norm_store(s) for s in roster}, (
            f"{platform} has brand rows for storefronts the roster does not name")


# ---------------------------------------------------------------------------
# The completeness control
# ---------------------------------------------------------------------------

def test_a_rendered_config_supplies_every_key_the_pipeline_reads(imported):
    from service import config_render
    _, rendered = _rendered(imported)
    config_render.assert_complete(rendered)


@pytest.mark.parametrize("key", sorted(REQUIRED_SETTINGS))
def test_the_keys_whose_absence_changes_behaviour_are_caught(imported, key):
    """A missing row here is not a missing setting — it is a silent behaviour change,
    which is why rendering asserts rather than relies on `.get`.

    `dedupe_rows` had a code default that was the OPPOSITE of its configured value
    (True: legitimate duplicate order lines dropped, revenue understated);
    `drop_unmapped_columns` did too until M8/2.4. **Neither has a default any more** —
    A9 (2026-08-21) made both, plus `cross_window_order_backfill`, hard-stop through
    `src.config.require`.

    This test does not become redundant. A run that hard-stops has already been
    queued, materialised and started, so refusing an incomplete contract at RENDER
    time is the earlier and cheaper failure. What changed is that it parametrizes
    over `src.config.REQUIRED_SETTINGS` rather than a list kept here — one set of
    keys for both layers, so they cannot disagree about which ones matter."""
    from service import config_render
    _, rendered = _rendered(imported)
    rendered.pop(key)
    with pytest.raises(ValueError, match=key):
        config_render.assert_complete(rendered)


def test_an_unmodelled_settings_key_is_refused_not_skipped(tmp_path):
    """A key the importer does not model would stop reaching the pipeline the moment
    rendering becomes the source of truth. Silently dropping it is the same class of
    bug as a column map that quietly loses a header."""
    from service import config_import
    text = (CONFIG / "settings.yaml").read_text(encoding="utf-8")
    sandbox = tmp_path / "config"
    sandbox.mkdir()
    (sandbox / "settings.yaml").write_text(
        text + "\nsomething_nobody_modelled: 3\n", encoding="utf-8")

    with pytest.raises(ValueError, match="something_nobody_modelled"):
        config_import.import_settings(None, sandbox, changed_by="test")


# ---------------------------------------------------------------------------
# The cutover
# ---------------------------------------------------------------------------

def test_an_unpinned_window_resolves_to_the_rendered_config(imported, service_settings):
    """The fix for A1. Before this, an unpinned run read `settings.yaml` off the
    filesystem of whichever process asked — and the api and the worker are separate
    containers with separate baked copies and no volume between them, so an edit
    applied in the browser never reached the process that computes the money."""
    from service import config_store
    resolved = config_store.resolve_for_window(
        imported, service_settings.config_dir, "lazada", "2026-05_l1")
    assert not resolved.pinned
    assert resolved.content == imported.render_config()

    with imported._conn() as conn, conn.cursor() as cur:            # noqa: SLF001
        cur.execute("select source from config_versions where id = %s",
                    (resolved.version_id,))
        assert cur.fetchone()[0] == "rendered"


def test_with_no_rows_the_resolver_falls_back_to_the_file(repo, service_settings):
    """A fresh deployment seeds itself from the image, and the CLI keeps working with
    no service at all (D24). `None` from `render_config` is that signal."""
    from service import config_store
    assert repo.render_config() is None
    resolved = config_store.resolve_for_window(
        repo, service_settings.config_dir, "lazada", "2026-05_l1")
    assert resolved.content == config_store.read_text(service_settings.config_dir)


# ---------------------------------------------------------------------------
# The expensive half
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_config_render_produces_the_committed_goldens(imported):
    """Every golden window, re-run under DB-rendered config, at zero tolerance.

    This is the gate that makes migrating the domain contract defensible. Same
    engine, so bit-exact is achievable; a moved digest here is a real difference in
    what the config says, never a reason to widen anything.

    Skips per window when that window's inputs are not on this machine, which is
    correct and is also how a regression could go unnoticed on a machine without
    client data — `RECON_REQUIRE_CLIENT_DATA=1` makes that a failure instead.
    """
    import os
    import sys
    import tempfile

    sys.path.insert(0, str(ROOT / "tests" / "goldens"))
    from cellset import load_cellset, manifest as cellset_manifest

    from src import pipeline

    committed = json.loads(
        (ROOT / "tests" / "goldens" / "manifest.json").read_text("utf-8"))["windows"]
    rendered = imported.render_config()
    require = os.environ.get("RECON_REQUIRE_CLIENT_DATA") == "1"

    checked = 0
    for key, entry in committed.items():
        period, platform = key.split("/")
        if not (ROOT / "input" / period).is_dir():
            if require:
                pytest.fail(f"{key}: inputs absent but RECON_REQUIRE_CLIENT_DATA=1")
            continue
        with tempfile.TemporaryDirectory() as tmp:
            ctx = pipeline.build_context(
                platform, period, config_dir=CONFIG, input_root=ROOT / "input",
                output_root=Path(tmp), settings_text=rendered,
                partial_roster=bool(entry.get("partial_roster")))
            result = pipeline.run(ctx)
            assert result.workbook is not None, f"{key} hard-stopped: {result.error}"
            pipeline.write_artifacts(result)
            produced = cellset_manifest(load_cellset(result.workbook_path))
        assert produced == entry["workbook"], (
            f"{key}: the rendered config moved a workbook cell")
        checked += 1

    assert checked, "no golden window had local inputs — this proved nothing"
