"""The service can be seen (Phase 6 / C5 + C6's second half).

Three surfaces: `/healthz` answers "does a worker exist" (the question 'queued
with no worker' used to make unanswerable), `/metrics` serves counts an alert
rule can match on, and `service/obs.py` emits one JSON object per line so a log
collector never has to parse prose.
"""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("psycopg")


# ---------------------------------------------------------------------------
# Worker liveness — C6's second half
# ---------------------------------------------------------------------------

def _one_beat(repo, store, service_settings) -> None:
    from service.worker import Worker
    Worker(repo, store, service_settings).serve(once=True)


def test_a_worker_loop_turn_is_visible_to_healthz(
        make_client, repo, store, service_settings):
    client = make_client(role=None)
    before = client.get("/healthz").json()
    assert before["workers_alive"] == 0, "no worker has ever run in this database"

    _one_beat(repo, store, service_settings)

    after = client.get("/healthz").json()
    assert after["workers_alive"] == 1
    assert after["workers_known"] == 1
    assert after["worker_last_seen_seconds"] <= 60


def test_a_worker_that_stopped_beating_reads_as_absent_not_recent(
        make_client, repo, store, service_settings):
    """The exact confusion C6 names: `queued: 3, workers_alive: 0` must be a
    readable state, distinct from a worker that beat a second ago."""
    _one_beat(repo, store, service_settings)
    with repo._conn() as conn, conn.cursor() as cur:            # noqa: SLF001
        cur.execute("update worker_heartbeats set last_seen = now() - interval '5 minutes'")

    health = make_client(role=None).get("/healthz").json()
    assert health["workers_alive"] == 0
    assert health["workers_known"] == 1, (
        "the stale row stays: when it was last seen IS the diagnostic fact")
    assert health["worker_last_seen_seconds"] >= 290


def test_a_lease_extension_beats_too(repo):
    """A worker deep inside a 269-second run never touches its idle-loop beat;
    the lease extension (every log flush) must keep it alive in /healthz."""
    job, _ = repo.enqueue("lazada", "2026-05_l1")
    claimed = repo.claim("mid-run-worker", 60)
    assert claimed is not None
    repo.heartbeat(claimed.id, "mid-run-worker", 60)

    assert repo.healthcheck()["workers_alive"] == 1


# ---------------------------------------------------------------------------
# /metrics — C5
# ---------------------------------------------------------------------------

def test_metrics_reports_counts_and_never_client_data(make_client, repo):
    repo.enqueue("lazada", "2026-05_l1")
    repo.enqueue("tiktok", "2026-05_w1")
    repo.claim("w1", 60)

    body = make_client("recon.viewer").get("/metrics").json()

    assert body["jobs"] == {"queued": 1, "leased": 1}
    assert body["workers"]["known"] == 0
    assert body["oldest_queued_seconds"] is not None
    # The content rule: identifiers, counts and ages only. A store name in the
    # service's own telemetry would put client identity on a surface (container
    # stdout, scrape responses) the run log never reaches.
    text = json.dumps(body)
    for forbidden in ("Unilever", "Masan", "KAO", "VND"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# The JSON line formatter — C5
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _detach_obs_handler():
    """The obs handler binds the stream capsys installed for THIS test; left
    attached, every later INFO record would write into a dead buffer."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_recon_obs", False):
            root.removeHandler(handler)


def test_obs_emits_one_parseable_json_object_per_line(capsys):
    from service import obs

    logger = obs.setup_logging("test-component")
    obs.event(logger, "job_finished", job_id=3, status="ok", wall_s=171.2)

    line = capsys.readouterr().out.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["message"] == "job_finished"
    assert parsed["component"] == "test-component"
    assert parsed["job_id"] == 3 and parsed["wall_s"] == 171.2
    assert parsed["level"] == "INFO"


def test_obs_setup_is_idempotent(capsys):
    """Calling setup twice (api tests build several apps per process) must not
    double every line."""
    from service import obs

    logger = obs.setup_logging("twice")
    obs.setup_logging("twice")
    obs.event(logger, "only_once")

    lines = [l for l in capsys.readouterr().out.splitlines() if "only_once" in l]
    assert len(lines) == 1
