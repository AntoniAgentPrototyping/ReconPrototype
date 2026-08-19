"""The team's own totals, and the ways recording them could go quietly wrong.

`src/pipeline.py` has always compared a run against reference figures and reported
UNVERIFIED when there are none. The api has accepted them on a job since M4. No
screen ever sent any, so every browser-driven run since M6 has been UNVERIFIED —
it ran clean and nothing corroborated it (**A3**).

What is worth testing is not that a number round-trips. It is the three ways a
reference figure can be worse than no reference figure at all:

1. recorded under a name nothing compares, so it *looks* checked,
2. a blank read as zero, which turns "we were not given this" into "the team says
   this window is worth 0 VND",
3. attached to a job, so a re-run silently makes a weaker claim than the first run.
"""

from __future__ import annotations

import pytest

from service import references


# ---------------------------------------------------------------------------
# 1. A field nothing reads is refused, not stored
# ---------------------------------------------------------------------------

def test_a_figure_no_check_compares_is_refused():
    """**The failure this guards.** A number typed into a box, saved, shown back —
    and never compared against anything, because the key is not one `_tie_grand`
    reads. It would look verified. Refusing is the only honest answer, and the
    refusal names the fields that do exist so it is actionable."""
    with pytest.raises(references.ReferenceError) as exc:
        references.parse("tiktok", {"total_revenue": 1_000_000})
    assert "total_revenue" in str(exc.value)
    assert "pre_vat" in str(exc.value), "a refusal has to say what IS accepted"


def test_the_fields_offered_are_the_keys_the_pipeline_reads():
    """The UI renders whatever this returns, so a drift here is a form field
    collecting a number nothing checks. Lazada's are per VAT rate because its
    invoice sheets are, and TikTok/Shopee's are the pre-VAT / with-VAT pair."""
    assert {f.key for f in references.fields_for("tiktok")} == {"pre_vat", "with_vat"}
    assert {f.key for f in references.fields_for("shopee")} == {"pre_vat", "with_vat"}
    assert {f.key for f in references.fields_for("lazada")} == {
        "pre_vat_105", "pre_vat", "pre_vat_110"}


def test_an_unknown_platform_is_refused():
    with pytest.raises(references.ReferenceError):
        references.fields_for("tokopedia")


# ---------------------------------------------------------------------------
# 2. Blank is not zero
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_field_is_absent_not_zero(blank):
    """**The one that would produce a false variance on every window.**
    `_tie_grand` skips a key it does not find, but compares against a key it does.
    A blank stored as 0.0 would report the entire window as a disagreement with the
    team — and the natural fix a hurried operator would reach for is to widen a
    tolerance, which is how the original checks became worthless."""
    parsed = references.parse("tiktok", {"pre_vat": blank, "with_vat": "5000"})
    assert "pre_vat" not in parsed["grand"]
    assert parsed["grand"]["with_vat"] == 5000.0


def test_thousands_separators_survive_a_copy_out_of_excel():
    """These figures are read off a spreadsheet by a person. `1,234,567` is how
    they appear there, and refusing it teaches nothing."""
    parsed = references.parse("tiktok", {"pre_vat": "1,234,567", "with_vat": "1 333 332"})
    assert parsed["grand"] == {"pre_vat": 1234567.0, "with_vat": 1333332.0}


def test_something_that_is_not_a_number_is_refused_by_name():
    with pytest.raises(references.ReferenceError) as exc:
        references.parse("tiktok", {"pre_vat": "about 1.2 billion"})
    assert "pre_vat" in str(exc.value)


def test_no_figures_at_all_is_recorded_as_no_figures():
    """Saving an empty form is allowed — it is how a withdrawal-by-blanking reads —
    but it must not become a comparison against nothing masquerading as one."""
    assert references.parse("lazada", {})["grand"] == {}


# ---------------------------------------------------------------------------
# 3. An explicit answer beats a standing one
# ---------------------------------------------------------------------------

def test_a_jobs_own_refs_win_over_the_windows():
    """`tools/devrun.py --refs` and the M4 api both pass figures for one specific
    run. The window's are the standing answer, not an override of an explicit one."""
    merged = references.merge({"grand": {"pre_vat": 100.0, "with_vat": 108.0}},
                              {"grand": {"pre_vat": 999.0}})
    assert merged["grand"] == {"pre_vat": 999.0, "with_vat": 108.0}, (
        "the job overrides the key it names and leaves the others standing")


def test_merging_nothing_leaves_the_window_figures_alone():
    window = {"grand": {"pre_vat": 100.0}}
    assert references.merge(window, None) == window
    assert references.merge(window, {}) == window


def test_a_window_with_no_figures_and_a_job_with_some_uses_the_jobs():
    assert references.merge(None, {"grand": {"pre_vat": 5.0}})["grand"] == {"pre_vat": 5.0}


# ---------------------------------------------------------------------------
# What the screen says
# ---------------------------------------------------------------------------

def test_the_summary_names_what_is_NOT_covered():
    """A partial set is the dangerous middle: it looks checked. The sentence says
    which figures are missing rather than only how many are present."""
    summary = references.summarise("lazada", {"grand": {"pre_vat": 1.0}})
    assert "1 of 3" in summary
    assert "VAT 5%" in summary and "VAT 10%" in summary


def test_the_summary_of_nothing_says_the_run_will_not_be_checked():
    assert "NOT CHECKED" in references.summarise("tiktok", {})
    assert "NOT CHECKED" in references.summarise("tiktok", None)
    assert "CHƯA ĐỐI CHIẾU" in references.summarise("tiktok", {}, "vi")


def test_a_complete_set_says_so_without_qualification():
    full = {"grand": {"pre_vat": 1.0, "with_vat": 2.0}}
    assert references.summarise("shopee", full) == "All 2 figures supplied."
    assert references.summarise("shopee", full, "vi") == "Đã nhập đủ 2 số."


def test_both_languages_name_the_same_missing_fields():
    """The Vietnamese is served from the same spec as the English (M8/5.3), not
    copied into the web layer — so a field added to one cannot be missing from the
    other, which is the drift this module exists to prevent one language later."""
    partial = {"grand": {"pre_vat": 1.0}}
    en = references.summarise("lazada", partial, "en")
    vi = references.summarise("lazada", partial, "vi")
    assert "1 of 3" in en and "1/3" in vi
    assert "VAT 5%" in en and "VAT 5%" in vi
    assert "VAT 10%" in en and "VAT 10%" in vi


def test_every_field_carries_both_languages():
    """A missing translation would render an empty label — a money box with no name."""
    for platform in ("tiktok", "shopee", "lazada"):
        for f in references.fields_for(platform):
            assert f.label and f.label_vi, f"{platform}/{f.key} label"
            assert f.help and f.help_vi, f"{platform}/{f.key} help"
            assert f.label_vi != f.label, f"{platform}/{f.key} is not translated"


# ---------------------------------------------------------------------------
# End to end: a run is actually checked against them
# ---------------------------------------------------------------------------
#
# Everything above is arithmetic on dicts. What has to be true is that a figure
# typed into the browser reaches `_tie_grand` in the worker — which is the step
# that did not exist, and the reason every browser-driven run has been UNVERIFIED
# since M6.

pytest.importorskip("pandas")
pytest.importorskip("httpx")


@pytest.fixture
def uploaded_smoke_window(repo, make_client, tmp_path):
    """The synthetic smoke window, pushed through the real upload endpoint.

    Synthetic on purpose, like every worker test here: this has to run on a machine
    with no client data.
    """
    from tools.smoke_test import PERIOD, build_window

    build_window(tmp_path / "elsewhere")
    folder = tmp_path / "elsewhere" / "input" / PERIOD / "lazada" / "Weekly"
    # Returned, not re-made: `make_client` creates an identity, so calling it twice
    # in one test collides on the username.
    client = make_client("recon.user")
    for export in sorted(folder.iterdir()):
        with export.open("rb") as fh:
            response = client.post("/uploads", files={"file": (export.name, fh)},
                                   data={"platform": "lazada", "period": PERIOD,
                                         "kind": "weekly"})
        assert response.status_code == 201, response.text
    return client


def test_a_run_with_no_references_reports_unverified(repo, store, service_settings,
                                                     uploaded_smoke_window):
    """The baseline, and today's behaviour for every run made in a browser.

    Asserted so the test below is measuring a change rather than a coincidence.
    """
    from service.worker import Worker
    from src.pipeline import RunStatus
    from tools.smoke_test import PERIOD

    repo.enqueue("lazada", PERIOD)
    run_id = Worker(repo, store, service_settings).serve(once=True)[0].run_id
    assert repo.get_run(run_id).status is RunStatus.UNVERIFIED


def test_figures_recorded_on_the_window_reach_the_run(repo, store, service_settings,
                                                      uploaded_smoke_window):
    """**The whole point of A3.** Recorded through the real endpoint, by a session
    identity, then picked up by the worker without anything being passed on the job.

    The figures are deliberately WRONG by a wide margin: a run that ties is
    indistinguishable from a run that compared nothing, so the only way to prove the
    comparison happened is to make it disagree and see the disagreement.
    """
    from service.worker import Worker
    from src.pipeline import RunStatus
    from tools.smoke_test import PERIOD

    response = uploaded_smoke_window.put(
        f"/windows/lazada/{PERIOD}/references",
        json={"values": {"pre_vat": "999999999"}, "note": "team file, sheet PV used"})
    assert response.status_code == 200, response.text

    repo.enqueue("lazada", PERIOD)
    run_id = Worker(repo, store, service_settings).serve(once=True)[0].run_id
    run = repo.get_run(run_id)
    assert run.status is RunStatus.VARIANCE, (
        "a figure that disagrees must produce a variance; UNVERIFIED here would mean "
        "the reference never reached the tie-out")
    assert any("pre_vat" in v for v in run.variances)


def test_the_figures_survive_a_re_run(repo, store, service_settings,
                                      uploaded_smoke_window):
    """Why this is stored on the WINDOW and not on the job.

    `jobs.refs` is per job, so a second run of the same window would compare against
    nothing and quietly make a weaker claim than the first — with no visible cause.
    """
    from service.worker import Worker
    from src.pipeline import RunStatus
    from tools.smoke_test import PERIOD

    uploaded_smoke_window.put(f"/windows/lazada/{PERIOD}/references",
                              json={"values": {"pre_vat": "999999999"}})

    statuses = []
    for _ in range(2):
        repo.enqueue("lazada", PERIOD)
        run_id = Worker(repo, store, service_settings).serve(once=True)[0].run_id
        statuses.append(repo.get_run(run_id).status)
    assert statuses == [RunStatus.VARIANCE, RunStatus.VARIANCE]


def test_the_author_comes_from_the_session_not_the_body(repo, make_client):
    """Same rule as requested_by / uploaded_by / declared_by / proposed_by. This
    number decides whether a run is called verified, so who supplied it is audit."""
    from tools.smoke_test import PERIOD

    client = make_client("recon.user")
    response = client.put(f"/windows/lazada/{PERIOD}/references",
                          json={"values": {"pre_vat": "1"},
                                "supplied_by": "somebody.else@ada"})
    assert response.status_code == 200, response.text
    assert response.json()["references"]["supplied_by"] != "somebody.else@ada"


def test_a_field_the_pipeline_does_not_read_is_refused_at_the_door(make_client):
    from tools.smoke_test import PERIOD

    client = make_client("recon.user")
    response = client.put(f"/windows/lazada/{PERIOD}/references",
                          json={"values": {"grand_total": "1"}})
    assert response.status_code == 422
    assert "grand_total" in response.json()["detail"]
