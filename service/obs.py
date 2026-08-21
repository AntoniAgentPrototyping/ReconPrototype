"""Service telemetry (Phase 6 / C5): structured logs for the SERVICE itself.

The per-run log has been excellent since M4 — streamed to the database, polled by
the browser, written as an artifact. What had zero coverage was the service
around it: a worker claiming and finishing jobs, the reclaim sweep, migrations,
retention. `grep "import logging" service/` returned nothing.

One JSON object per line on stdout, which is the integration point every
collector (Docker, Railway, CloudWatch, Loki) already consumes. **This module is
the logging design, not a wrapper**: error *reporting* and *alerting* are the
host's half — a JSON line with `"level": "ERROR"` is what an alert rule matches
on, and building a second alerting system inside the service would be config
nobody wires up.

THE CONTENT RULE, stricter than the run log's: **identifiers, counts and
durations only — never a store name, never a filename, never a figure.** The run
log's store names are an accepted exposure inside Postgres (defect 2.6, behind
authentication); container stdout is a different, wider surface (docker logs,
whatever ships them), and nothing here needs client data to be useful.
"""

from __future__ import annotations

import json
import logging
import sys
import time


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line: ts, level, component, message, plus whatever the
    call site passed via `extra={"data": {...}}`. Keys are flattened into the
    object so a collector can index them without unwrapping."""

    def __init__(self, component: str) -> None:
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        out: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(record.created)),
            "level": record.levelname,
            "component": self.component,
            "logger": record.name,
            "message": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if isinstance(data, dict):
            out.update(data)
        if record.exc_info and record.exc_info[0] is not None:
            out["exception"] = record.exc_info[0].__name__
        return json.dumps(out, ensure_ascii=False, default=str)


def setup_logging(component: str, *, level: int = logging.INFO) -> logging.Logger:
    """Route the root logger (and uvicorn's, which propagate to it) through the
    JSON formatter. Idempotent per process: calling twice replaces the handler
    rather than doubling every line."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter(component))

    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_recon_obs", False):
            root.removeHandler(existing)
    handler._recon_obs = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn attaches its own handlers before the app runs; drop them so its
    # access and error lines come out in the same shape as everything else.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True

    return logging.getLogger(f"recon.{component}")


def event(logger: logging.Logger, message: str, /, level: int = logging.INFO,
          **data) -> None:
    """`event(log, "job_finished", job_id=3, status="ok", wall_s=171.2)` — the
    one-liner that keeps call sites from re-inventing the extra-dict shape."""
    logger.log(level, message, extra={"data": data})
