"""Per-stage fingerprints for the workbook golden gate.

Purpose: turn "a cell moved" into "the change enters at classify_ledger".
A TikTok workbook is ~650,000 cells; without this, localizing a regression to
the stage that caused it means bisecting by hand.

Built for cross-engine parity and **kept** when that migration was descheduled
(docs/06-DECISIONS.md#d25). The M1 seam refactor restructures the call graph
while promising identical output, which is exactly the situation where
per-stage row counts, column sets and sums earn their keep.

PII discipline: store names are HASHED, and no cell value is ever recorded —
only counts, column names, and column-level sums. Fingerprints are written
next to the (gitignored) goldens; only their digest is committed.

Floats are serialized via float.hex() so a fingerprint comparison is bitwise
and lossless. Everything here is compared at ZERO tolerance.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

# pandas is imported lazily inside the frame-inspecting functions so that
# digest_json and provenance stay usable without it — the manifest-integrity
# test imports this module purely to recompute committed digests.

ROOT = Path(__file__).resolve().parents[1]


def hash_label(value: object) -> str:
    """Stable short digest for a store/brand name, so a divergence can be
    localized without naming a client in a committed artifact."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _numeric_columns(df) -> list[str]:
    import pandas as pd
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _hex(x: float) -> str:
    return float(x).hex()


def frame_fingerprint(df) -> dict[str, Any]:
    """Shape + nullness + column sums. No values, no row-level anything."""
    num = _numeric_columns(df)
    fp: dict[str, Any] = {
        "rows": int(len(df)),
        "cols": [str(c) for c in df.columns],
        "null_counts": {str(c): int(df[c].isna().sum()) for c in df.columns},
        "sums": {str(c): _hex(df[c].sum(skipna=True)) for c in num},
    }
    if "store" in df.columns:
        by_store = []
        for store, g in df.groupby("store", dropna=False, sort=True):
            by_store.append({
                "store_h": hash_label(store),
                "rows": int(len(g)),
                "sums": {str(c): _hex(g[c].sum(skipna=True)) for c in num},
            })
        fp["by_store"] = by_store
    return fp


class RunFingerprint:
    """Accumulates one entry per instrumented pipeline stage, in call order."""

    def __init__(self, period: str, platform: str, engine: str) -> None:
        self.period = period
        self.platform = platform
        self.engine = engine
        self.stages: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self._counts: dict[str, int] = {}

    def record(self, stage: str, result: object) -> None:
        # Several stages are called more than once per run (read_parts and
        # derive_brand run for orders then income), so occurrences are
        # numbered — otherwise the two collapse and a divergence in one is
        # indistinguishable from the other.
        n = self._counts.get(stage, 0) + 1
        self._counts[stage] = n
        label = stage if n == 1 else f"{stage}#{n}"

        import pandas as pd

        # Stages return a DataFrame, or a tuple of them (classify_ledger ->
        # (classified, unmapped)). Record each positionally.
        frames: list[tuple[str, Any]] = []
        if isinstance(result, pd.DataFrame):
            frames.append((label, result))
        elif isinstance(result, tuple):
            for i, item in enumerate(result):
                if isinstance(item, pd.DataFrame):
                    frames.append((f"{label}[{i}]", item))
        for name, df in frames:
            self.stages.append({"stage": name, **frame_fingerprint(df)})

    def record_checks(self, checks: object) -> None:
        """The template control-block verdicts. Compared instead of diffing
        run_log.txt, which carries a wall-clock timestamp (runlog.py:39) and so
        can never be compared literally."""
        if isinstance(checks, list):
            self.checks = [
                {k: (v.hex() if isinstance(v, float) else v) for k, v in c.items()}
                for c in checks if isinstance(c, dict)
            ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "engine": self.engine,
            "period": self.period,
            "platform": self.platform,
            "stages": self.stages,
            "checks": self.checks,
        }

    def row_counts(self) -> dict[str, int]:
        """Compact, PII-free summary worth committing alongside the digest."""
        return {s["stage"]: s["rows"] for s in self.stages}

    def stores_seen(self) -> int:
        """How many distinct stores the run actually processed. A count, not
        names — enough to tell a single-store golden from a full-roster one."""
        return max((len(s["by_store"]) for s in self.stages if "by_store" in s), default=0)


def digest_json(payload: object) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# Provenance — which interpreter and libraries produced a golden.
# ---------------------------------------------------------------------------
#
# There used to be an `oracle_rev()` here: a sha256 over src/ + config/ + pinned
# dependency versions, used to key goldens by the exact tree that produced them.
# It was **dropped in M1** (docs/06-DECISIONS.md#d26).
#
# It solved a cross-engine problem — attributing a parity diff to engine vs
# config — and cost more than it was worth for a regression gate: because
# config changed in every month tested, every edit orphaned every golden and
# the gate SILENTLY STOPPED GATING (no golden for the new revision -> skip).
# A regression gate wants the opposite: always compare, and make moving the
# baseline an explicit, reviewable act. That is `make_golden.py --rebaseline`,
# with `git diff` on the manifest as the audit trail.

_DEPS = ("pandas", "openpyxl", "python-calamine", "pyxlsb", "PyYAML")


def provenance() -> dict[str, Any]:
    """Interpreter + library versions, stamped into the manifest.

    Not a control — it cannot fail a build. It is the first thing you want when
    a golden diffs on a machine that is not yours.
    """
    from importlib.metadata import PackageNotFoundError, version
    deps = {}
    for name in _DEPS:
        try:
            deps[name] = version(name)
        except PackageNotFoundError:
            deps[name] = "absent"
    return {"python": ".".join(str(v) for v in sys.version_info[:3]), "deps": deps}
