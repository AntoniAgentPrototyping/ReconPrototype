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

    from src import lazada
    source = tmp_path / "1_Store.csv"
    mapped = list(lazada.WEEKLY_MAP)[:4]
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
    assert upload_lib.column_map_for(settings_dict, "lazada", "weekly") == dict(lazada.WEEKLY_MAP)
    assert upload_lib.column_map_for(settings_dict, "tiktok", "orders") \
        == dict(settings_dict["column_maps"]["tiktok"]["orders"])


def test_sanitize_keeps_only_mapped_columns(tmp_path, settings_dict):
    import pandas as pd
    from src import lazada

    mapped = list(lazada.WEEKLY_MAP)[:4]
    source = tmp_path / "1_Store.xlsx"
    frame = pd.DataFrame({**{c: ["x"] for c in mapped},
                          "Recipient": ["a real person"],
                          "Phone #": ["0900000000"],
                          "Detail Address": ["a real address"]})
    with pd.ExcelWriter(source, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada.SHEETS["weekly"], index=False)

    result = upload_lib.sanitize(source, tmp_path / "out.xlsx",
                                       settings=settings_dict, platform="lazada", kind="weekly")

    assert set(result.kept_columns) == set(mapped)
    assert set(result.dropped_columns) == {"Recipient", "Phone #", "Detail Address"}
    assert result.dropped_known_pii == ["Detail Address", "Phone #", "Recipient"]

    written = pd.read_excel(tmp_path / "out.xlsx", sheet_name=lazada.SHEETS["weekly"])
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
    from src import lazada
    mapped = list(lazada.WEEKLY_MAP)[:5]
    path = tmp_path / name
    frame = pd.DataFrame({**{c: [f"{c}-{name}"] for c in mapped},
                          "Recipient": ["a person"]})
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        frame.to_excel(w, sheet_name=lazada.SHEETS["weekly"], index=False)
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
    sandbox = config_store.apply_edit(
        config_store.read_text(service_settings.config_dir),
        ["expected_stores", "tiktok"], ["Only This Store"])
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
