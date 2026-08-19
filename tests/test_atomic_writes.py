"""Write-path robustness: atomic artifact writes (1.7/D10) and frame aliasing (1.10).

Artifacts are written through a temp file and renamed into place.

Defect 1.7's residual, register item D10: every writer wrote straight to its final
path, so a crash, a full disk or a killed worker mid-write left a truncated
`finance_file.xlsx`. That file still OPENS in Excel — the failure being removed is
a finance file that looks current and is short some tabs.

**What these tests do not claim.** `os.replace` on Windows still raises
`PermissionError` when the destination is open in Excel, which is routine operator
behaviour. The guarantee is narrower and worth stating exactly: the previous
artifact survives intact and the failure is reported, instead of being clobbered
halfway. `test_a_locked_destination_leaves_the_previous_artifact_intact` is that
claim, and it is the honest boundary of the fix.
"""

from __future__ import annotations

import pytest

# Vestigial — see the note in test_tieout_blindness.py.
pytest.importorskip("pandas", reason="pandas is a hard dependency; guard is vestigial")

from src import pipeline  # noqa: E402


def test_a_failed_write_leaves_no_artifact_at_all(tmp_path):
    """The temp file is cleaned up and the final path never appears."""
    target = tmp_path / "finance_file.xlsx"

    def explode(p):
        p.write_text("half a workbook", encoding="utf-8")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        pipeline._write_atomically(target, explode)

    assert not target.exists(), "a failed write left a partial artifact behind"
    assert list(tmp_path.iterdir()) == [], f"temp file not cleaned up: {list(tmp_path.iterdir())}"


def test_a_failed_write_leaves_a_previous_artifact_byte_intact(tmp_path):
    """The case that matters on a re-run: last week's file must survive this
    week's failure rather than being truncated in place."""
    target = tmp_path / "finance_file.xlsx"
    target.write_bytes(b"last week's workbook, complete")
    before = target.read_bytes()

    with pytest.raises(RuntimeError):
        pipeline._write_atomically(target, lambda p: (_ for _ in ()).throw(RuntimeError("boom")))

    assert target.read_bytes() == before, (
        "the previous artifact was modified by a write that failed")


def test_a_successful_write_replaces_the_target(tmp_path):
    target = tmp_path / "run_metrics.json"
    target.write_text("stale", encoding="utf-8")

    pipeline._write_atomically(target, lambda p: p.write_text("fresh", encoding="utf-8"))

    assert target.read_text(encoding="utf-8") == "fresh"
    assert not (tmp_path / "run_metrics.json.tmp").exists()


def test_the_temp_file_is_a_sibling(tmp_path):
    """`os.replace` is only atomic within one filesystem. A temp file in the
    system temp dir would silently degrade to copy-then-delete across volumes."""
    target = tmp_path / "sub" / "finance_file.xlsx"
    target.parent.mkdir()
    seen: list = []

    pipeline._write_atomically(target, lambda p: (seen.append(p), p.write_text("x")))

    assert seen[0].parent == target.parent, (
        f"temp file was written to {seen[0].parent}, not alongside the artifact")


def test_a_locked_destination_leaves_the_previous_artifact_intact(tmp_path, monkeypatch):
    """The honest limit, asserted rather than described.

    A destination held open by Excel makes `os.replace` raise. The new bytes do not
    land — that is not fixable here — but the old file is still whole and the error
    propagates to `service/failures.py`, which turns `PermissionError` into a
    sentence naming the file.
    """
    target = tmp_path / "finance_file.xlsx"
    target.write_bytes(b"the file an operator has open")

    def locked(src, dst):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(pipeline.os, "replace", locked)

    with pytest.raises(PermissionError):
        pipeline._write_atomically(target, lambda p: p.write_text("new", encoding="utf-8"))

    assert target.read_bytes() == b"the file an operator has open"
    assert not (tmp_path / "finance_file.xlsx.tmp").exists(), (
        "the temp file must still be cleaned up when the rename is the thing that failed")


# ---------------------------------------------------------------------------
# Defect 1.10 — in-place mutation of a passed-in frame
# ---------------------------------------------------------------------------

def test_blank_repeats_does_not_mutate_its_argument():
    """`_blank_repeats` blanked its caller's frame in place AND returned it.

    Safe today only because every caller happens to pass a freshly-built frame and
    reassign the result. The hazard is not that a caller is surprised — it is that
    the "non repeat" columns are built from the same Series as the real ones, so
    under a no-copy frame constructor blanking one would empty the other. See the
    function's docstring and the `pandas<3` pin.
    """
    import pandas as pd

    from src import finance_template

    store = pd.Series(["KAO", "KAO", "AHC"])
    df = pd.DataFrame({"store": store, "store_non_repeat": store, "n": [1, 2, 3]})
    before = df.copy()

    out = finance_template._blank_repeats(df, "store", ["store_non_repeat"])

    pd.testing.assert_frame_equal(df, before, obj="the caller's frame")
    assert out["store_non_repeat"].tolist() == ["KAO", None, "AHC"]
    assert out["store"].tolist() == ["KAO", "KAO", "AHC"], (
        "the real store column was blanked along with its 'non repeat' twin")
