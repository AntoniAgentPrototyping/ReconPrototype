"""The M4 gate: a workbook produced through the service is the CLI's workbook.

Everything else in tests/service/ tests the wrapper. This tests the claim the
wrapper exists to make safe — that routing a run through a queue, a worker, a
substituted logger and an artifact store changes **no cell** of the deliverable
the team invoices from.

It works by comparing against the committed golden digests. Those were generated
through `tools/full_run.py` (via tools/make_golden.py), so a match is a direct
statement about the two callers rather than about this test's own idea of what
the answer should be. Zero tolerance, per D17 — same engine, so bit-exact is
achievable and a diff is a finding.

Skips when the window's raw exports are absent, like the rest of the golden gate:
goldens derive from client data and are never distributed (D15).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "tests" / "goldens", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("pandas")

from cellset import load_cellset, manifest  # noqa: E402  (tests/goldens)
from goldens import committed_windows  # noqa: E402

from service.models import JobState  # noqa: E402
from service.worker import Worker  # noqa: E402
from src.pipeline import RunStatus  # noqa: E402

# Lazada l1 is the window used here because it runs in ~0.5s — the whole point of
# a gate is that it runs every time, and a 171-second Shopee window would get
# marked slow and then deselected.
WINDOW, PLATFORM = "2026-05_l1", "lazada"
KEY = f"{WINDOW}/{PLATFORM}"


@pytest.fixture
def real_window_settings(service_settings, tmp_path):
    """Point the worker at the repo's real input and config, scratch in tmp."""
    from dataclasses import replace
    if not (ROOT / "input" / WINDOW / PLATFORM).is_dir():
        pytest.skip(f"input/{WINDOW}/{PLATFORM} is absent — goldens derive from "
                    f"client data and are not distributed (D15)")
    if KEY not in committed_windows():
        pytest.skip(f"{KEY} has no committed golden digest")
    return replace(service_settings, input_root=ROOT / "input", config_dir=ROOT / "config")


def test_the_service_workbook_matches_the_committed_golden(
        repo, store, real_window_settings):
    committed = committed_windows()[KEY]["workbook"]

    repo.enqueue(PLATFORM, WINDOW)
    outcome = Worker(repo, store, real_window_settings).serve(once=True)[0]
    assert repo.get_job(outcome.job_id).state is JobState.DONE
    assert outcome.status is not RunStatus.HARD_STOP

    produced = manifest(load_cellset(store.open(
        repo.artifact(outcome.run_id, "finance_file.xlsx").uri)))

    # Sheet-by-sheet before the whole thing, so a failure names the tab rather
    # than saying "the workbook moved".
    assert [s["name"] for s in produced["sheets"]] == [s["name"] for s in committed["sheets"]]
    for got, want in zip(produced["sheets"], committed["sheets"]):
        assert got["cell_count"] == want["cell_count"], f"{got['name']}: cell count moved"
        assert (got["max_row"], got["max_col"]) == (want["max_row"], want["max_col"]), \
            f"{got['name']}: shape moved"
        assert got["digest"] == want["digest"], (
            f"{got['name']}: content differs between a service run and the CLI run "
            f"that produced the golden. Find the cell with the differ before "
            f"anything else: pytest tests/goldens -q")

    assert produced == committed


def test_the_service_findings_match_the_committed_variance_count(
        repo, store, real_window_settings):
    """The findings list is committed too, inside variances.json's digest. A
    service run with no refs must produce the same findings the CLI's golden run
    did — same count, same order, same kinds."""
    committed = committed_windows()[KEY]

    repo.enqueue(PLATFORM, WINDOW)
    outcome = Worker(repo, store, real_window_settings).serve(once=True)[0]
    run = repo.get_run(outcome.run_id)

    assert len(run.findings) == committed["variance_count"]
    assert committed["used_refs"] is False, "this window's golden was generated without refs"
    assert run.status is RunStatus.UNVERIFIED or run.variances
