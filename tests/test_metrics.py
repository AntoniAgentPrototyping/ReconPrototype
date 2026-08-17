"""M1 — the instrumentation must produce numbers the port trigger can read.

The failure mode worth guarding is not a crash; it is a metric that silently
reports zero. Peak RSS did exactly that during M1: `GetCurrentProcess()`
returned a truncated pseudo-handle, psapi rejected it, the exception path
returned 0.0, and every run reported "peak RSS 0 MB" while looking healthy.
A trigger defined on a number that is always 0 never fires.
"""

from __future__ import annotations

import time

import pytest

from src.metrics import RunMetrics, peak_rss_mb


def test_peak_rss_is_actually_measured():
    """Not a smoke test — the whole point is that 0.0 is the silent-failure
    value, so anything at or below it means the platform branch is broken."""
    mb = peak_rss_mb()
    assert mb > 0.0, (
        "peak RSS reported 0 MB — the platform branch in src/metrics.py is "
        "failing and returning its fallback. The engine-port memory trigger "
        "would never fire.")
    # A Python process with pandas imported cannot plausibly be under 5 MB;
    # 100 GB means a unit confusion (KiB vs bytes) rather than a real reading.
    assert 5.0 < mb < 100_000.0, f"implausible peak RSS: {mb} MB"


def test_the_three_kinds_are_accounted_separately():
    # Gaps of 60-70ms, not the original 10ms. Windows' default timer
    # granularity is ~15.6ms, so 30/20/10 left the ordering inside the noise
    # and this test failed once under load while goldens were regenerating —
    # a gate that fails at random teaches people to re-run it, which is how a
    # control stops being one. Costs ~0.25s.
    m = RunMetrics()
    with m.stage("read", "io"):
        time.sleep(0.15)
    with m.stage("crunch", "compute"):
        time.sleep(0.02)
    with m.stage("build", "serialize"):
        time.sleep(0.08)

    assert m.io_s > 0 and m.compute_s > 0 and m.serialize_s > 0
    assert m.wall_s == pytest.approx(m.io_s + m.compute_s + m.serialize_s)
    # Ordering, not exact values: timing assertions on a shared box are flaky.
    assert m.io_s > m.serialize_s > m.compute_s


def test_serialize_time_does_not_count_as_engine_addressable():
    """The bug this catches is the one that actually happened: build_workbook
    was tagged `compute`, which pushed the measured share from ~1% to 31% and
    fired the engine-port trigger on work no DataFrame engine can touch.

    serialize belongs in the denominator (it is real wall time) and never in
    the numerator (polars would not speed up openpyxl).
    """
    m = RunMetrics()
    with m.stage("build", "serialize"):
        time.sleep(0.02)

    assert m.serialize_s > 0
    assert m.compute_s == 0.0
    assert m.compute_share == 0.0, (
        "workbook materialization must not read as engine-addressable time")
    assert m.wall_s == pytest.approx(m.serialize_s), "but it IS part of wall time"


def test_compute_share_is_zero_when_nothing_computes():
    m = RunMetrics()
    with m.stage("read", "io"):
        pass
    assert m.compute_share == 0.0


def test_empty_metrics_do_not_divide_by_zero():
    assert RunMetrics().compute_share == 0.0


def test_rows_out_is_evaluated_after_the_body():
    """rows_out is a callable because the frame does not exist until the body
    has run. Passing a value would capture None every time — silently."""
    m = RunMetrics()
    rows = []
    with m.stage("build", "compute", rows_out=lambda: len(rows)):
        rows.extend([1, 2, 3])
    assert m.stages[0].rows_out == 3


def test_a_failing_rows_out_never_breaks_the_run():
    """Bookkeeping must not cost a finance file."""
    m = RunMetrics()
    with m.stage("build", "compute", rows_out=lambda: 1 / 0):
        pass
    assert m.stages[0].rows_out is None
    assert m.stages[0].wall_s >= 0


def test_stage_records_even_when_the_body_raises():
    """A stage that dies is exactly the one you want timing for."""
    m = RunMetrics()
    with pytest.raises(ValueError):
        with m.stage("doomed", "compute"):
            raise ValueError("boom")
    assert [s.name for s in m.stages] == ["doomed"]


def test_summary_lines_are_empty_without_stages():
    assert RunMetrics().summary_lines() == []
