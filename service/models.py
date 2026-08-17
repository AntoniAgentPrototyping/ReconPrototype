"""Row shapes, as plain dataclasses.

These mirror the tables in migrations/001_init.sql. They are not an ORM: the
repository builds them from `dict` rows and nothing here writes SQL or knows a
connection exists.

`JobState` and the run's status are two different enums on purpose — see the
header comment in the migration. The run's status is not redefined here at all;
it is `src.pipeline.RunStatus`, because a second copy of those four values would
eventually disagree with the pipeline that produces them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.pipeline import RunStatus


class JobState(enum.Enum):
    """Did the worker manage to execute this job?

    Note what is absent: there is no 'failed' state meaning "the numbers didn't
    tie". A run with variances is a job that DONE its work — the disagreement is
    the deliverable, not an error (docs/06-DECISIONS.md#d4). `ERROR` means the
    worker could not execute the job at all.
    """

    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


# Terminal states, i.e. states a sweep must never touch and a re-enqueue may
# legitimately follow.
TERMINAL_JOB_STATES = frozenset({JobState.DONE, JobState.ERROR, JobState.CANCELLED})


class LogKind(enum.Enum):
    LINE = "line"
    WARNING = "warning"
    SECTION = "section"


@dataclass(frozen=True)
class Job:
    id: int
    platform: str
    period: str
    state: JobState
    partial_roster: bool = False
    refs: dict = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 1
    priority: int = 0
    leased_by: str | None = None
    lease_expires_at: datetime | None = None
    requested_by: str | None = None
    idempotency_key: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None
    run_id: int | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Job":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in row.items() if k in known}
        data["state"] = JobState(row["state"])
        data["refs"] = row.get("refs") or {}
        return cls(**data)


@dataclass(frozen=True)
class Run:
    id: int
    job_id: int
    platform: str
    period: str
    status: RunStatus | None = None      # None while in flight — see the migration
    exit_code: int | None = None
    findings: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    wall_s: float | None = None
    io_s: float | None = None
    compute_s: float | None = None
    serialize_s: float | None = None
    peak_rss_mb: float | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    # Which rules this run actually used (M5). `config_was_pinned` distinguishes
    # "read from disk at the time" from "frozen to what an earlier run of this
    # window used" — a re-run that quietly used different rules than the run its
    # invoice came from is the worst failure this system could have.
    config_version_id: int | None = None
    config_was_pinned: bool = False

    # How many expected stores had no file in the window (M6). **None, not 0, when
    # the input came from a directory rather than from uploads** — no roster preview
    # was computed, and 0 would read as "nothing missing". Purely rendered: the
    # control that stops an undeclared incomplete window is still
    # `ingest.check_stores`, and nothing branches on this.
    roster_missing: int | None = None

    @property
    def in_flight(self) -> bool:
        return self.finished_at is None

    @property
    def variances(self) -> list[str]:
        return [m for kind, m in self.findings if kind == "variance"]

    @property
    def unverified(self) -> list[str]:
        return [m for kind, m in self.findings if kind == "unverified"]

    @classmethod
    def from_row(cls, row: dict) -> "Run":
        known = {f for f in cls.__dataclass_fields__}
        data = {k: v for k, v in row.items() if k in known}
        data["status"] = RunStatus(row["status"]) if row.get("status") else None
        # jsonb arrays come back as lists of lists; the findings contract is an
        # ordered sequence of (kind, message) pairs.
        data["findings"] = [tuple(f) for f in (row.get("findings") or [])]
        data["checks"] = list(row.get("checks") or [])
        return cls(**data)


@dataclass(frozen=True)
class LogLine:
    seq: int
    kind: LogKind
    text: str
    at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "LogLine":
        return cls(seq=row["seq"], kind=LogKind(row["kind"]),
                   text=row["text"], at=row.get("at"))


@dataclass(frozen=True)
class Artifact:
    name: str
    uri: str
    bytes: int
    bytes_sha256: str
    run_id: int | None = None
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict) -> "Artifact":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in row.items() if k in known})


def payload(obj: Any) -> Any:
    """Recursively JSON-safe view of a dataclass, for the API layer.

    Enums become their values and datetimes become ISO strings, which is what a
    client wants and what `json` refuses to do on its own.
    """
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [payload(v) for v in obj]
    if hasattr(obj, "__dataclass_fields__"):
        out = {f: payload(getattr(obj, f)) for f in obj.__dataclass_fields__}
        return out
    return obj
