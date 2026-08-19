from __future__ import annotations

from datetime import datetime
from pathlib import Path


class RunLog:
    """Collects the audit trail for run_log.txt and echoes it to stdout."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []

    def _print(self, text: str) -> None:
        # Windows consoles often run cp1252, which chokes on Vietnamese text;
        # run_log.txt keeps the exact text (utf-8), stdout degrades gracefully.
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode())

    def add(self, text: str = "") -> None:
        self._print(text)
        self.lines.append(text)

    def warn(self, text: str) -> None:
        line = f"WARNING: {text}"
        self._print(line)
        self.lines.append(line)
        self.warnings.append(text)

    def section(self, title: str) -> None:
        self.add()
        self.add("=" * 64)
        self.add(title)
        self.add("=" * 64)

    def write(self, path: Path, *, write_to: Path | None = None) -> None:
        """`write_to` overrides where the bytes land, for the atomic write staged by
        `pipeline.write_artifacts`. `path` stays what the run produced."""
        header = [f"Run started: {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        footer = ["", f"Warnings: {len(self.warnings)}"]
        (write_to or path).write_text(
            "\n".join(header + self.lines + footer) + "\n", encoding="utf-8")
