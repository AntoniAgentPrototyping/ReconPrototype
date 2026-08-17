"""Developer runner: one settlement window, end to end, from a terminal.

**This is not the production entry point.** Since M6 a settlement run is queued
from the browser and executed by `service/worker.py`; there is no CLI a finance
operator is meant to touch. What survives here is the developer-facing half of
the old `tools/full_run.py`, kept for exactly three jobs:

    1. `tools/make_golden.py` needs `build_context` to regenerate a golden.
    2. `tools/smoke_test.py` needs it for the synthetic self-test.
    3. Debugging a window without a database, a bucket or a browser in the way.

Deleting it would take the golden gate with it, which is why `full_run.py` was
replaced rather than simply removed (M6-PLAN.md phase 1).

Usage:
    python tools/devrun.py --platform tiktok --period 2026-05_w1 \
        --refs <refs json>            # from the refs-extraction scripts

Refs JSON shape:
    {"per_store": {"<normalized store>": {"metric": value, ...}},
     "grand": {"pre_vat": x, "with_vat": y, ...},
     "grand_tolerance": 2000}

All pipeline logic lives in `src/pipeline.py`. This file is argument parsing,
one call to `run()`, one call to `write_artifacts()`, and an exit code — so the
worker and this runner stay two callers of the same function rather than two
implementations of the same idea (docs/06-DECISIONS.md#d24).

Exit codes:

    0  OK          every figure tied against the team's references
    1  VARIANCE    a real numeric disagreement, or a tie-out breach
    2  UNVERIFIED  the run completed cleanly but had nothing to check against
                   (no --refs). NOT a failure.
    3  HARD_STOP   nothing was produced
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import pipeline  # noqa: E402
from src.pipeline import EXIT_CODES, RunContext, RunStatus  # noqa: E402
from src.runlog import RunLog  # noqa: E402


def build_context(platform: str, period: str, refs_path: str | None = None,
                  *, log=None, root: Path = ROOT,
                  partial_roster: bool = False) -> RunContext:
    """Read `--refs` off disk, then hand over to the seam.

    The assembly itself lives in `pipeline.build_context` because the service
    worker needs it and cannot import `tools/`. What stays here is the one
    CLI-shaped part: turning a `--refs` PATH into a dict. That keeps the file
    read in the driver, where the rest of this repo's file I/O already lives.
    """
    refs = json.loads(Path(refs_path).read_text(encoding="utf-8")) if refs_path else {}
    return pipeline.build_context(platform, period, root=root, refs=refs, log=log,
                                  partial_roster=partial_roster)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        description="Developer runner. Production runs are queued from the web app.")
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--period", required=True)
    ap.add_argument("--refs", default=None)
    ap.add_argument(
        "--partial-roster", action="store_true",
        help="This window deliberately covers a SUBSET of the store roster. "
             "Makes every expected store optional for this run, so check_stores "
             "stops failing on absent stores, while the UNEXPECTED-store check "
             "stays armed. Config on disk is not touched, and the run log says "
             "loudly that the totals are not the month's. Needed here because a "
             "subset golden must stay regenerable; the browser has no equivalent "
             "per-run switch — an incomplete window is declared once, with a "
             "reason, when its files are uploaded (M6 workstream C).")
    args = ap.parse_args(argv)

    log = RunLog()
    ctx = build_context(args.platform, args.period, args.refs, log=log,
                        partial_roster=args.partial_roster)
    result = pipeline.run(ctx)

    # The RESULT section lives in the seam so the worker emits the same text
    # rather than a second copy of it. Variances and unverified stores stay
    # under separate headings — see pipeline.log_result.
    pipeline.log_result(result)

    # The METRICS section is emitted by write_artifacts, so that it can include
    # the cost of writing the workbook itself.
    #
    # write_artifacts always runs, so a hard stop still leaves a log behind —
    # previously an unwritable finance_file.xlsx killed the process with no
    # record at all (docs/08-KNOWN-DEFECTS.md#17).
    try:
        pipeline.write_artifacts(result)
    except Exception as exc:                                    # noqa: BLE001
        print(f"FAILED to write artifacts: {type(exc).__name__}: {exc}")
        return EXIT_CODES[RunStatus.HARD_STOP]

    return EXIT_CODES[result.status]


if __name__ == "__main__":
    sys.exit(main())
