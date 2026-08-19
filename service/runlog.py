"""QueueRunLog — the pipeline's audit log, mirrored into Postgres as it happens.

The pipeline accepts any object exposing `add`, `warn`, `section` and `write`;
nothing runs an isinstance check and every annotation is a string
(docs/02-ARCHITECTURE.md#substitutable-logger). That is the hook this class uses.

**Why it subclasses RunLog instead of reimplementing the four methods.**
`run_log.txt` is an artifact an operator reads and the team keeps. If this class
formatted its own section rules and its own `WARNING: ` prefix, the file a
service run produces would drift from the file a CLI run produces — silently,
one whitespace at a time, and the golden gate covers the workbook rather than
the log. Subclassing means there is exactly one implementation of the text and
this class only decides *where else it goes*.

**The log doubles as the lease heartbeat.** Every flush extends the job's lease,
so liveness is measured by "is this run still saying anything" rather than by a
separate timer thread that would keep a hung run looking healthy.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Protocol, Sequence

from src.runlog import RunLog


class LogSink(Protocol):
    """Where mirrored lines go. Narrow on purpose: a fake in a test is four
    lines, and nothing in this module needs a database to be tested."""

    def append(self, rows: Sequence[tuple[int, str, str]]) -> None: ...


class RepositoryLogSink:
    """Adapts `Repository` to `LogSink`, and beats the lease on every flush."""

    def __init__(self, repo, run_id: int, *, heartbeat: Callable[[], bool] | None = None) -> None:
        self._repo = repo
        self._run_id = run_id
        self._heartbeat = heartbeat

    def append(self, rows: Sequence[tuple[int, str, str]]) -> None:
        self._repo.append_log(self._run_id, rows)
        if self._heartbeat is not None:
            self._heartbeat()


class QueueRunLog(RunLog):
    """A RunLog that also streams to a sink, with producer-assigned sequence.

    `seq` is assigned here rather than by a database sequence so that it is
    gapless: a client polling `?after_seq=N` can tell "nothing new yet" from
    "I lost a line", and a replayed batch is idempotent because the sequence
    numbers come with it.
    """

    def __init__(self, sink: LogSink, *, echo: bool = True,
                 flush_every: int = 25, flush_interval_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__()
        self._sink = sink
        self._echo = echo
        self._flush_every = max(1, int(flush_every))
        self._flush_interval_s = float(flush_interval_s)
        self._clock = clock
        self._buffer: list[tuple[int, str, str]] = []
        self._next_seq = 0
        self._last_flush = clock()
        # Set while section() is running, so the four lines a section header
        # emits are tagged as one unit without this class restating RunLog's
        # formatting. Same trick for warn().
        self._kind = "line"
        self.flushes = 0

    # -- RunLog's contract, extended rather than replaced -------------------

    def _print(self, text: str) -> None:
        if self._echo:
            super()._print(text)

    def add(self, text: str = "") -> None:
        super().add(text)
        self._buffer.append((self._next_seq, self._kind, text))
        self._next_seq += 1
        self._maybe_flush()

    def warn(self, text: str) -> None:
        # RunLog.warn appends to `lines` directly rather than going through
        # add(), so mirror the line it just produced instead of re-deriving the
        # "WARNING: " prefix here — one owner of the format, as above.
        super().warn(text)
        self._buffer.append((self._next_seq, "warning", self.lines[-1]))
        self._next_seq += 1
        self._maybe_flush()

    def section(self, title: str) -> None:
        self._kind, previous = "section", self._kind
        try:
            super().section(title)      # emits 4 lines through self.add
        finally:
            self._kind = previous

    def write(self, path: Path, *, write_to: Path | None = None) -> None:
        """`pipeline.write_artifacts` calls this last, so it is the natural
        final flush: after it returns, the database holds every line the file
        does.

        `write_to` is passed straight through — it is the atomic-write temp path and
        has nothing to do with the database mirror. The signature has to match
        `RunLog.write` or the subclass silently stops being substitutable, which is
        the whole point of subclassing rather than reimplementing (D34)."""
        self.flush()
        super().write(path, write_to=write_to)

    # -- flushing -----------------------------------------------------------

    def _maybe_flush(self) -> None:
        if not self._buffer:
            return
        due = (len(self._buffer) >= self._flush_every
               or (self._clock() - self._last_flush) >= self._flush_interval_s)
        if due:
            self.flush()

    def flush(self) -> None:
        """Push buffered lines. **Never raises.**

        A database hiccup must not cost a finance file. The pipeline is midway
        through producing the month's invoicing workbook and the mirrored log is
        a convenience — the authoritative copy is still `run_log.txt`, written
        by the inherited `write`. So a failed flush keeps its lines buffered,
        records the problem in the local log, and lets the run continue.
        """
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        try:
            self._sink.append(batch)
            self.flushes += 1
            self._last_flush = self._clock()
        except Exception as exc:                                    # noqa: BLE001
            self._buffer = batch + self._buffer
            self._last_flush = self._clock()      # don't hammer a sick database
            # RunLog.warn, not self.warn: the complaint about the mirror must not
            # itself be queued for the mirror.
            RunLog.warn(self, f"run log mirror unavailable, {len(self._buffer)} "
                              f"line(s) buffered: {type(exc).__name__}: {exc}")

    @property
    def pending(self) -> int:
        return len(self._buffer)

    @property
    def next_seq(self) -> int:
        return self._next_seq
