# 10 — Roadmap

> **M6 is complete (2026-08-17).** The system is browser-only for users: username/password sessions, uploads to an object store with a uniform naming scheme, a per-window roster declaration replacing the per-run checkbox, a sectioned config editor whose fields carry their own evidence, an automatic verification run measuring whether a config change moved a cell, and a deterministic three-platform demo window. **No phase moved a committed golden digest.** Defects 2.4 and 2.7 are closed, 2.8 is closed by inspection, and [open question 13](11-OPEN-QUESTIONS.md) is answered. New gaps are named as 2.10 and 2.11 rather than left implicit. See [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md) for what was measured and came back differently than expected.

Where this is going and in what order. Rationale for the sequencing is in [06-DECISIONS](06-DECISIONS.md); the authoritative working plan lives at `~/.claude/plans/want-you-to-explore-tingly-wolf.md`.

## One track, one gate

Earlier revisions of this page described two tracks: a compute-engine migration (pandas → polars) and a full-stack system. **The engine migration was descheduled on 2026-08-12** — see [D25](06-DECISIONS.md#d25). There is one track now: turn a developer-operated CLI into a system internal finance users run themselves, with working controls underneath it.

The gate that orders everything: **controls must work before automation hides them.** Every automation layer removes a human touchpoint, and a green PASS badge in a finance UI would be read as an institutional control and cited in a close sign-off. That gate was cleared in M2 — the tie-out checks [now cross a file boundary and can fail](08-KNOWN-DEFECTS.md#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13) — and its two remaining limits closed on 2026-08-13: **all three platforms now have a measured money crossing**, and golden coverage is 8 windows rather than 3. What is left is roster breadth (2 of 25 TikTok stores, 2 of 17 Shopee) and external ground truth, which no amount of internal checking supplies.

## Milestones

| # | Milestone | Exit gate | Status |
|---|---|---|---|
| **M0** | Falsification harness | Mutation tests pin every known control gap; `raises=AssertionError` on all strict xfails | ✅ done |
| **M0.5** | Workbook differ + goldens | Goldens per platform, regenerable, bit-stable; a mutated input **must** move the manifest; differ self-test green | ✅ done (1 store/platform) |
| **M1** | **Seam + instrumentation** | a) `run()`/`write_artifacts()` extraction is **output-identical**; b) metrics land with manifests unchanged; c) I/O-boundary lint green | ✅ done |
| **M2** | Rebuild the controls | Class A (adds signal, same cells) then Class B (changes cells), delta stated in advance; the six revenue-loss mutations must BREACH | ✅ done |
| **M2.5** | Staging normalizer | Replaces the hand-written monthly script; Lazada schema by sheet content; SHA-256 dedupe catches the double-pull class | ✅ done |
| **M4** | Service skeleton | FastAPI + worker + Postgres; job queue; streaming run log | ✅ done |
| **M5** | Web app v1 | Month board · staging · run view · exception queue · config editor | ✅ done |
| **M6** | **Browser-only for users** | Passwords + sessions · uploads to object storage under a uniform naming scheme · per-window roster declaration · sectioned config editor · deterministic demo window. **No committed golden digest moved** | ✅ done |
| **M7** | Hardening | Runbook, CLI-only path, restore drill | not started |
| **M8** | Production readiness | The register in [14-PRODUCTION-READINESS](14-PRODUCTION-READINESS.md) worked down: config into the database, the month-end master as a deliverable, failure made legible, the UI de-jargoned and translated | in progress — **Phases 1–5 done (2026-08-19)**. Phase 3 landed once July's exports and the master arrived; it tied Lazada exactly and found [defect 2.12](08-KNOWN-DEFECTS.md) on TikTok. That defect is **fixed and switched on** as of 2026-08-20 (`cross_window_order_backfill: apply`), taking July's gap from 4,527,401,608 VND to 1,579,645,766 with no golden cell moved. **Phase 6 (deployment hardening) completed 2026-08-20** — register C3–C13, hosting-side halves named in docs/14 — so **M8 is complete** |
| — | Exception queue depth | Dispositions persist across runs on a fingerprint, not a run. **This was M6's original scope**; the schema was ready long before the work was done | **done (2026-08-21)** — [D61](06-DECISIONS.md#d61): mark reviewed/expected with a mandatory reason, append-only history, annotate-never-hide. Landed with the board's window index (register D2), A4's manual master trigger, and D3's per-store roster declaration + workbook stamp |
| — | Engine port (polars) | **Not scheduled.** Trigger-gated — see below | |

M3 is intentionally unused: the old M3′ became M2, and M4–M7 keep their numbers so existing references stay valid.

> **M6 was re-scoped and this table was not updated.** It read "Exception queue depth" with a blank status until 2026-08-18, while the milestone that shipped was the browser-only cutover. Exception dispositions stayed unbuilt until 2026-08-21, so the row moved rather than being deleted — recorded as [E6](14-PRODUCTION-READINESS.md) and [D1](14-PRODUCTION-READINESS.md). (The re-scope history stays here on purpose; only the status changed.)

## M1 in brief

The three pieces, and why each is worth building independent of any engine question:

**a) The seam.** `tools/full_run.py:91-201` interleaves compute, disk I/O and tie-out in each of three platform functions. M1 extracts `run(ctx) -> RunResult` — which reads input and **writes nothing** — plus `write_artifacts(result)`. The M4 worker needs exactly that split to put artifacts in blob storage while the CLI puts them on disk, with no branch inside the pipeline. `RunStatus` also separates *variance* from *unverified*, which is the prerequisite for the exit-code fix in M2.

**b) Instrumentation.** `src/metrics.py` tags every stage `io`, `compute` or `serialize` and records wall time, row counts and peak RSS (via `GetProcessMemoryInfo` on Windows, `getrusage` on POSIX — the worker runs in a Linux container). Without the split the engine trigger below is unmeasurable, because a run is almost entirely I/O and workbook building, and total time hides the number that matters.

**c) Boundary lint.** File I/O is already confined to `ingest.py`, `lazada.py`, `config.py`, `masters.py` and `finance_template.py`. Nothing enforces it, so it will erode. An AST walk over `src/**/*.py` (~50 lines) is the cheapest item on this roadmap and the one that keeps a future port mechanical instead of archaeological.

M1a is a **pure refactor** — same numbers, same cells, same row order, proven by the golden gate at zero tolerance. Everything that changes output or exit status is deliberately held back to M2 so a refactor bug and a control fix can never be confused.

## The engine port is trigger-gated

Polars was descheduled because the premise didn't survive measurement: the largest month is 427,917 order rows (pandas is comfortable an order of magnitude up), and the documented performance pain was cloud-sync contention on Excel reads, which polars does not touch. Full reasoning in [D25](06-DECISIONS.md#d25).

Port when a measurement says so. Any one of:

| Signal | Threshold | Measured (May 2026, 3 windows) |
|---|---|---|
| Peak RSS | > 50% of the worker container limit | **832 MB** — 41% of a 4 GB container |
| Compute share | `compute_s` > 25% of wall time | **1.3% – 3.8%** |
| Rows per window | > 2M | **174,425** |

**Nothing is close.** `tools/metrics_report.py --month <YYYY-MM> [--container-mb N]` is the dashboard.

### What the instrumentation actually found

Three categories, not two — and the third one decided the question:

| Window | wall | io | serialize | compute |
|---|---|---|---|---|
| `2026-05_w1` TikTok | 120.7s | 79.5s | 38.7s | **2.5s** |
| `2026-05_s1` Shopee | 171.4s | 139.5s | 29.7s | **2.3s** |
| `2026-05_l1` Lazada | 0.5s | 0.4s | 0.1s | **0.0s** |

`serialize` is openpyxl building the workbook cell by cell. It is **engine-independent** — no DataFrame library would change it — so it sits in the denominator of `compute_share` but never the numerator. The first version of the instrumentation lumped it into `compute`, which reported 31% and *fired the trigger*; tagging it honestly gives ~2%.

So the descheduling decision now rests on measurement rather than argument: **a 5× faster compute engine would save about 2 seconds of a 120-second run.** The real costs are Excel reading (66%) and workbook materialization (32%), and the second of those is the more interesting optimization target — it is 30–39s of pure openpyxl.

**Reach for the cheaper fix first if the signal is memory.** pandas 2.x supports `string[pyarrow]` and `Categorical`; store names, SKU IDs and fee names are very low-cardinality, so a dtype map at the ingest boundary captures much of Arrow's memory win with no port. It is not risk-free — `object` uses `np.nan` where `string[pyarrow]` uses `pd.NA`, and propagation semantics differ — so it is a Class B change gated on the golden regression.

## Immediate to-do

**M5 is complete (2026-08-14). M6 (exception queue depth) is next.**

### M5 — web app v1

All five surfaces, plus the two defects that had to close first. In order of how much they matter:

**Authentication, and it fails closed** ([D35](06-DECISIONS.md#d35), [D36](06-DECISIONS.md#d36)). Every endpoint names a role. Entra ID SSO is the destination and is blocked on a tenant app registration needing directory permissions a developer does not have, so this ships **bearer tokens** with the seam already Entra-shaped: role strings are Entra's own, and the substitution changes who vouches for a role rather than what a role means. The api now **refuses to start** on a routable address with auth off. `requested_by` comes from the token, so "who asked for this settlement run" stopped being a caller-supplied claim.

**Period-versioned config** ([D37](06-DECISIONS.md#d37)) — defect 2.5. A window is pinned to the config its first successful run used, so an August rate change cannot alter a re-run of May. The full file is stored, comments and all, because the comments are the evidence.

**A config editor that writes** ([D38](06-DECISIONS.md#d38), [D39](06-DECISIONS.md#d39)). One path, one value, one stated reason → a one-line diff → approve → apply and commit. `ruamel.yaml` round-trips the real 300-line file **byte-identically**, which is the property the whole feature rests on. Who may approve is a deployment setting, not a decision this repo makes.

**The upload boundary** ([D40](06-DECISIONS.md#d40)) — defect 2.3. PII is stripped using the pipeline's own column map, so there is no second list to go stale, and a byte-identical re-upload is refused as the double-pull class. Gated by sanitizing a real window and matching the committed golden cell for cell.

**The web app** ([D41](06-DECISIONS.md#d41)) — Next.js in a container, BFF pattern, only it published. Month board, run view with a live polled log, exception queue, config editor. 27 npm packages, no UI framework.

**Defect 2.2 closed as a side effect:** Docker arrived on the development machine, so the container images were built and the full stack run for the first time. Building it immediately found a real error the file had accumulated while unbuilt.

**Left open, all named in [08-KNOWN-DEFECTS Part 1b](08-KNOWN-DEFECTS.md#part-1b--open-gaps-in-the-m4-service-new-2026-08-13):** SSO itself (2.1 residual), object storage (2.4), the approval-policy question (2.7), and — the honest one — **the web UI has never been opened in a browser** (2.8). It type-checks and its container serves; nothing has clicked a button.

### What M6 should do first

**Exception dispositions on a fingerprint, not a run.** M5 built the identity deliberately: `run_exceptions.fingerprint` is stable across runs and derived from identity columns only, so "this unmatched order has recurred for six weeks" is already a query. What is missing is the decision — mark it reviewed, mark it expected, and have that survive the next run. That is the whole of M6, and the schema is ready for it.

The other thing worth doing early is **opening the web app and using it for a real month**, because 2.8 is the largest unverified surface in the system.

### M4 — the service skeleton (complete 2026-08-13)

`service/` is a wrapper: FastAPI, a worker, and Postgres holding the queue, the run record, the log and the artifact index. What it buys is that a run becomes a thing you can request, watch and fetch rather than a command somebody types.

**The gate it had to clear** was the one this project cares about — did routing a run through a queue, a substituted logger and an artifact store move a cell? No: the service's `2026-05_l1` workbook matches the committed golden digest across all 12 tabs and 2,193 cells, at zero tolerance ([07-VERIFICATION](07-VERIFICATION.md#the-m4-service-gate)). And the three helpers that moved into `src/pipeline.py` to make the worker possible (`build_context`, `EXIT_CODES`, the `RESULT` section) were proven output-identical by regenerating **all eight** golden windows with zero refusals.

The design decisions carried out of the target architecture below, and what each cost:

- **Queue in Postgres** ([D29](06-DECISIONS.md#d29)) — `FOR UPDATE SKIP LOCKED`, hand-written SQL, no ORM and no Alembic. Verified against a real PostgreSQL 17.10: eight threads racing on five jobs claim each exactly once.
- **Job state and run status are separate axes** ([D30](06-DECISIONS.md#d30)). A run that hard-stops on bad input is a job that executed *perfectly*, and `max_attempts` defaults to 1 so nothing retries a settlement run automatically.
- **Polling before streaming** ([D32](06-DECISIONS.md#d32)) — gapless producer-assigned `seq`, `?after_seq=N`, and SSE later with no schema change. The genuinely streamed part is the producer: lines reach Postgres mid-run, and the flush doubles as the lease heartbeat.
- **Write-then-upload, never a second writer** ([D31](06-DECISIONS.md#d31)). The worker calls the same `write_artifacts` the CLI calls.
- **The wrapper is deletable, and that is a test** ([D28](06-DECISIONS.md#d28)) — three import directions, plus one test that denies `import service` at the interpreter level and runs the pipeline anyway.
- **One active job per window** ([D33](06-DECISIONS.md#d33)), enforced by a partial unique index. A queue with a button is one impatient double-click away from double-invoicing.

**Two other things fell out of it.** `tools/full_run.py` gained `--partial-roster`, so a genuine single-store production run no longer needs a config edit — the named sharp edge from M2.5. And defect [1.9](08-KNOWN-DEFECTS.md#19-shared-mutable-state-via-the-config-dict--fixed-2026-08-19) (the `_vat_sku` back-channel) was *contained* here: a worker is the first thing that runs two windows in one process, so a fresh context per job is pinned by a test. It is **fixed** as of 2026-08-19 — that channel and a second, undocumented one are gone, replaced by `RunContext` fields and an explicit parameter. This line said "contained" until 2026-08-20.

**Left deliberately unfinished**, all named in [08-KNOWN-DEFECTS Part 1b](08-KNOWN-DEFECTS.md#part-1b--open-gaps-in-the-m4-service-new-2026-08-13): no authentication (2.1), container images that have never been built (2.2), no upload/staging endpoint so the manual-download step survives (2.3), local-filesystem artifacts only (2.4), and no period-versioned config or config audit table (2.5).

### What M5 must do first

Not a screen. **Authentication** — because M4 built the thing the gate at the top of this page warns about: a service that will show a status in a browser. Right now anything that can reach the port can queue a settlement run and read every store's revenue. The order for M5 is Entra ID SSO, then the upload/staging boundary (which is also the PII-stripping boundary), then period-versioned config, then the screens.

### M2.5 — the staging normalizer

`tools/stage_exports.py` replaces `tools/stage_july.ps1`, which is deleted. The old script hardcoded one developer's absolute paths and a hand-maintained folder-name → window table, was rewritten every month, and validated nothing.

**The window is now derived from the exports' own settlement dates** — grouped, overlapping groups merged, sorted chronologically, indexed. That reproduces the labels a human assigned by hand for all eight staged windows, which is the acceptance test. It plans by default and copies only with `--apply`, and refuses on unclassifiable/unreadable files, duplicate content (the double-pull class, by SHA-256), an export whose range starts before its siblings' (the mis-pull shape), and data-less exports. Every staged window gets a `staging.json` provenance record. Detail in [04-DATA-FLOW](04-DATA-FLOW.md#staging).

Three bugs it found in itself while being built, each written up in [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md): the tool had been unable to find its own source directory since M1, it silently staged nothing from nested folders, and its first dedupe pass flagged every file as its own duplicate because the raw dump lives inside `input/`.

**Left open deliberately:** order-ID-level overlap detection between windows. The date-range check catches the observed failure shape; ID-level comparison remains the ad-hoc analysis that found the July double-pull.

### Earlier items, all now closed

1. ~~**Close the Shopee money crossing**~~ — **done 2026-08-13**, once the team's June consolidated file arrived. Their `Net revenue` formula reproduces to **0.000000 VND on all 82,714 rows**, and rearranged it gives a pure order-file-vs-income-file crossing that ties at **0.00 VND on 17.5B across 80,239 May orders**, with refund orders held out as a named class. Seven revenue-loss mutations must BREACH. Full detail in [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md#shopees-money-crossing--closed-2026-08-13). **Every platform now has a working money crossing.**
2. ~~**Broaden golden coverage**~~ — **done 2026-08-13**, once the raw exports arrived. **3 windows → 8**, and one store per platform → **two on each platform's primary window**. Detail and what it bought is in [07-VERIFICATION](07-VERIFICATION.md#current-gate-status). Still `partial_roster: true` on TikTok (2 of 25) and Shopee (2 of 17) — a full roster needs the remaining stores' exports. One Lazada week (`_l5`, settled 05-25..31) could not be staged: **its export is encrypted by a Microsoft sensitivity label** — recorded as "password-protected" until 2026-08-19, which pointed at a fix (ask for the password) that does not exist.
3. ~~**The four remaining pinned defects**~~ — **done 2026-08-13.** [1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13) silent VAT default, [1.5](08-KNOWN-DEFECTS.md#15-joins-key-on-order_id-alone--fixed-m25-2026-08-13) `order_id` fan-out ×2, [1.6](08-KNOWN-DEFECTS.md#16-silent-numeric-and-date-coercion--fixed-m25-2026-08-13-numeric-date-part-still-open) silent coercion. See below.

### Completed 2026-08-13 — the last of the pinned defects

The strict-xfail list is **empty for the first time since M0**: `91 passed, 3 skipped, 0 xfailed`. Each fix was a pair (fix → XPASS as the evidence → marker), each stated its expected golden delta before running the gate, and each prediction held exactly.

- **1.4 silent VAT default** — `vat_factor_for` returns NaN where the master says nothing; `resolve_vat_factors` applies the team's default fall-through *and counts it*. Numbers unchanged, all three manifests byte-identical. **It also measured something worse than the defect as written: the VAT master covers 0 of 224 TikTok, 0 of 461 Shopee and 0 of 92 Lazada SKUs.** The per-SKU exception mechanism is inert on live data, which makes [open question 9](11-OPEN-QUESTIONS.md) considerably more urgent than "what happens when a new SKU trades".
- **1.5 `order_id` fan-out** — stitch, both explodes and the four per-order transforms key on `(store, order_id)`. Zero-delta *measured* (0 colliding IDs, identical merge row counts, no borrowed dates), manifests unchanged. `tieout.py`'s order-id keying is a named residual.
- **1.6 silent coercion** — blank and accounting-dash cells parse to 0.0; only genuinely unparseable values stay NaN, and those now hard-stop with the column named (`numeric_coercion: warn` to override). This one was live: a Shopee income column feeding the discount allocation carries the accounting dash in 46,972 of 83,134 rows and was being silently zeroed every run — at the right value, through the wrong path. Workbook digests unchanged; `fingerprint_digest` re-baselined on TikTok and Shopee for null counts only, with the reason recorded in the manifest.

### Completed in M2

Class A — added signal, moved no cells (all three workbook digests, fingerprints, row counts and variance digests **unchanged**):

- **The tie-out checks were rebuilt so they can fail** ([defect 1.1](08-KNOWN-DEFECTS.md#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13)) — against a `SourceReference` captured from the income export, checked against the SKU frame rebuilt from the order export. All six revenue-loss mutations now BREACH; on real data TikTok conservation ties to **0.00 VND on 12.8B**.
- **The result is consumed**, all three platforms run checks, and Shopee's roster check was armed — it fired immediately.
- **Exit codes off `RunStatus`** (0/1/2/3), so "not checked" stops reading as failure.
- **`exceptions.xlsx` is written** — it had been computed and dropped every run since the beginning.

Class B — one change, delta stated in advance and confirmed exactly:

- **NFC header normalization at ingest** ([defect 1.2](08-KNOWN-DEFECTS.md#12-vietnamese-headers-arrive-in-unicode-nfd--fixed-m2-2026-08-13)). Predicted: workbook unchanged, `fingerprint_digest` moves. Confirmed. Tracing the consumers first also **disproved the documented claim that this defect overstated revenue** — `shopee_subsidy` feeds no money formula.

### Completed in M1

- Unwound `tools/parity/` → `tools/`, `tests/parity/` → `tests/goldens/`; dropped `oracle_rev`; deleted the polars venv. `cellset.py`, `diff.py` and the manifests were **repurposed** rather than deleted — they close the "no regression tests over settlement bounds and the template exporter" gap in [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md#durability-gaps-that-were-predicted).
- Added `--rebaseline --reason` so moving a baseline is a reviewable act, with `tests/goldens/test_rebaseline_guard.py` proving the refusal works.
- **The old M0.6 landed too:** deleted the unverified placeholder path (`calculate.explode_to_sku` / `compute_sku_columns`, `PLACEHOLDER_FORMULAS`, `tieout.run_checks`, `recon.py`, `src/export_platforms.py`; kept `export.write_exceptions_file`). **All three manifests were unchanged by the deletion — the proof the path was unreachable from production** ([D19](06-DECISIONS.md#d19)).
- Rewrote `tools/smoke_test.py`: it used to drive the deleted `recon.py`, so it proved the machine could run code production never called. It now runs `pipeline.run()` on a synthetic Lazada window.

## Target full-stack architecture

```
  Browser ── Entra ID SSO
     │
  [ web ]      Next.js App Router · BFF proxy
     │ REST (+ streaming later)
  [ api ]      FastAPI · validates · enqueues · serves artifacts
     │
  [ db  ]      Postgres · jobs · runs · log lines · exceptions · config audit
     │
  [worker]     Python · calls pipeline.run() unchanged · one image shared with api
     │
  [store]      Blob/object storage · xlsx artifacts + staged inputs
```

Selected design decisions carried forward:

**Every box now exists.** `api`, `db` and `worker` arrived in M4, `web` in M5, `store` in M6. `deploy/Dockerfile` and `deploy/docker-compose.yml` were built and the whole stack brought up and exercised end to end on Docker 29.7.2, 2026-08-17 — defects [2.2](08-KNOWN-DEFECTS.md#22-deploydockerfile-and-deploydocker-composeyml-have-never-been-built--fixed-m5-2026-08-14) and [2.4](08-KNOWN-DEFECTS.md#24-artifacts-are-local-filesystem-only--fixed-m6-2026-08-17) are closed. Sign-in is passwords rather than Entra ID, which remains blocked on a tenant app registration.

> *This paragraph asserted the opposite until 2026-08-18 — that `web` and `store` did not exist and the compose file had never been built. It was stale by two milestones, in the page an operator reads for orientation. Recorded as [E6](14-PRODUCTION-READINESS.md) rather than quietly corrected.*

- **Job queue in Postgres** (`FOR UPDATE SKIP LOCKED`), not Redis/Celery — ~14 jobs a month does not justify a second stateful service, and the jobs table doubles as the audit record finance will ask for. **Built in M4** and verified against a real server ([D29](06-DECISIONS.md#d29)).
- **Polling before streaming.** Log lines carry a monotonic sequence so `?after_seq=N` serves polling now and server-sent events later with no schema change. Streaming dies silently through corporate proxies and is miserable to debug at month-end. **Built in M4** ([D32](06-DECISIONS.md#d32)).
- **Config is rows in the database; the YAML is rendered from them.** This bullet said "config stays git-backed YAML" long after M8/1.2 reversed [D2](06-DECISIONS.md#d2) — the reversal is argued in migration `007`'s header (evidence became a *column*, the rendered file still exists, month-end does not newly depend on Postgres). The audit record is the `config_versions` row, decided explicitly in [D60](06-DECISIONS.md#d60); the git commit happens only in a developer checkout and is a convenience, not a control.
- **Period-versioned config.** Changing a rate in August must not change what a re-run of May produces.
- **PII stripped at the upload boundary**; raw uploads on short retention.
- **The CLI stays first-class** ([D24](06-DECISIONS.md#d24)) — and the M1 seam is now how that is enforced rather than a promise.
- **Exception dispositions live on a fingerprint**, not a run, so a decision survives re-runs.

## Longer term

- **Automated platform API pulls**, replacing manual downloads — removes the mis-pull class of error entirely. Requires seller-account owners to register developer apps, which is an access task and likely the long pole. Needs a read-*write* token store with concurrency control, because all three platforms use rotating refresh tokens.
- **A unified transaction store**, append-only with run lineage — solves cross-period stitching, gives reporting a real source, and makes "why did this row get this number" answerable. This, not the engine, is the change that would make data volume a real design input.

  *Its first increment exists as of 2026-08-19.* `upload_order_index` (migration `015`) records which uploaded file holds which `(store, order_id)` — the "where did this come from" half, added for defect 2.12 rather than for this. It is a deliberate model for the rest: **the database may know where every number came from; it may never compute one** ([D58](06-DECISIONS.md#d58)). A transaction store that also *computes* would be a second, unverified implementation of the money math, which is [D31](06-DECISIONS.md#d31) and would discard the row-verified provenance that is this project's value. Extend it with identifiers and lineage; leave the arithmetic in `src/`.
- **Automated D365 posting.** Highest-risk item: the pipeline currently produces an *invoicing worksheet*, not a GL journal, and the account/dimension mapping does not exist yet. Prerequisites are idempotency keys, a posting-state record written *before* the call, dry-run diffing, a reversal path, and a human release gate.
- **External ground truth** — tying to bank settlement or platform statements of account. Arguably the highest-value item on this list and currently in nobody's plan ([07-VERIFICATION](07-VERIFICATION.md#what-verification-does-not-cover--honest-limits)).

## Where AI does and does not belong

A stakeholder proposal placed AI agents on the calculation path (classification, invoice calculation, reconciliation, D365 posting). The evidence argues against all four: classification is a four-predicate ladder verified to exact match, calculation is ~25 branchless arithmetic expressions, tie-outs are `abs(diff) ≤ tolerance` against tolerances as tight as 10 VND, and posting is an integration rather than an AI problem. Non-determinism on the money path is disqualifying — a revenue figure that cannot be reproduced cannot be defended to an auditor.

Where it does pay is **absorbing change**, which is the genuine recurring cost ([12-CHANGE-HISTORY](12-CHANGE-HISTORY.md) logs 13 format changes in two months): proposing column-map diffs on schema drift, proposing fee-name buckets, and triaging the exception report into prose. All recommend-only, under the boundary **AI recommends, deterministic code executes, a human approves** — with the alias case as the standing example of why evidence must decide rather than similarity ([D7](06-DECISIONS.md#d7)).

The full argument, written for stakeholders, is in `ARCHITECTURE_POSITION.md`.

## Standing risks

- **Single maintainer.** The runbook and the CLI-only path are the mitigation, not a nice-to-have.
- **The team-owned `.xlsb` master** is an unowned runtime dependency; how a server obtains it is unresolved.
- **Production booking is still unauthorised** and the protocol is undefined ([11-OPEN-QUESTIONS](11-OPEN-QUESTIONS.md)). The `PLACEHOLDER_FORMULAS` flag that used to advertise this was removed in M1 ([D10](06-DECISIONS.md#d10)) — the gate is procedural now, with nothing in the code to represent it.
- **The per-SKU VAT exception mechanism matches nothing on live data** (0% master coverage on all three platforms — [defect 1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13)). Every run now reports it; nobody has yet decided what should happen when a non-default-rate SKU actually trades.
- **Monthly format drift is a certainty, not a risk.** It has happened every month tested.
- **Hard-stop posture becomes a close blocker** at higher platform counts without quarantine semantics.
- **Deferring the engine port is reversible only while the seam holds.** The boundary lint is what keeps it reversible; exempting or deleting it expires the bet ([D25](06-DECISIONS.md#d25)).
