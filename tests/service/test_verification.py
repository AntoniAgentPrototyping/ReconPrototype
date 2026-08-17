"""Did that config change actually move a number?

The replacement for M1's deleted `oracle_rev` ([D26](docs/06-DECISIONS.md#d26)).
`oracle_rev` keyed golden manifests on a hash of `src/` + `config/`, so any change
to either orphaned every golden, the lookup missed, and the zero-tolerance gate
silently degraded into a **skip** — a gate that turns itself off when the code
changes, reporting green.

These tests pin the inversion: the five outcomes stay distinguishable, an unknown
counts as invalidating rather than as harmless, and the strength of the claim
travels with it.
"""

from __future__ import annotations

import pytest

from service import verification
from service.verification import State, Verdict

pytest.importorskip("httpx")


# ---------------------------------------------------------------------------
# The five states are five different statements
# ---------------------------------------------------------------------------

def test_verified_unavailable_and_not_applicable_read_differently():
    """Collapsing these into a boolean is exactly how "we never checked" comes to
    read as "we checked and it was fine"."""
    verified = Verdict(state=State.VERIFIED, window="2026-05_l1/lazada",
                       cells_moved=0, strong=True).message()
    unavailable = Verdict(state=State.UNAVAILABLE).message()
    not_applicable = Verdict(state=State.NOT_APPLICABLE).message()

    assert "no cells moved" in verified
    assert "Not verified" in unavailable and "no claim can be made" in unavailable
    assert "no verification run was needed" in not_applicable
    assert len({verified, unavailable, not_applicable}) == 3


def test_a_synthetic_canary_says_it_is_the_weaker_claim():
    """A real committed golden exercises the real column maps, sheet names and header
    spellings. The demo window only exercises the paths its own generator emits, so a
    column-map edit for a header the generator never writes would move nothing there
    and everything in production."""
    strong = Verdict(state=State.VERIFIED, window="2026-05_l1/lazada", strong=True)
    weak = Verdict(state=State.VERIFIED, window="2026-05_demo/lazada", strong=False)

    assert "SYNTHETIC" in weak.message()
    assert "SYNTHETIC" not in strong.message()
    assert weak.message() != strong.message()


def test_the_verdict_names_which_window_answered():
    """"Verified against 2026-05_l1", "verified against the demo window" and "not
    verified" are three different statements and the UI must not render them as one."""
    for window in ("2026-05_l1/lazada", "2026-05_demo/lazada"):
        assert window in Verdict(state=State.VERIFIED, window=window).message()


def test_moved_cells_say_what_to_do_next():
    verdict = Verdict(state=State.MOVED, window="2026-05_l1/lazada", cells_moved=2193,
                      sheets_moved=("Summary", "1.08"), strong=True)
    message = verdict.message()
    assert "2193 cell(s) moved" in message
    assert "Summary" in message
    assert "deliberate re-baseline" in message, (
        "a moved cell is a finding that needs a stated reason, never a reason to "
        "widen a tolerance or quietly regenerate")


def test_the_verdict_serialises_with_its_message():
    import json
    parsed = json.loads(Verdict(state=State.VERIFIED, window="w", strong=False).to_json())
    assert parsed["state"] == "verified" and parsed["strong"] is False
    assert "SYNTHETIC" in parsed["message"]


# ---------------------------------------------------------------------------
# Choosing a canary
# ---------------------------------------------------------------------------

class _NoUploads:
    @staticmethod
    def uploads_for_window(platform, period):
        return []


class _HasUploads:
    def __init__(self, *windows):
        self.windows = set(windows)

    def uploads_for_window(self, platform, period):
        return [{"id": 1}] if f"{period}/{platform}" in self.windows else []


def test_no_input_means_no_canary_and_no_claim(tmp_path):
    """A committed digest with no input is a digest nothing can be compared
    against, so it is `unavailable` rather than a silent pass."""
    assert verification.choose_canary(_NoUploads(), tmp_path) is None


def test_a_real_window_is_preferred_over_the_demo(tmp_path):
    import json
    (tmp_path / "tests" / "goldens").mkdir(parents=True)
    (tmp_path / "tests" / "goldens" / "manifest.json").write_text(json.dumps({
        "windows": {"2026-05_l1/lazada": {"workbook": {"sheets": []}},
                    "2026-05_demo/lazada": {"workbook": {"sheets": []}}}}),
        encoding="utf-8")

    both = verification.choose_canary(
        _HasUploads("2026-05_l1/lazada", "2026-05_demo/lazada"), tmp_path)
    assert both is not None
    platform, period, _entry, strong = both
    assert (platform, period) == ("lazada", "2026-05_l1")
    assert strong is True, "a real settlement window is the stronger claim"

    only_demo = verification.choose_canary(_HasUploads("2026-05_demo/lazada"), tmp_path)
    assert only_demo is not None
    assert only_demo[1] == "2026-05_demo"
    assert only_demo[3] is False, "and the weakness must travel with it"


def test_a_missing_manifest_is_unavailable_not_verified(tmp_path):
    """A container ships no `tests/`. That is the `unavailable` state — reported,
    never guessed at."""
    assert verification.committed_goldens(tmp_path) == {}
    assert verification.choose_canary(_HasUploads("2026-05_l1/lazada"), tmp_path) is None


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def test_identical_manifests_report_nothing_moved():
    produced = {"sheets": [{"name": "Summary", "digest": "abc", "cells": 10}]}
    assert verification._compare(produced, produced) == ([], 0)


def test_a_changed_digest_names_the_sheet():
    produced = {"sheets": [{"name": "Summary", "digest": "abc", "cells": 10},
                           {"name": "1.08", "digest": "def", "cells": 20}]}
    committed = {"sheets": [{"name": "Summary", "digest": "abc", "cells": 10},
                            {"name": "1.08", "digest": "CHANGED", "cells": 20}]}
    moved, cells = verification._compare(produced, committed)
    assert moved == ["1.08"] and cells == 20


def test_an_added_or_removed_sheet_counts_as_moved():
    produced = {"sheets": [{"name": "Summary", "digest": "abc", "cells": 10}]}
    committed = {"sheets": [{"name": "Summary", "digest": "abc", "cells": 10},
                            {"name": "Gone", "digest": "x", "cells": 5}]}
    moved, _ = verification._compare(produced, committed)
    assert moved == ["Gone (added)"] or "Gone" in moved[0]


def test_no_committed_digest_counts_as_everything_moved():
    """The whole lesson of `oracle_rev`: an unknown must not read as unchanged."""
    moved, _ = verification._compare({"sheets": [{"name": "S", "digest": "a"}]}, {})
    assert moved == ["<no committed digest>"]


# ---------------------------------------------------------------------------
# End to end, through apply
# ---------------------------------------------------------------------------

def test_a_harmless_change_reports_not_applicable(editor_client, repo):
    """Most changes — a tolerance, an alias, a roster addition — move nothing, and
    saying so is the outcome `oracle_rev` could never report because it could not
    tell "unchanged" from "unknown"."""
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [{"op": "set", "path": ["tolerances", "tiktok", "pv_sum_vnd"],
                   "value": 13000}],
        "summary": "the team widened their own PV sum check"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    applied = admin.post(f"/config/proposals/{proposal['id']}/apply").json()

    assert applied["verification"]["state"] == State.NOT_APPLICABLE
    version = repo.config_version(applied["config_version_id"])
    assert version["verification_state"] == State.NOT_APPLICABLE


def test_a_goldens_affecting_change_is_verified_or_honestly_unavailable(
        editor_client, repo):
    """The sandbox has no uploaded canary window, so the correct answer is
    `unavailable` — and it must SAY so rather than reporting a pass."""
    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        "summary": "the 8% concession has ended"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    applied = admin.post(f"/config/proposals/{proposal['id']}/apply").json()

    state = applied["verification"]["state"]
    assert state in (State.VERIFIED, State.UNAVAILABLE, State.FAILED)
    assert state != State.NOT_APPLICABLE, (
        "a VAT change can move every cell in the workbook; claiming it needed no "
        "check would be the oracle_rev failure again")
    version = repo.config_version(applied["config_version_id"])
    assert version["verification_state"] == state
    assert version["verification"]["message"]


def test_a_verification_failure_never_undoes_the_applied_change(
        editor_client, sandbox_settings, monkeypatch):
    """The config is on disk and in git by the time this runs. Claiming success is
    the one unacceptable outcome; failing the apply would be almost as bad, because
    the change has already landed."""
    from service import config_store, verification as verification_mod

    def explode(*_a, **_k):
        raise RuntimeError("canary exploded")

    monkeypatch.setattr(verification_mod, "verify", explode)

    admin = editor_client("recon.admin")
    proposal = admin.post("/config/proposals", json={
        "edits": [{"op": "set", "path": ["vat_factors", "default"], "value": 1.10}],
        "summary": "applied while the canary is broken"}).json()
    admin.post(f"/config/proposals/{proposal['id']}/approve", json={})
    applied = admin.post(f"/config/proposals/{proposal['id']}/apply")

    assert applied.status_code == 200
    assert applied.json()["verification"]["state"] == State.FAILED
    # And the change really is on disk.
    assert config_store.read_value(
        config_store.read_text(sandbox_settings.config_dir),
        ["vat_factors", "default"]) == 1.10
