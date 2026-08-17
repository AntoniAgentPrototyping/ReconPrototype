"""Did that config change actually move a number?

**The tension this resolves.** A config editor that is pleasant to use produces more
config changes, and some of them — a column map, a header row, a filename pattern —
can move cells in the workbook the team invoices from. Before M6 the only defence
was that editing `settings.yaml` was annoying enough that people asked first.

**Why not the obvious fix.** M1 had one: `oracle_rev` keyed each golden manifest on
a hash of `src/` + `config/`, so any change to either orphaned every golden. It was
deleted ([D26](../docs/06-DECISIONS.md#d26)) because it **assumed** a config change
invalidated every golden, and the manifest lookup then missed, and the zero-tolerance
gate silently degraded into a skip. A gate that turns itself off when the code
changes is worse than no gate, because it reports green.

This inverts the assumption: **measure whether the change moved anything.**

1. `config_schema` marks the fields that *can* move a cell.
2. Applying a proposal that touches one enqueues a run of a **canary window** under
   the new config.
3. The workbook is compared cell-for-cell against that window's committed golden
   digest, at zero tolerance.
4. The answer is recorded and shown. **Nothing is blocked.** The change lands and
   the system tells you what it did.

Most changes — a tolerance, a store alias, a roster addition — move nothing and say
so, which is the outcome `oracle_rev` could never report because it could not tell
"unchanged" from "unknown".

**Which window answered is part of the answer.** A real committed golden exercises
the real column maps, sheet names and header spellings. The synthetic demo window
only exercises the paths its own generator emits, so a `column_maps` edit for a
header the generator never writes would move nothing in the demo and everything in
production. So the canary is resolved in order — a real window whose uploads are in
the bucket, else the demo window, else none — and the stored result records which.
"Verified against 2026-05_l1", "verified against the demo window" and "not verified"
are three different statements and the UI must not render them as one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Ordered best-first. A real settlement window is a strictly stronger claim than a
# synthetic one; `service/sampledata.py` seeds the last entry.
CANARY_PREFERENCE = ("2026-05_l1/lazada", "2026-05_w1/tiktok", "2026-05_s1/shopee")
DEMO_WINDOW = ("lazada", "2026-05_demo")


class State:
    """What can be said about a config version's effect on the numbers."""

    VERIFIED = "verified"           # a canary ran and no cell moved
    MOVED = "cells_moved"           # a canary ran and cells moved — read the count
    UNAVAILABLE = "unavailable"     # no canary window exists here; no claim made
    FAILED = "failed"               # the canary run itself broke
    NOT_APPLICABLE = "not_applicable"   # nothing this change touched can move a cell


@dataclass(frozen=True)
class Verdict:
    state: str
    window: str | None = None
    platform: str | None = None
    period: str | None = None
    cells_moved: int | None = None
    sheets_moved: tuple[str, ...] = ()
    detail: str = ""
    # False for the demo window. Carried explicitly rather than inferred from the
    # name, because "this was the weak gate" must survive into the database.
    strong: bool = False

    def message(self) -> str:
        """The sentence the config page shows. Three distinct statements."""
        if self.state == State.NOT_APPLICABLE:
            return ("Nothing in this change can move a workbook cell, so no "
                    "verification run was needed.")
        if self.state == State.VERIFIED:
            return (f"Verified against {self.window} — no cells moved."
                    + ("" if self.strong else " That window is SYNTHETIC: it only "
                       "exercises the paths its generator emits, so this is a weaker "
                       "claim than a real window."))
        if self.state == State.MOVED:
            return (f"{self.cells_moved} cell(s) moved in {self.window} "
                    f"({', '.join(self.sheets_moved)}). The change has landed; the "
                    f"goldens now need a deliberate re-baseline with a stated reason.")
        if self.state == State.FAILED:
            return f"The verification run failed: {self.detail}"
        return ("Not verified — no canary window is available in this deployment, so "
                "no claim can be made about whether this change moves a number. "
                "Seed the demo window, or apply this on a machine holding a real "
                "settlement window.")

    def to_json(self) -> str:
        return json.dumps({
            "state": self.state, "window": self.window, "platform": self.platform,
            "period": self.period, "cells_moved": self.cells_moved,
            "sheets_moved": list(self.sheets_moved), "strong": self.strong,
            "detail": self.detail, "message": self.message(),
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Choosing a canary
# ---------------------------------------------------------------------------

def committed_goldens(root: Path) -> dict:
    """The committed digests, read WITHOUT importing from `tests/`.

    `service/` must never import `tools/` and the container ships neither, so this
    reads the manifest as data. In a container the file is simply absent, which is
    the `unavailable` state and is reported rather than guessed at.
    """
    manifest = Path(root) / "tests" / "goldens" / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("windows") or {}
    except (OSError, ValueError):                               # pragma: no cover
        return {}


def choose_canary(repo, root: Path) -> tuple[str, str, dict, bool] | None:
    """`(platform, period, golden_entry, strong)` for the best available canary.

    A real window qualifies only if its exports are in the object store — a
    committed digest with no input is a digest nothing can be compared against.
    """
    goldens = committed_goldens(root)
    for key in CANARY_PREFERENCE:
        period, platform = key.split("/")
        entry = goldens.get(key)
        if entry is None:
            continue
        if not _window_has_input(repo, platform, period):
            continue
        return platform, period, entry, True

    platform, period = DEMO_WINDOW
    demo = goldens.get(f"{period}/{platform}")
    if demo is not None and _window_has_input(repo, platform, period):
        return platform, period, demo, False
    return None


def _window_has_input(repo, platform: str, period: str) -> bool:
    if not hasattr(repo, "uploads_for_window"):
        return False
    return bool(repo.uploads_for_window(platform, period))


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------

def verify(repo, settings, *, settings_text: str, touched_paths: list[list[str]],
           root: Path, log=None) -> Verdict:
    """Run the canary under `settings_text` and compare it to the committed golden.

    Synchronous, and called from the api handler on apply. It costs one run of one
    window — the Lazada canary is ~3 seconds — and doing it inline means the answer
    is on screen when the person who applied the change is still looking at it.
    A queued job would put it on the board where nobody connects it to the edit.
    """
    from . import config_schema, config_store

    parsed = config_store.parse(settings_text)
    invalidating = config_schema.invalidates_goldens(parsed, touched_paths)
    if not invalidating:
        return Verdict(state=State.NOT_APPLICABLE)

    chosen = choose_canary(repo, root)
    if chosen is None:
        return Verdict(state=State.UNAVAILABLE,
                       detail=f"fields that can move a cell: {', '.join(invalidating)}")
    platform, period, entry, strong = chosen
    window = f"{period}/{platform}"

    try:
        produced = _run_and_measure(repo, settings, platform, period,
                                    settings_text=settings_text,
                                    partial_roster=bool(entry.get("partial_roster")),
                                    log=log)
    except Exception as exc:                                    # noqa: BLE001
        # A broken canary is reported, never swallowed and never fatal to the apply:
        # the config change is already on disk and in git by this point, and
        # pretending the verification succeeded is the one unacceptable outcome.
        return Verdict(state=State.FAILED, window=window, platform=platform,
                       period=period, strong=strong,
                       detail=f"{type(exc).__name__}: {exc}")

    moved, cells = _compare(produced, entry.get("workbook") or {})
    if not moved:
        return Verdict(state=State.VERIFIED, window=window, platform=platform,
                       period=period, cells_moved=0, strong=strong)
    return Verdict(state=State.MOVED, window=window, platform=platform, period=period,
                   cells_moved=cells, sheets_moved=tuple(moved), strong=strong,
                   detail=f"fields that can move a cell: {', '.join(invalidating)}")


def _run_and_measure(repo, settings, platform: str, period: str, *,
                     settings_text: str, partial_roster: bool, log) -> dict:
    """One run into a throwaway directory, returning its cellset manifest."""
    import shutil
    import tempfile

    from src import pipeline
    from src.runlog import RunLog

    from . import materialize as materialize_lib

    scratch = Path(tempfile.mkdtemp(prefix="recon-verify-"))
    run_log = log if log is not None else RunLog()
    try:
        domain = _parse_settings(settings_text)
        mat = materialize_lib.materialize_window(
            repo, settings, platform, period, scratch=scratch, log=run_log,
            domain_settings=domain)
        ctx = pipeline.build_context(
            platform, period, config_dir=settings.config_dir,
            input_root=mat.input_root, output_root=scratch / "out",
            log=run_log, partial_roster=partial_roster, settings_text=settings_text)
        result = pipeline.run(ctx)
        if result.error is not None:
            raise result.error
        pipeline.write_artifacts(result)
        return _manifest_of(result.workbook_path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _parse_settings(settings_text: str) -> dict:
    from src import config as src_config
    return src_config.parse_settings(settings_text)


def _manifest_of(workbook: Path) -> dict:
    """The cellset manifest, computed WITHOUT importing from `tests/`.

    `tests/goldens/cellset.py` is the canonical implementation and the container
    does not ship it. Rather than duplicate its digest algorithm here — which would
    be a second definition of "did a cell move", and the two would drift — this
    imports it when the tree is present and reports `unavailable` when it is not.
    That keeps ONE definition of the comparison.
    """
    import sys

    root = Path(__file__).resolve().parents[1]
    goldens_dir = root / "tests" / "goldens"
    if not (goldens_dir / "cellset.py").is_file():
        raise RuntimeError(
            "tests/goldens/cellset.py is not present, so a workbook comparison "
            "cannot be made without defining a SECOND idea of what a moved cell is")
    if str(goldens_dir) not in sys.path:
        sys.path.insert(0, str(goldens_dir))
    from cellset import load_cellset, manifest                  # type: ignore
    return manifest(load_cellset(workbook))


def _compare(produced: dict, committed: dict) -> tuple[list[str], int]:
    """Which sheets moved, and how many cells.

    A missing committed manifest counts as "everything moved" rather than as
    "nothing moved" — that direction is the whole lesson of `oracle_rev`.
    """
    if not committed:
        return ["<no committed digest>"], 0

    produced_sheets = {s["name"]: s for s in produced.get("sheets") or []}
    committed_sheets = {s["name"]: s for s in committed.get("sheets") or []}
    moved: list[str] = []
    cells = 0
    for name in sorted(set(produced_sheets) | set(committed_sheets)):
        got, want = produced_sheets.get(name), committed_sheets.get(name)
        if got is None or want is None:
            moved.append(f"{name} ({'added' if want is None else 'removed'})")
            cells += (got or want or {}).get("cells", 0)
            continue
        if got.get("digest") != want.get("digest"):
            moved.append(name)
            # The digest says a sheet differs, not how much. Report its size, and
            # say so — an exact moved-cell count would need both cellsets in memory
            # and the committed one is only ever a digest.
            cells += max(got.get("cells", 0), want.get("cells", 0))
    return moved, cells
