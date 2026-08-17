"""Do the local goldens still match what was committed?

Two jobs:

1. **Integrity** (skips without goldens) — the committed digests must match
   the goldens on this machine. This is what lets a second developer prove
   reproducibility from their own copy of the raw exports without either of us
   moving a byte of client data.

2. **PII guard** (always runs) — the committed manifest must contain hashes
   and shapes only. The exports carry customer PII (settings.yaml:285:
   Recipient / Phone # / Detail Address) and the store/brand names are client
   identities, so a manifest that leaked either would be a disclosure in git
   history that cannot be recalled.
"""

from __future__ import annotations

import json

import pytest
import yaml

from cellset import load_jsonl, manifest
from fingerprint import digest_json
from goldens import ROOT, committed_windows, default_goldens_dir, discover_windows


def test_committed_manifest_is_parseable_and_stamped(committed_manifest):
    if not committed_manifest:
        pytest.skip("no committed manifest yet")
    assert committed_manifest["windows"], "manifest has no windows"
    prov = committed_manifest["provenance"]
    assert prov["python"] and prov["deps"]["pandas"], "provenance must pin python + pandas"


def test_every_committed_window_has_a_golden():
    """Coverage report, not a gate — it fails only if goldens exist for SOME
    windows but not others, which means a partially-populated machine is
    silently gating less than the manifest claims.

    With no goldens at all the whole suite is honestly skipped; with all of
    them it passes. The dangerous middle is the case worth naming.
    """
    committed = set(committed_windows())
    if not committed:
        pytest.skip("no committed manifest yet")
    present = set(discover_windows())
    if not present:
        pytest.skip(f"no goldens on this machine — set RECON_GOLDENS "
                    f"(expected under {default_goldens_dir()})")
    missing = sorted(committed - present)
    assert not missing, (
        f"goldens present for {len(present)}/{len(committed)} committed windows; "
        f"missing: {missing}. Those windows are NOT being gated on this machine — "
        f"regenerate them with tools/make_golden.py or the run is less verified "
        f"than the manifest implies.")


def test_golden_matches_committed_digest(golden_window):
    if golden_window is None:
        pytest.skip("no goldens on this machine — set RECON_GOLDENS to enable")
    key, (golden_dir, entry) = golden_window

    rebuilt = manifest(load_jsonl(golden_dir / "cellset.jsonl"))
    assert rebuilt == entry["workbook"], (
        f"{key}: stored cellset no longer hashes to the committed manifest — "
        f"either the golden was edited or the committed digest is stale")

    fp = json.loads((golden_dir / "fingerprint.json").read_text(encoding="utf-8"))
    assert digest_json(fp) == entry["fingerprint_digest"], f"{key}: fingerprint digest drifted"
    assert {s["stage"]: s["rows"] for s in fp["stages"]} == entry["stage_row_counts"]


def test_fingerprint_records_no_cell_values(golden_window):
    """Fingerprints hold counts, column names and column-level sums. A raw
    store name appearing there would end up in a committed digest's source."""
    if golden_window is None:
        pytest.skip("no goldens on this machine")
    key, (golden_dir, _entry) = golden_window
    fp = json.loads((golden_dir / "fingerprint.json").read_text(encoding="utf-8"))

    for stage in fp["stages"]:
        for row in stage.get("by_store", []):
            assert set(row) == {"store_h", "rows", "sums"}, f"{key}: unexpected by_store key"
            assert len(row["store_h"]) == 12 and all(c in "0123456789abcdef" for c in row["store_h"])


def _walk_numbers(node, path="$"):
    """Yield (json_path, number) for every number in a parsed document."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_numbers(v, f"{path}[{i}]")


def test_committed_manifests_contain_only_integer_numbers(committed_manifest):
    """Money is fractional; counts and dimensions are not.

    Checking parsed NUMBERS rather than grepping the serialized text matters:
    a text scan trips over the Python version ("3.12.10") and over Lazada's
    per-VAT-rate tab names ("1.05", "1.08", "1.10"), which are sheet labels
    rather than amounts. A non-integer number, by contrast, cannot be a row
    count or a cell count — it would be a value that escaped hashing.
    """
    if not committed_manifest:
        pytest.skip("no committed manifest yet")
    fractional = [(p, n) for p, n in _walk_numbers(committed_manifest)
                  if not float(n).is_integer()]
    assert not fractional, f"manifest carries fractional numbers: {fractional[:5]}"


def _client_store_names() -> list[str]:
    """Store rosters from settings.yaml — the per-window, data-derived client
    identities. These are what must never sit next to a figure."""
    cfg = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for stores in (cfg.get("expected_stores") or {}).values():
        names.update(stores or [])
    # Short tokens would false-positive against hex digests.
    return sorted(n for n in names if isinstance(n, str) and len(n) >= 6)


def test_fingerprints_never_pair_a_client_name_with_a_figure(golden_window):
    """The disclosure that would actually matter.

    Fingerprints carry per-store subtotals, so a raw store name appearing there
    would tie a named client to revenue. That is why by_store is keyed on
    store_h. Workbook SHEET names are a different matter and are deliberately
    not covered here: Lazada's brand tabs ('KAO.xlsx', 'Curel.xlsx',
    'Merries.xlsx') are structural constants already committed in
    src/finance_template.py:90-92, and they carry a digest and a cell count —
    never an amount. Recording them discloses nothing new, and the differ needs
    them to report a missing or reordered sheet at all.
    """
    if golden_window is None:
        pytest.skip("no goldens on this machine")
    key, (golden_dir, _entry) = golden_window
    names = _client_store_names()
    assert names, "settings.yaml yielded no store names — this guard would be vacuous"

    blob = (golden_dir / "fingerprint.json").read_text(encoding="utf-8")
    leaked = sorted({n for n in names if n in blob})
    assert not leaked, f"{key}: fingerprint pairs client name(s) with figures: {leaked}"
