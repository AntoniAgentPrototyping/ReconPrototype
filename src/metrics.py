"""Per-stage timing, row counts and peak memory.

**Why the stage tag is load-bearing.** Runs are dominated by Excel read/write,
so total wall time cannot answer "would a faster compute engine help?" — which
is the one question it gets asked. Without the split, the engine-port trigger in
docs/10-ROADMAP.md is unmeasurable and the decision stays opinion
(docs/06-DECISIONS.md#d27).

**Three kinds, not two.** The first version of this module had only `io` and
`compute`, and it lied: `build_workbook` spends ~37s constructing openpyxl cell
objects, which got tagged `compute` and pushed the measured compute share to
31% — over the 25% trigger. But openpyxl materialization is engine-independent;
a faster DataFrame library would not touch a second of it. Tagged honestly,
actual DataFrame math is ~1.4% of wall time.

    io          reading and writing files
    compute     DataFrame math — the ONLY thing a different engine would change
    serialize   building the openpyxl workbook — engine-independent

`compute_share` is therefore compute over total, and it is the number the
trigger reads. Mis-tagging one stage inverted the verdict, which is the whole
argument for keeping this taxonomy narrow and explicit.

**Why peak RSS and not tracemalloc.** tracemalloc counts Python allocations and
misses the numpy/Arrow buffers underneath a DataFrame, which is precisely the
exposure being measured. Peak working set comes from the OS.

**Why this is not part of RunLog.** A QueueRunLog substitutes for RunLog under
the web app (docs/06-DECISIONS.md#d24); requiring it to also implement a metrics
protocol would make that substitution harder for no gain. Metrics ride on
RunResult instead.

Nothing here can fail a run: a metrics error must never cost a finance file, so
`stage()` swallows its own bookkeeping errors and records what it can.
"""

from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

StageKind = Literal["io", "compute", "serialize"]


# ---------------------------------------------------------------------------
# Peak resident set size, stdlib only, both platforms that matter.
# ---------------------------------------------------------------------------

def peak_rss_mb() -> float:
    """Peak working set of this process, in MB. 0.0 if unavailable.

    Windows via psapi.GetProcessMemoryInfo; POSIX via getrusage. The POSIX
    branch is not hypothetical — the worker runs in a Linux container, and the
    container memory limit is what the port trigger is really about.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _COUNTERS()
            counters.cb = ctypes.sizeof(_COUNTERS)

            # GetCurrentProcess returns the pseudo-handle (HANDLE)-1. ctypes
            # defaults an unset restype to C int, which truncates that to a
            # 32-bit -1 on a 64-bit build; psapi then rejects it and returns 0.
            # The failure is silent — it reads as "0 MB", not as an error — so
            # declare the types rather than trusting the defaults.
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi = ctypes.windll.psapi
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(_COUNTERS), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

            handle = ctypes.c_void_p(kernel32.GetCurrentProcess())
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return counters.PeakWorkingSetSize / (1024 * 1024)
        except Exception:
            return 0.0
        return 0.0

    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        return peak / 1024 if sys.platform.startswith("linux") else peak / (1024 * 1024)
    except Exception:
        return 0.0


@dataclass
class StageMetric:
    name: str
    kind: StageKind
    wall_s: float
    rows_in: int | None = None
    rows_out: int | None = None
    peak_rss_mb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.name, "kind": self.kind, "wall_s": round(self.wall_s, 4),
                "rows_in": self.rows_in, "rows_out": self.rows_out,
                "peak_rss_mb": round(self.peak_rss_mb, 1)}


@dataclass
class RunMetrics:
    stages: list[StageMetric] = field(default_factory=list)

    # -- the numbers the engine-port trigger is defined on -------------------

    @property
    def io_s(self) -> float:
        return sum(s.wall_s for s in self.stages if s.kind == "io")

    @property
    def compute_s(self) -> float:
        """DataFrame math only — excludes workbook materialization."""
        return sum(s.wall_s for s in self.stages if s.kind == "compute")

    @property
    def serialize_s(self) -> float:
        """openpyxl workbook construction. Engine-independent: a different
        DataFrame library would not change this number."""
        return sum(s.wall_s for s in self.stages if s.kind == "serialize")

    @property
    def wall_s(self) -> float:
        return sum(s.wall_s for s in self.stages)

    @property
    def compute_share(self) -> float:
        """Fraction of measured time a faster DataFrame engine could address.

        Deliberately the narrow definition: serialize time is in the
        denominator but not the numerator, because speeding up pandas does not
        speed up openpyxl. The engine-port trigger sits at 0.25.
        """
        total = self.wall_s
        return (self.compute_s / total) if total else 0.0

    @property
    def peak_rss_mb(self) -> float:
        return max((s.peak_rss_mb for s in self.stages), default=peak_rss_mb())

    @property
    def max_rows(self) -> int:
        return max((s.rows_out or 0 for s in self.stages), default=0)

    @contextlib.contextmanager
    def stage(self, name: str, kind: StageKind, *,
              rows_in: int | None = None,
              rows_out: Callable[[], int | None] | None = None):
        """Time one stage.

        `rows_out` is a callable because the frame does not exist until the body
        has run — passing a value would capture None every time.
        """
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            out = None
            if rows_out is not None:
                try:
                    out = rows_out()
                except Exception:
                    out = None      # never let bookkeeping break a run
            self.stages.append(StageMetric(name=name, kind=kind, wall_s=elapsed,
                                           rows_in=rows_in, rows_out=out,
                                           peak_rss_mb=peak_rss_mb()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "wall_s": round(self.wall_s, 3),
            "io_s": round(self.io_s, 3),
            "compute_s": round(self.compute_s, 3),
            "serialize_s": round(self.serialize_s, 3),
            "compute_share": round(self.compute_share, 4),
            "peak_rss_mb": round(self.peak_rss_mb, 1),
            "max_rows": self.max_rows,
            "stages": [s.to_dict() for s in self.stages],
        }

    def summary_lines(self) -> list[str]:
        """Rendered into run_log.txt, so the operator sees the split without
        opening a JSON file."""
        if not self.stages:
            return []
        out = [
            f"  wall {self.wall_s:,.1f}s  =  io {self.io_s:,.1f}s "
            f"+ compute {self.compute_s:,.1f}s + serialize {self.serialize_s:,.1f}s"
            f"   ({self.compute_share:.1%} engine-addressable)",
            f"  peak RSS {self.peak_rss_mb:,.0f} MB   ·   largest frame "
            f"{self.max_rows:,} rows",
        ]
        slowest = sorted(self.stages, key=lambda s: s.wall_s, reverse=True)[:3]
        for s in slowest:
            rows = f"{s.rows_out:,} rows" if s.rows_out is not None else "-"
            out.append(f"    {s.wall_s:>7.1f}s  {s.kind:<7} {s.name:<32} {rows}")
        return out
