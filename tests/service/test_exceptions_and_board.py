"""The exception queue, the month board, and config pinning.

Three M5 surfaces that only mean anything once a real run has produced real
rows, so most of this drives the worker over the synthetic Lazada window rather
than inserting fixtures.

The claim worth stating up front: **a capped queue must never read as a complete
one.** TikTok's unmatched-settlement class alone is ~11,765 orders; storing a
bounded slice is reasonable, implying it is everything is not. Hence
`total_rows` alongside `stored_rows` everywhere, and a test for it.
"""

from __future__ import annotations

import pytest

from service import exceptions as exc_module
from service.models import JobState
from service.worker import Worker
from src.pipeline import RunStatus

pytest.importorskip("pandas")
pytest.importorskip("httpx")

from tools.smoke_test import PERIOD, build_window  # noqa: E402


@pytest.fixture
def window(service_settings):
    build_window(service_settings.input_root.parent)
    return PERIOD


@pytest.fixture
def worker(repo, store, service_settings):
    return Worker(repo, store, service_settings)


# ---------------------------------------------------------------------------
# Fingerprints — the identity M6 will hang dispositions off
# ---------------------------------------------------------------------------

def test_a_fingerprint_is_stable_across_runs():
    """The same unmatched order next week must hash the same, or "has this
    recurred?" is unanswerable and M6's dispositions cannot survive a re-run."""
    row = {"store": "KAO", "order_id": "SMOKE-0001", "net_revenue": 1_000_000.0}
    later = {"store": "KAO", "order_id": "SMOKE-0001", "net_revenue": 999_999.0}
    assert exc_module.fingerprint("unmatched_orders", row) \
        == exc_module.fingerprint("unmatched_orders", later), (
        "identity columns only — a fingerprint that moves with an amount is not "
        "an identity")


def test_different_rows_get_different_fingerprints():
    a = {"store": "KAO", "order_id": "A"}
    b = {"store": "KAO", "order_id": "B"}
    c = {"store": "Other", "order_id": "A"}
    assert len({exc_module.fingerprint("unmatched_orders", r) for r in (a, b, c)}) == 3


def test_sheets_cannot_collide_on_a_shared_key():
    """`store` appears in nearly every sheet, so the sheet name is inside the
    hash."""
    row = {"store": "KAO", "order_id": "A", "fee_name": "A"}
    assert exc_module.fingerprint("unmatched_orders", row) \
        != exc_module.fingerprint("unmapped_fees", row)


def test_dtype_drift_does_not_change_identity():
    """pandas hands back 1.0 where the export held 1. A fingerprint that changes
    with a dtype is not an identity."""
    assert exc_module.fingerprint("unmatched_orders", {"store": "K", "order_id": 1}) \
        == exc_module.fingerprint("unmatched_orders", {"store": "K", "order_id": 1.0})


def test_a_row_with_no_identity_columns_falls_back_and_says_so():
    row = {"something": "else", "amount": 5}
    assert exc_module.uses_fallback("unmatched_orders", row)
    assert exc_module.fingerprint("unmatched_orders", row)
    assert exc_module.summarize([row], "unmatched_orders")["weak_identity"] == 1


def test_frame_rows_caps_and_reports_the_true_total():
    import pandas as pd
    frame = pd.DataFrame({"store": ["s"] * 120, "order_id": range(120)})
    rows, total = exc_module.frame_rows(frame, cap=50)
    assert len(rows) == 50 and total == 120


def test_frame_rows_handles_an_absent_frame():
    assert exc_module.frame_rows(None, cap=10) == ([], 0)


# ---------------------------------------------------------------------------
# The queue, from a real run
# ---------------------------------------------------------------------------

def test_exceptions_from_a_run_are_queryable(repo, worker, window, make_client):
    """The synthetic window maps every fee, so it produces no exception rows —
    which is itself worth asserting: an empty queue must be empty, not absent."""
    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]
    assert outcome.status is RunStatus.UNVERIFIED

    body = make_client("recon.viewer").get(f"/runs/{outcome.run_id}/exceptions").json()
    assert body["exceptions"] == []
    assert body["sheets"] == []


def test_unmapped_fees_reach_the_queue(repo, worker, service_settings, make_client):
    """Give the window a fee name the master does not know, which is the real
    recurring exception class on Lazada."""
    import pandas as pd
    from src import lazada

    folder = service_settings.input_root / "2026-05_exc" / "lazada" / "Weekly"
    folder.mkdir(parents=True)
    rows = [{
        "Transaction Date": "2026-05-02", "Fee Name": "A Fee Nobody Mapped",
        "Details": "x", "Seller SKU": "SKU-A", "Lazada SKU": "LZD-A",
        "Amount": "100000", "VAT in Amount": "8000",
        "Order No.": "EXC-1", "Order Item No.": "EXC-1-A", "Paid Quantity": "1",
    }]
    with pd.ExcelWriter(folder / "1_ExcStore.xlsx", engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, sheet_name=lazada.SHEETS["weekly"], index=False)

    repo.enqueue("lazada", "2026-05_exc")
    outcome = Worker(repo, __import__("service.artifacts", fromlist=["x"]).LocalArtifactStore(
        service_settings.artifact_root), service_settings).serve(once=True)[0]

    body = make_client("recon.viewer").get(f"/runs/{outcome.run_id}/exceptions").json()
    sheets = {s["sheet"]: s for s in body["sheets"]}
    assert "unmapped_fees" in sheets
    assert sheets["unmapped_fees"]["total_rows"] >= 1
    assert sheets["unmapped_fees"]["truncated"] is False

    row = body["exceptions"][0]
    assert row["sheet"] == "unmapped_fees"
    assert row["fingerprint"]
    assert "A Fee Nobody Mapped" in str(row["payload"])


def test_a_capped_sheet_reports_the_true_total(repo, service_settings):
    """A queue that shows 50 of 11,765 and says so is useful; one that shows 50
    and implies completeness is a lie with a UI on it."""
    import pandas as pd
    job, _ = repo.enqueue("lazada", "2026-05_cap")
    run_id = repo.start_run(job.id, "lazada", "2026-05_cap")

    frame = pd.DataFrame({"store": ["s"] * 200, "order_id": range(200)})
    rows, total = exc_module.frame_rows(frame, cap=50)
    repo.record_exceptions(run_id, "unmatched_orders", rows, total=total,
                           fingerprint_of=lambda r: exc_module.fingerprint(
                               "unmatched_orders", r))

    sheets = repo.exception_sheets(run_id)
    assert sheets == [{"sheet": "unmatched_orders", "total_rows": 200,
                       "stored_rows": 50, "truncated": True}]


def test_exception_history_spans_runs(repo):
    """An unmatched order that recurs for six weeks is a different thing from one
    that appeared once, and no per-run view can tell them apart."""
    fingerprint = exc_module.fingerprint("unmatched_orders",
                                         {"store": "KAO", "order_id": "RECUR-1"})
    for period in ("2026-05_l1", "2026-05_l2"):
        job, _ = repo.enqueue("lazada", period)
        run_id = repo.start_run(job.id, "lazada", period)
        repo.record_exceptions(run_id, "unmatched_orders",
                               [{"store": "KAO", "order_id": "RECUR-1"}], total=1,
                               fingerprint_of=lambda r: fingerprint)
        repo.finish_job(job.id, JobState.DONE)

    history = repo.exception_history(fingerprint)
    assert len(history) == 2
    assert {h["period"] for h in history} == {"2026-05_l1", "2026-05_l2"}


def test_the_history_endpoint_is_reachable(repo, make_client):
    fingerprint = exc_module.fingerprint("unmapped_fees", {"store": "s", "fee_name": "f"})
    body = make_client("recon.viewer").get(f"/exceptions/{fingerprint}/history").json()
    assert body["fingerprint"] == fingerprint and body["runs"] == []


def test_the_run_endpoint_carries_the_sheet_summary(repo, worker, window, make_client):
    repo.enqueue("lazada", window)
    run_id = worker.serve(once=True)[0].run_id
    assert "exception_sheets" in make_client("recon.viewer").get(f"/runs/{run_id}").json()


# ---------------------------------------------------------------------------
# The month board
# ---------------------------------------------------------------------------

def test_the_board_is_empty_before_anything_runs(make_client):
    assert make_client("recon.viewer").get("/board").json()["windows"] == []


def test_the_board_shows_one_row_per_window_with_its_verdict(
        repo, worker, window, make_client):
    repo.enqueue("lazada", window)
    worker.serve(once=True)
    repo.enqueue("lazada", "2026-05_never_staged")
    worker.serve(once=True)

    rows = {r["period"]: r for r in make_client("recon.viewer").get("/board").json()["windows"]}
    assert set(rows) == {window, "2026-05_never_staged"}

    good = rows[window]
    assert good["job_state"] == "done" and good["status"] == "unverified"
    assert good["exit_code"] == 2 and good["wall_s"] > 0
    assert good["job_count"] == 1

    # A hard stop is a job that executed and a run that concluded nothing was
    # produced — the two axes the board has to keep apart.
    bad = rows["2026-05_never_staged"]
    assert bad["job_state"] == "done" and bad["status"] == "hard_stop"


def test_the_board_shows_the_latest_run_and_counts_the_rest(repo, worker, window, make_client):
    repo.enqueue("lazada", window)
    first = worker.serve(once=True)[0]
    repo.enqueue("lazada", window)
    second = worker.serve(once=True)[0]

    row = make_client("recon.viewer").get("/board").json()["windows"][0]
    assert row["run_id"] == second.run_id != first.run_id
    assert row["job_count"] == 2, "a window run four times is telling you something"


def test_the_board_filters_by_month(repo, make_client):
    repo.enqueue("lazada", "2026-05_l1")
    repo.enqueue("lazada", "2026-06_l1")
    client = make_client("recon.viewer")
    assert len(client.get("/board", params={"month": "2026-05"}).json()["windows"]) == 1
    assert len(client.get("/board").json()["windows"]) == 2


def test_a_queued_window_appears_before_it_runs(repo, make_client):
    repo.enqueue("tiktok", "2026-05_w1")
    row = make_client("recon.viewer").get("/board").json()["windows"][0]
    assert row["job_state"] == "queued" and row["run_id"] is None and row["status"] is None


# ---------------------------------------------------------------------------
# Config pinning — defect 2.5
# ---------------------------------------------------------------------------

def test_the_first_run_pins_the_window_to_its_config(repo, worker, window):
    """The fix for "changing a rate in August must not change a re-run of May".

    Pinning happens on the first run that produces a workbook, so an ordinary
    first run behaves exactly as it did before M5 and only a re-run is protected.
    """
    assert repo.pinned_config("lazada", window) is None

    repo.enqueue("lazada", window)
    outcome = worker.serve(once=True)[0]

    pinned = repo.pinned_config("lazada", window)
    assert pinned is not None
    run = repo.get_run(outcome.run_id)
    assert run.config_version_id == pinned["id"]


def test_a_re_run_uses_the_pinned_config_not_todays(repo, worker, window, service_settings):
    """Change the config on disk between two runs of one window and the second
    run must ignore the change."""
    repo.enqueue("lazada", window)
    first = worker.serve(once=True)[0]
    pinned_id = repo.pinned_config("lazada", window)["id"]

    from dataclasses import replace
    import shutil
    from service import config_store

    sandbox = service_settings.scratch_root / "config"
    sandbox.mkdir(parents=True, exist_ok=True)
    for item in service_settings.config_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, sandbox / item.name)
    text = config_store.read_text(sandbox)
    config_store.settings_path(sandbox).write_text(
        config_store.apply_edit(text, ["vat_factors", "default"], 1.10), encoding="utf-8")

    moved = Worker(repo, worker.store, replace(service_settings, config_dir=sandbox))
    repo.enqueue("lazada", window)
    second = moved.serve(once=True)[0]

    run = repo.get_run(second.run_id)
    assert run.config_version_id == pinned_id, "the re-run used today's config"
    assert run.config_was_pinned is True

    lines, _, _ = repo.log_lines(second.run_id, limit=5000)
    assert any("config PINNED" in l.text for l in lines), (
        "a run under frozen rules must say so in its audit trail")
    assert repo.get_run(first.run_id).config_version_id == pinned_id


def test_a_hard_stop_pins_nothing(repo, worker):
    """A run that produced no workbook should not freeze the rules — the fix for
    it may well be a config change."""
    repo.enqueue("lazada", "2026-05_never_staged")
    worker.serve(once=True)
    assert repo.pinned_config("lazada", "2026-05_never_staged") is None


def test_pins_are_visible_and_removable_over_http(repo, worker, window, make_client):
    repo.enqueue("lazada", window)
    worker.serve(once=True)

    admin = make_client("recon.admin")
    pins = admin.get("/config/pins").json()["pins"]
    assert [(p["platform"], p["period"]) for p in pins] == [("lazada", window)]

    assert admin.delete(f"/config/pins/lazada/{window}").status_code == 200
    assert admin.get("/config/pins").json()["pins"] == []
    assert admin.delete(f"/config/pins/lazada/{window}").status_code == 404


def test_only_an_admin_may_unpin(repo, worker, window, make_client):
    repo.enqueue("lazada", window)
    worker.serve(once=True)
    assert make_client("recon.user").delete(
        f"/config/pins/lazada/{window}").status_code == 403


def test_pinning_an_unknown_version_is_404(make_client):
    r = make_client("recon.admin").post("/config/pins", json={
        "platform": "lazada", "period": "2026-05_l1", "config_version_id": 9999})
    assert r.status_code == 404

# ---------------------------------------------------------------------------
# NFC (M6, workstream F)
# ---------------------------------------------------------------------------

def test_the_same_store_in_two_unicode_forms_is_one_exception():
    """A fingerprint that changes with a Unicode form is not an identity.

    `store` and `fee_name` are Vietnamese values, and NFD is byte-unequal to the
    visually identical NFC. Before M6 an export arriving decomposed hashed
    differently and silently orphaned every stored fingerprint — the same bug
    `ingest.py:211` fixes for headers.
    """
    import unicodedata

    from service import exceptions as exc_module

    nfc = "Unilever Chăm Sóc Vẻ Đẹp"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfd != nfc, "the fixture is not actually testing two forms"

    assert exc_module.fingerprint("unmatched_orders", {"store": nfc, "order_id": "X1"}) \
        == exc_module.fingerprint("unmatched_orders", {"store": nfd, "order_id": "X1"})
    assert exc_module.fingerprint("unmapped_fees", {"store": nfc, "fee_name": nfd}) \
        == exc_module.fingerprint("unmapped_fees", {"store": nfd, "fee_name": nfc})


def test_normalising_did_not_move_todays_fingerprints():
    """Measured, not assumed (`service/nfc_audit.py`, 2026-08-17): 0 non-NFC identity
    values anywhere — 0 of 118 live .xlsb fee names, 0 of 118 in the CSV snapshot, 0
    store names in settings.yaml. So an already-NFC value must hash to what it hashed
    before, which is what makes `006_exception_nfc.sql` a recorded no-op rather than a
    migration that quietly needed to do work."""
    import unicodedata

    from service import exceptions as exc_module

    for value in ("Curel", "KAO", "Unilever 2", "Item Price Credit"):
        assert unicodedata.is_normalized("NFC", value)
        assert exc_module._norm(value) == value


def test_normalisation_still_leaves_the_float_rule_alone():
    """Both rules exist for one reason — an identity must not change with a
    representation — and adding the second must not disturb the first."""
    from service import exceptions as exc_module

    assert exc_module._norm(1.0) == "1"
    assert exc_module._norm(float("nan")) == ""
    assert exc_module._norm(None) == ""
    assert exc_module._norm("  padded  ") == "padded"
