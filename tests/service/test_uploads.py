"""The upload boundary — and the gate that makes it safe to have one.

M5 lets an operator upload a raw export instead of copying it into a folder by
hand, and strips customer PII at that boundary rather than only at read time
(defect 2.3). Stripping means **rewriting the file before the verified pipeline
reads it**, which inserts a transformation into the path that produced every
number this project has ever verified.

M6 goes further: files are also **renamed** to a uniform scheme on the way in
(`service/naming.py`), and store identity is derived from the filename (D6) — so
the rename can silently reassign a storefront's revenue.

This repo does not take either on trust ([D12](docs/06-DECISIONS.md#d12)). So the
load-bearing test here is
`test_a_sanitized_renamed_window_produces_the_committed_golden`: sanitize AND
rename a real window's exports, run the pipeline over the copies, and demand the
workbook match the committed digest cell for cell. If it ever fails, the sanitizer
or the naming scheme is wrong — fix it, never loosen the comparison.

It covered **Lazada only** before M6, which is precisely why nobody noticed that
the sanitizer flattened Shopee income's two band rows and TikTok orders' junk row.
It now runs per platform.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for extra in (ROOT / "tests" / "goldens", ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

pytest.importorskip("pandas")
pytest.importorskip("httpx")

from service import uploads as upload_lib  # noqa: E402
from service.uploads import UploadRejected  # noqa: E402

WINDOW, PLATFORM, KIND = "2026-05_l1", "lazada", "weekly"


@pytest.fixture
def settings_dict():
    from src import config as src_config
    return src_config.load_settings(ROOT / "config")


# ---------------------------------------------------------------------------
# Filenames are data, not decoration
# ---------------------------------------------------------------------------

def test_a_normal_export_filename_is_accepted():
    """Store identity is derived from the filename (D6), so it must survive
    intact — including Vietnamese characters and the numeric prefix."""
    assert upload_lib.check_filename("12_Unilever Chăm Sóc Vẻ Đẹp_Unilever 2.xlsx")
    assert upload_lib.check_filename("2_KAO.xlsx") == "2_KAO.xlsx"
    assert upload_lib.check_filename("Income.Masan part 1.xlsx")


@pytest.mark.parametrize("name", [
    "../../etc/passwd.xlsx", "a/b.xlsx", "a\\b.xlsx", ".hidden.xlsx",
    "", "notes.txt", "script.xlsx.exe", "x" * 200 + ".xlsx",
])
def test_unsafe_or_wrong_filenames_are_rejected(name):
    with pytest.raises(UploadRejected):
        upload_lib.check_filename(name)


def test_the_materialized_path_is_the_layout_the_cli_reads(tmp_path):
    """The service must not invent a second layout, or `tools/devrun.py` stops
    being able to run the same window with the service switched off.

    Moved from `uploads.staged_path` to `naming.target_path` in M6 — same layout,
    and it has to stay the same layout: the golden generator reads exactly these
    directories.
    """
    from service import naming

    assert naming.target_path(tmp_path, "2026-05_l1", "lazada", "weekly", "001_KAO.xlsx") \
        == tmp_path / "2026-05_l1" / "lazada" / "Weekly" / "001_KAO.xlsx"
    assert naming.target_path(tmp_path, "2026-05_w1", "tiktok", "orders", "a.xlsx") \
        == tmp_path / "2026-05_w1" / "tiktok" / "orders" / "a.xlsx"
    # Capitalised for Lazada, lowercase for the other two — because that is what
    # `lazada.read_ledger` and `read_parts` respectively look for.
    assert naming.folder_for("lazada", "daily") == "Daily"
    assert naming.folder_for("shopee", "income") == "income"
    with pytest.raises(naming.NamingError):
        naming.folder_for("tiktok", "nonsense")


def test_a_csv_upload_is_stored_as_xlsx(tmp_path, settings_dict):
    """Closes a latent bug rather than a stylistic one.

    The sanitizer writes openpyxl bytes. Before M6 a `.csv` upload was written
    under its original `.csv` name, and `read_parts` dispatches on the suffix — so
    the pipeline would have handed a zip archive to `pd.read_csv`.
    """
    import pandas as pd

    assert upload_lib.sanitized_name("part_1.csv") == "part_1.xlsx"
    assert upload_lib.sanitized_name("1_KAO.xlsx") == "1_KAO.xlsx"

    from _contract import lazada_headers
    source = tmp_path / "1_Store.csv"
    mapped = lazada_headers("weekly", settings_dict)[:4]
    pd.DataFrame({**{c: ["x"] for c in mapped},
                  "Phone #": ["0900000000"]}).to_csv(source, index=False,
                                                     encoding="utf-8-sig")
    target = tmp_path / upload_lib.sanitized_name(source.name)
    result = upload_lib.sanitize(source, target, settings=settings_dict,
                                 platform="lazada", kind="weekly")
    assert target.suffix == ".xlsx"
    assert result.dropped_known_pii == ["Phone #"]
    # Readable as the workbook it now is, not as the csv it was.
    assert "0900000000" not in pd.read_excel(target, sheet_name=0).to_csv()


# ---------------------------------------------------------------------------
# The strip itself
# ---------------------------------------------------------------------------

def test_the_allowlist_is_the_pipelines_own_column_map(settings_dict):
    """No second list of PII column names to maintain and go stale — the strip
    uses the same contract `ingest.read_parts` does."""
    from src import lazada
    assert (upload_lib.column_map_for(settings_dict, "lazada", "weekly")
            == lazada.column_map(settings_dict, "weekly"))
    assert upload_lib.column_map_for(settings_dict, "tiktok", "orders") \
        == dict(settings_dict["column_maps"]["tiktok"]["orders"])


def test_sanitize_keeps_only_mapped_columns(tmp_path, settings_dict):
    import pandas as pd
    from _contract import lazada_headers, lazada_sheet

    mapped = lazada_headers("weekly", settings_dict)[:4]
    source = tmp_path / "1_Store.xlsx"
    frame = pd.DataFrame({**{c: ["x"] for c in mapped},
                          "Recipient": ["a real person"],
                          "Phone #": ["0900000000"],
                          "Detail Address": ["a real address"]})
    with pd.ExcelWriter(source, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada_sheet("weekly", settings_dict), index=False)

    result = upload_lib.sanitize(source, tmp_path / "out.xlsx",
                                       settings=settings_dict, platform="lazada", kind="weekly")

    assert set(result.kept_columns) == set(mapped)
    assert set(result.dropped_columns) == {"Recipient", "Phone #", "Detail Address"}
    assert result.dropped_known_pii == ["Detail Address", "Phone #", "Recipient"]

    written = pd.read_excel(tmp_path / "out.xlsx",
                            sheet_name=lazada_sheet("weekly", settings_dict))
    assert "Phone #" not in written.columns
    assert "0900000000" not in written.to_csv(), "a PII VALUE survived the strip"


def test_a_file_with_no_recognisable_columns_is_rejected(tmp_path, settings_dict):
    """Either the wrong file kind, or the headers drifted — and the message says
    to add the new spelling as a PARALLEL entry rather than replacing the old."""
    import pandas as pd
    source = tmp_path / "1_Store.xlsx"
    with pd.ExcelWriter(source, engine="openpyxl") as w:
        pd.DataFrame({"Nothing": [1]}).to_excel(w, sheet_name="Transaction Overview", index=False)

    with pytest.raises(UploadRejected, match="parallel entry"):
        upload_lib.sanitize(source, tmp_path / "o.xlsx", settings=settings_dict,
                                  platform="lazada", kind="weekly")


def test_digest_is_content_addressed():
    assert upload_lib.digest_bytes(b"abc") == upload_lib.digest_bytes(b"abc")
    assert upload_lib.digest_bytes(b"abc") != upload_lib.digest_bytes(b"abd")


# ---------------------------------------------------------------------------
# The gate: sanitized input must produce the committed golden
# ---------------------------------------------------------------------------

# One window per platform, each with a committed golden digest. Lazada was the
# only one covered before M6, which is exactly why the sanitizer's shape handling
# was broken for the other two and nothing said so: Shopee income's leaf header is
# on row 3 under two band rows and TikTok orders carry a junk row under the header,
# and the old sanitizer flattened both.
GATE_WINDOWS = [
    ("2026-05_l1", "lazada", ("weekly", "daily")),
    ("2026-05_w1", "tiktok", ("orders", "income")),
    ("2026-05_s1", "shopee", ("orders", "income")),
]


def _client_data_present() -> list[str]:
    return [f"{period}/{platform}" for period, platform, _ in GATE_WINDOWS
            if (ROOT / "input" / period / platform).is_dir()]


@pytest.mark.parametrize("period,platform,kinds", GATE_WINDOWS,
                         ids=[f"{p}-{pl}" for p, pl, _ in GATE_WINDOWS])
def test_a_sanitized_renamed_window_produces_the_committed_golden(
        tmp_path, settings_dict, period, platform, kinds):
    """Rewriting AND renaming an export before the pipeline reads it must not move
    a cell.

    The strongest gate in the repo, and M6 widened it twice over. It now covers all
    three platforms rather than Lazada alone, and it exercises the uniform rename
    as well as the strip — so it is the single test that makes
    `service/naming.py` safe to trust.

    Two independent claims, proved together at zero tolerance against digests
    generated through the developer CLI:

    * the strip drops only columns, and preserves the file SHAPE the config
      describes (`header_rows`, `skip_rows_after_header`, `sheet_patterns`);
    * the rename is a fixed point of the pipeline's own store parser, and
      `sorted(new_names)` preserves `sorted(originals)` — which is what keeps
      `pd.concat` row order, and therefore workbook cell positions, unchanged.

    If it fails, the sanitizer or the naming scheme is wrong. **Do not loosen the
    comparison and do not re-baseline the golden** — there is no phase of M6 where
    movement here is expected.
    """
    from cellset import load_cellset, manifest
    from goldens import committed_windows
    from service import naming

    source_root = ROOT / "input" / period / platform
    if not source_root.is_dir():
        pytest.skip(f"input/{period} is absent — goldens derive from client data "
                    f"and are not distributed (D15)")
    entry = committed_windows().get(f"{period}/{platform}")
    if entry is None:
        pytest.skip(f"{period}/{platform} has no committed golden digest")

    dropped = renamed = files = 0
    for kind in kinds:
        folder = source_root / naming.folder_for(platform, kind)
        if not folder.is_dir():
            continue
        names = sorted(p.name for p in folder.iterdir()
                       if p.suffix.lower() in (".xlsx", ".xls", ".csv"))
        if not names:
            continue
        for item in naming.plan_window(names, platform, kind, settings_dict):
            result = upload_lib.sanitize(
                folder / item.original,
                naming.target_path(tmp_path / "input", period, platform, kind, item.name),
                settings=settings_dict, platform=platform, kind=kind)
            dropped += len(result.dropped_columns)
            renamed += int(item.renamed)
            files += 1

    from src import pipeline
    from src.runlog import RunLog

    ctx = pipeline.build_context(
        platform, period, config_dir=ROOT / "config",
        input_root=tmp_path / "input", output_root=tmp_path / "output", log=RunLog(),
        # Two of the three goldens are deliberate subsets, and the manifest says
        # which. Reading the flag from there rather than hardcoding it means this
        # test cannot drift from how the baseline was generated.
        partial_roster=bool(entry.get("partial_roster")))
    result = pipeline.run(ctx)
    assert result.error is None, result.error
    pipeline.write_artifacts(result)

    produced = manifest(load_cellset(result.workbook_path))
    for got, want in zip(produced["sheets"], entry["workbook"]["sheets"]):
        assert got["digest"] == want["digest"], (
            f"{platform} {got['name']}: sanitizing or renaming the input moved a "
            f"cell. The sanitizer or service/naming.py is wrong — do not loosen "
            f"this comparison.")
    assert produced == entry["workbook"]
    assert dropped, "the sanitizer dropped nothing, so this proved very little"
    assert renamed == files, (
        f"only {renamed} of {files} files were renamed, so the rename is not fully "
        f"under test")


def test_the_gate_is_not_silently_skipped_where_client_data_exists():
    """The gate above skips without local client data, which is correct and is also
    how a real regression could go unnoticed.

    On the machine that HOLDS `input/`, set `RECON_REQUIRE_CLIENT_DATA=1` and this
    fails rather than shrugging. CI has no client data and never sets it.
    """
    import os
    if not os.environ.get("RECON_REQUIRE_CLIENT_DATA"):
        pytest.skip("RECON_REQUIRE_CLIENT_DATA is not set")
    present = _client_data_present()
    assert len(present) == len(GATE_WINDOWS), (
        f"only {present} of the three gate windows have client data. With "
        f"RECON_REQUIRE_CLIENT_DATA set, a missing window is a failure: the "
        f"upload gate covering one platform is how the sanitizer's shape handling "
        f"stayed broken for the other two.")


# ---------------------------------------------------------------------------
# Over HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def upload_client(make_client):
    return make_client("recon.user")


def sample_export(tmp_path: Path, name: str = "1_TestStore.xlsx") -> Path:
    """A minimal valid Lazada weekly export.

    The cell values vary with the filename, because they must: byte-identical
    uploads are refused by the database (the double-pull control), so a fixture
    that ignored the name would make every second file in a window a 409.
    """
    import pandas as pd
    from _contract import lazada_headers, lazada_sheet
    mapped = lazada_headers("weekly")[:5]
    path = tmp_path / name
    frame = pd.DataFrame({**{c: [f"{c}-{name}"] for c in mapped},
                          "Recipient": ["a person"]})
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada_sheet("weekly"), index=False)
    return path


def test_upload_strips_quarantines_and_records(upload_client, tmp_path, service_settings):
    path = sample_export(tmp_path)
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": WINDOW, "kind": "weekly"})
    assert r.status_code == 201
    body = r.json()
    assert body["sanitized"] is True
    assert body["dropped_known_pii"] == ["Recipient"]
    # From the session, never the body. The name follows the role fixture, which
    # became `recon.user` in M6 when `recon.operator` was renamed.
    assert body["uploaded_by"] == "user@test"
    assert body["state"] == "stored", "the bucket is the window; there is no staging step"
    assert body["object_key"], "an upload with no object key cannot be materialised"
    # Resolved at the door by the pipeline's own regex, not inside a run.
    assert body["store"] == "TestStore"
    assert body["store_derived_from_filename"] == "TestStore"
    assert body["store_corrected"] is False
    assert body["uniform_name_preview"] == "NNN_TestStore.xlsx"

    # NEITHER copy outlives the request: the stripped bytes are in the object
    # store, the unstripped bytes are nowhere. Before M6 the sanitized file stayed
    # on a volume the api needed and the worker also needed (defect 2.4).
    leftovers = [p.name for p in (service_settings.scratch_root / "incoming").glob("*")
                 ] if (service_settings.scratch_root / "incoming").is_dir() else []
    assert leftovers == [], f"an upload was left on disk: {leftovers}"


def test_a_byte_identical_reupload_is_refused(upload_client, tmp_path):
    """The double-pull class, caught at the door. One instance of it carried
    5.97B VND of double-invoicing risk (D9)."""
    path = sample_export(tmp_path)
    for _ in range(1):
        with path.open("rb") as fh:
            first = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                       data={"platform": "lazada", "period": WINDOW,
                                             "kind": "weekly"})
    with path.open("rb") as fh:
        again = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                   data={"platform": "lazada", "period": WINDOW,
                                         "kind": "weekly"})
    assert first.status_code == 201
    assert again.status_code == 409
    assert again.json()["existing"]["id"] == first.json()["id"]


def test_there_is_no_stage_route(upload_client):
    """`POST /uploads/{id}/stage` is deleted, and its absence is pinned.

    The bucket is the window, so there is nothing to move — and its two defects
    went with it: no collision guard on the target filename, and reading an upload
    through `ArtifactStore.open`, the conflation that made the api need an input
    volume the worker also had (defect 2.4).
    """
    assert upload_client.post("/uploads/1/stage").status_code in (404, 405)


def test_the_window_plan_shows_names_stores_and_what_is_missing(upload_client, tmp_path):
    """The screen an operator works from before queueing.

    It answers three questions the old flow could only answer by starting a run
    and reading a hard stop.
    """
    for name in ("2_KAO.xlsx", "1_TestStore.xlsx"):
        path = sample_export(tmp_path, name)
        with path.open("rb") as fh:
            assert upload_client.post(
                "/uploads", files={"file": (path.name, fh)},
                data={"platform": "lazada", "period": WINDOW,
                      "kind": "weekly"}).status_code == 201

    plan = upload_client.get("/uploads/plan",
                             params={"platform": "lazada", "period": WINDOW}).json()
    weekly = plan["files"]["weekly"]
    assert [f["uniform_name"] for f in weekly] == ["001_TestStore.xlsx", "002_KAO.xlsx"], (
        "ordinals follow sorted(originals), which is the order read_ledger reads — "
        "assigning them by arrival would let two uploads race to decide workbook "
        "row order")
    assert [f["ordinal"] for f in weekly] == [1, 2]
    assert all(f["renamed"] for f in weekly)
    assert plan["stores_present"] == ["KAO", "TestStore"]
    assert plan["problems"] == []
    # expected_stores.lazada is empty in the real config, so there is no roster to
    # be short of and the window is ready.
    assert plan["missing_stores"] == []
    assert plan["ready"] is True


def test_a_rejected_upload_leaves_the_window_and_the_record(upload_client, tmp_path):
    """Never a delete: which file was rejected and why is the audit trail for a
    window whose numbers somebody later queries."""
    path = sample_export(tmp_path, "3_Rejectme.xlsx")
    with path.open("rb") as fh:
        created = upload_client.post(
            "/uploads", files={"file": (path.name, fh)},
            data={"platform": "lazada", "period": WINDOW, "kind": "weekly"}).json()

    short = upload_client.post(f"/uploads/{created['id']}/reject", json={"reason": "no"})
    assert short.status_code == 422, "a one-word reason tells the next person nothing"

    rejected = upload_client.post(f"/uploads/{created['id']}/reject",
                                  json={"reason": "wrong settlement week, superseded"})
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"

    plan = upload_client.get("/uploads/plan",
                             params={"platform": "lazada", "period": WINDOW}).json()
    assert plan["files"]["weekly"] == [], "a rejected file is not part of the window"
    listed = upload_client.get("/uploads", params={"period": WINDOW}).json()["uploads"]
    assert [u["state"] for u in listed] == ["rejected"], "but the row survives"


def test_a_store_the_roster_does_not_name_is_refused_at_the_door(upload_client, tmp_path):
    """`check_stores` still hard-stops on an unexpected store — this makes that a
    backstop rather than the first line, and names the fix."""
    path = sample_export(tmp_path, "1_NotAStore.xlsx")
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "tiktok", "period": "2026-05_w1",
                                     "kind": "orders"})
    # The filename does not even parse as a tiktok name, which is the first guard.
    assert r.status_code == 422
    assert "store_from_filename" in r.json()["detail"]


def test_an_unparseable_filename_is_refused_while_a_human_is_looking(upload_client, tmp_path):
    """Store identity comes from the filename (D6). Refusing now beats
    hard-stopping a run at month end."""
    path = sample_export(tmp_path, "no store here.xlsx")
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": WINDOW,
                                     "kind": "weekly"})
    assert r.status_code == 422
    assert "store" in r.json()["detail"].lower()


def test_a_kind_the_platform_does_not_have_is_refused_as_a_pair(upload_client, tmp_path):
    """`lazada`/`orders` is not a typo in one field, it is an incoherent pair, and
    reporting it as "unknown kind" sends an operator to fix the wrong thing."""
    path = sample_export(tmp_path)
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": WINDOW,
                                     "kind": "orders"})
    assert r.status_code == 422
    assert "lazada has no 'orders' files" in r.json()["detail"]


def test_upload_rejects_a_wrong_kind_or_platform(upload_client, tmp_path):
    path = sample_export(tmp_path)
    with path.open("rb") as fh:
        assert upload_client.post("/uploads", files={"file": (path.name, fh)},
                                  data={"platform": "amazon", "period": WINDOW,
                                        "kind": "weekly"}).status_code == 422
    with path.open("rb") as fh:
        assert upload_client.post("/uploads", files={"file": (path.name, fh)},
                                  data={"platform": "lazada", "period": WINDOW,
                                        "kind": "nonsense"}).status_code == 422


def test_upload_rejects_a_path_shaped_period(upload_client, tmp_path):
    path = sample_export(tmp_path)
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": "../evil",
                                     "kind": "weekly"})
    assert r.status_code == 422


def test_a_viewer_cannot_upload(make_client, tmp_path):
    path = sample_export(tmp_path)
    with path.open("rb") as fh:
        r = make_client("recon.viewer").post(
            "/uploads", files={"file": (path.name, fh)},
            data={"platform": "lazada", "period": WINDOW, "kind": "weekly"})
    assert r.status_code == 403


def test_the_plan_uses_the_config_the_window_will_actually_run_under(
        upload_client, repo, service_settings):
    """A preview that disagrees with the control is worse than no preview.

    A window that has run before is pinned to the config it ran under, and the demo
    window is pinned to a synthetic roster the moment it is seeded. Reading the roster
    from `config/settings.yaml` instead asks the wrong question.

    **Found by running the seeded demo through the containers**: every plan screen
    reported `ready: false` and listed 14-17 absent real stores, while the runs
    themselves completed with `roster_missing = 0` — because the worker resolves the
    pinned config correctly and only the preview did not. An operator sees "not ready"
    and has nothing they can do about it.
    """
    from service import config_store

    # A config whose tiktok roster is exactly one invented store, pinned to a window.
    document = config_store.parse(
        config_store.read_text(service_settings.config_dir))
    document["expected_stores"]["tiktok"] = ["Only This Store"]
    sandbox = config_store.dump(document)
    version = repo.record_config_version(sandbox, source="proposal", created_by="test")
    repo.pin_period_config("tiktok", "2026-05_pinned", version["id"],
                           pinned_by="test", reason="pinned for this test")

    plan = upload_client.get("/uploads/plan", params={
        "platform": "tiktok", "period": "2026-05_pinned"}).json()

    assert plan["missing_stores"] == ["Only This Store"], (
        "the plan read the live settings.yaml roster instead of the window's pinned "
        f"config; it reported {len(plan['missing_stores'])} missing stores")
    assert plan["expected_store_count"] == 1

    # An UNPINNED window still reads the live config, which is the other half.
    live = upload_client.get("/uploads/plan", params={
        "platform": "tiktok", "period": "2026-05_unpinned"}).json()
    assert live["expected_store_count"] > 1


# --- the settlement-date door (defect 2.3's residual, defect 2.12) -----------
#
# `check_span` is pure, so most of this needs no database and no HTTP. The two
# integration tests at the end are the ones that prove the api actually calls it.


def test_a_window_defining_file_from_another_month_is_refused():
    """The July mis-pull's shape, stated as a rule.

    A file settling wholly outside its window's month is either mis-labelled or a
    mis-pull. Before this the api validated `period` for character safety alone, so
    the answer arrived at the month-end tie instead of at the door.
    """
    refusal, warning = upload_lib.check_span(
        "2026-05_l1", "weekly",
        settles_from=date(2026, 7, 1), settles_to=date(2026, 7, 7))

    assert refusal and "2026-05" in refusal and "2026-07-01" in refusal
    assert warning is None, "a refusal and a warning are different answers"


def test_a_weekly_that_laps_into_the_next_month_is_accepted():
    """INTERSECT, not contain — and this is why.

    Lazada's 25th-to-month-end Daily week is a permanent monthly fixture and its
    weeklies lap the boundary. A containment test would refuse the healthy case
    every single month, which is how a control gets switched off.
    """
    refusal, _ = upload_lib.check_span(
        "2026-07_l5", "daily",
        settles_from=date(2026, 7, 29), settles_to=date(2026, 8, 4))
    assert refusal is None


def test_an_order_export_that_predates_its_window_is_not_date_checked():
    """An order created in June legitimately settles in July.

    TikTok also re-ships each store's prior-month order pull in *every* weekly
    folder, because the cross-period stitch needs it. Date-checking order exports
    would flag every healthy window in the tree.
    """
    refusal, warning = upload_lib.check_span(
        "2026-07_w2", "orders",
        settles_from=date(2026, 5, 1), settles_to=date(2026, 6, 30),
        sibling_starts=[date(2026, 7, 8)])
    assert (refusal, warning) == (None, None)


def test_a_file_with_no_readable_date_is_not_checked_rather_than_guessed():
    """A Lazada ledger has no `statement_date`; some exports carry no parseable
    date at all. Silence is a legitimate answer and must not become a refusal."""
    assert upload_lib.check_span("2026-05_l1", "weekly",
                                 settles_from=None, settles_to=None) == (None, None)


def test_the_mispull_shape_warns_and_never_refuses():
    """`find_outliers`' signal, at the door.

    Warn rather than refuse: D9's `window_settlement_bounds` owns the hard control
    at run time, and a door that refuses on suspicion teaches operators to fight it.
    """
    refusal, warning = upload_lib.check_span(
        "2026-07_w2", "income",
        settles_from=date(2026, 7, 1), settles_to=date(2026, 7, 14),
        sibling_starts=[date(2026, 7, 8), date(2026, 7, 8)])

    assert refusal is None, "suspicion is not grounds for refusal here"
    assert warning and "window_settlement_bounds" in warning
    assert "2026-07-08" in warning


def test_the_first_file_of_a_window_has_no_siblings_and_so_no_warning():
    """The reason this cannot be a refusal: with one file there is nothing to
    compare against, and every window starts with one file."""
    assert upload_lib.check_span(
        "2026-07_w2", "income", settles_from=date(2026, 7, 1),
        settles_to=date(2026, 7, 7), sibling_starts=[]) == (None, None)


def test_an_ambiguous_sibling_split_stays_silent():
    """Earlier than EVERY sibling, not merely earlier than the modal start.

    `find_outliers` compares against the mode, which needs an arbitrary tie-break
    on an even split. At the door nobody is reviewing a plan, so an ambiguous case
    says nothing rather than guessing which half is the anomaly.
    """
    _, warning = upload_lib.check_span(
        "2026-07_w2", "income",
        settles_from=date(2026, 7, 5), settles_to=date(2026, 7, 14),
        sibling_starts=[date(2026, 7, 1), date(2026, 7, 8)])
    assert warning is None


def dated_export(tmp_path: Path, name: str, *, day: str) -> Path:
    """A Lazada weekly export carrying real dates and order ids.

    `day` is spelled in the format the contract declares for lazada/weekly
    (`%d-%b-%Y`), read through `date_formats` rather than hardcoded here — the
    door parses with that same accessor, so a test that spelled it its own way
    would be asserting against a second idea of the format (D54).
    """
    import pandas as pd
    from _contract import domain, lazada_headers, lazada_sheet

    fmt = ((domain().get("date_formats") or {}).get("lazada") or {}).get("weekly")
    assert fmt == "%d-%b-%Y", f"contract changed the weekly date format to {fmt!r}"

    headers = lazada_headers("weekly")
    frame = pd.DataFrame({
        **{c: [f"{c}-{name}", f"{c}-{name}-2"] for c in headers},
        "Transaction Date": [day, day],
        "Order No.": [f"ORD-{name}-1", f"ORD-{name}-2"],
        "Recipient": ["a person", "another person"],
    })
    path = tmp_path / name
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada_sheet("weekly"), index=False)
    return path


def test_an_export_settling_in_another_month_is_refused_at_the_door(
        upload_client, tmp_path, repo):
    """The integration half: the api must actually consult `check_span`.

    And the refusal must happen before anything durable exists — a file this
    window should not contain must leave no row and no object behind.
    """
    path = dated_export(tmp_path, "9_TestStore.xlsx", day="03-Jul-2026")
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": WINDOW,
                                     "kind": "weekly"})

    assert r.status_code == 422, r.text
    assert "2026-05" in r.json()["detail"]
    assert not [u for u in repo.list_uploads(period=WINDOW)
                if u["filename"] == "9_TestStore.xlsx"], (
        "a refused upload left a row behind")


def test_order_ids_and_the_settlement_span_are_indexed_at_the_door(
        upload_client, tmp_path, repo):
    """The index that lets defect 2.12's question be asked at all.

    Identifiers only — no amount reaches this table. The span is recorded on the
    upload row so a later file can be compared against its siblings.
    """
    path = dated_export(tmp_path, "8_TestStore.xlsx", day="03-May-2026")
    with path.open("rb") as fh:
        body = upload_client.post("/uploads", files={"file": (path.name, fh)},
                                  data={"platform": "lazada", "period": WINDOW,
                                        "kind": "weekly"}).json()

    assert body["order_ids_indexed"] == 2, body
    assert body["index_note"] == "", body["index_note"]
    assert body["settles_from"] == "2026-05-03"
    assert body["settles_to"] == "2026-05-03"
    assert body["settles_checked"] is True

    # The span reached the upload row, which is what the sibling comparison reads.
    spans = repo.upload_spans("lazada", WINDOW, "weekly")
    assert any(s["filename"] == "8_TestStore.xlsx" for s in spans), spans

    # And the upload is no longer outstanding work for the backfill CLI.
    assert not [u for u in repo.uploads_unindexed()
                if u["filename"] == "8_TestStore.xlsx"]


def test_the_mispull_warning_reaches_the_response_through_the_real_sibling_query(
        upload_client, tmp_path, repo):
    """The wiring, not the rule: the api must read siblings from the DATABASE.

    The pure test above proves `check_span` decides correctly given sibling starts.
    This one proves the api supplies them — the span has to have been persisted by
    the earlier upload and read back by `upload_spans`, which is the half a unit
    test cannot see.
    """
    first = dated_export(tmp_path, "6_TestStore.xlsx", day="10-May-2026")
    with first.open("rb") as fh:
        ok = upload_client.post("/uploads", files={"file": (first.name, fh)},
                                data={"platform": "lazada", "period": WINDOW,
                                      "kind": "weekly"}).json()
    assert ok["span_warning"] == "", "the first file of a window has no siblings"

    # Same month, so not a refusal — but earlier than the only sibling.
    earlier = dated_export(tmp_path, "7_TestStore.xlsx", day="01-May-2026")
    with earlier.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (earlier.name, fh)},
                               data={"platform": "lazada", "period": WINDOW,
                                     "kind": "weekly"})

    assert r.status_code == 201, "the mis-pull shape must never refuse"
    body = r.json()
    assert "2026-05-10" in body["span_warning"], body["span_warning"]
    assert "window_settlement_bounds" in body["span_warning"]


# ---------------------------------------------------------------------------
# The size cap (Phase 6 / C7, 2026-08-20)
# ---------------------------------------------------------------------------

def test_an_oversized_upload_is_refused_before_anything_reads_it(
        repo, store, service_settings, issue_session, tmp_path):
    """Refused at 413 naming the limit, before the sanitizer or the object store
    see a byte. The cap is proven by a bounded read, not trusted from
    Content-Length — a body that fills the extra byte is over whatever the
    header claimed."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from service.api import create_app
    from service.auth import AuthPolicy

    app = create_app(repo, store,
                     settings=replace(service_settings, max_upload_mb=1),
                     policy=AuthPolicy(enabled=True))
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {issue_session('recon.user')}"

    big = tmp_path / "1_TestStore.xlsx"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    with big.open("rb") as fh:
        r = client.post("/uploads", files={"file": (big.name, fh)},
                        data={"platform": "lazada", "period": WINDOW,
                              "kind": "weekly"})
    assert r.status_code == 413
    assert "1 MB" in r.json()["detail"], "the refusal must name the limit"
    assert repo.list_uploads(platform="lazada", period=WINDOW) == [], (
        "an oversized body must leave no trace")


def test_a_file_under_the_cap_still_uploads(upload_client, tmp_path):
    """The control: the default cap (512 MB, ~2.8x the largest real export)
    changes nothing for a legitimate file."""
    path = sample_export(tmp_path, "2_CapControl.xlsx")
    with path.open("rb") as fh:
        r = upload_client.post("/uploads", files={"file": (path.name, fh)},
                               data={"platform": "lazada", "period": WINDOW,
                                     "kind": "weekly"})
    assert r.status_code == 201


def test_ready_respects_which_stores_the_declaration_names(
        upload_client, repo, service_settings):
    """D3: a missing store the declaration does not name will hard-stop the run,
    and `ready` must say so rather than promising a run the pipeline refuses."""
    from service import config_store

    document = config_store.parse(
        config_store.read_text(service_settings.config_dir))
    document["expected_stores"]["tiktok"] = ["Store A", "Store B"]
    version = repo.record_config_version(config_store.dump(document),
                                         source="proposal", created_by="test")
    repo.pin_period_config("tiktok", "2026-05_d3", version["id"],
                           pinned_by="test", reason="pinned for this test")

    params = {"platform": "tiktok", "period": "2026-05_d3"}
    plan = upload_client.get("/uploads/plan", params=params).json()
    assert plan["missing_stores"] == ["Store A", "Store B"]
    assert plan["expected_stores"] == ["Store A", "Store B"], \
        "the declaration form's picklist reads names, not a count"
    assert plan["ready"] is False

    # Naming ONE of the two missing stores does not make the window ready.
    upload_client.post("/windows/roster", json={
        "platform": "tiktok", "period": "2026-05_d3", "partial": True,
        "reason": "Store A wound down this month", "stores": ["Store A"]})
    plan = upload_client.get("/uploads/plan", params=params).json()
    assert plan["ready"] is False, \
        "Store B is missing and undeclared — POST /jobs would hard-stop"

    # Naming both does.
    upload_client.post("/windows/roster", json={
        "platform": "tiktok", "period": "2026-05_d3", "partial": True,
        "reason": "both storefronts wound down this month",
        "stores": ["Store A", "Store B"]})
    plan = upload_client.get("/uploads/plan", params=params).json()
    assert plan["ready"] is True

    # A blanket declaration still covers everything (pre-021 rows).
    upload_client.post("/windows/roster", json={
        "platform": "tiktok", "period": "2026-05_d3", "partial": True,
        "reason": "declared before the store list existed"})
    plan = upload_client.get("/uploads/plan", params=params).json()
    assert plan["ready"] is True


def test_the_plan_flags_a_declared_absent_store_that_now_has_files(
        upload_client, tmp_path):
    """D3's re-evaluation nudge: the page re-renders after every upload action,
    so `declared_absent_present` IS the upload-time hook — no listener needed."""
    path = sample_export(tmp_path, "2_KAO.xlsx")
    with path.open("rb") as fh:
        assert upload_client.post(
            "/uploads", files={"file": (path.name, fh)},
            data={"platform": "lazada", "period": WINDOW,
                  "kind": "weekly"}).status_code == 201
    upload_client.post("/windows/roster", json={
        "platform": "lazada", "period": WINDOW, "partial": True,
        "reason": "KAO expected absent this week", "stores": ["KAO"]})

    plan = upload_client.get("/uploads/plan",
                             params={"platform": "lazada", "period": WINDOW}).json()
    assert plan["declared_absent_present"] == ["KAO"], \
        "the declaration no longer describes the window, and the page must say so"
