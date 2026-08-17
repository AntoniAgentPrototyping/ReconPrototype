"""M4 — the service skeleton around the pipeline, and nothing more than that.

`src/` is the reconciliation pipeline. This package is a wrapper: an HTTP API
that enqueues work, a worker that executes it, and a Postgres database that
holds the queue, the run record, the log and the artifact index.

**The direction of dependency is one-way and load-bearing.** `service/` imports
`src/`; `src/` must never import `service/`. That is what makes D24 ("the CLI
stays first-class") a structural fact rather than a promise: delete this
directory and `tools/full_run.py` still produces the month's invoicing workbook.
`tests/service/test_service_is_deletable.py` enforces it.

Two consequences worth stating because they are easy to erode:

* **The worker adds no compute.** It calls `pipeline.run()` and then
  `pipeline.write_artifacts()` — the same two functions, in the same order, that
  the CLI calls. It does not write a workbook itself, so there is no second
  implementation of the deliverable to drift (see service/artifacts.py for why
  write-then-upload beats a second writer).
* **Nothing here is authenticated.** M4 is a skeleton; Entra ID SSO arrives with
  the web app in M5. The API binds 127.0.0.1 by default and must not be exposed
  until then — docs/08-KNOWN-DEFECTS.md records this as an open defect rather
  than a footnote.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
