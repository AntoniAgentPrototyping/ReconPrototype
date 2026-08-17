"""The worker assembles its own input, and the two modes that must not diverge.

The end of the manual-staging error class: an operator uploads in a browser and
the worker builds `input/<period>/<platform>/<folder>/` itself, in scratch, right
before `build_context`. `run()` still writes nothing — the download is in
`service/`, which is what keeps `tests/test_io_boundary.py` needing no new grant.

Uses the same synthetic Lazada window `tools/smoke_test.py` generates, for the
same reason it does: this must run on a machine with no client data.
"""

from __future__ import annotations

import pytest

from service.repository import Repository
from service.worker import Worker
from src.pipeline import RunStatus

pytest.importorskip("pandas")
pytest.importorskip("httpx")

from tools.smoke_test import PERIOD, STORE, build_window          # noqa: E402


@pytest.fixture
def worker(repo, store, service_settings):
    return Worker(repo, store, service_settings)


@pytest.fixture
def uploaded_window(repo, make_client, service_settings, tmp_path):
    """The smoke window, pushed through the real upload endpoint.

    Built in a scratch directory and uploaded — NOT written under
    `settings.input_root`, which is the whole point: the worker must find these
    files in the object store and nowhere else.
    """
    build_window(tmp_path / "elsewhere")
    folder = tmp_path / "elsewhere" / "input" / PERIOD / "lazada" / "Weekly"
    client = make_client("recon.user")
    created = []
    for export in sorted(folder.iterdir()):
        with export.open("rb") as fh:
            response = client.post("/uploads", files={"file": (export.name, fh)},
                                   data={"platform": "lazada", "period": PERIOD,
                                         "kind": "weekly"})
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


# ---------------------------------------------------------------------------
# The bucket is the window
# ---------------------------------------------------------------------------

def test_a_run_materializes_its_input_from_the_uploads(
        repo: Repository, worker: Worker, uploaded_window, service_settings):
    """No input volume, no staging step, and a real workbook at the end."""
    assert not (service_settings.input_root / PERIOD).exists(), (
        "the window was never written under input_root — if this exists, the test "
        "is not proving anything about the object store")

    repo.enqueue("lazada", PERIOD)
    outcome = worker.serve(once=True)[0]

    run = repo.get_run(outcome.run_id)
    assert run.status is not RunStatus.HARD_STOP, run.error
    assert "finance_file.xlsx" in {a.name for a in repo.artifacts(run.id)}


def test_the_uniform_name_is_what_the_pipeline_read(
        repo: Repository, worker: Worker, uploaded_window):
    """The rename happens at materialisation, and the run log names the file the
    pipeline actually opened — so `003_KAO.xlsx` in a finance file is traceable
    back to the uploaded bytes."""
    repo.enqueue("lazada", PERIOD)
    run_id = worker.serve(once=True)[0].run_id

    lines, _, _ = repo.log_lines(run_id, limit=5000)
    text = "\n".join(l.text for l in lines)
    assert f"001_{STORE}.xlsx" in text, "the pipeline read the uniform name"
    assert "input materialized from 1 upload(s)" in text
    assert "1 renamed" in text


def test_the_files_a_successful_run_read_are_attributed_to_it(
        repo: Repository, worker: Worker, uploaded_window):
    """`consumed_by_run_id` is provenance, not removal: the row stays part of the
    window so a SECOND run of it materialises the same files rather than silently
    falling back to the local input directory."""
    repo.enqueue("lazada", PERIOD)
    first = worker.serve(once=True)[0]

    rows = repo.uploads_for_window("lazada", PERIOD)
    assert [r["state"] for r in rows] == ["consumed"]
    assert rows[0]["consumed_by_run_id"] == first.run_id

    repo.enqueue("lazada", PERIOD)
    second = worker.serve(once=True)[0]
    assert repo.get_run(second.run_id).status is not RunStatus.HARD_STOP, (
        "a re-run must still find the window in the bucket")
    # Attribution is not rewritten — the interesting fact is which run FIRST read
    # an export, because that is the one whose workbook was invoiced from.
    assert repo.uploads_for_window("lazada", PERIOD)[0]["consumed_by_run_id"] \
        == first.run_id


def test_a_hard_stop_does_not_consume_the_uploads(
        repo: Repository, worker: Worker, make_client, tmp_path):
    """The fix for a hard stop may well be to reject one file and upload the right
    one, so a run that produced nothing must not mark anything consumed."""
    import pandas as pd
    from src import lazada

    # A file with the right shape and the wrong contents: it sanitizes and stores,
    # then hard-stops inside the run.
    export = tmp_path / "1_Broken.xlsx"
    with pd.ExcelWriter(export, engine="openpyxl") as w:
        pd.DataFrame({list(lazada.WEEKLY_MAP)[0]: ["x"]}).to_excel(
            w, sheet_name=lazada.SHEETS["weekly"], index=False)
    client = make_client("recon.user")
    with export.open("rb") as fh:
        assert client.post("/uploads", files={"file": (export.name, fh)},
                           data={"platform": "lazada", "period": "2026-05_broken",
                                 "kind": "weekly"}).status_code == 201

    repo.enqueue("lazada", "2026-05_broken")
    run_id = worker.serve(once=True)[0].run_id
    assert repo.get_run(run_id).status is RunStatus.HARD_STOP

    rows = repo.uploads_for_window("lazada", "2026-05_broken")
    assert [r["state"] for r in rows] == ["stored"]
    assert rows[0]["consumed_by_run_id"] is None


# ---------------------------------------------------------------------------
# The local-disk mode is real, not a fallback for tests
# ---------------------------------------------------------------------------

def test_a_window_with_no_uploads_reads_the_input_root_and_says_so(
        repo: Repository, worker: Worker, service_settings):
    """What keeps every M4 worker test passing verbatim, and what lets a developer
    run a window they copied in by hand.

    The mode is REPORTED rather than left to be inferred: a run whose input came
    from a volume and a run whose input came from a bucket are two different
    provenance claims.
    """
    build_window(service_settings.input_root.parent)
    repo.enqueue("lazada", PERIOD)
    run_id = worker.serve(once=True)[0].run_id

    assert repo.get_run(run_id).status is not RunStatus.HARD_STOP
    lines, _, _ = repo.log_lines(run_id, limit=5000)
    text = "\n".join(l.text for l in lines)
    assert "no uploads recorded" in text
    assert str(service_settings.input_root) in text
    # No roster preview was computed, so the count is NULL rather than 0 — 0 would
    # read as "nothing missing".
    assert repo.get_run(run_id).roster_missing is None


# ---------------------------------------------------------------------------
# Refusals: a window must never quietly run on fewer files
# ---------------------------------------------------------------------------

def test_a_missing_object_stops_the_run_rather_than_under_reporting(
        repo: Repository, worker: Worker, uploaded_window, service_settings):
    """An upload recorded but absent from the store is a hard stop, not a skip.

    Running the window anyway would produce a workbook that looks complete and
    under-invoices one storefront.
    """
    from service import objects as object_lib

    store = object_lib.upload_store(service_settings)
    assert store.delete(uploaded_window[0]["object_key"]) is True

    repo.enqueue("lazada", PERIOD)
    run_id = worker.serve(once=True)[0].run_id
    run = repo.get_run(run_id)
    assert run.status is RunStatus.HARD_STOP
    assert "not in the store" in (run.error or "")


def test_two_uploads_with_one_filename_are_refused(repo: Repository, worker: Worker,
                                                   make_client, tmp_path):
    """One would overwrite the other in the window folder and the run would use the
    wrong bytes — the double-pull class arriving by a new route."""
    import pandas as pd
    from src import lazada

    client = make_client("recon.user")
    mapped = list(lazada.WEEKLY_MAP)[:5]
    for marker in ("a", "b"):
        export = tmp_path / f"1_{STORE}.xlsx"
        with pd.ExcelWriter(export, engine="openpyxl") as w:
            pd.DataFrame({**{c: [f"{c}{marker}"] for c in mapped}}).to_excel(
                w, sheet_name=lazada.SHEETS["weekly"], index=False)
        with export.open("rb") as fh:
            assert client.post("/uploads", files={"file": (export.name, fh)},
                               data={"platform": "lazada", "period": "2026-05_dupe",
                                     "kind": "weekly"}).status_code == 201

    repo.enqueue("lazada", "2026-05_dupe")
    run_id = worker.serve(once=True)[0].run_id
    run = repo.get_run(run_id)
    assert run.status is RunStatus.HARD_STOP
    assert "both" in (run.error or "") and "named" in (run.error or "")


# ---------------------------------------------------------------------------
# The roster preview is the same arithmetic as the control
# ---------------------------------------------------------------------------

def test_the_roster_preview_uses_the_same_set_arithmetic_as_check_stores():
    """A preview that disagreed with the control would be worse than none: an
    operator would see "ready" and get a hard stop."""
    from service.materialize import roster_gap

    settings = {"expected_stores": {"tiktok": ["A", "B", "C"]},
                "stores_optional": {"tiktok": ["C"]}}
    missing, unexpected = roster_gap(settings, "tiktok", {"A", "Z"})
    assert missing == ["B"], "an optional store absent is not missing"
    assert unexpected == ["Z"]

    # No roster configured means no claim, matching check_stores' own early return.
    assert roster_gap({}, "lazada", {"anything"}) == ([], [])


def test_an_alias_resolves_before_the_roster_is_checked():
    """`store_aliases` is itself editable config, and the roster is checked against
    the canonical name — but an unresolved `TODO-HUMAN` placeholder must resolve to
    itself, not to the literal string."""
    from service.materialize import canonical_store

    settings = {"store_aliases": {"shopee": {"kao vn": "Kao", "mystery": "TODO-HUMAN"}}}
    assert canonical_store(settings, "shopee", "kao vn") == "Kao"
    assert canonical_store(settings, "shopee", "mystery") == "mystery"
    assert canonical_store(settings, "shopee", "Untouched") == "Untouched"
