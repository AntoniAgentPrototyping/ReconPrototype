"""The worker assembles its own input, and the two modes that must not diverge.

The end of the manual-staging error class: an operator uploads in a browser and
the worker builds `input/<period>/<platform>/<folder>/` itself, in scratch, right
before `build_context`. `run()` still writes nothing — the download is in
`service/`, which is what keeps `tests/test_io_boundary.py` needing no new grant.

Uses the same synthetic Lazada window `tools/smoke_test.py` generates, for the
same reason it does: this must run on a machine with no client data.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from service.repository import Repository
from service.worker import Worker
from src.pipeline import RunStatus

pytest.importorskip("pandas")
pytest.importorskip("httpx")

from tools.smoke_test import PERIOD, STORE, build_window          # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


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
    from _contract import lazada_headers, lazada_sheet

    # A file with the right shape and the wrong contents: it sanitizes and stores,
    # then hard-stops inside the run.
    export = tmp_path / "1_Broken.xlsx"
    with pd.ExcelWriter(export, engine="openpyxl") as w:
        pd.DataFrame({lazada_headers("weekly")[0]: ["x"]}).to_excel(
            w, sheet_name=lazada_sheet("weekly"), index=False)
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
    from _contract import lazada_headers, lazada_sheet

    client = make_client("recon.user")
    mapped = lazada_headers("weekly")[:5]
    for marker in ("a", "b"):
        export = tmp_path / f"1_{STORE}.xlsx"
        with pd.ExcelWriter(export, engine="openpyxl") as w:
            pd.DataFrame({**{c: [f"{c}{marker}"] for c in mapped}}).to_excel(
                w, sheet_name=lazada_sheet("weekly"), index=False)
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


def test_object_bytes_that_do_not_match_the_recorded_digest_stop_the_run(
        repo: Repository, worker: Worker, uploaded_window, service_settings):
    """The bytes the pipeline reads must be the bytes that were checked at the door.

    Until M8/2.5 they were merely assumed to be: a digest was recorded at upload,
    carried through the database and copied onto `MaterializedFile` — and never
    compared against what landed in scratch (defect 2.10). Every claim the upload
    boundary makes, PII stripping included, is a claim about a file this run might
    not have been reading.

    Replacing an object's bytes under its key, leaving the recorded digest alone, is
    the smallest honest simulation of that: a truncated download and a mixed-up key
    both arrive here looking exactly like this.

    The digest compared is `object_sha256`, NOT `sha256` — the latter digests the
    original upload while the store holds the sanitized rewrite. Writing this check
    against `sha256` failed every healthy window, which is what
    `010_object_digest.sql` records.
    """
    from service import objects as object_lib

    store = object_lib.upload_store(service_settings)
    key = uploaded_window[0]["object_key"]
    store.put(key, b"different bytes entirely")

    repo.enqueue("lazada", PERIOD)
    run_id = worker.serve(once=True)[0].run_id
    run = repo.get_run(run_id)
    assert run.status is RunStatus.HARD_STOP
    assert "does NOT match what was stored" in (run.error or "")


def test_an_intact_window_passes_the_digest_check(
        repo: Repository, worker: Worker, uploaded_window):
    """The other half: the check must not fire on a healthy window.

    Without this, a `verify_digest` that raised unconditionally would still make the
    test above green — and the suite would be pinning "materialisation fails" rather
    than "materialisation is verified".
    """
    repo.enqueue("lazada", PERIOD)
    run_id = worker.serve(once=True)[0].run_id
    run = repo.get_run(run_id)
    assert run.status is not RunStatus.HARD_STOP
    assert "does NOT match" not in (run.error or "")


# ---------------------------------------------------------------------------
# Predecessor order files — defect 2.12's cross-window comparison
# ---------------------------------------------------------------------------
#
# On the CLI every window is a sibling directory under the staged input root, so
# `src/backfill.py` can just look. Here the input root is a scratch dir the worker
# built from THIS window's uploads, so without this the cross-window report would
# find nothing — and finding nothing would look like good news.

def _tiktok_orders(tmp_path, name: str, order_ids: list[str]):
    import pandas as pd

    from src import config as src_config

    settings = src_config.load_settings(ROOT / "config")
    colmap = src_config.column_map(settings, "tiktok", "orders")
    id_header = next(raw for raw, canon in colmap.items() if canon == "order_id")
    sheet = ((settings.get("sheet_names") or {}).get("tiktok") or {})["orders"]

    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        pd.DataFrame({id_header: order_ids}).to_excel(w, sheet_name=sheet, index=False)
    return path


def _upload_orders(client, tmp_path, name, period, order_ids):
    path = _tiktok_orders(tmp_path, name, order_ids)
    with path.open("rb") as fh:
        r = client.post("/uploads", files={"file": (path.name, fh)},
                        data={"platform": "tiktok", "period": period, "kind": "orders"})
    assert r.status_code == 201, r.text
    return r.json()


def test_an_earlier_windows_order_files_are_materialized_for_comparison(
        repo, make_client, service_settings, tmp_path):
    from service import materialize as materialize_lib
    from src import config as src_config
    from src.runlog import RunLog

    client = make_client("recon.user")
    _upload_orders(client, tmp_path, "1. order Purite 7.5.xlsx", "2026-05_w1", ["A-1"])
    _upload_orders(client, tmp_path, "2. order Purite 14.5.xlsx", "2026-05_w2", ["B-1"])

    scratch = tmp_path / "scratch"
    added = materialize_lib.materialize_predecessor_orders(
        repo, service_settings, "tiktok", "2026-05_w2",
        scratch=scratch, log=RunLog(),
        domain_settings=src_config.load_settings(ROOT / "config"))

    assert added == 1
    landed = list((scratch / "input" / "2026-05_w1" / "tiktok" / "orders").iterdir())
    assert len(landed) == 1, landed
    # Under the PREDECESSOR window's own uniform naming, because that is the layout
    # `read_parts` — and therefore `backfill` — expects to find.
    assert landed[0].suffix == ".xlsx"


def test_the_window_being_run_is_not_re_materialized_as_its_own_predecessor(
        repo, make_client, service_settings, tmp_path):
    """`w1` has no predecessor, so this must download nothing rather than itself."""
    from service import materialize as materialize_lib
    from src import config as src_config
    from src.runlog import RunLog

    client = make_client("recon.user")
    _upload_orders(client, tmp_path, "1. order Purite 7.5.xlsx", "2026-05_w1", ["A-1"])

    scratch = tmp_path / "scratch"
    added = materialize_lib.materialize_predecessor_orders(
        repo, service_settings, "tiktok", "2026-05_w1",
        scratch=scratch, log=RunLog(),
        domain_settings=src_config.load_settings(ROOT / "config"))

    assert added == 0
    assert not (scratch / "input").exists()


def _predecessor_with_altered_bytes(repo, make_client, service_settings, tmp_path):
    """A `w1` whose stored order object no longer matches its recorded digest."""
    from service import objects as object_lib

    client = make_client("recon.user")
    older = _upload_orders(client, tmp_path, "1. order Purite 7.5.xlsx",
                           "2026-05_w1", ["A-1"])
    _upload_orders(client, tmp_path, "2. order Purite 14.5.xlsx", "2026-05_w2", ["B-1"])
    object_lib.upload_store(service_settings).put(
        older["object_key"], b"not the file that was uploaded")


def test_a_predecessors_altered_bytes_are_refused_under_apply(
        repo, make_client, service_settings, tmp_path):
    """These bytes reach the same reader and, under `apply`, the same invoice — so
    they get the same digest check the window's own files get (2.10 / D52)."""
    from service import materialize as materialize_lib
    from service.materialize import MaterializationError
    from src import config as src_config
    from src.runlog import RunLog

    _predecessor_with_altered_bytes(repo, make_client, service_settings, tmp_path)

    with pytest.raises(MaterializationError) as exc:
        materialize_lib.materialize_predecessor_orders(
            repo, service_settings, "tiktok", "2026-05_w2",
            scratch=tmp_path / "scratch", log=RunLog(),
            domain_settings=src_config.load_settings(ROOT / "config"),
            strict=True)
    assert "does NOT match what was stored" in str(exc.value)


def test_a_predecessors_altered_bytes_only_warn_under_report(
        repo, make_client, service_settings, tmp_path):
    """The other half of one policy (2026-08-20). Report mode's contract is that it
    changes nothing, and a settlement run that DIES over a sibling window's corrupted
    file has changed a great deal. The corrupt file is never handed to the pipeline —
    it is dropped from scratch and named in the log.

    This test asserted the refusal in BOTH modes until 2026-08-20, which is how the
    inconsistency stayed invisible: a digest mismatch hard-stopped a report-mode run
    while an unnameable file and a missing object beside it only warned.
    """
    from service import materialize as materialize_lib
    from src import config as src_config
    from src.runlog import RunLog

    _predecessor_with_altered_bytes(repo, make_client, service_settings, tmp_path)
    log = RunLog()
    scratch = tmp_path / "scratch"

    added = materialize_lib.materialize_predecessor_orders(
        repo, service_settings, "tiktok", "2026-05_w2",
        scratch=scratch, log=log,
        domain_settings=src_config.load_settings(ROOT / "config"))

    assert added == 0
    assert any("does NOT match what was stored" in line for line in log.lines), log.lines
    landed = scratch / "input" / "2026-05_w1" / "tiktok" / "orders"
    assert not any(landed.iterdir()) if landed.is_dir() else True, (
        "the unverified file was left where the pipeline would read it")
