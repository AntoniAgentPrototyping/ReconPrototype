"""The HTTP surface, against a real Postgres and a real artifact store.

No mock repository. A fake would have to reimplement the enqueue guards to be
useful, and then the tests would prove the fake enforces them — which is the
opposite of what is worth knowing here.

Since M5 every request here carries a **real bearer token**, checked against a
hashed row in the database. So these tests also assert, incidentally, that each
endpoint is reachable *with* credentials; `test_auth.py` covers what happens
without them.
"""

from __future__ import annotations

import pytest

from service.models import JobState
from src.pipeline import RunStatus

pytest.importorskip("httpx")


@pytest.fixture
def client(make_client):
    with make_client("recon.admin") as c:
        yield c


# ---------------------------------------------------------------------------
# Health and metadata
# ---------------------------------------------------------------------------

def test_healthz_reports_the_database(client):
    """An api that answers 200 while the queue is unreachable is worse than one
    that is down."""
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["migrations"] >= 1
    assert body["queued"] == 0


def test_meta_publishes_one_definition_of_the_exit_codes(client):
    """The web app must not carry its own copy of what 2 means."""
    body = client.get("/meta").json()
    assert body["exit_codes"] == {"ok": 0, "variance": 1, "unverified": 2, "hard_stop": 3}
    assert set(body["platforms"]) == {"tiktok", "shopee", "lazada"}


# ---------------------------------------------------------------------------
# Enqueue
# ---------------------------------------------------------------------------

def test_enqueue_returns_the_queued_job(client):
    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"})
    assert r.status_code == 201
    job = r.json()
    assert (job["platform"], job["period"], job["state"]) == ("lazada", "2026-05_l1", "queued")
    assert job["run"] is None, "no run until a worker picks it up"


def test_requested_by_comes_from_the_token_not_the_body(client):
    """Changed in M5, and the point of the milestone.

    Through M4 the caller supplied this field, which made "who asked for this
    settlement run" a claim rather than evidence — the first thing an auditor
    would ask about, and unanswerable. It is now the authenticated subject, and
    a body that tries to say otherwise is ignored rather than honoured.
    """
    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1",
                                   "requested_by": "someone-else"})
    assert r.json()["requested_by"] == "admin@test"


def test_refs_travel_in_the_body_not_as_a_path(client):
    """A worker in a container cannot read the operator's disk, so the team's
    reference totals arrive inline — the same shape --refs takes."""
    refs = {"per_store": {"kao": {"pre_vat": 1000.0}}, "grand": {"pre_vat": 1000.0}}
    r = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1",
                                   "refs": refs})
    assert r.json()["refs"] == refs


def test_an_unknown_platform_is_rejected_before_it_reaches_the_queue(client):
    assert client.post("/jobs", json={"platform": "amazon", "period": "x"}).status_code == 422


def test_a_path_shaped_period_is_rejected(client):
    """The period becomes a directory name on the worker. Nothing here should have
    to think about `..`."""
    for bad in ("../../etc", "2026-05_l1/../l2", "a b"):
        r = client.post("/jobs", json={"platform": "lazada", "period": bad})
        assert r.status_code == 422, bad


def test_a_second_live_job_for_one_window_answers_409_with_the_existing_one(client):
    first = client.post("/jobs", json={"platform": "tiktok", "period": "2026-05_w1"}).json()

    r = client.post("/jobs", json={"platform": "tiktok", "period": "2026-05_w1"})
    assert r.status_code == 409
    body = r.json()
    assert body["existing"]["id"] == first["id"], (
        "the existing job travels in the body so a UI can link to the run in flight "
        "rather than telling the operator to go and look for it")


def test_a_retried_post_with_an_idempotency_key_is_not_a_second_run(client):
    payload = {"platform": "lazada", "period": "2026-05_l1", "idempotency_key": "k1"}
    first = client.post("/jobs", json=payload)
    again = client.post("/jobs", json=payload)

    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert client.get("/jobs").json()["count"] == 1


def test_max_attempts_is_bounded(client):
    """Raising it means "retry this settlement run automatically if the worker
    dies", so it is a small deliberate range rather than an open integer."""
    ok = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1",
                                    "max_attempts": 3})
    assert ok.status_code == 201
    bad = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l2",
                                     "max_attempts": 99})
    assert bad.status_code == 422


def test_a_run_cannot_relax_the_roster_by_asking(client):
    """The per-run `partial_roster` toggle is gone (M6, workstream C).

    A subset run must still be recorded as one — that requirement did not change
    ([D23](docs/06-DECISIONS.md#d23)) — but the statement is now made once per
    WINDOW, with a reason and an author, instead of per run by a checkbox. So
    sending the old field must not silently relax anything: pydantic ignores the
    unknown key and the job is an ordinary full-roster job.
    """
    r = client.post("/jobs", json={"platform": "tiktok", "period": "2026-05_w1",
                                  "partial_roster": True})
    assert r.status_code == 201
    assert r.json()["partial_roster"] is False, (
        "a request body must not be able to relax the store-count hard stop — "
        "that is what POST /windows/roster is for, and it demands a reason")


def test_a_partial_window_is_declared_once_with_a_reason(client):
    """The replacement control, and the reason it is better than a checkbox."""
    declared = client.post("/windows/roster", json={
        "platform": "shopee", "period": "2026-05_s1", "partial": True,
        "reason": "only Masan and Xmenforboss settled in this sub-window"})
    assert declared.status_code == 201
    body = declared.json()
    assert body["roster_declared_partial"] is True
    # From the session, never the body — the same rule as `requested_by`.
    assert body["declared_by"] == "admin@test"

    fetched = client.get("/windows/shopee/2026-05_s1").json()
    assert fetched["roster_declaration"]["reason"].startswith("only Masan")

    # Re-declaring replaces rather than accumulating: one statement per window.
    again = client.post("/windows/roster", json={
        "platform": "shopee", "period": "2026-05_s1", "partial": False})
    assert again.status_code == 201
    assert again.json()["roster_declared_partial"] is False

    assert client.delete("/windows/shopee/2026-05_s1/roster").status_code == 200
    assert client.get("/windows/shopee/2026-05_s1").json()["roster_declaration"] is None
    assert client.delete("/windows/shopee/2026-05_s1/roster").status_code == 404


def test_a_partial_declaration_without_a_reason_is_refused(client):
    """The reason is the entire difference between this and the checkbox it
    replaced, so an empty one is a 422 rather than a null column."""
    for reason in (None, "", "   ", "typo"):
        r = client.post("/windows/roster", json={
            "platform": "tiktok", "period": "2026-05_w1", "partial": True,
            "reason": reason})
        assert r.status_code == 422, f"reason={reason!r} was accepted"
    # A COMPLETE window needs no explanation — there is nothing to explain.
    assert client.post("/windows/roster", json={
        "platform": "tiktok", "period": "2026-05_w1", "partial": False,
    }).status_code == 201


# ---------------------------------------------------------------------------
# Listing, fetching, cancelling
# ---------------------------------------------------------------------------

def test_jobs_are_listed_newest_first_and_filterable(client, repo):
    client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"})
    client.post("/jobs", json={"platform": "tiktok", "period": "2026-05_w1"})

    body = client.get("/jobs").json()
    assert [j["platform"] for j in body["jobs"]] == ["tiktok", "lazada"]
    assert client.get("/jobs", params={"platform": "lazada"}).json()["count"] == 1
    assert client.get("/jobs", params={"state": "queued"}).json()["count"] == 2
    assert client.get("/jobs", params={"state": "done"}).json()["count"] == 0


def test_an_unknown_state_filter_is_a_422_not_an_empty_list(client):
    """An empty list would read as "nothing matched" and hide the typo."""
    assert client.get("/jobs", params={"state": "finished"}).status_code == 422


def test_an_unknown_job_is_404(client):
    assert client.get("/jobs/9999").status_code == 404


def test_cancel_a_queued_job(client):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    assert client.post(f"/jobs/{job['id']}/cancel").json()["state"] == "cancelled"


def test_cancelling_a_leased_job_is_409(client, repo):
    """`pipeline.run()` has no cancellation point, and killing it partway through
    build_workbook leaves a truncated .xlsx that looks like a deliverable."""
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    r = client.post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 409 and "not queued" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Runs and the log
# ---------------------------------------------------------------------------

def test_a_job_carries_its_run_once_one_starts(client, repo):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")

    body = client.get(f"/jobs/{job['id']}").json()
    assert body["run"]["id"] == run_id
    assert body["run"]["in_flight"] is True
    assert body["run"]["status"] is None


def test_a_finished_run_exposes_status_findings_and_metrics(client, repo):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")
    repo.finish_run(run_id, status=RunStatus.VARIANCE,
                    findings=[("unverified", "KAO: no team reference found"),
                              ("variance", "KAO pre_vat: +1,234")],
                    metrics={"wall_s": 0.5, "io_s": 0.4, "compute_s": 0.0,
                             "serialize_s": 0.1, "peak_rss_mb": 210.0})
    repo.finish_job(job["id"], JobState.DONE)

    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "variance" and run["exit_code"] == 1
    assert run["in_flight"] is False
    # The two views the log prints under separate headings, so a client does not
    # have to know that findings is one ordered list of pairs.
    assert run["variances"] == ["KAO pre_vat: +1,234"]
    assert run["unverified"] == ["KAO: no team reference found"]
    assert [f[0] for f in run["findings"]] == ["unverified", "variance"], "order kept"
    assert run["peak_rss_mb"] == 210.0


def test_the_log_polls_by_seq(client, repo):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")
    repo.append_log(run_id, [(i, "line", f"line {i}") for i in range(5)])

    first = client.get(f"/runs/{run_id}/log", params={"limit": 3}).json()
    assert [l["seq"] for l in first["lines"]] == [0, 1, 2]
    assert first["next_seq"] == 2 and first["complete"] is False

    rest = client.get(f"/runs/{run_id}/log",
                      params={"after_seq": first["next_seq"]}).json()
    assert [l["text"] for l in rest["lines"]] == ["line 3", "line 4"]
    assert rest["complete"] is False, "the run has not finished"

    repo.finish_run(run_id, status=RunStatus.OK, findings=[])
    done = client.get(f"/runs/{run_id}/log", params={"after_seq": 4}).json()
    assert done["lines"] == [] and done["complete"] is True


def test_complete_stays_false_while_a_page_is_full(client, repo):
    """A full page means there may be more, even on a finished run — otherwise a
    client stops polling with lines still unread."""
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")
    repo.append_log(run_id, [(i, "line", f"line {i}") for i in range(10)])
    repo.finish_run(run_id, status=RunStatus.OK, findings=[])

    page = client.get(f"/runs/{run_id}/log", params={"limit": 10}).json()
    assert len(page["lines"]) == 10 and page["complete"] is False
    tail = client.get(f"/runs/{run_id}/log",
                      params={"after_seq": page["next_seq"], "limit": 10}).json()
    assert tail["lines"] == [] and tail["complete"] is True


def test_log_kinds_survive_the_round_trip(client, repo):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")
    repo.append_log(run_id, [(0, "section", "TIE-OUT"), (1, "warning", "WARNING: x"),
                             (2, "line", "  ok")])

    kinds = [l["kind"] for l in client.get(f"/runs/{run_id}/log").json()["lines"]]
    assert kinds == ["section", "warning", "line"]


def test_the_log_of_an_unknown_run_is_404(client):
    assert client.get("/runs/4242/log").status_code == 404


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------

def test_an_artifact_is_listed_and_downloadable(client, repo, store, tmp_path):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")

    src = tmp_path / "scratch" / "finance_file.xlsx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"workbook bytes")
    art = store.put(period="2026-05_l1", platform="lazada", run_id=run_id, path=src)
    repo.record_artifact(run_id, name=art.name, uri=art.uri, bytes_=art.bytes,
                         sha256=art.sha256)

    listed = client.get(f"/runs/{run_id}/artifacts").json()["artifacts"]
    assert [a["name"] for a in listed] == ["finance_file.xlsx"]
    assert listed[0]["bytes_sha256"] == art.sha256

    got = client.get(f"/runs/{run_id}/artifacts/finance_file.xlsx")
    assert got.status_code == 200 and got.content == b"workbook bytes"


def test_an_unknown_artifact_is_404(client, repo):
    job = client.post("/jobs", json={"platform": "lazada", "period": "2026-05_l1"}).json()
    repo.claim("w1", 60)
    run_id = repo.start_run(job["id"], "lazada", "2026-05_l1")
    assert client.get(f"/runs/{run_id}/artifacts/nope.xlsx").status_code == 404
