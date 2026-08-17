"""QueueRunLog — no database required.

The sink is a Protocol with one method, so these tests use a four-line fake and
run on any machine. What they pin:

* `run_log.txt` from a service run is the same file a CLI run produces. That is
  the reason this class subclasses RunLog instead of reimplementing it, and the
  reason is only worth anything if something checks it.
* `seq` is gapless from 0, so `?after_seq=N` can distinguish "nothing new" from
  "I lost a line".
* a database outage costs log lines, never the finance file.
"""

from __future__ import annotations

import pytest

from service.runlog import QueueRunLog
from src.runlog import RunLog


class FakeSink:
    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str]] = []
        self.batches = 0

    def append(self, rows) -> None:
        self.rows.extend(rows)
        self.batches += 1


class BrokenSink:
    def __init__(self, *, fail_times: int = 99) -> None:
        self.rows: list[tuple[int, str, str]] = []
        self.calls = 0
        self.fail_times = fail_times

    def append(self, rows) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("database is having a moment")
        self.rows.extend(rows)


def drive(log) -> None:
    """The same sequence of calls the pipeline makes, in the same shapes."""
    log.section("FULL RUN lazada 2026-05_l1")
    log.add("  read_ledger: 1,965 rows")
    log.warn("VAT master coverage: 0 of 92 SKUs matched")
    log.add()
    log.section("TIE-OUT")
    log.add("  Check revenue conservation: PASS")


def test_the_log_file_is_identical_to_a_cli_run(tmp_path):
    """A service run and a CLI run must leave the same run_log.txt.

    Only the timestamp header differs, and only because it is a timestamp — so
    compare the body. If this ever fails, the mirrored log has started formatting
    its own text and the two copies have begun to drift, one whitespace at a time.
    """
    plain, mirrored = RunLog(), QueueRunLog(FakeSink(), echo=False)
    drive(plain)
    drive(mirrored)

    assert mirrored.lines == plain.lines
    assert mirrored.warnings == plain.warnings

    a, b = tmp_path / "cli.txt", tmp_path / "svc.txt"
    plain.write(a)
    mirrored.write(b)
    body = lambda p: p.read_text(encoding="utf-8").split("\n")[1:]   # noqa: E731
    assert body(a) == body(b)


def test_seq_is_gapless_and_starts_at_zero():
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)
    drive(log)
    log.flush()

    seqs = [seq for seq, _, _ in sink.rows]
    assert seqs == list(range(len(seqs)))
    assert log.next_seq == len(seqs)


def test_every_emitted_line_reaches_the_sink():
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)
    drive(log)
    log.flush()
    assert [text for _, _, text in sink.rows] == log.lines


def test_section_lines_are_tagged_as_a_unit():
    """A section is four lines — a blank, a rule, the title, a rule — and a UI
    wants to render them as one header. Tagging happens without this class
    restating RunLog's formatting."""
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)
    log.section("TIE-OUT")

    kinds = [kind for _, kind, _ in sink.rows]
    assert kinds == ["section"] * 4
    assert [text for _, _, text in sink.rows][2] == "TIE-OUT"


def test_a_warning_is_tagged_and_keeps_the_pipelines_own_prefix():
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)
    log.warn("net_revenue had 3 unparseable values")

    assert sink.rows == [(0, "warning", "WARNING: net_revenue had 3 unparseable values")]
    assert log.warnings == ["net_revenue had 3 unparseable values"]


def test_ordinary_lines_are_tagged_line():
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)
    log.add("  masters: live master matches the CSV snapshots exactly")
    assert [kind for _, kind, _ in sink.rows] == ["line"]


def test_lines_flush_in_batches_rather_than_one_round_trip_each():
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=5, flush_interval_s=1e9)
    for i in range(12):
        log.add(f"line {i}")

    assert sink.batches == 2 and len(sink.rows) == 10
    assert log.pending == 2
    log.flush()
    assert len(sink.rows) == 12


def test_time_triggers_a_flush_for_a_quiet_run():
    """A run whose log lands only at the end is a batch job with extra steps, so
    the interval matters as much as the batch size."""
    clock = iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0])
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1000, flush_interval_s=1.0,
                      clock=lambda: next(clock))
    log.add("first")
    assert sink.rows == [], "not due yet"
    log.add("second")
    assert len(sink.rows) == 2, "the interval elapsed, so both lines went"


def test_write_flushes_whatever_is_left(tmp_path):
    sink = FakeSink()
    log = QueueRunLog(sink, echo=False, flush_every=1000, flush_interval_s=1e9)
    log.add("dangling")
    assert log.pending == 1

    log.write(tmp_path / "run_log.txt")
    assert log.pending == 0 and len(sink.rows) == 1


def test_a_dead_database_costs_log_lines_not_the_run(tmp_path):
    """The pipeline is midway through producing the month's invoicing workbook.
    A failed mirror must not raise into it — the authoritative log is still the
    file on disk."""
    sink = BrokenSink()
    log = QueueRunLog(sink, echo=False, flush_every=1)

    drive(log)                                       # must not raise
    assert log.pending >= 6, "the unsent lines stay buffered"
    assert any("run log mirror unavailable" in w for w in log.warnings)

    path = tmp_path / "run_log.txt"
    log.write(path)
    assert "read_ledger: 1,965 rows" in path.read_text(encoding="utf-8")


def test_buffered_lines_are_sent_once_the_database_returns():
    sink = BrokenSink(fail_times=1)
    log = QueueRunLog(sink, echo=False, flush_every=1000, flush_interval_s=1e9)
    log.add("a")
    log.add("b")
    log.flush()                                      # fails, keeps both
    assert log.pending == 2

    log.flush()                                      # succeeds
    assert [text for _, _, text in sink.rows] == ["a", "b"]
    assert log.pending == 0


def test_replayed_lines_keep_their_original_seq():
    """Recovery must not renumber: the sequence numbers travel with the batch,
    which is what makes the insert idempotent on the database side."""
    sink = BrokenSink(fail_times=1)
    log = QueueRunLog(sink, echo=False, flush_every=1000, flush_interval_s=1e9)
    log.add("a")
    log.flush()
    log.add("b")
    log.flush()
    assert [seq for seq, _, _ in sink.rows] == [0, 1]


def test_the_pipeline_accepts_it_without_an_isinstance_check(tmp_path, monkeypatch):
    """The substitution this whole class depends on: `RunContext.log` is
    duck-typed and every annotation is a string
    (docs/02-ARCHITECTURE.md#substitutable-logger)."""
    pytest.importorskip("pandas")
    from src import pipeline

    log = QueueRunLog(FakeSink(), echo=False)
    ctx = pipeline.RunContext(
        platform="lazada", period="2026-05_l1",
        input_root=tmp_path / "input", output_root=tmp_path / "out",
        config_dir=tmp_path / "config", settings={}, log=log)

    # No input exists, so this hard-stops — which is the point: it got far enough
    # to write to the substituted logger before failing.
    result = pipeline.run(ctx)
    assert result.status is pipeline.RunStatus.HARD_STOP
    assert log.lines, "the substituted logger received no output"
