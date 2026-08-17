"""Does the golden gate actually refuse to move?

Same philosophy as test_diff_selftest.py: a gate nobody has tried to fool is
not a gate. This project exists because the finance team's own controls were
faithful ports of checks whose value had already died — and nobody noticed
because nothing ever tried to break them.

The specific risk here: `make_golden.py` both *generates* the golden and
*writes* the committed digest. If regenerating silently overwrote a differing
baseline, the manifest would stop being a gate and become a log of whatever the
code did most recently — while still looking exactly like a gate in git.
"""

from __future__ import annotations

import json

import pytest

from make_golden import merge_manifest

ENTRY = {"workbook": {"sheets": [{"name": "S", "digest": "sha256:aaa", "cell_count": 3}]},
         "fingerprint_digest": "sha256:bbb",
         "stage_row_counts": {"read": 10},
         "variance_count": 0,
         "used_refs": False,
         "partial_roster": False,
         "stores_seen": 1}
PROV = {"python": "3.12.10", "deps": {"pandas": "2.3.3"}}
KEY = "2026-05_l1/lazada"


def _write(path, entry, **kw):
    merge_manifest(path, PROV, KEY, entry,
                   **{"rebaseline": False, "reason": None, **kw})


def test_a_first_write_needs_no_permission(tmp_path):
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    assert json.loads(p.read_text(encoding="utf-8"))["windows"][KEY] == ENTRY


def test_rewriting_an_identical_entry_is_allowed(tmp_path):
    """Regenerating an unchanged window must not require a ceremony —
    otherwise the guard trains people to pass --rebaseline reflexively."""
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    _write(p, dict(ENTRY))
    assert json.loads(p.read_text(encoding="utf-8"))["windows"][KEY] == ENTRY


@pytest.mark.parametrize("field,value", [
    ("fingerprint_digest", "sha256:CHANGED"),
    ("stage_row_counts", {"read": 9}),
    ("variance_count", 1),
    ("stores_seen", 2),
    ("partial_roster", True),
])
def test_a_moved_digest_is_refused(tmp_path, field, value):
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    moved = {**ENTRY, field: value}

    with pytest.raises(SystemExit) as exc:
        _write(p, moved)
    assert field in str(exc.value), "the refusal must name what moved"

    on_disk = json.loads(p.read_text(encoding="utf-8"))["windows"][KEY]
    assert on_disk == ENTRY, "a refused write must leave the baseline untouched"


def test_a_changed_workbook_cell_count_is_refused(tmp_path):
    """The nested case — a digest buried inside the workbook structure."""
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    moved = json.loads(json.dumps(ENTRY))
    moved["workbook"]["sheets"][0]["cell_count"] = 4

    with pytest.raises(SystemExit):
        _write(p, moved)


def test_rebaseline_moves_it_and_records_why(tmp_path):
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    moved = {**ENTRY, "fingerprint_digest": "sha256:CHANGED"}

    _write(p, moved, rebaseline=True, reason="VAT revert 8% -> 10%")

    entry = json.loads(p.read_text(encoding="utf-8"))["windows"][KEY]
    assert entry["fingerprint_digest"] == "sha256:CHANGED"
    assert entry["rebaselined"]["reason"] == "VAT revert 8% -> 10%"
    assert "fingerprint_digest" in entry["rebaselined"]["changed"]


def test_the_rebaseline_stamp_does_not_itself_trip_the_guard(tmp_path):
    """After a re-baseline the entry carries a `rebaselined` block. Comparing
    it against a fresh run — which has no such block — must not read as a
    regression, or every window would need re-baselining twice."""
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    moved = {**ENTRY, "variance_count": 1}
    _write(p, moved, rebaseline=True, reason="expected: NFC fix maps subsidy")

    _write(p, dict(moved))   # must not raise

    entry = json.loads(p.read_text(encoding="utf-8"))["windows"][KEY]
    assert entry["variance_count"] == 1


def test_an_ordinary_rerun_does_not_erase_why_the_baseline_moved(tmp_path):
    """The reason a baseline moved is the audit trail ([D26](../../docs/06-DECISIONS.md#d26)):
    "`git diff` on that file IS the record". It was being **deleted** by the
    next ordinary regeneration — the entry a clean run produces has no
    `rebaselined` block, and it overwrote the one that did. The digests were
    identical, the guard stayed quiet, and the explanation evaporated.

    Caught in M2.5 by re-running a window after re-baselining it. The test
    above walks the same path and only asserted "does not raise", which is why
    it never noticed."""
    p = tmp_path / "manifest.json"
    _write(p, ENTRY)
    moved = {**ENTRY, "fingerprint_digest": "sha256:CHANGED"}
    _write(p, moved, rebaseline=True, reason="defect 1.6: null counts only")

    _write(p, dict(moved))          # an ordinary regeneration, nothing changed

    entry = json.loads(p.read_text(encoding="utf-8"))["windows"][KEY]
    assert entry["rebaselined"]["reason"] == "defect 1.6: null counts only", (
        "the reason a golden was re-baselined must survive later regenerations"
    )
