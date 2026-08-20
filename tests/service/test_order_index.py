"""The order index and its backfill — defect 2.12's detection half.

The index answers one question nothing could ask before: *does some OTHER window's
order export hold this order's SKU lines?* `uploads_for_window` is hard-keyed to one
(platform, period) and the stored objects are opaque blobs, so the cross-window
question had no route to an answer — and that is the question July's
4,527,401,608 VND of understatement turned on.

The two properties these tests exist to hold:

* **The digest is CHECKED, never derived.** Indexing reads bytes out of the object
  store, so it must first establish that those bytes are the ones the door accepted.
  Recomputing the expected value from the store would certify the store against
  itself — the [D26](../docs/06-DECISIONS.md#d26) / 2.10 failure — so a NULL digest
  is skipped and a mismatch is refused, neither indexed on trust.
* **Nothing here computes money.** Every column is an identifier. The money math
  stays in `src/`, verified row-by-row against the team's own workbooks; a SQL
  second implementation of it would be the D31 failure.
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
pytest.importorskip("httpx")

from service import objects as object_lib, order_index  # noqa: E402

WINDOW, PLATFORM, KIND = "2026-05_l1", "lazada", "weekly"


@pytest.fixture
def upload_client(make_client):
    return make_client("recon.user")


def _export(tmp_path: Path, name: str, *, day: str = "03-May-2026", rows: int = 2):
    """A Lazada weekly export with real dates and order ids.

    The date spelling comes from the contract (`date_formats.lazada.weekly`), not from
    this file — the indexer parses with that same accessor, so hardcoding a format here
    would assert against a second idea of it (D54).
    """
    import pandas as pd
    from _contract import domain, lazada_headers, lazada_sheet

    fmt = ((domain().get("date_formats") or {}).get("lazada") or {}).get("weekly")
    assert fmt == "%d-%b-%Y", f"contract changed the weekly date format to {fmt!r}"

    headers = lazada_headers("weekly")
    frame = pd.DataFrame({
        **{c: [f"{c}-{name}-{i}" for i in range(rows)] for c in headers},
        "Transaction Date": [day] * rows,
        "Order No.": [f"ORD-{name}-{i}" for i in range(rows)],
        "Recipient": ["a person"] * rows,
    })
    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada_sheet("weekly"), index=False)
    return path


def _upload(upload_client, tmp_path, name, **kw) -> dict:
    path = _export(tmp_path, name, **kw)
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": PLATFORM, "period": WINDOW,
                                     "kind": KIND})
    assert r.status_code == 201, r.text
    return r.json()


def _deindex(pool, upload_id: int) -> None:
    """Put a row back the way an upload from before 2026-08-19 looks."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("delete from upload_order_index where upload_id = %s", (upload_id,))
        cur.execute("update uploads set indexed_at = null where id = %s", (upload_id,))


def test_the_backfill_indexes_an_upload_that_predates_the_door(
        upload_client, tmp_path, repo, pool, service_settings):
    body = _upload(upload_client, tmp_path, "20_TestStore.xlsx")
    _deindex(pool, body["id"])
    assert any(u["id"] == body["id"] for u in repo.uploads_unindexed())

    store = object_lib.upload_store(service_settings)
    out = order_index.backfill(repo, store, service_settings)

    assert out.indexed == 1, out.notes
    assert out.order_rows == 2
    assert out.refused_mismatch == 0
    assert not any(u["id"] == body["id"] for u in repo.uploads_unindexed())


def test_running_the_backfill_twice_does_not_double_anything(
        upload_client, tmp_path, repo, pool, service_settings):
    """Idempotence is a property of `record_order_index`, and the operator has to be
    able to re-run a sweep that was interrupted without wondering."""
    body = _upload(upload_client, tmp_path, "21_TestStore.xlsx")
    store = object_lib.upload_store(service_settings)

    _deindex(pool, body["id"])
    order_index.backfill(repo, store, service_settings)
    _deindex(pool, body["id"])
    order_index.backfill(repo, store, service_settings)

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("select count(*) from upload_order_index where upload_id = %s",
                    (body["id"],))
        assert cur.fetchone()[0] == 2


def test_an_upload_with_no_recorded_digest_is_skipped_and_named(
        upload_client, tmp_path, repo, pool, service_settings):
    """The D26 rule. Nothing can establish that the stored bytes are the uploaded
    bytes, so the file is not indexed and the message says what to do instead."""
    body = _upload(upload_client, tmp_path, "22_TestStore.xlsx")
    _deindex(pool, body["id"])
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("update uploads set object_sha256 = null where id = %s",
                    (body["id"],))

    store = object_lib.upload_store(service_settings)
    out = order_index.backfill(repo, store, service_settings)

    assert out.indexed == 0
    assert out.skipped_no_digest == 1
    joined = " ".join(out.notes)
    assert "re-upload" in joined.lower(), joined
    assert "M8/2.5" in joined
    # Still outstanding: a skip must not look like completed work.
    assert any(u["id"] == body["id"] for u in repo.uploads_unindexed())


def test_bytes_that_do_not_match_the_recorded_digest_are_refused_not_indexed(
        upload_client, tmp_path, repo, pool, service_settings):
    """The same condition that stops a run (2.10), in the indexing path.

    An object store serving different bytes under one key must not quietly become
    the record of where an order's lines live.
    """
    body = _upload(upload_client, tmp_path, "23_TestStore.xlsx")
    _deindex(pool, body["id"])

    store = object_lib.upload_store(service_settings)
    store.put(body["object_key"], b"not the workbook that was uploaded")

    out = order_index.backfill(repo, store, service_settings)

    assert out.indexed == 0
    assert out.refused_mismatch == 1
    joined = " ".join(out.notes)
    assert "REFUSED" in joined
    # Both digests named, mirroring materialize.verify_digest's message: an operator
    # comparing them is how a truncated download is told from a replaced object.
    assert body["object_sha256"][:12] in joined, joined
    assert "2.10" in joined, joined
    assert any(u["id"] == body["id"] for u in repo.uploads_unindexed())


def test_a_dry_run_writes_nothing(upload_client, tmp_path, repo, pool,
                                  service_settings):
    body = _upload(upload_client, tmp_path, "24_TestStore.xlsx")
    _deindex(pool, body["id"])

    store = object_lib.upload_store(service_settings)
    out = order_index.backfill(repo, store, service_settings, dry_run=True)

    assert out.indexed == 1 and out.order_rows == 2
    assert any(u["id"] == body["id"] for u in repo.uploads_unindexed()), (
        "a dry run stamped indexed_at")


def test_the_index_carries_no_money_column(pool):
    """A structural assertion, not a behavioural one.

    The rule this table is built under is that the database may know where every
    number came from and may never compute one. A future migration adding an amount
    here would be the D31 failure, and it should fail a test rather than a review.
    """
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""
            select column_name, data_type from information_schema.columns
            where table_name = 'upload_order_index'
        """)
        columns = {name: kind for name, kind in cur.fetchall()}

    assert set(columns) == {"upload_id", "store", "order_id"}, columns
    assert not [k for k in columns.values()
                if k in ("numeric", "double precision", "real", "money")], columns


def _tiktok(tmp_path: Path, name: str, kind: str, order_ids: list[str], *,
            day: str = "2026/05/03"):
    """A TikTok income or orders export carrying named order ids.

    TikTok because only it and Shopee have an `orders` kind at all — Lazada is a
    fee-event ledger with no order files, which is exactly why defect 2.12 cannot
    happen there. Spellings come from the contract, not from this file.
    """
    import pandas as pd
    from _contract import domain

    from src import config as src_config

    settings = domain()
    colmap = src_config.column_map(settings, "tiktok", kind)
    id_header = next(raw for raw, canon in colmap.items() if canon == "order_id")
    frame = pd.DataFrame({id_header: order_ids})
    if kind == "income":
        date_header = next(raw for raw, canon in colmap.items()
                           if canon == "statement_date")
        frame[date_header] = [day] * len(order_ids)

    sheet = ((settings.get("sheet_names") or {}).get("tiktok") or {})[kind]
    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=sheet, index=False)
    return path


def test_the_cross_window_query_finds_an_order_held_by_an_earlier_window(
        upload_client, tmp_path, repo):
    """The signal with **zero** legitimate traffic, and defect 2.12 in one assertion.

    `w2` settles order `SHARED-1`, but only `w1`'s order export contains it — the
    exact July shape, where the income row matches nothing and the revenue leaves the
    invoice through the documented "~21% unmatched" door. The legitimate members of
    that door's traffic have lines in **no** window, which is what makes this
    distinguishable from them at all.

    `MINE-1` is the control: settled and covered by w2's own export, so it must not
    appear.
    """
    def post(name, kind, period, ids):
        path = _tiktok(tmp_path, name, kind, ids)
        with path.open("rb") as fh:
            r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                   data={"platform": "tiktok", "period": period,
                                         "kind": kind})
        assert r.status_code == 201, r.text
        return r.json()

    # w1's order export holds SHARED-1. w2 settles it, and w2's own export does not.
    post("1. order Purite 7.5.xlsx", "orders", "2026-05_w1", ["SHARED-1", "OLD-1"])
    post("2. order Purite 14.5.xlsx", "orders", "2026-05_w2", ["MINE-1"])
    post("3. income Purite 14.5.xlsx", "income", "2026-05_w2",
         ["SHARED-1", "MINE-1"])

    holders = repo.cross_window_order_holders("tiktok", "2026-05_w2")

    assert len(holders) == 1, holders
    found = holders[0]
    assert found["holder_period"] == "2026-05_w1"
    assert found["filename"] == "1. order Purite 7.5.xlsx"
    assert found["orders"] == 1, "MINE-1 is covered by w2's own export"
    assert found["store"] == "Purite"

    # Predecessor-only: asking w1 the same question finds nothing, so a re-run of an
    # early window cannot change its answer because a later window arrived.
    assert repo.cross_window_order_holders("tiktok", "2026-05_w1") == []


# --- the report surfaces (C12) ----------------------------------------------

def test_the_coverage_endpoint_names_the_holder_window(upload_client, tmp_path):
    """The browser-visible half of defect 2.12's detection.

    Counts only — an operator gets an identity and a file to go and look at, not a
    number that pretends to be money.
    """
    def post(name, kind, period, ids):
        path = _tiktok(tmp_path, name, kind, ids)
        with path.open("rb") as fh:
            r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                   data={"platform": "tiktok", "period": period,
                                         "kind": kind})
        assert r.status_code == 201, r.text

    post("1. order Purite 7.5.xlsx", "orders", "2026-05_w1", ["SHARED-1"])
    post("3. income Purite 14.5.xlsx", "income", "2026-05_w2", ["SHARED-1"])

    body = upload_client.get("/windows/tiktok/2026-05_w2/order-coverage").json()

    assert body["stores"] == [{"store": "Purite", "income_orders": 1,
                               "unmatched_orders": 1}]
    assert body["cross_window"] == [{
        "store": "Purite", "holder_period": "2026-05_w1",
        "filename": "1. order Purite 7.5.xlsx",
        "upload_id": body["cross_window"][0]["upload_id"], "orders": 1}]
    assert body["indexed"] is True

    # No money anywhere in the payload — the boundary this table is built under.
    import json
    assert "amount" not in json.dumps(body).lower()


def test_an_unindexed_window_says_so_rather_than_reporting_all_covered(
        upload_client, tmp_path, pool):
    """The failure mode worth naming: empty results because nothing was indexed look
    exactly like a window with perfect coverage."""
    path = _tiktok(tmp_path, "4. income Purite 14.5.xlsx", "income", ["A-1"])
    with path.open("rb") as fh:
        body = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                  data={"platform": "tiktok",
                                        "period": "2026-05_w3",
                                        "kind": "income"}).json()
    _deindex(pool, body["id"])

    report = upload_client.get("/windows/tiktok/2026-05_w3/order-coverage").json()

    assert report["indexed"] is False
    assert report["unindexed_files"] == ["4. income Purite 14.5.xlsx"]
    assert report["stores"] == [], "an unindexed window has nothing to report"


def test_the_worker_logs_cross_window_orders_before_the_run(repo, tmp_path):
    """Log lines only — the worker adds no compute to the money path (D31).

    Called through the real `_report_order_coverage` with a stub log, because what is
    being asserted is that the two repository queries reach the operator's log at all.
    """
    from src.runlog import RunLog

    class Job:
        platform, period = "tiktok", "2026-05_w2"

    class StubRepo:
        def order_coverage(self, platform, period):
            return [{"store": "Purite", "income_orders": 10, "unmatched_orders": 4}]

        def cross_window_order_holders(self, platform, period):
            return [{"store": "Purite", "holder_period": "2026-05_w1",
                     "filename": "1. order Purite 7.5.xlsx", "upload_id": 7,
                     "orders": 4}]

    from service.worker import Worker

    log = RunLog()
    worker = Worker.__new__(Worker)
    worker.repo = StubRepo()
    worker._report_order_coverage(Job(), log)

    text = "\n".join(log.lines)
    assert "order coverage Purite: 4 of 10" in text
    assert "CROSS-WINDOW ORDERS Purite" in text
    assert "2026-05_w1" in text and "upload 7" in text
    # The expected class is a plain line; the zero-legitimate-traffic class warns.
    assert len(log.warnings) == 1, log.warnings


def test_an_m4_repository_makes_the_report_silent_not_broken(tmp_path):
    """A repository without the index is a real deployment, not a bug — and a
    settlement run must not fail because a report cannot be produced."""
    from src.runlog import RunLog

    from service.worker import Worker

    class Job:
        platform, period = "tiktok", "2026-05_w2"

    log = RunLog()
    worker = Worker.__new__(Worker)
    worker.repo = object()                      # no order_coverage at all
    worker._report_order_coverage(Job(), log)

    assert log.lines == [] and log.warnings == []


def test_a_failing_query_is_reported_and_never_fatal(tmp_path):
    from src.runlog import RunLog

    from service.worker import Worker

    class Job:
        platform, period = "tiktok", "2026-05_w2"

    class Broken:
        def order_coverage(self, platform, period):
            raise RuntimeError("relation does not exist")

        def cross_window_order_holders(self, platform, period):
            return []

    log = RunLog()
    worker = Worker.__new__(Worker)
    worker.repo = Broken()
    worker._report_order_coverage(Job(), log)      # must not raise

    assert any("order coverage not reported" in w for w in log.warnings), log.warnings
