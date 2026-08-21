"""Assemble the month-end master's inputs, the way `materialize_window` does.

`src/master_summary.py` is pure compute plus the workbook build. This is the half
that knows where a month's finance files live: which windows ran (from the
database, never a hardcoded table), where each one's `finance_file.xlsx` is in the
artifact store, and what is missing.

**Why the window list comes from the database.** The prototype carried
`WINDOWS = {"TikTok": [w1..w5], "Shopee": [s1..s4], ...}` in a dict, which omitted
the real sub-batch windows `s2x` and `s3k` — and its own tie check re-read that
same dict, so the omission was structurally invisible. Asking the database "which
windows ran this month" cannot omit a window that ran.

**Why a missing window is data and not a log line.** A master is rebuilt whenever
a window finishes, so for most of the month it is partial by construction. The
windows that did not contribute are carried in `Coverage` and printed on the face
of the workbook. A master that looks complete and is not is precisely the failure
this project exists to prevent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from src import master_summary as ms
from src.errors import ReconHardStop

FINANCE_FILE = "finance_file.xlsx"

# A run that produced a workbook. HARD_STOP produced nothing; a run still in
# flight has no workbook yet. Both are "missing", with different reasons.
_USABLE = ("ok", "variance", "unverified")

_WHY = {
    None: "no run yet — the window's files are in the system but nobody has run it",
    "hard_stop": "the run stopped and produced no finance file",
    "in_flight": "the run is still going",
    "no_artifact": "the run finished but its finance file is not in the artifact store",
}


def month_of(period: str) -> str:
    """'2026-07_w1' -> '2026-07'. The month is the part before the underscore."""
    return period.split("_", 1)[0]


def plan_month(repo, month: str) -> tuple[list[dict], list[tuple[str, str, str]]]:
    """(usable window rows, missing (platform, period, why)) for one month."""
    usable: list[dict] = []
    missing: list[tuple[str, str, str]] = []
    for row in repo.month_windows(month):
        platform, period = row["platform"], row["period"]
        status = row.get("status")
        run_id = row.get("latest_run_id")
        if run_id is None:
            missing.append((ms.DIR_PLATFORM[platform], period, _WHY[None]))
        elif status is None:
            missing.append((ms.DIR_PLATFORM[platform], period, _WHY["in_flight"]))
        elif status not in _USABLE:
            missing.append((ms.DIR_PLATFORM[platform], period, _WHY["hard_stop"]))
        else:
            usable.append({"platform": platform, "period": period, "run_id": run_id,
                           "status": status})
    return usable, missing


def _fetch(store, repo, run_id: int, scratch: Path) -> Path | None:
    """Copy one run's finance file out of the artifact store, or None.

    Copied into scratch rather than read in place: `ArtifactStore.open` may hand
    back a path inside a bucket mount, and the reader should not hold a handle on
    the store while it works.
    """
    try:
        artifact = repo.artifact(run_id, FINANCE_FILE)
    except Exception:                                           # noqa: BLE001
        return None
    source = store.open(artifact.uri)
    if source is None or not Path(source).is_file():
        return None
    target = scratch / f"run-{run_id}-{FINANCE_FILE}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def collect(repo, store, month: str, *, scratch: Path, log,
            built_by: str = "") -> tuple[ms.Coverage, list[ms.Window]]:
    """Read every usable window of `month` into memory. Returns (coverage, windows)."""
    usable, missing = plan_month(repo, month)
    windows: list[ms.Window] = []
    included: list[tuple[str, str]] = []

    for row in usable:
        platform = ms.DIR_PLATFORM[row["platform"]]
        path = _fetch(store, repo, row["run_id"], scratch)
        if path is None:
            missing.append((platform, row["period"], _WHY["no_artifact"]))
            log.warn(f"  {row['period']} {row['platform']}: run {row['run_id']} has no "
                     f"{FINANCE_FILE} in the artifact store — EXCLUDED from the master")
            continue
        totals = ms.read_window(path, platform)
        windows.append(ms.Window(platform=platform, period=row["period"],
                                 label=ms.window_label(row["period"]), totals=totals,
                                 source=f"run {row['run_id']}"))
        included.append((platform, row["period"]))
        log.add(f"  {row['period']} {row['platform']}: {len(totals)} storefront(s), "
                f"with-VAT {totals['wv'].sum():,.0f} (run {row['run_id']}, "
                f"{row['status']})")

    if not windows:
        raise ReconHardStop(
            f"No window of {month} has a finance file, so there is nothing to "
            f"consolidate. {len(missing)} window(s) are known and none produced "
            f"one.")

    coverage = ms.Coverage(month=month, included=included,
                           missing=sorted(missing), built_by=built_by)
    for platform, period, why in coverage.missing:
        log.warn(f"  MISSING from the {month} master: {platform} {period} — {why}")
    log.add(f"  {coverage.headline()}")
    return coverage, windows


# `brand_map(config_dir)` was here until 2026-08-21, reading `config/brand_map.csv`
# beside the config directory. The mapping is `store_to_brand` in the contract now
# (D12), so the worker calls `ingest.brand_map(settings)` on the config it resolved
# for the month — which is the point of the move: the master and a settlement run
# read one mapping, and an edit made in the browser reaches both.
