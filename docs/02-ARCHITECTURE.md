# 02 — Architecture

## Shape

A batch ETL library plus thin drivers, and — since M4 — a **deletable service wrapper** around it. The pipeline itself still has no web framework, no database and no network access at runtime: deterministic Python over Excel files.

```
                    config/settings.yaml  ──┐  (the contract)
                    Lib & VAT rate.xlsb  ──┤
                                           ▼
                              pipeline.run(ctx)  ── returns RunResult, writes nothing
                    ┌──────────────────────┴───────────────────────┐
raw exports ──▶  ingest ──▶ stitch ──▶ classify ──▶ calculate ──▶ tie-out ──▶ finance_template
  (disk)         (I/O)      └──────── pure DataFrame-in / DataFrame-out ────────┘   (Workbook in memory)
                                                                                            │
                              pipeline.write_artifacts(result) ──▶ .xlsx · run_log.txt · run_metrics.json
```

The important structural fact: **file I/O is confined to four modules** — `ingest.py`, `lazada.py`, `config.py`, `masters.py` — plus the single writer, `pipeline.write_artifacts`. Everything between them takes and returns DataFrames with no awareness of paths, sheets or the filesystem. That is what makes the compute layer testable without data, embeddable in a worker, and swappable.

## Module map

| Module | Role | Notes |
|---|---|---|
| `src/ingest.py` | Stage 1. Multi-part reading, header mapping, store-from-filename, aliases, settlement bounds, roster check | The engine boundary. All Excel reading for TikTok/Shopee. |
| `src/stitch.py` | Stage 2. Attributes income lines to true order-creation dates | 31 lines. One group-by and one join. |
| `src/classify.py` | Stage 3. Status rules per platform | TikTok = ported M code; Shopee = derived + verified rules. |
| `src/calculate.py` | Stage 4. The "yellow columns" for TikTok/Shopee | Per-formula provenance in `*_FORMULA_STATUS` dicts. |
| `src/lazada.py` | Lazada's entire vertical | Own reader, classifier and calculation. Column maps hardcoded here, not in YAML. |
| `src/masters.py` | Live `.xlsb` master read, CSV snapshot fallback, drift report | Only 1 line touches pandas. |
| `src/tieout.py` | Coverage + conservation checks against a `SourceReference` captured upstream | Rebuilt in M2 so a check crosses a file boundary and can fail. Shopee's money crossing is still unverified. |
| `src/finance_template.py` | **The only deliverable exporter.** Team invoicing-template shape | Also a second compute layer — see below. |
| `src/master_summary.py` | The **month-end master**: every window of a month by brand and by storefront (M8 Phase 3) | Pure compute + workbook build; returns an unwritten `Workbook`, so `pipeline.write_artifacts` stays the only writer. Reads finance files through `ingest`, the declared boundary, so it needs no I/O grant. |
| `src/config.py` | YAML/CSV config loading | Raw dict access, no schema validation. |
| `src/runlog.py` | The audit log | Duck-typed; see "Substitutable logger". |
| `src/errors.py` | `ReconHardStop` | 6 lines, one exception class, raised in 14 places. |
| `src/pipeline.py` | **The seam.** `RunContext` · `RunResult` · `run()` · `write_artifacts()` | The only writer. See below. |
| `src/metrics.py` | Per-stage timing, row counts, peak RSS | Tags stages `io` / `compute` / `serialize`. |
| `src/export.py` | `exceptions.xlsx` writer | Routed through `write_artifacts` in M2. |

### The service wrapper (M4)

`service/` is a separate top-level package, not a subdirectory of `src/`. That is deliberate: the I/O-boundary lint scopes to `src/**`, and putting a web framework inside it would force exemptions — which is precisely what expires the deferred-engine-port bet ([D25](06-DECISIONS.md#d25)).

| Module | Role | Notes |
|---|---|---|
| `service/config.py` | `ServiceSettings.from_env()` | Deployment facts only. `settings.yaml` stays the *domain* contract. |
| `service/db.py` | Connection pool + migration runner | Numbered `.sql`, recorded by content hash; an edited migration is an error. |
| `service/migrations/001_init.sql` | `jobs` · `runs` · `run_log_lines` · `artifacts` | The header comment is the schema's rationale. |
| `service/models.py` | Row dataclasses, `JobState` | Does **not** redefine `RunStatus` — imports it. |
| `service/repository.py` | **Every SQL statement in the service** | The claim protocol is `claim` / `heartbeat` / `reclaim_expired`, on one screen. |
| `service/runlog.py` | `QueueRunLog` — mirrors the log to Postgres as it happens | Subclasses `RunLog` so `run_log.txt` cannot drift ([D34](06-DECISIONS.md#d34)). |
| `service/artifacts.py` | `ArtifactStore` protocol + `LocalArtifactStore` + `S3ArtifactStore` | Write-then-upload, never a second writer ([D31](06-DECISIONS.md#d31)). `stream()` is the one method M6 added, and it is what stops the download route returning 501 in the target deployment ([D43](06-DECISIONS.md#d43)). |
| `service/worker.py` | claim → run → store → finish | Four calls into `src/`; no compute, no formatting. |
| `service/api.py` | FastAPI: sessions, users, jobs, runs, uploads, windows, config, demo | Sync handlers over a connection pool. Every endpoint names a role, and `tests/service/test_auth.py` walks the router against an explicit route→role table. |

Added in M5:

| Module | Role | Notes |
|---|---|---|
| `service/auth.py` | `Principal` · `Role` · session minting and checking | The seam OIDC substitutes into ([D35](06-DECISIONS.md#d35)). No database import. Since M6 the credential is an opaque session, not a pasted token, and `Role` has three values. |
| `service/repository_m5.py` | SQL for config versions, proposals, uploads, windows, exceptions | Subclasses `IdentityRepository` (which subclasses `Repository`), so callers still hold one object. The token methods are gone. |
| `service/repository_identity.py` | SQL for `users` and `user_sessions` | Every session-liveness condition in one `UPDATE … WHERE`, and the role resolved by **join** — never copied onto the session row, so a demotion bites on the next request. |
| `service/config_store.py` | `ruamel.yaml` round-trip, evidence extraction, diff, git commit, per-window resolution | **Never `PyYAML`** — it discards the comments that are the audit trail. `resolve_for_window` prefers a pin, then the rendered contract, then disk. |
| `service/uploads.py` | Filename checks, the PII strip, `.csv`/`.xls` dispatch, the settlement-date door | Strips using the pipeline's own column map ([D40](06-DECISIONS.md#d40)) and reads through `ingest.read_excel_sheet`, never a copy. **Preserves the file's shape** — band rows, junk rows, multi-sheet income — because `read_parts` applies those to whatever it writes. `check_span` decides whether a file belongs to the window it was addressed to: refuse a non-intersecting window-defining file, warn on the mis-pull shape, never date-check an order export ([D57](06-DECISIONS.md#d57)). |
| `service/objects.py` | `ObjectStore` protocol + `S3Objects` + `LocalDirObjects` | Five operations, no more. `LocalDirObjects` is the single-machine mode, not a test double ([D43](06-DECISIONS.md#d43)). |
| `service/naming.py` | The uniform upload naming scheme | `validate_roundtrip` re-runs the pipeline's own store parser on every generated name ([D44](06-DECISIONS.md#d44)). |
| `service/materialize.py` | Assembles a window's input from its uploads | In `service/`, not `src/`, so `run()` still writes nothing and the I/O lint needs no new grant. Reports which mode it used. |
| `service/config_rows.py` | The eleven config tables, and the two operations on them | Replaced `config_schema.py` + `config_edits.py` in M8/1.6. A table that refuses a new row says why in a sentence and the refusal quotes it; `invalidates_goldens` is read off the row, never inferred from a path. |
| `service/config_import.py` | `config/` → the config tables | Idempotent by truncate-and-load in one transaction; **refuses** an unmodelled key rather than skipping it. Runs once on first boot. |
| `service/config_render.py` | The config tables → `settings.yaml` text | Byte-stable, because `config_versions` is content-addressed. `assert_complete` refuses a render missing a key whose code default is the opposite of its configured value. |
| `service/verification.py` | Did that config change move a cell? | Runs a canary window and compares to a committed golden. Five distinguishable outcomes ([D45](06-DECISIONS.md#d45)). |
| `service/sampledata.py` | The synthetic demo window | In `service/` so an admin can seed it from a browser, on a machine with no client data. |
| `service/passwords.py` | Argon2id, the policy, generated passwords | Parameters are module constants, never environment — a lowered cost would make rehash-on-login *downgrade* strong hashes. |
| `service/ratelimit.py` | Per-username sliding-window throttle | Keyed on username, not IP: every request reaches the api from the BFF's address. |
| `service/exceptions.py` | Exception fingerprints and capping | Identity columns only — M6 hangs dispositions off these. |
| `service/order_index.py` | `--backfill`: index which uploaded file holds which `(store, order_id)` | Identifiers and counts only — **the database may know where every number came from; it may never compute one** ([D58](06-DECISIONS.md#d58)). The expected digest is **checked, never derived**, before any byte is indexed; a NULL digest is skipped and a mismatch refused, so this is the opposite of the [D26](06-DECISIONS.md#d26) trap. Reads a stored object through `uploads.read_source`, the door's own reader. |
| `service/admin.py` | `user create/list/reset-password/disable/enable`, `config pins/versions/unpin` | Solves the bootstrap: the FIRST identity cannot come from the api, because creating one needs an admin credential. `reset-password` is break-glass and is why this CLI cannot be deleted once the admin UI exists. |
| `web/` | Next.js BFF — board, run view, exceptions, config | Its own image; the only published service. |

### The web layer

```
Browser ──httpOnly cookie──▶ [ web ] ──Bearer token, server-side──▶ [ api ] ──▶ [ db ]
   public                                    private network              private
```

The browser never holds the bearer token: it lives in an httpOnly cookie and is attached inside the Next.js server (`web/lib/api.ts`, `import "server-only"`). That is what lets the api have no public address at all — and it is why a front end on a separate host, talking directly to a public api, was rejected ([D41](06-DECISIONS.md#d41)).

The private network is **defence in depth, not the control**. The BFF is a deliberate hole through it, any service in the project can reach the api, and publishing a port is one dashboard click. Authentication is what closes the defect.

## Entry points — one pipeline, two callers

`tools/devrun.py` is the **developer** driver: one window end to end. Users have gone through the browser since M6; this is what regenerates a golden or debugs a hand-staged window. It is ~90 lines of argument parsing over `pipeline.run()` and `pipeline.write_artifacts()`, plus an exit code.

`service/worker.py` is the second caller, added in M4, and it makes exactly the same four calls in the same order:

```
pipeline.build_context(...)  ->  pipeline.run(ctx)  ->  pipeline.log_result(result)  ->  pipeline.write_artifacts(result)
```

All four live in `src/pipeline.py` **because** there are two callers. `build_context`, `EXIT_CODES` and the `RESULT` log section each moved out of `tools/full_run.py` in M4 for one reason: the copy in the worker would have been the one that drifted, and what would have drifted first is the `settings["_vat_sku"]` back-channel, which silently changes numbers ([D28](06-DECISIONS.md#d28)). The moves were proven output-identical — all eight golden windows regenerated with zero refusals.

The direction of dependency is one-way and tested: `service/` imports `src/`, and `tests/service/test_service_is_deletable.py` fails if `src/` or `tools/` ever imports `service/`, or if `service/` imports `tools/`.

The second entry point, `recon.py`, **was deleted in M1**. It was the only caller of the unverified placeholder functions (`calculate.explode_to_sku`, `calculate.compute_sku_columns`, `tieout.run_checks`) and of the superseded `export_platforms.py`. No production number was ever computed by placeholder math, and the proof is that deleting all of it left every golden manifest unchanged ([D19](06-DECISIONS.md#d19)).

Supporting drivers: `tools/build_master_summary.py` (monthly cross-platform master, refuses to ship on a tie failure), `tools/calc_verify*.py` (row-level verification harnesses — the evidence behind every "verified" claim), `tools/stage1_probe.py` (ingest-only diagnostics for a new window), `tools/make_golden.py` + `tools/stage_exports.py` (golden generation and deterministic staging), `tools/metrics_report.py` (the engine-port trigger dashboard), `tools/smoke_test.py` (synthetic end-to-end, needs no real data).

## Three platforms, two shapes

TikTok and Shopee share the orders+income model and therefore the whole `ingest → stitch → classify → calculate` spine. **Lazada is a separate vertical**: a fee-event ledger with no order rows, its own reader, its own classifier (fee-name → bucket), and its own calculation. When changing shared code, Lazada often needs nothing; when changing Lazada, nothing else does.

## The hidden second compute layer

`src/finance_template.py` looks like an exporter and is mostly a calculator. Roughly **500 of its 695 lines are computation**: ~24 grouped aggregations, ~40 scalar reductions, per-brand and per-VAT-rate slicing, and — critically — **the invoice rounding model** (`aveg = round(pre_exact/qty)`, then `pre = aveg*qty`). The actual openpyxl writing is a small `_Tab` class that streams rows into a write-only workbook.

Two consequences:

- Its **frame column order literally is the Excel header row** (emitted via `list(out.columns)`), and `tools/build_master_summary.py` matches those exact strings when reading the output back. The compute→export contract is wide (134 column references across ~36 names) but stable.
- Any rewrite of the compute layer must port this file too. Treating it as "just the writer" underestimates the work substantially.

## Substitutable logger

32 functions take a `log` parameter. `RunLog` is a concrete class, but every annotation in the pipeline is a string (`from __future__ import annotations`) and **nothing runs an isinstance check** — so any object exposing `add()`, `warn()`, `section()` and `write()` is accepted. `tests/recording_log.py` relies on this.

**M4 cashed that in.** `service/runlog.py::QueueRunLog` is the substitution, and it turned out to be ~60 lines rather than 15 — the extra is batching, a failure path that never raises into a run, and the lease heartbeat. It *subclasses* `RunLog` rather than reimplementing the four methods, so `run_log.txt` from a service run is byte-identical to the CLI's ([D34](06-DECISIONS.md#d34)); the substitution being duck-typed is what makes that a free choice rather than a constraint.

## Where a run's output goes

Two writers, one implementation. `pipeline.write_artifacts` is still the only code that writes a deliverable; the worker points it at a per-job scratch directory and then hands the finished files to an `ArtifactStore`:

```
worker:  scratch/job-<id>/<period>/<platform>/{finance_file,exceptions}.xlsx · run_log.txt · run_metrics.json
              │  write_artifacts (the same function the CLI calls)
              ▼  store.put()
         artifacts/<period>/<platform>/run-<id>/<name>          + a row in `artifacts`
CLI:     output/<period>/<platform>/<name>
```

Run-scoped paths, so two runs of one window do not overwrite each other — comparing them is normal and the earlier one is evidence. The stored `bytes_sha256` is **transfer integrity only**; workbook equality is `tests/goldens/cellset.py`'s job, because openpyxl stamps timestamps into every file ([D16](06-DECISIONS.md#d16)).

## Workbook building is separated from disk

`finance_template.build_tiktok/build_shopee/build_lazada` return `(Workbook, checks)` **in memory**; `write_workbook` is the only function that touches disk, and since M1 it is called only from `pipeline.write_artifacts`. Artifacts can therefore be streamed to object storage without a temp file — which is what the M4 worker needs.

## Import hygiene

`src/` modules are pure definitions: no import side effects, no path mutation, no config loading at import, and `src/__init__.py` is empty. All `sys.path` manipulation lives in `tools/`. Two lazy imports (`calculate.py` → `masters.vat_factor_type`) exist to avoid a circular import.

There is one piece of shared mutable state: `settings["_vat_sku"]` is injected by `full_run.build_context` and read inside `calculate.py`, using the config dict as a data channel. Two concurrent runs sharing one settings dict would cross-contaminate VAT rates.

## The I/O boundary is the load-bearing structural property

File I/O is confined to four modules — `ingest.py`, `lazada.py`, `config.py`, `masters.py` — plus `finance_template.write_workbook`. Everything else is frame-in/frame-out. This single property is what makes the pipeline testable without real data, embeddable in a worker, and cheap to move to another compute engine if that ever becomes necessary.

**Nothing enforced it**, so it would have eroded. `tests/test_io_boundary.py` now fails on any file-I/O call outside a per-module grant table, with `file:line`. It also checks the grants for *dead entries* — permission for a module or a call that no longer exists is permission nobody reviews — and asserts at **function** granularity that `run()` and the three platform runners contain no write call at all, which the module-level table cannot express.

## The run seam

**Landed in M1.** `tools/full_run.py` used to interleave compute, disk I/O and tie-out inside each of three platform functions; it is now ~100 lines of argument parsing over:

```
src/pipeline.py
  RunContext   platform · period · input_root · output_root · settings · log · refs
  RunResult    workbook (in memory) · checks · variances · unverified · exceptions
               · frames · metrics · status
  run(ctx) -> RunResult          reads input, WRITES NOTHING
  write_artifacts(result)        the only disk writer
src/metrics.py
  StageMetric  name · kind ("io" | "compute" | "serialize") · wall_s · rows · peak_rss_mb
```

Why it matters here rather than only in the roadmap: the CLI and the future worker are two callers of one function, so [D24](06-DECISIONS.md#d24) (the CLI stays first-class) stops being a promise and becomes a structural fact. `RunStatus` also separates *variance* from *unverified*, which used to share one channel.

`RunResult.findings` is one **ordered** list of `(kind, message)` rather than two lists. That is not stylistic: the original loop interleaved the two kinds store by store, and that order is committed inside `variances.json`'s digest — two lists concatenated would have moved a golden during a refactor required to be output-identical.

## On the compute engine

A polars rewrite was planned and **descheduled** before any polars was written — the data volume didn't justify it and the measured bottleneck is Excel I/O, not compute ([D25](06-DECISIONS.md#d25)). pandas stays. A port is trigger-gated on instrumented thresholds ([10-ROADMAP](10-ROADMAP.md#the-engine-port-is-trigger-gated)); the seam and the boundary lint above are what keep that option cheap.

The openpyxl writer stays regardless of engine: no DataFrame library writes formatted multi-tab workbooks.
