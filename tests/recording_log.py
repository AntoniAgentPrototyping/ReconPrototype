"""A duck-typed stand-in for src.runlog.RunLog.

Stdlib only, deliberately. The target state removes pandas from the runtime
dependency set, so the suite has to be collectable in a pandas-free
environment; keeping this separate from the frame builders in helpers.py is
what makes that possible.

This is not merely a convenience. `RunLog` is a concrete class, but every
annotation in the pipeline is a string (`from __future__ import annotations`)
and nothing runs an isinstance check — so any object with these four methods is
accepted. The plan's approach to streaming run progress to a web UI depends on
that being true, and `test_runlog_is_duck_typed` pins it.
"""

from __future__ import annotations

from pathlib import Path


class RecordingLog:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.sections: list[str] = []

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)
        self.lines.append(f"WARNING: {text}")

    def section(self, title: str) -> None:
        self.sections.append(title)
        self.lines.append(f"== {title} ==")

    def write(self, path: Path) -> None:  # pragma: no cover - unused in tests
        Path(path).write_text("\n".join(self.lines), encoding="utf-8")

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def mentions(self, needle: str) -> bool:
        return needle.lower() in self.text.lower()
