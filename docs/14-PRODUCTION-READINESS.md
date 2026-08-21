# 14 — Production Readiness

What stands between the system as built and the finance team using it unattended. Every item was verified in the code on **2026-08-18**; none is speculative. This page is the working register for that programme — [10-ROADMAP](10-ROADMAP.md) says where the project is going, [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md) records defects in the money math, and this records what makes the system unusable *as a product* even when the money math is right.

Status key: `OPEN` · `IN PROGRESS (phase)` · `FIXED (phase, date)` · `ACCEPTED` (understood, deliberately not fixed).

Severity:

| | Meaning |
|---|---|
| **A** | Produces a wrong or missing number, or loses evidence — and does so quietly |
| **B** | A user gets stuck, confused, or misled |
| **C** | Breaks when the system is deployed for real |
| **D** | A hole in the workflow the team actually has |
| **E** | Process and governance |

**The gap is category, not quality.** Fail-closed auth, argon2 with the downgrade trap reasoned about, session liveness decided in one SQL statement, `FOR UPDATE SKIP LOCKED` verified against a real server, an index that prevents double-invoicing, config pinned per window, PII stripped with the pipeline's own allowlist and gated by a cell-for-cell golden test — the engineering underneath this register is careful. Almost everything reliability-shaped simply lives in **M7, which has not started**.

---

## The three that matter most

Read these before the tables. Each defeats a control the system advertises as working.

**A1 — a config change made in the browser never reached the process that computes the money.** ✅ **Fixed in Phase 1.5 (2026-08-18).** The config editor is M6's flagship feature and in the containerised deployment it did not work: `config/` is baked into the image (`deploy/Dockerfile:38`) and no config volume is mounted, so applying a proposal wrote `settings.yaml` into the *api* container's writable layer while the worker resolved an unpinned window from its own untouched copy. A VAT change approved through the UI, showing a green verification badge, did not change what got calculated — and was lost on the next restart.

The fix is the config migration: an unpinned window now resolves to a contract **rendered from the database**, which both containers share. `config/settings.yaml` is the seed and the CLI's input, not the live source of truth. Proven by re-running all eight golden windows under rendered config: **8 matched, 0 moved**.

**A3 — every browser-triggered run is permanently UNVERIFIED.** Comparing against the team's own totals is this system's only external correctness control. `POST /jobs` accepts `refs` and the CLI has `--refs`, but **no screen collects them** — `grep refs web/` returns nothing. So `_tie` emits one `"{store}: no team reference found"` per store (`src/pipeline.py:319`) and the run lands on `UNVERIFIED` with a long list of alarming lines. The code comment at `src/pipeline.py:576-580` predicted this exact outcome: *"an operator sees a long list of scary text for a run that was simply never compared … which is how an operator learns to ignore a list."* M6 made the system browser-only without giving the browser the input that turns that list off.

**A9 — a missing config key silently drops revenue.** ✅ **Fixed 2026-08-21.** `settings.get("dedupe_rows", True)` (`src/ingest.py:480`) defaulted to the *opposite* of the configured value (`config/settings.yaml:197`), and the comment above it records why that matters: byte-identical order lines are legitimate — duplicated gift SKUs, where the team's quantity of 2 became 1 under deduping. `drop_unmapped_columns` had the same inversion in the PII direction until M8/2.4. "Key absent" is not a safe state anywhere in this config.

The sentence above turned out to understate it. Planning the fix found a **second live inversion** the register never named — `cross_window_order_backfill` defaulting to `"off"` against a contract that says `apply`, which silently reverts defect 2.12's fix and 2.33B VND of measured July recovery. Its default was written on 2026-08-20 while the mode still *was* `off`, so it was correct and safe that morning and wrong by that evening. Nothing said so, and a test asserted the stale behaviour on the strength of its own comment. So the fix is the class: three flags with no default at all, `vat_factors.default`'s last code fallback gone, one set of required keys shared by the read-time and render-time refusals, and an AST walk in `tests/test_config_defaults.py` that fails whenever a code default and the contract disagree. **A default is a claim about the configured value, and it goes stale on its own.**

*(This paragraph carried the pre-2026-08-19 citations — `ingest.py:243`, `settings.yaml:111-115` — until 2026-08-20: the table row was corrected and the prose was not, so the two disagreed. On 2026-08-21 both were wrong again — `:121-125` is the tolerances block — and both now point at `settings.yaml:197`.)*

---

## A — wrong or missing money, or lost evidence

| # | Finding | Evidence | Status |
|---|---|---|---|
| **A1** | Browser config changes never reach the worker, and are lost on restart | `deploy/Dockerfile:38`; `deploy/docker-compose.yml:118-152`; `service/config_store.py:283,329` | **`FIXED (Phase 1.5, 2026-08-18)`** — unpinned runs render from the shared database; 8/8 goldens unmoved. Phase 1.6 closed the last opening: the editor writes rows rather than a file, and an unseeded deployment refuses instead of editing its own container's copy |
| **A2** | The config verification badge cannot work in a container — `service/verification.py:115` needs `tests/goldens/manifest.json`, which no image ships, so the canary is always `UNAVAILABLE` while the UI presents it as a working control | `service/verification.py:115`; `deploy/Dockerfile:23-38` | **`FIXED` (Phase 2.2, 2026-08-18)** |
| **A3** | Every browser run is UNVERIFIED — no screen collects the team's reference totals | `service/api.py:109,643`; `grep refs web/` → none; `src/pipeline.py:319,284-289` | **`FIXED` (Phase 2.1, 2026-08-18)** |
| **A4** | The month-end master file is a developer script, not part of the product. No API route, no screen; it reads generated workbooks off local disk while the browser system keeps artifacts in an object store | `tools/build_master_summary.py`; no route in `service/api.py` | **`FIXED` (2026-08-21)** — and this row's evidence had been **stale since Phase 3**, which shipped most of it: `kind='month_master'` runs write `month_master.xlsx` through the same `write_artifacts`/`ArtifactStore` as every settlement run, the generic `GET /runs/{id}/artifacts/{name}` (digest-checked, download-audited) serves it, and the board and run page render it. What was genuinely missing and is now built: a person can REQUEST a master (`POST /months/{month}/master`, `service.admin job enqueue-master` as break-glass, a board form) — rebuilding one no longer requires re-running a window as a side effect; the auto-chain's outcome persists on the run row (`runs.chained`, migration `019` — the run log is already stored when the chain runs) so a failed enqueue is visible, with a WARNING service-log event as the alerting hook; and the run page no longer breadcrumbs a master to a window URL that 404s |
| **A5** | That master silently drops Shopee sub-batch windows. `WINDOWS` hardcoded `s1..s4`; `s2x` and `s3k` are real and tied. Its own tie-check re-read the same hardcoded list, so it validated arithmetic and could never detect an omitted window — [defect 1.1](08-KNOWN-DEFECTS.md)'s exact shape in a different file | `tools/build_master_summary.py`; `service/month_master.py` | **`FIXED` (Phase 3, 2026-08-19)** — status verified stale on 2026-08-19 and corrected. The dict is gone on **both** paths: the CLI discovers windows by globbing `output/<month>_*/` (`discover()`), the service unions runs, roster declarations and uploads (`M5Repository.month_windows`). Both answers come from the world rather than a list somebody must remember to update, and `--check` re-derives every column total from the window it was read from |
| **A6** | Lazada has no store roster **and** `_run_lazada` never calls `check_stores` at all, so adding a roster alone would change nothing. The upload-time refusal is guarded by `if expected`, so any Lazada store name is accepted | `src/pipeline.py`; `service/api.py` | **Half `FIXED` (Phase 1.7, 2026-08-18)** — `check_stores` is wired in, and an unrostered upload is reported rather than silently accepted. `expected_stores.lazada` is still empty, which is a **business question**; the check self-skips until it is answered |
| **A7** | The roster check only ever reads income files. A window whose *orders* export is missing a store passes | `src/pipeline.py:392,461` | **`FIXED` (Phase 2.3, 2026-08-18)** — measured first: orders and income derive identical store sets on all four rostered golden windows, so no window that runs clean today newly stops |
| **A8** | `drop_unmapped_columns` defaults to `False` — if the key ever goes missing, PII stripping fails **open** | `src/ingest.py:243` vs `config/settings.yaml:110` | **`FIXED` (Phase 2.4, 2026-08-18)** |
| **A9** | `dedupe_rows` defaults to `True` — a missing key silently drops legitimate duplicate order lines and understates revenue | `src/ingest.py:480` vs **`config/settings.yaml:197`** (the third correction to this row's citation: `:446` on 2026-08-19 pointed inside the `drop_unmapped_columns` comment block, `:121-125` on 2026-08-20 was the tolerances block, and the `read_parts`/`normalize_parts` split had moved the code line too) | **`FIXED` (2026-08-21)** — as a class, because the row named one member of two. The second was found while planning the fix: `cross_window_order_backfill` defaulted to `"off"` against a contract that says `apply`, silently reverting defect 2.12 (942,869,056 VND on `2026-07_w2`, 1,390,095,674 on `_s4`). **That default was safe on the day it was written and stopped being safe the same day**, when the mode was flipped — a default is a claim about the configured value and goes stale on its own. `src/config.py` `REQUIRED_SETTINGS` + `require` hard-stop on all three flags (naming the key, what it decides, where to set it) and refuse a quoted `"false"`; `vat_factors.default`'s three bare `1.08` fallbacks became `config.vat_default`; `config_render.DANGEROUS_DEFAULTS` now **imports** that set instead of restating it, its own copy having described the backfill default as "the SAFE direction" inside the control meant to catch it. The lasting part is `tests/test_config_defaults.py`, which pairs every remaining `settings.get(key, literal)` in `src/` with the configured value and fails on disagreement. All eight goldens re-ran at zero tolerance unmoved |
| **A10** | Materialised objects are never digest-checked before the pipeline reads them | `service/materialize.py` | **`FIXED` (Phase 2.5, 2026-08-18)** — but **not** against `uploads.sha256` as this row originally said: that digests the ORIGINAL upload while the store holds the sanitized rewrite, so comparing them failed every healthy window. Migration `010` adds `object_sha256`; `NULL` (predating the check) is refused, never recomputed from the store itself |
| **A11** | The live VAT/fee master is mounted at a path the code does not read — `src/masters.py` resolves `config_dir / "Lib & VAT rate.xlsb"` while compose mounts the directory at `/app/config/masters`. On a miss the run falls back to the CSV snapshots with a single `log.warn` and carries on with stale rules | `src/masters.py`; `deploy/docker-compose.yml:123,152` | **`FIXED` (Phase 1.8, 2026-08-18)** — **confirmed in a running container**: the pre-fix image reports `not found — using CSV snapshots` with the file mounted and present. Both locations are searched now, the log names which answered, and the fallback is a finding. The snapshots matched the master exactly, so no number was wrong *yet* |
| **A12** | Seven tolerances are read but never configured, so code literals are the source of truth by accident; three *dead* tolerance keys rendered in the editor as editable fields with named readers that do not read them | `src/tieout.py:307,359,373,444,465`; `service/config_schema.py:167-171` (deleted in 1.6) | **`FIXED` (Phase 1.1–1.6, 2026-08-18)** — the three dead keys are deleted; the seven unconfigured ones are rows carrying the literal each already fell back to, and `config_tolerances` refuses a new one because a tolerance nothing reads is what 1.1 deleted |
| **A13** | Two copies of the store-name normaliser. The copy's docstring still cites `tools/full_run.py`, deleted in M6 | `tools/build_master_summary.py:58`; `src/pipeline.py:50` | **`FIXED` (Phase 2.6, 2026-08-18)** |
| **A14** | The invoice bucket lists and `VAT_RATES` are code, while `store_to_brand` advertises itself as their home. The "one-line VAT change" promise holds for the *rate*, not the *bucket set*; there is also a second bare `1.08` at `finance_template.py:172` that does not read `vat_factors.default` | `src/finance_template.py:70,90-92,172`; `config/settings.yaml:265-271` | **`FIXED` (2026-08-20)** — `vat_factors.rates`, `invoice_buckets.<platform>` and `fee_buckets.lazada` in the contract (migration `016`: two row tables + one scalar row), read through hard-stop accessors with no code fallback (`finance_template.vat_rates`/`invoice_buckets`, `lazada.revenue_bucket`/`promo_buckets`); the bare `1.08` (`_sku_pivot`'s zero-pre-VAT fill) now reads `vat_factors.default` — value-identical today, semantically live. What stays code, stated rather than latent: the workbook TAB layout is template geometry, and a configured bucket the template has no tab for (or a rate list Shopee/Lazada's hard-wired control rows cannot lay out) is a `ReconHardStop`, never a silent leak into a drift breach. All eight goldens re-ran at zero tolerance unmoved |
| **A15** | The team's reference master `input/master/ADA marketplace MASTER July 2026.xlsx` cannot be opened by this system. **This row's diagnosis was wrong and is corrected in place** (2026-08-19): it read *"an OLE2 compound file — a legacy `.xls` carrying an `.xlsx` extension … prefer a re-save by the team over adding a legacy reader"*. The magic bytes are indeed `D0 CF 11 E0 A1 B1 1A E1`, but that signature is the **encryption wrapper of a Microsoft Purview sensitivity label**, not a legacy workbook — it is a genuine `.xlsx` inside, `xlrd` finds no workbook stream, and **a re-save fixes nothing** because there is nothing readable to re-save. A container signature identifies the container, not what is inside it | `src/ingest.py:rights_protected`; corrected 2026-08-19 (was "verified 2026-08-18") | **Superseded by C15** ([section C](#c--breaks-when-deployed-for-real)) — detection is `FIXED` (the failure now names the label and says re-saving will not help); the access constraint is `OPEN` and is not engineering's to close. The remedy is the label granting rights to the identity this system runs as ([13-ENTRA-SETUP](13-ENTRA-SETUP.md)), or the team supplying a per-tab CSV export ([16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md)) — which is what `tools/compare_master.py` reads today |

---

## B — a user gets stuck, confused, or misled

| # | Finding | Evidence | Status |
|---|---|---|---|
| **B1** | Python tracebacks render in the browser. `traceback.format_exception(exc)` goes into `runs.error` and is displayed raw; `ReconHardStop` messages take the same path, container paths and all | `service/worker.py:140-141,162-164`; `web/app/runs/[id]/page.tsx:50` | **`FIXED` (Phase 4.1, 2026-08-18)** — a sentence on the run record, the traceback in the log. Legibility, not disclosure: both routes are `VIEWER` |
| **B2** | No error boundaries anywhere in `web/app` — no `error.tsx`, `not-found.tsx`, `loading.tsx` or `global-error.tsx`. A bad URL or any API failure produces Next's *"Application error … Digest: …"* screen | verified by `find web/app` | **`FIXED` (Phase 4.2, 2026-08-18)** |
| **B3** | The run page never refreshes. It is a server component fetched once, with no `router.refresh` anywhere in `web/`. A finished run still shows **running**, `—` timings, and **"Nothing was produced."** while the workbook exists | `web/app/runs/[id]/page.tsx:17,36,118` | **`FIXED` (Phase 4.3, 2026-08-18)** |
| **B4** | No re-run, no cancel. `cancelJob` has **zero callers** and `POST /jobs/{id}/cancel` is unreachable from the browser; the run page has no link back to its window | `web/app/actions.ts:347` | **`FIXED` (Phase 4.4, 2026-08-18)** |
| **B5** | Every config field is captioned with a Python file and line — *"read by src/ingest.py:290 check_stores"* — and **the line numbers are wrong**: `check_stores` is at `:309`, `read_excel_sheet` at `:131` (claimed 148), `report_unparseable` at `:102` (claimed 125). Nothing tests them, and the "no dotted paths in user text" test exempts `reader` | `web/app/config/tables.tsx`; `service/config_rows.py` | **`FIXED` (Phase 1.6, 2026-08-18)** — no Python path is rendered on the config screen at all. `reader` survives as a **column**, module only, and `test_every_editable_setting_names_the_module_that_reads_it` asserts both that it is present and that it carries no line number; the payload test no longer exempts anything |
| **B6** | Developer vocabulary throughout: `exit code 3`, `hard stop`, `Peak RSS`, `DataFrame`, a `SHA-256` column header, `goldens`, `git commit`, `openpyxl`/`calamine` as radio labels, a raw `<dimension>` tag, `TODO-HUMAN`. The login page instructs users to run `python -m service.admin user create` | `web/app/login/page.tsx:65`; `web/app/page.tsx:132,141,164`; `web/app/config/tables.tsx` | **`FIXED` (Phase 5.2, 2026-08-19)** — linted by `tests/test_ui_vocabulary.py`, so it cannot drift back |
| **B7** | Nothing is in Vietnamese, for a Vietnamese team. `<html lang="en">`, no i18n library, zero Vietnamese words in the app — and the English is dense and idiomatic | `web/app/layout.tsx:22`; `web/package.json` | **`FIXED` (Phase 5.3, 2026-08-19)** on the finance path; the rules editor and accounts screen stay English, stated as a boundary rather than a gap |
| **B8** | Destructive admin actions have no confirmation, and **role change fires on `onChange`** — a scroll wheel over the select promotes someone to admin. Applying and committing a config change is also one click. The typed-reason pattern exists at `file-row.tsx:67-98` and was simply not applied here | `web/app/admin/users/user-actions.tsx:48,56,65,79`; `web/app/config/proposal-actions.tsx:85-89` | **`FIXED` (Phase 4.6, 2026-08-18)** |
| **B9** | No `role="alert"` or `aria-live` on any of the ~10 result banners, so a screen-reader user gets no feedback on submit | `web/app/**` notice call sites | **`FIXED` (Phase 5.5, 2026-08-19)** — 12 banners announced, type floor 12px→13px, uppercase headers dropped |
| **B10** | Smaller wear: one browser-tab title for all 8 pages; 11–12px type; no upload progress for 40 MB files; batch upload errors concatenated into one string; download failures returning double-encoded JSON into the address bar; a log poller that retries forever and styles its own failure with the `hard_stop` badge; `run_metrics.json` offered to finance users as a deliverable | `web/app/layout.tsx:7`; `actions.ts:273-279`; `runs/[id]/download/[name]/route.ts:37-40`; `runs/[id]/log.tsx:51-57,87`; `src/pipeline.py:639` | **Partly `FIXED` (Phase 5.5, 2026-08-19)** — per-page titles, type sizes and an upload busy-state with count and size are done; `run_metrics.json` is off the finance-facing file list (Phase 4.7). **Still open:** byte-level upload progress (needs an XHR rewrite that would lose per-file refusal handling), batch upload error reporting, the double-encoded download failure, and the log poller's unbounded retry |

**What is already good, and should not be rewritten:** the empty states, the missing-store panel at `web/app/windows/[platform]/[period]/page.tsx:88-101` (it names the stores, states the consequence — *"a workbook that looks complete and under-invoices"* — and points at the remedy), the duplicate-upload 409, and the mandatory-reason pattern on file removal. The problem is register, not content.

---

## C — breaks when deployed for real

| # | Finding | Evidence | Status |
|---|---|---|---|
| **C1** | A stuck job blocks its window forever with no operator recourse. `reclaim_expired`'s only caller is the worker's own loop, so a dead worker never reclaims; `jobs_one_active_per_window` then refuses every further run of that window, `cancel_job` refuses a leased job by design, and `service/admin.py` has no job subcommands. The only fix is SQL against a database compose deliberately does not publish | `service/worker.py:78`; `service/repository.py:198-199`; `service/migrations/001_init.sql:65-67` | **`FIXED` (Phase 4.5, 2026-08-18)** — CLI, admin endpoint and a board button; it closes jobs out and deliberately does not retry them |
| **C2** | Every restart kills a running job. `Worker.stop()` promises to finish the job in hand, but no `stop_grace_period` is set, so Docker's 10-second default applies against a 171-second Shopee window | `service/worker.py:66-73`; `deploy/docker-compose.yml` | **`FIXED` (Phase 4.5, 2026-08-18)** — a heartbeat file, threshold set from the lease so a long run is not mistaken for a wedged worker |
| **C3** | No Postgres backup and no restore drill. The database holds the run history, config versions, approvals and exception records — the audit trail the whole M5/M6 design exists to create. The artifacts bucket would survive; the record of who ran what would not | `grep pg_dump\|backup` → prose only; `10-ROADMAP.md:25` | **`FIXED` (Phase 6.2, 2026-08-20)** — nightly `pg_dump -Fc` under `--profile backup`, and the restore drill was PERFORMED: `tools/db_restore_drill.sh` on `recon_dev`, 30 tables / 1,037 rows, every per-table count identical. The hosting half — a copy that leaves the machine — is named, not claimed |
| **C4** | No TLS, no reverse proxy, no ingress. `uvicorn.run` with no `ssl_keyfile`; loopback binding is the entire control. The moment a hostname goes in front of this, TLS termination, `serverActions.allowedOrigins` and BFF-side rate limiting all become required at once | `service/api.py:1465`; `deploy/docker-compose.yml:53,117,185` | **`FIXED` (Phase 6.1, 2026-08-20)** — nginx ingress profile: TLS, pinned X-Forwarded-For, per-IP limits; `RECON_ALLOWED_ORIGINS` build arg. `nginx -t`-verified in the real image; not yet browsed through (2.8's standing limit) |
| **C5** | No observability of any kind — zero uses of `logging` in `service/`, no metrics endpoint, no tracing, no error reporting, no alerting. The per-run log is excellent; there is no log for the service itself | `grep "import logging\|getLogger" service/` → 0 | **`FIXED` (Phase 6.3, 2026-08-20)** — `service/obs.py` JSON-line logging (api + worker), worker lifecycle events, `GET /metrics`. Alerting is the host's half: a `"level":"ERROR"` line is the hook |
| **C6** | An operator cannot tell whether a worker exists. `/healthz` reports the database and queue depth, never the object store and never a worker; there is no worker registry or heartbeat. The worker also has **no `HEALTHCHECK`**, so a hung — as opposed to exited — worker is never restarted, and it is the process holding the recovery sweep | `service/api.py:369-377`; `deploy/Dockerfile:83-99` | **`FIXED` in full (Phase 4.5 + Phase 6.4, 2026-08-20)** — Phase 4.5 gave the worker a `HEALTHCHECK` reading a heartbeat file, so a hung worker is restarted. Phase 6.4 closed the operator half: `worker_heartbeats` (migration `017`) is beaten each idle loop turn AND each lease extension (a worker mid-269-second-run stays visible), and `/healthz` reports `workers_alive` / `workers_known` / `worker_last_seen_seconds` — `queued: 3, workers_alive: 0` is now a readable sentence |
| **C7** | No upload size limit. The whole body is read into memory before any check, on a 40-thread pool, against 382 MB windows | `service/api.py:848` | **`FIXED` (Phase 6.5, 2026-08-20)** — `RECON_MAX_UPLOAD_MB` (default 512, ~2.8× the largest real export's 184 MB), bounded read, 413 naming the limit; mirrored at the ingress as `client_max_body_size` |
| **C8** | Migrations race on first boot. Both the api and the worker migrate on start with no advisory lock; the loser crashes on a duplicate key and `restart: unless-stopped` masks it as an unexplained restart | `service/db.py:57-84`; `service/api.py:1437`; `service/worker.py:313` | **`FIXED` (Phase 6.6, 2026-08-20)** — `pg_advisory_lock` before the applied-list read, released in `finally` after a rollback; race-tested with two threads |
| **C9** | Rate limiting covers only `POST /sessions`, in-process, lost on restart, per-replica. The module defers per-IP limiting to the BFF; there is none in `web/`. Nothing throttles uploads or the config-apply canary | `service/ratelimit.py:19-23,44-46` | **`FIXED` (Phase 6.7, 2026-08-20)** — per-IP zones at the ingress (general / mutations via the Next-Action header / `/login`); the api's per-username throttle stays as the declared backstop |
| **C10** | Nothing is ever cleaned up: scratch directories are deliberately kept on every failure and never removed, rejected uploads never expire in local-dir mode, `run_log_lines` and `user_sessions` are never pruned, and no disk-usage check exists | `service/worker.py:148`; `service/api.py:1003-1016` | **`FIXED` (Phase 6.11, 2026-08-20)** — `service/retention.py`: scratch 14d (kept-on-failure dirs age out after the diagnosis window), run-log DB mirror 90d (run_log.txt artifact is the durable copy), dead sessions 30d, disk-free warning; hourly in the worker plus `service.admin retention sweep --dry-run`. Bucket uploads stay under the MinIO lifecycle rule |
| **C11** | The git half of the config audit silently no-ops in containers — no git binary in the image, `.git/` excluded — so the API reports `committed: false` while `10-ROADMAP.md:189` states the design premise as git-backed YAML committed with comments preserved | `service/config_store.py:288-296`; `.dockerignore:22` | **`FIXED` (Phase 6.10, 2026-08-20)** — decided as [D60](06-DECISIONS.md#d60): the `config_versions` row is the audit record, git is a developer-checkout convenience; the api response and the config page now say so instead of an unexplained `committed: false` |
| **C12** | No record of who downloaded a workbook. Every store's revenue is downloadable by any viewer with no audit row — even though the refusal to use presigned URLs was justified partly on audit grounds | `service/api.py:707-738`; `service/objects.py:21-25` | **`FIXED` (Phase 6.8, 2026-08-20)** — `artifact_downloads` (migration `018`), written after the digest check and before a byte streams; refused downloads record nothing; read via `service.admin audit downloads` |
| **C13** | `deploy/.env` holds real credentials inside a OneDrive-synced tree. Correctly gitignored, but syncing database and object-store credentials to a cloud tenant. The repo already treats OneDrive sync as a hazard for other reasons | repo path; `.gitignore`; `09-OPERATIONS.md:7` | **`FIXED` (Phase 6.9, 2026-08-20)** — the real `.env` moved to `%LOCALAPPDATA%econ-deploy\`, compose started with `--env-file`; the ingress certs follow the same rule |
| **C14** | No deployment story beyond single-node compose. Railway appears in the docs three times, always as a constraint that justified object storage, never as a plan — no config, no CI, no pipeline. M7 is unstarted, and *single maintainer* is a standing risk whose mitigation **is** that unstarted milestone | `10-ROADMAP.md:25,212` | `OPEN` |
| **C15** | **Files the team labels are unreadable to this system.** Two files in the tree carry a Microsoft Purview sensitivity label with encryption — the month-end master and one Lazada weekly export — with the **same label id and tenant** on both, applied deliberately (`method="Privileged"`). Excel opens them for a person with rights to the label; no reader, no service identity and no re-save does. Nothing detected or explained this until M8: openpyxl said "File is not a zip file" and one of the two was written up for months as a legacy `.xls` and the other as "password-protected" | `input/master/…xlsx`; `input/original exports/lazada/…xlsx`; `src/ingest.py:rights_protected` | **Detection `FIXED` (2026-08-19)** — the failure now names the label and says re-saving will not help. **The constraint is `OPEN` and is not engineering's to close**: hosting this system means the label must grant rights to the identity it runs as ([13-ENTRA-SETUP](13-ENTRA-SETUP.md), [16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md)) |

---

## D — holes in the workflow

| # | Finding | Evidence | Status |
|---|---|---|---|
| **D1** | The exception queue cannot be worked. The stable fingerprint exists and history is queryable, but nothing can be marked reviewed or expected, so every run re-presents the same list. The roadmap defines M6 as exactly this and M6 shipped something else | `service/api.py:756`; `service/exceptions.py`; `10-ROADMAP.md` milestone table | **`FIXED` (2026-08-21)** — [D61](06-DECISIONS.md#d61): `exception_dispositions` + append-only `exception_disposition_events` (migration `020`, the pin/pin-events shape), `POST`/`DELETE /exceptions/{fingerprint}/disposition` at `recon.user` with a mandatory 8-char reason, actor from the session. **Annotates, never hides**: the queue's default answer carries every row badged; `open_only` is an explicit filter, because the fingerprint hashes identity columns, not amounts, and an "expected" variance that has grown must still be seen. The run-page queue gained the controls and filter; `words.ts:130`'s "Rows needing a decision" heading finally has something behind it |
| **D2** | Windows with uploads but no run are invisible. The board query is `from jobs j`, so 17 uploaded files appear nowhere; there is no window index, and `/windows/{platform}/{period}` is reachable only by typing the period into a free-text box. The month filter is URL-only with no picker | `service/repository_m5.py:517`; `web/app/page.tsx:20`; `web/app/queue-form.tsx:48-56` | **`FIXED` (2026-08-21)** — `board()` now starts from the same known-window union as `month_windows` (jobs ∪ roster declarations ∪ live uploads; the uploads-evidence condition is ONE spelling, `UPLOAD_EVIDENCE`, shared by both queries), so an uploaded-but-never-queued window appears with an upload count and a "not yet run" state and links to its window page; `GET /months` feeds a real month picker (a plain GET form, no client code); the queue form's period box gained a per-platform datalist of known windows, with free text kept for a brand-new period |
| **D3** | The roster declaration is all-or-nothing, is never re-evaluated when the window later fills up, and does not appear on the artefact. `apply_partial_roster` makes *every* expected store optional, so a genuinely forgotten store is waved through with the legitimately absent ones; [D46](06-DECISIONS.md#d46) deliberately did not stamp "n stores absent" into the finance workbook, so the file the team invoices from does not say it is partial | `src/pipeline.py:141-144`; `06-DECISIONS.md:394-395` | **`FIXED` in full (2026-08-21)** — all three clauses. *Named stores* ([D62](06-DECISIONS.md#d62), migration `021`): the declaration carries `declared_absent_stores`, `apply_partial_roster` relaxes only those and hard-stops on a name the run's own roster does not know; NULL keeps the legacy blanket working (and warned as one); the CLI's `--partial-roster` stays blanket-mode so golden regeneration is byte-identical (pinned in `tests/test_partial_roster.py`); the roster form is a store picklist pre-checked from the live missing set. *Re-evaluation*: a declared-absent store that now has files is warned in the run log and amber-flagged on the window page (`declared_absent_present` — the page re-renders after every upload, so it IS the upload hook); `ready` refuses to promise a run the pipeline will hard-stop. *The stamp* ([D63](06-DECISIONS.md#d63)): a partial run writes `Roster` + a Vietnamese sentence naming the absent stores at A1/B1 of the primary control tab; exactly the four partial-roster goldens moved by exactly those two cells, rebaselined with the reason recorded. Finding the stamp's safe cell exposed **defect 2.13** (control cells below a tab's data region are silently dropped) — recorded in [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md), deliberately not fixed in this pass |
| **D4** | Lazada's rules are hardcoded and outside the config editor — column maps, sheet names, filename regex and bucket names. Four service modules hardcode the exception and say so | `src/lazada.py`; `service/naming.py`; `service/uploads.py`; `tools/stage_exports.py` | **`FIXED` (Phase 1.7, 2026-08-18)** — maps, sheet names and the filename regex are in the contract and editable; 4 Lazada goldens unmoved. The bucket-name residual (`REVENUE_BUCKET`, `PROMO_BUCKETS`) was **A14**, closed 2026-08-20 in its own change — nothing of D4 remains |
| **D5** | Format drift has needed a developer every month tested, and nothing in the product helps a finance user absorb it | `12-CHANGE-HISTORY.md:5` | `OPEN` |
| **D6** | Config editor promises that are not implemented: the roster "optional" flag is described but falls through to a plain list and `stores_optional` has no schema field at all (so adding to it is refused); `window_settlement_bounds` is declared editable with a date-picker widget but renders read-only; `store_to_brand` is live, empty, and absent from the schema; and `drop_unmapped_columns`'s `locked_reason` claims the upload sanitizer reads the flag, which it never does | `service/config_rows.py`; `web/app/config/tables.tsx` | **`FIXED` (Phase 1.6, 2026-08-18)** — optional is a boolean on the storefront's row, bounds are a table with two date columns, `store_to_brand` is `config_store_brands` with its `in_pipeline_contract` gate, and the locked reason no longer claims what the sanitizer does not do |
| **D7** | The per-file "correct the store" control is read server-side but no input with that name is ever rendered, so the documented affordance is unreachable | `web/app/actions.ts:254` vs `web/app/windows/[platform]/[period]/upload-form.tsx` | `OPEN` |
| **D8** | The demo window has no button. `POST /demo/seed` exists and is admin-only; nothing in `web/` calls it | `service/api.py:1026`; `grep demo web/app` → none | `OPEN` |
| **D9** | No end-user documentation. All 13 docs are engineering-facing; `09-OPERATIONS.md` is written for a developer with a venv | `docs/` | **`FIXED` (Phase 5.4, 2026-08-19)** — [17-USER-GUIDE](17-USER-GUIDE.md) |
| **D10** | Workbook writes are not atomic — a finance file left open in Excel still fails the write. Scoped to M2, never landed | `src/pipeline.py::_write_atomically`; [defect 1.7](08-KNOWN-DEFECTS.md#17-no-error-handling-in-the-production-driver--fixed) | **`FIXED` (2026-08-19)** — all four artifact writers go through a sibling `.tmp` + `os.replace`, so no truncated `finance_file.xlsx` can sit at the final path (and a truncated one still opens in Excel, which is why it mattered). **The Excel-lock case is NOT solved and is not claimed to be:** `os.replace` raises `PermissionError` there too. What changed is that the previous artifact survives intact and the failure is reported. Both halves are pinned in `tests/test_atomic_writes.py` |
| **D11** | Dead config surface, with zero callers in `src/`, `tools/` or `tests/`: `config/sku_master.csv` (no reader anywhere), `config/brand_rules.yaml`, `classify.classify()`, `config.invoice_grouping()`, three `STATUS_*` constants, an unused import in `calculate.py`, five unread settings keys, and the `DEAD` widget that existed to display two of them. Survivors of the M1 placeholder deletion | verified 2026-08-18 | `FIXED (Phase 1.1, 2026-08-18)` — all removed, goldens unchanged |
| **D12** | Two separate storefront→brand mappings, used by different things: `store_to_brand` in the contract is `{}` (so every store falls back to its own name with a loud warning every run), while `config/brand_map.csv` holds 60 rows and is read only by a developer script | `config/settings.yaml:271`; `src/ingest.py:299-306`; `tools/build_master_summary.py:202` | `OPEN` |

---

## E — process and governance

| # | Finding | Evidence | Status |
|---|---|---|---|
| **E1** | The whole project is **two git commits**. The discipline this repo rests on — never mix a refactor with a semantic fix, each behaviour change its own commit with the delta stated in advance, moving a golden baseline as a reviewable act — has no corresponding history. M4, M5 and M6 are one commit. (The retired docs *are* recoverable from `ec8acca`, so that claim holds.) | `git log --all --oneline` → 2 | `OPEN` — nothing recovers it; the discipline applies from here |
| **E2** | Zero tests on the web app. `web/package.json` has only `typecheck`; no test runner, no `*.test.*`, and still no browser automation — so every claim about rendering is a claim about code that compiles | `web/package.json`; [defect 2.8](08-KNOWN-DEFECTS.md) carried-forward caveat | `OPEN` |
| **E3** | SSO is unbuilt. Every identity is issued and revoked by hand and there is no "this person has left" event | `08-KNOWN-DEFECTS.md:218`; [13-ENTRA-SETUP](13-ENTRA-SETUP.md) | `OPEN` — blocked on directory permissions |
| **E4** | Fifteen of sixteen open questions are unanswered, and production booking is not authorised. #1 (external ground truth), #2 (sign-off protocol) and #3 (rollback story) gate whether anything can be booked from at all | [11-OPEN-QUESTIONS](11-OPEN-QUESTIONS.md); `07-VERIFICATION.md:118` | `OPEN` — business decisions, not engineering |
| **E5** | Golden coverage is 2 of 25 TikTok stores and 2 of 17 Shopee, so both still run partial; one Lazada window is encrypted by a sensitivity label and cannot be staged at all | `07-VERIFICATION.md:116` | `OPEN` — a data question, not a tooling one |
| **E6** | Stale claims in the doc an operator reads first: `10-ROADMAP.md:185` says `web` and `store` do not exist and the compose file has never been built (both closed in M5/M6), and the M6 row in the milestone table is blank and describes exception-queue depth, which is not what M6 shipped | `10-ROADMAP.md:185` and the milestone table | `FIXED (Phase 0, 2026-08-18)` |

---

---

# The programme

Six phases, in this order, decided with the user on 2026-08-18. **Correctness that
silently misleads → the month-end master → failure legibility → jargon and
Vietnamese**, with deployment hardening scheduled because the target is cloud
(Railway/Azure with a real hostname).

Legend: ✅ done and verified · 🔜 next · ⬜ not started.

## Before you start — what a fresh session needs

```bash
PY="$LOCALAPPDATA/recon-venv/Scripts/python.exe"

# Postgres must be UP for the service tests. Start it DETACHED — a foreground
# pg_ctl killed by a command timeout takes the server down with it. Re-confirmed
# the hard way 2026-08-21: `pg_ctl … -w -t 60 start` from a bash tool call did NOT
# return after the server was ready (it holds the pipe), the 2-minute tool timeout
# killed it, and the server logged `terminated by exception 0xC0000142` /
# `shutting down due to startup process failure` ~10s after reporting "ready to
# accept connections". Use PowerShell's Start-Process, which detaches properly:
#
#   Start-Process -FilePath "$env:LOCALAPPDATA\recon-pg\pgsql\bin\pg_ctl.exe" `
#     -ArgumentList @("-D","$env:LOCALAPPDATA\recon-pg\data",
#                     "-l","$env:LOCALAPPDATA\recon-pg\server.log","start") `
#     -WindowStyle Hidden
#
# then poll pg_isready (below) rather than waiting on pg_ctl.
PG="$LOCALAPPDATA/recon-pg/pgsql/bin"; DATA="$LOCALAPPDATA/recon-pg/data"
"$PG/pg_isready.exe" -h 127.0.0.1 -p 55432 -U recon

export RECON_TEST_DATABASE_URL="postgresql://recon:$(cat "$LOCALAPPDATA/recon-pg/pgpass.txt")@127.0.0.1:55432/recon_test"
export RECON_REQUIRE_CLIENT_DATA=1

"$PY" -m pytest -m "not slow" -q      # 899 passed, 3 skipped, 4 deselected (~6.5 min)

# The inner loop, with the opt-in Excel read cache. Content-addressed, off by
# default, and OFF is what you want for anything you intend to cite.
export RECON_TEST_IO_CACHE=1
```

**Know what the fast suite proves, and what it does not.** `tests/goldens/` in it
compares committed digests against the *static* golden cellsets in
`%LOCALAPPDATA%\recon-goldens` and never executes `pipeline.run`, so a green fast
suite is **not** evidence that output is unchanged.

*This paragraph claimed "it never re-runs the pipeline on real data" until
2026-08-18, which is true of `tests/goldens/` and false of the suite. Measured with
`--durations=25`: `test_a_sanitized_renamed_window_produces_the_committed_golden`
runs the real pipeline over a real Shopee window (269s) and a real TikTok one (201s)
— together 67% of a 706s suite — because proving the sanitizer did not change the
output is the entire point of that test. Corrected rather than quietly fixed, since
"which test proves this" is the thing this project cannot be loose about.*

The money gate is the golden re-run, and it costs ~11 minutes:

```bash
"$PY" -m pytest tests/service/test_config_render.py::test_config_render_produces_the_committed_goldens -q
```

That is the test to run before believing any claim in this programme about cells not
moving. It re-runs all eight windows under DB-rendered config and compares fresh
workbook digests to `tests/goldens/manifest.json`.

**Standing rules** (`.claude/CLAUDE.md`): never mix a refactor with a semantic fix;
state the expected golden delta *before* running the gate; never re-baseline to make
a suite green — `tools/make_golden.py` refuses without `--rebaseline --reason`, and
that refusal is the control.

---

## Phase 0 — The register ✅

| | Task | Status |
|---|---|---|
| 0.1 | Write `docs/14-PRODUCTION-READINESS.md` — this file | ✅ |
| 0.2 | Index it from `README.md` and `.claude/CLAUDE.md` | ✅ |
| 0.3 | Fix E6, the stale roadmap claims: `10-ROADMAP.md:185` said `web` and `store` did not exist and compose had never been built (both closed in M5/M6); the M6 milestone row was blank and described exception-queue depth, which is not what M6 shipped | ✅ |
| 0.4 | Correct the suite baseline in `CLAUDE.md` — it said `630 passed`, measured `716` | ✅ |

---

## Phase 1 — Configuration into the database

**Why.** Config was fragmented across seven files (two read by nothing), Lazada's
rules lived in Python, five settings keys were dead, seven tolerances were read but
never configured, and two flags had code defaults that were the **opposite** of their
configured values. And A1: browser edits never reached the worker.

**The design, and why it is safe.** The pipeline's entire config interface is a YAML
*string* — `build_context(settings_text=…)` → `yaml.safe_load` → a plain dict. So
normalized tables → renderer → YAML text → the existing `config_versions` snapshot →
the existing per-window pin. `src/` never learns about Postgres, the I/O-boundary
allowlist is unchanged, `service/` stays deletable, and `config/settings.yaml`
survives as the seed and the CLI's input. This reverses [D2](06-DECISIONS.md#d2) and
that reversal is argued in `service/migrations/007_config_normalized.sql`'s header.

### 1.1 Delete what is dead ✅

Proof of deadness is unchanged output, exactly as M1 proved the placeholder path
unreachable ([D19](06-DECISIONS.md#d19)).

- ✅ `config/sku_master.csv` — no reader anywhere in the repo
- ✅ `config/brand_rules.yaml` — read only by `classify.classify()`, which had no callers
- ✅ `classify.classify()`, `config.invoice_grouping()`, three `STATUS_*` constants, an unused import in `calculate.py`
- ✅ Five unread settings keys: `vat_rate`, `periods.rolling_window_months`, `tolerances.split_rounding_vnd`, `tolerances.exact_check_vnd`, `tolerances.shopee.pv_sum_vnd` — note `tolerances.TIKTOK.pv_sum_vnd` **is** read and survives; the two are one keystroke apart
- ✅ The `DEAD` widget itself, its two `Field` declarations, its web rendering and its TypeScript union member
- ✅ The stale `tools/sample_config/` reference in the settings header — that directory went with the placeholder path in M1 and the pointer outlived it by three milestones
- ✅ Replacement drift detector: `test_keys_that_nothing_reads_are_gone_from_the_contract`

### 1.2 The schema ✅

`service/migrations/007_config_normalized.sql` — 12 tables, each carrying the same
evidence quartet (`evidence`, `changed_by`, `changed_at`, `source`) plus an explicit
`sort_order` so rendering is deterministic.

`config_scalars` · `config_platforms` · `config_reading` · `config_column_maps` ·
`config_stores` · `config_store_aliases` · `config_store_brands` ·
`config_tolerances` · `config_settlement_bounds` · `config_fee_types` ·
`config_vat_sku`, plus `config_versions.source` extended with `'rendered'`.

Design notes worth not re-deriving:
- **`config_stores` merges two lists.** `expected_stores` + `stores_optional` become one row with a boolean — which is what the roster widget's help text always described and what the dotted-path editor refused to let anyone do. 1.6 made it reachable.
- **`config_reading` collapses six sibling maps** keyed identically (`sheet_names`, `sheet_patterns`, `header_rows`, `skip_rows_after_header`, `reader_engine`, and per-platform `dayfirst`).
- **`config_column_maps.active`** keeps a superseded header spelling with the date it retired, instead of deleting it or keeping it indistinguishable from a current one.
- **`config_store_aliases.canonical` is nullable**, replacing the `TODO-HUMAN` sentinel that three modules had to special-case.
- **`config_store_brands.in_pipeline_contract`** is the column that keeps this a migration rather than a behaviour change — see 1.3.
- **The canonical field vocabulary is deliberately NOT a table.** It stays derived from `src.ingest`'s own constants by `config_rows.canonical_fields`; a table would be a second definition of what the pipeline understands, free to drift from the code that consumes it.

### 1.3 The importer ✅

`service/config_import.py`, idempotent by truncate-and-load inside one transaction.

- ✅ Lifts each key's comment block into `evidence` via the existing `config_store.evidence_for` — which reads the raw text, not ruamel's `.ca`, for the reason in [D42](06-DECISIONS.md#d42)
- ✅ **Refuses an unmodelled key** rather than skipping it (`KNOWN_KEYS`)
- ✅ **Refuses a duplicate with a conflicting value.** Measured: `lazada_vat_sku.csv` has 668 rows for 660 SKUs and every repeat agrees, so collapsing is lossless *today* — but a silent first-wins would one day pick a VAT factor by row order
- ✅ Imports the 7 tolerances `src/tieout.py` reads and the file never configured, at the code literal each already fell back to. Precise about who reads what: `run_checks` (tiktok + shopee) reads `conservation_vnd`/`grand_vnd`/`per_store_vnd`; `run_checks_lazada` reads `conservation_vnd`/`price_ka_rounding_vnd` only
- ✅ **`brand_map.csv` is imported but flagged `in_pipeline_contract = false`.** Rendering its 60 rows into `store_to_brand` would change the brand of **28 stores** inside what must be an output-identical refactor. Reconciling the two mappings is D12's own commit with its own stated delta

Measured import: 7 scalars · 2 platforms · 4 reading rules · 45 column maps · 42
stores · 20 aliases · 60 brand rows · 13 tolerances · 1 settlement bound · 118 fee
types · 660 VAT SKUs.

### 1.4 The renderer and the gate ✅

`service/config_render.py` — rows → YAML text, evidence re-emitted as comment blocks.

- ✅ **Byte-stable**, because `config_versions` is content-addressed and a wobbling render would mint a version per call and break pin de-duplication
- ✅ Parses through `yaml.safe_load` to a plain `dict`/`float`/`bool` — one parser for anything reaching the money math
- ✅ `assert_complete` refuses a render missing any key the pipeline reads, with `drop_unmapped_columns` and `dedupe_rows` called out by name because their absence is not neutral
- ✅ Semantic diff against `config/settings.yaml`: **7 differences, all enumerated in the test** — the 7 previously-unconfigured tolerances. Roster list *order* differs and is explicitly not meaning (both keys are consumed as sets)
- ✅ **The gate: 8 golden windows re-run under rendered config → `matched 8 | moved 0`**

Tests: `tests/service/test_config_render.py` (10 fast + 1 `slow`).

### 1.5 Cut over — the A1 fix ✅

- ✅ `M5Repository.render_config()` returns the rendered contract, or `None` when the tables are empty
- ✅ `config_store.resolve_for_window` prefers the rendered contract for an unpinned window; disk remains the fallback, which is how a fresh deployment seeds from the image and how the CLI runs with the service switched off
- ✅ Applying a proposal **re-imports into the tables**, or the api would report success while the worker went on computing under the previous contract — A1 one layer up
- ✅ `python -m service.admin config export` writes the tables back to `settings.yaml`, which is what keeps [D24](06-DECISIONS.md#d24) true

### 1.6 CRUD on the tables ✅

The dotted-path edit model is gone. `service/config_edits.py` and
`service/config_schema.py` are **deleted**; `service/config_rows.py` replaces both
with a closed registry of the eleven tables, two operations (`upsert`, `delete`) and
the refusals that used to be spread across a schema, an edit module and a store.

- ✅ Proposal → approve → apply is unchanged, and so are `base_sha256` optimistic
  concurrency, the `self_approved` generated column and the audit columns. What
  `base_sha256` compares is now the **rendered** contract, so the concurrency check
  is against what the worker would run rather than against this container's disk copy
- ✅ `invalidates_goldens` is a column on all eleven tables (migration `008`), read
  off the row and never inferred from a path. `verification.verify` takes the answer
  rather than computing it. Two tables are argued down to `false` — the roster
  decides whether a run *stops*, not what a cell holds; a tolerance is read by
  `src/tieout.py`, which reports variances and writes no cell — and everything else
  keeps the safe default. Two were **tightened**: an alias reassigns a whole file's
  rows, and `store_to_brand` had no schema field at all so it was only ever
  "unknown ⇒ invalidating" by luck
- ✅ `LOCKED` stays a column, and the API refuses the write rather than the UI hiding
  the control. Both inaccuracies in `drop_unmapped_columns`'s entry are gone: `reader`
  now names modules with no line numbers, and `locked_reason` says the sanitizer
  *strips on the same column map* rather than claiming it reads the flag
- ✅ **Evidence is served from the row.** `test_every_row_carries_its_own_evidence_not_its_containers`
  is what holds it: through M6 every alias showed the same block, because a file can
  only caption a top-level key
- ✅ `OrphanedEvidence` and `comment_disposition` are gone. A row deletes its own
  evidence, so the failure they modelled cannot happen —
  `test_the_evidence_of_a_removed_row_goes_with_it`
- ✅ Both promised-but-missing widgets exist, closing **D6**: the roster "optional"
  flag is a boolean on the storefront's own row, and `window_settlement_bounds` is a
  table with two date columns and a 40-character evidence floor
- ✅ The `_reimport_config` bridge is deleted. In its place `seed_config_tables` runs
  once on first boot, so a fresh deployment turns the committed contract into rows
  instead of presenting an empty editor — and an editor with no rows **refuses with
  a 503 naming `python -m service.config_import`** rather than silently falling back
  to editing this process's copy of `config/`, which is the shape of A1

Also landed here, because the row model made them one-line rather than structural:

- A scalar keeps the **type** it was seeded with. `config_scalars.value` is `jsonb`,
  so nothing at the database level stopped `dedupe_rows` becoming the string
  `"false"` — truthy in Python, and a silent inversion of the flag
- An alias may only point at a storefront in the roster, and edits apply **in the
  order given**, so adding both in one proposal works and doing it backwards is
  refused with a sentence rather than accepted
- A retired column-map spelling leaves the rendered contract and **keeps its row**,
  with the date it retired

Tests: `tests/service/test_config_rows.py` (29) and a rewritten
`tests/service/test_config_editor.py` (33). `tests/service/test_config_sections.py`
is deleted — five of its six ruamel canaries guarded a write path that no longer
exists; the load-bearing one (round trip is byte-identical) moved into
`test_config_editor.py`, because `service/sampledata.py` still round-trips the
contract and because an unstable render would mint a config version per call.

**Gated, not asserted.** Fast suite **729 passed, 3 skipped** (6:13). The money gate
— all eight golden windows re-run under DB-rendered config at zero tolerance, with
`RECON_REQUIRE_CLIENT_DATA=1` so an absent input fails rather than skips — **passed
with no cell moved**, which was the delta stated before running it.

**One performance fix fell out of it, and it predates this phase.**
`config_store.evidence_for` re-parsed the whole 400-line contract with ruamel on
every call, and the importer asks for evidence ~140 times per import — so seeding a
contract cost **4.8 seconds**, and 1.6's tests pay that per test. A four-entry cache
of the read-only parse takes it to **0.07s**, and the fast suite from **790s to
373s** — below the 473s it ran at before this phase, because the same cost was
already being paid by the M6 config tests. `parse()` itself stays uncached: it is
the one callers mutate.

### 1.7 Bring Lazada into the contract ✅

Closes **D4** and half of **A6**. Lazada was the last part of the domain contract
that could only be changed by editing Python.

- ✅ `WEEKLY_MAP`, `DAILY_MAP`, `SHEETS` and `STORE_PATTERN` are gone from
  `src/lazada.py`. They are `column_maps.lazada`, `sheet_names.lazada` and
  `store_from_filename.lazada`, read through `lazada.column_map()`,
  `sheet_name()` and `store_pattern()`. Those **hard-stop** rather than falling back
  to a copy kept in the module — two definitions of one header map is exactly what
  the move removed, so keeping one as a safety net would have re-created it
- ✅ The modules that imported those constants and said so in a comment now read the
  contract: `service/uploads.py`, `service/naming.py`, `tools/stage_exports.py`.
  `naming.pattern_for` is one lookup for all three platforms instead of a Lazada
  branch. Tests reach the same way via `tests/service/_contract.py`, because a test
  that hardcoded the spellings would be a *third* copy and the first to drift
- ✅ `ingest.check_stores` is wired into `_run_lazada`, which never called it at all
- ⬜ `expected_stores.lazada` is still **unpopulated — a business question**, not
  derivable from code. `check_stores` self-skips with a warning while it is, so the
  wiring is behaviour-neutral and lands first
- ✅ The upload guard no longer silently accepts an unrostered store. Removing the
  `if expected` test outright would 422 every Lazada upload, so absence is
  **reported** instead: the response carries `roster_checked: false` and a sentence
  saying nothing verified the storefront and the run will not check it either
- ✅ **A `dayfirst` nothing reads is not emitted.** A Lazada platform row would have
  put `dayfirst.lazada: false` into the contract, and `read_ledger` calls
  `pd.to_datetime` with no `dayfirst` at all. Migration `009` makes the column
  nullable, and null means "this reader does not consume it". Teaching `read_ledger`
  to honour it is a behaviour change — Lazada dates could shift by up to eleven days
  — and gets its own commit with its own stated delta
- **Expected delta: none — and measured.** All four Lazada golden windows re-ran:
  `matched 4 | moved 0`

### 1.8 The team-owned master ✅ (bar the part that is not engineering's to decide)

- ✅ **A11 confirmed against a running container, not read off a Dockerfile.** With
  the team's directory mounted exactly as `deploy/docker-compose.yml` mounts it
  (`${RECON_MASTERS_DIR}:/app/config/masters:ro`), the pre-fix image reports
  `masters file 'Lib & VAT rate.xlsb' not found — using CSV snapshots`. The file was
  present at `/app/config/masters/` the whole time; the code looked only at
  `/app/config/`. **Every containerised run has been on the snapshots.**
- ✅ Fixed: `masters.master_candidates` searches `config_dir` then
  `config_dir/masters`, honours an absolute `masters_file` as given, and the run log
  names *which* location answered. Verified in the container both ways — with the
  mount it reads the live master (118 fee names, 660 VAT SKUs, 4 non-1.08); with no
  mount it falls back and names both paths it looked in
- ✅ The snapshot fallback is a **finding** (`pipeline._masters_finding`), not a log
  line. Emitted before the platform runner so its position in the ordered
  `RunResult.findings` is fixed — that list's interleaving is committed inside
  `variances.json`'s digest, so a finding that could appear at different points
  would move a golden depending on what else happened
- ⬜ *Later, once the team agrees:* upload-and-parse the `.xlsb` into
  `config_fee_types` / `config_vat_sku` with a drift report. That closes
  [open question 8](11-OPEN-QUESTIONS.md) and removes a live file from the run path
  — but it changes how the team works, so engineering does not decide it alone

**How much did A11 actually cost?** Measured, not assumed: the live master currently
**matches the CSV snapshots exactly**, so the containerised runs that fell back were
not producing wrong numbers *today*. They would have the moment the team edited the
file — and nothing would have said so, because the drift report only runs when the
live master is found. That is the shape of this register's whole A-series: not a
wrong number, a control that was not running.

---

## Phase 2 — The rest of the correctness work ✅ (2026-08-18)

**Done. Golden gate re-run over all eight windows at zero tolerance: no cell moved.**
Two tasks turned out to be misdescribed in the table below, and both corrections are
recorded rather than quietly applied — 2.5 was checking the wrong digest, and 2.3's
premise had to be measured before it could be landed.

| | Task | Outcome |
|---|---|---|
| 2.1 | Reference totals from a user | ✅ `window_references` (migration `011`), named fields from `service/references.py`, form on the window page, picked up by the worker. **A run made in a browser can now be verified at all** — every one since M6 has reported UNVERIFIED because no screen ever sent figures |
| 2.1b | Stop the scary list | ✅ Web layer only. When *nothing* was supplied, one sentence replaces one line per store |
| 2.2 | Honest verification badge | ✅ `verification.capability()` says **before** an edit whether this deployment can check one, distinguishing "no digests" from "no inputs". The `unavailable` chip is no longer muted |
| 2.3 | Roster check on orders files | ✅ **Measured first, then landed.** Orders and income derive identical store sets on all four rostered golden windows (symmetric difference 0), so it adds no hard stop to any window that runs clean today |
| 2.4 | PII flag default | ✅ `False` → `True`. The fail-open direction was the leaking one |
| 2.5 | Digest-check materialised objects | ✅ …against `object_sha256`, **not** `uploads.sha256`. See below |
| 2.6 | De-duplicate `norm_store` | ✅ Imported from `src/pipeline.py` rather than copied |

**2.5 was written against the wrong column, and implementing it as specified failed
every healthy window.** The register said "`uploads.sha256` sits unused ten lines
later". It is not unused and it is not comparable: `sha256` digests the bytes the
user handed over, while the object store holds the *sanitized* rewrite — different
bytes, on purpose. The check needed a third value, so migration `010` adds
`object_sha256`. No backfill is attempted: recomputing it from whatever is in the
store today would certify the store against itself and pass even if the bytes had
already been replaced, which is the [D26](06-DECISIONS.md#d26) failure. `NULL` means
"predates the check" and is refused rather than trusted.

**One test-hygiene bug fell out of 2.1 and is worth naming.** `window_references`
was not in the per-test `truncate` list, so figures supplied by one test file leaked
into three worker tests in another and turned `UNVERIFIED` into `VARIANCE`. Caught
immediately because those tests assert the status. `windows` was truncated alongside
it — same shape of leak, same key, and every worker test reuses one synthetic window
name.

**Also landed here, at the user's request: the date counter.** Not a register item;
it closes the date half of [defect 1.6](08-KNOWN-DEFECTS.md). An unreadable date was
`errors="coerce"` with no counter, while an unreadable *amount* has hard-stopped
since M2.5. It produces a **missing** number rather than a wrong one —
`finance_template` groups on `.dt.month` and pandas drops a `NaN` group key — which
is quieter and worse. Now counted per column and reported, defaulting to `warn`
(a blank settlement date is legitimate), with `date_coercion: hard_stop` available.
The pandas warning that fires when a file's own format contradicts `dayfirst` is
captured too, and **it fires on real TikTok income today**: `%Y/%m/%d` against
`dayfirst.tiktok: true`. The durable fix — explicit `date_formats` per platform and
kind — is deliberately deferred to its own commit with its own stated delta.

2.4 and 2.5 are independent one-liners and can land any time.

| | Task | Register |
|---|---|---|
| 2.1 | **Let a user supply the team's reference totals.** New migration `008_window_references.sql` — `window_references (platform, period, refs jsonb, uploaded_by, uploaded_at)`, separate from `windows` because `windows.declared_by` is `not null`. Endpoints under `/windows/{platform}/{period}/references`, `uploaded_by` from the session; the worker picks them up the way it picks up the roster declaration (`service/worker.py:190-205` is the pattern). UI control beside the roster form | **A3** |
| 2.1b | **Stop the scary list** — when no references were supplied at all, say so once. **Web layer only** (`web/app/runs/[id]/page.tsx:52-57`): `RunResult.findings` order is committed inside `variances.json`'s digest, so changing what `_tie` emits would move goldens for no benefit | A3 |
| 2.2 | **Be honest about the verification badge.** `service/verification.py:115` needs `tests/goldens/manifest.json`, which no image ships, so the canary is `UNAVAILABLE` in every real deployment while the UI presents it as working. Surface that. Do **not** ship goldens into the image — that would put client-derived data in a container | **A2** |
| 2.3 | **Check orders files against the roster.** `check_stores` runs on income only (`src/pipeline.py:392,461`). Can only *add* hard stops — measure against all eight windows and report which would newly stop **before** landing it | **A7** |
| 2.4 | **Fix the PII flag default** — `src/ingest.py:243`, `False` → `True`. One line | **A8** |
| 2.5 | **Digest-check materialised objects** — `service/materialize.py:210` downloads by key while `uploads.sha256` sits unused ten lines later. Closes defect 2.10 | **A10** |
| 2.6 | **De-duplicate `norm_store`** — `tools/build_master_summary.py:58` copies `src/pipeline.py:50` and its docstring cites a file deleted in M6. Pure refactor | **A13** |

---

## Phase 3 — The month-end master, in the product ✅ (2026-08-19)

Absorbs **A4** and **A5**, and produces the first deliverable in this system that can
be checked against something the team made independently.

**Both blockers cleared on 2026-08-19.** The July raw exports arrived (1,051 files,
9.8 GB) and the month-end master arrived as a **per-tab CSV export** rather than a
delabelled workbook — enough to compare against. The underlying `.xlsx` is still
sensitivity-labelled and still unopenable here; that half is a hosting blocker
(**C15**), not a Phase 3 one. See [16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md).

| | Task | Outcome |
|---|---|---|
| 3.1 | Read the reference file | ✅ via the CSV export. Its shape is recorded in [07-VERIFICATION](07-VERIFICATION.md) rather than 05-DOMAIN-RULES, because it turned out to be *this system's own output shape* — see the note below |
| 3.2 | Builder into the verified pipeline | ✅ `src/master_summary.py` — pure compute plus the workbook build, returning an unwritten `Workbook`. `tools/build_master_summary.py` is now a thin CLI. `tests/test_io_boundary.py` needed no new grant |
| 3.3 | Window list from the database | ✅ `M5Repository.month_windows` unions runs, roster declarations and uploads. **A5 dies by construction** — `s2x`/`s3k` cannot be omitted by a list nobody maintains |
| 3.4 | Generate it as a chained job | ✅ `kind='month_master'` (migration `013`), own run record, log and artifacts, surfaced on the board as `month_masters` ([D55](06-DECISIONS.md#d55)) |
| 3.5 | Stamp what it covers | ✅ `Coverage` — a `Coverage` first tab naming included and missing windows, the banner repeated on `Summary`, and `UNVERIFIED` rather than `OK` when partial ([D56](06-DECISIONS.md#d56)) |
| 3.6 | Internal gate | ✅ `tests/test_master_summary.py` (8 tests) and `tools/build_master_summary.py --check`. Every column total is re-derived from its source window |
| 3.7 | External gate | ◐ **Run, and it found things — and the things are now largely fixed.** Lazada reproduces the team's master exactly on all five windows. TikTok reproduced 119 of 125 store-window cells; the six that differed were [defect 2.12](08-KNOWN-DEFECTS.md), a gap in the raw order exports. Shopee was blocked by roster drift and re-run after adding three July storefronts. **Since 2026-08-20** the cross-window fix is switched on: the month's gap is 4,527,401,608 → **1,579,645,766 VND**, `abbott pediasure` and `similac` tie exactly, `masan` closed 95.3%. What remains needs two order exports re-pulled ([16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md) Ask 3), not code. Still a *reproduction* gate, not independent ground truth — the reference is this system's own output ([open question 1](11-OPEN-QUESTIONS.md)) |

**Two things had to be fixed before any of this could run, and both are findings
in their own right.**

- **The TikTok date format** ([D54](06-DECISIONS.md#d54)). `dayfirst.tiktok: true`
  against `%Y/%m/%d` income inverted day and month on July data, deriving a 1–7 July
  window as `2026-01-07..2026-09-07`. Staging could not derive a single TikTok
  window from a 3.7 GB dump. Fixed with measured `date_formats`; **all eight golden
  windows regenerate with no cell moved.**
- **Shopee roster drift.** July introduced `Tolpa`, `pepsicofoods` and
  `xa_kho_gia_tot`, and the order files call Unilever AHC `AHC` while its income
  file spells it out. All four July Shopee windows hard-stopped on the
  unexpected-store check until the roster and one alias were extended — the check
  working as designed ([D3](06-DECISIONS.md#d3)).

**The reference is this system's own output, and that limits what 3.7 proves.** The
CSV tabs carry `src/master_summary`'s own f-string titles, `norm_store`-lowercased
row labels (`unilever chăm sóc gia đình…`), and the `UNMAPPED (kept as storefront)`
fall-through from `config/brand_map.csv`. [07-VERIFICATION](07-VERIFICATION.md)
already recorded that July "has not been externally tied — none existed at run
time". So this is a **reproduction** gate — raw exports → 14 windows → master →
the same figures — not independent ground truth. It is worth having, and it is not
the check [open question 1](11-OPEN-QUESTIONS.md) asks for.

---

### The original blockers, as recorded before they cleared

**Two blockers, both verified — and blocker 1 was diagnosed wrongly the first time:**

1. `input/master/ADA marketplace MASTER July 2026.xlsx` **is a real `.xlsx`**, and its extension is correct. It cannot be opened because it is encrypted by a **Microsoft Purview sensitivity label**: an OLE2 container holding `EncryptedPackage`, `DRMEncryptedTransform` and a `LabelInfo` stream reading `method="Privileged"`. Excel opens it for a person whose account has rights to the label; nothing else does. **The same label id and tenant is on one of the Lazada weekly exports**, so this is org policy rather than one bad file, and it is a constraint on hosting this system at all ([13-ENTRA-SETUP](13-ENTRA-SETUP.md)). The ask is in [16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md).

   *Recorded here on 2026-08-18 as "not an `.xlsx` — an OLE2 compound file, i.e. a legacy Excel 97–2003 `.xls` with the wrong extension", and corrected 2026-08-19 after reading the container's streams. The magic-byte check was right and the conclusion drawn from it was wrong: `D0 CF 11 E0` identifies OLE2, which is **also** what a rights-protected OOXML file looks like. `xlrd` opens it and finds no workbook stream, which is what gave it away. The lesson is narrow and worth keeping: **a container signature identifies the container, not what is inside it.***

2. **The July data to tie it against is not on this machine.** `output/` holds no `2026-07` windows and `input/original exports/` is the May-era set (75 files).

| | Task |
|---|---|
| 3.1 | **Read the reference file.** Needs the sensitivity label removed or extended — see blocker 1. Do **not** try to work around it with a reader: there is nothing to read until it is decrypted, and no library changes that. Then record its tabs, header rows and grouping in `05-DOMAIN-RULES.md`. **All tabs in scope except brand mapping**, which comes from `config_store_brands` |
| 3.2 | **Move the builder into the verified pipeline.** `src/master_summary.py` — pure compute plus the workbook build, taking the month's per-window frames and returning an unwritten `Workbook`, so `tests/test_io_boundary.py` needs no new grant. `service/` supplies the inputs the way `materialize_window` does. `tools/build_master_summary.py` becomes a thin CLI over it, keeping the CLI first-class ([D24](06-DECISIONS.md#d24)) |
| 3.3 | **The window list comes from the database** — which windows actually ran this month — not the hardcoded `WINDOWS` table at `tools/build_master_summary.py:34-40`. That kills **A5** by construction: `s2x` and `s3k` are real windows silently absent from that table, and its own tie-check re-reads the same list so it can never notice |
| 3.4 | **Generate it as a chained job.** A successful window run enqueues `kind='month_master'` for that month, with its own run record, log and artifacts. Not inline: a settlement run must not fail because a cross-month aggregation failed, and the window run's artifact set is golden-gated. Surface it on the month board so it appears without being asked for |
| 3.5 | **Stamp what it covers.** Rebuilt every time a window finishes, so it is partial most of the month. Name the included windows and the missing ones on the face of the workbook — a master that looks complete and is not is precisely the failure this project exists to prevent |
| 3.6 | **Internal gate (always available):** every figure ties to the per-window finance files it aggregated. `tools/build_master_summary.py:293-296` asserts this already — keep it, make it a test rather than a printed line. Give the master its own committed cellset digest |
| 3.7 | **External gate (needs July data):** compare tab by tab against the team's file. Until the July exports are staged this claim **cannot be made** and `07-VERIFICATION.md:115` stays open. When it runs, treat each difference as either documented-and-deliberate or a finding — the team's file is not automatically right; `08-KNOWN-DEFECTS.md` Part 2 records five defects already found in their workbooks |

---

## Phase 4 — Make failure legible ✅ (2026-08-18)

**Web-layer work, and it has the limit the whole web layer has: there is still no
browser automation.** Every screen below was reasoned about and typechecked, and the
service-side halves are tested. What nobody has done is *use* them. That is the same
gap [defect 2.8](08-KNOWN-DEFECTS.md) names for the M6 screens, it is not closed by
this phase, and it should not be read as closed.

| | Task | Outcome |
|---|---|---|
| 4.1 | No tracebacks in the browser | ✅ `service/failures.py`. `runs.error` is a sentence; the traceback goes to the run log. A `ReconHardStop` message passes through untouched — it is already written for a human and `09-OPERATIONS.md` quotes these strings |
| 4.2 | Error pages | ✅ `error.tsx`, `global-error.tsx`, `not-found.tsx`, `loading.tsx`, plus segment `not-found` for `/runs/[id]` and `/windows/…`, wired to real 404/422 responses |
| 4.3 | The run page updates itself | ✅ `RunRefresh` — `router.refresh()` every 4s while `in_flight`, latched off when it settles |
| 4.4 | Re-run, cancel, link back | ✅ `RunActions`. Re-run is confirmed, cancel is not — stopping something unfinished is recoverable, starting a second settlement run is the one that writes money twice |
| 4.5 | An unstick path | ✅ `admin job list\|reclaim`, `POST /jobs/reclaim` (**admin**), a board button that only appears when something *is* showing as running, a worker `HEALTHCHECK`, and `stop_grace_period: 400s` |
| 4.6 | Confirm destructive actions | ✅ Reset password, sign-out-everywhere and disable all ask first. The role `<select>` no longer commits on `onChange` |
| 4.7 | Smaller B10 items | ◐ `run_metrics.json` is split out of the finance-facing file list, and the SHA-256 column is now "Fingerprint" with an explanation. **Batch upload errors, the double-encoded download failure and the log poller's unbounded retry are NOT done** — see below |

**4.1 is a legibility fix, not a disclosure fix, and saying otherwise would be
comfortable and wrong.** `GET /runs/{id}` and `GET /runs/{id}/log` are both `VIEWER`,
so moving a traceback between them restricts nothing. What changes is that the first
thing a finance user sees on a failed run stops being a Python stack. The
`humanise()` fallback deliberately does **not** interpolate `str(exc)`: an
unrecognised exception's text is by definition not written for that reader, and half
of them contain a path or a connection string. A test asserts a fake password does
not survive it.

**Two bugs found in my own code by tests already in the tree.**

`humanise()` first gave `MaterializationError` a canned sentence, on the reasoning
that an exception class name is not an explanation. Three materialize tests failed
immediately, and they were right: that exception's own docstring promises to "always
name the file or the store", and the tests assert on exactly that text. Replacing
`upload 3 (…) is recorded but its bytes are not in the store` with "this window's
files could not be assembled" discards the most actionable message in the system to
satisfy a rule aimed at Python's own exceptions. `_OWN_MESSAGE` now exempts it
alongside `ReconHardStop`. **The rule is "is this text written for this reader",
not "is this an exception we recognise".**

`reclaim_expired` returns
`{"requeued": [...], "dead": [...]}` and both the endpoint and the CLI first read
`result["failed"]`. That is a *silent* no-op — the sweep runs, the rows change, and
the caller reports "nothing to reclaim". It was caught only because the test asserts
the job id comes back rather than asserting the call succeeded.

**Why the worker healthcheck is a file and not an endpoint.** The worker has no HTTP
server and is not getting one to answer a healthcheck — that would mean a port, a
framework, and a second thing that can fail inside a process whose whole job is to
hold one settlement run. It touches `scratch/worker.alive` at the top of each loop
turn. The 1200s threshold comes from the **lease** (900s), not the poll interval,
because the heartbeat is only touched *between* jobs and a worker legitimately inside
a 269-second Shopee run is silent. Anything tighter restarts healthy workers
mid-settlement.

**`stop_grace_period: 400s` fixes a real data hazard, not a tidiness issue.** Docker's
default is 10 seconds against runs of up to 269. Every `compose down` was SIGKILLing
a worker partway through `build_workbook`, leaving a leased job, an in-flight run and
a truncated `.xlsx` in scratch.

**Not done in 4.7, and named rather than quietly dropped:** batch upload error
reporting, the double-encoded download-failure path, and the log poller's unbounded
retry. They are independent of everything above and belong with Phase 5's pass over
the same screens.



| | Task | Register |
|---|---|---|
| 4.1 | **No tracebacks in the browser.** Split the run record: a short human `error` for everyone, full detail in the run log (already streamed, already role-gated). Add a translation layer at the API boundary — the pipeline's own messages stay as they are, since `09-OPERATIONS.md` depends on them | **B1** |
| 4.2 | **Error pages** — `error.tsx`, `not-found.tsx`, `loading.tsx`, `global-error.tsx`, plus `not-found` for the `/runs/[id]` and `/windows/…` segments | **B2** |
| 4.3 | **The run page must update itself** while `in_flight` and stop when it settles; `web/app/runs/[id]/log.tsx:47-57` has the right shape to copy | **B3** |
| 4.4 | **Wire re-run and cancel**, and link back to the window from the run | **B4** |
| 4.5 | **An unstick path:** `service/admin.py job list\|reclaim`, an admin endpoint and button, a worker `HEALTHCHECK` (`deploy/Dockerfile:83-91` declares one only for the api), and `stop_grace_period` — Docker's 10s default against a 171s run SIGKILLs mid-workbook | **C1**, **C2**, **C6** |
| 4.6 | **Confirmation on destructive actions**, and stop the role change firing on `onChange`. Reuse the typed-reason pattern from `file-row.tsx:67-98` | **B8** |
| 4.7 | Batch upload errors; double-encoded download failures; the log poller's unbounded retry and its `hard_stop`-styled failure; hide `run_metrics.json` from the finance-facing artifact list | **B10** |

---

## Phase 5 — Strip the jargon, add Vietnamese ✅ (2026-08-19)

**Done on the finance path, and the boundary is stated rather than blurred.** The
screens a finance user touches — the board, a run, a period, upload, the team's
figures, sign-in, the shell — render in Vietnamese by default. The **rules editor and
the accounts screen remain in English**, deliberately: they are operator screens, and
the rules editor's most important content is the per-row *evidence* text, which is
English in `settings.yaml` because that is where it was written and verified.
Translating the chrome around untranslated evidence would look finished and be worse
than not starting.

**The same limit as Phase 4 applies and has not moved: nobody has used these
screens.** `tests/test_ui_vocabulary.py` lints the source; it cannot tell you the
Vietnamese reads naturally to a Vietnamese speaker. Treat the first real session the
way [defect 2.8](08-KNOWN-DEFECTS.md) says to.

| | Task | Register |
|---|---|---|
| 5.1 | ✅ **Done in Phase 1.6.** Python paths are off the config screen entirely — the `read by …` caption is gone rather than disclosed, because nothing a finance user does with the screen needs it. `reader` stays a column so "a setting whose reader cannot be named is a setting nobody should be editing" is still enforceable, and it is now tested for the absence of a line number. | **B5** |
| 5.2 | ✅ **Vocabulary rewritten.** Every term on the list is gone from anything the browser paints, and `tests/test_ui_vocabulary.py` fails if one comes back. The *content* was kept — the missing-store panel and the duplicate-upload message say the same things, in words | **B6** |
| 5.3 | ✅ **Vietnamese**, via `web/lib/words.ts` — one dictionary, no framework, ~60 entries. `<html lang>` follows the reader. Default is the browser's `Accept-Language`, **falling back to Vietnamese**, and a toggle in the header overrides it | **B7** |
| 5.4 | ✅ [17-USER-GUIDE](17-USER-GUIDE.md) — Vietnamese first, English alongside, task-shaped rather than module-shaped | **D9** |
| 5.5 | ✅ `aria-live` on all 12 result banners (sign-in is `assertive`); a title per page; the 12px floor raised to 13 and body to 15; upload shows count and total size while posting. **Byte-level upload progress is NOT done** and is labelled as such — see below | **B9**, B10 |

**The four verdicts are the team's own words, and that is the decision worth
recording.** `ok` / `variance` / `unverified` / `hard stop` became *matches* / *does
not match* / *not checked* / *stopped* in English — and in Vietnamese they are
`ok có thể xuất HD` and `Cần check lại số có vấn đề`, lifted verbatim from
`src/finance_template.py`'s `VERDICT_OK` and `VERDICT_BAD`. Those are the phrases the
finance team already writes in their own workbooks. A more "correct" translation of
*variance* would have been fluent and would have made the screen read like a
different system from the file it produces. A test asserts the two never diverge.

**Why the default is the browser's language and not English.** The app shipped
`<html lang="en">` with zero Vietnamese because nobody had got to it, not because
anyone chose English. Reading `Accept-Language` means a Vietnamese browser gets
Vietnamese with nothing configured, and a maintainer's English browser still gets
English. Vietnamese wins ties: a finance user seeing English is a worse failure than
a maintainer seeing Vietnamese, because the maintainer can find the toggle.

**Upload progress is half-done, on purpose.** There is no byte-level bar: uploads go
through a server action, which gives the browser no progress events. A real bar means
rewriting the form as an XHR with `upload.onprogress` — and re-implementing the
per-file refusal handling, which is the part that matters when the fourth of eleven
files is the wrong kind. What is there instead is the honest half: the file count and
total MB while posting, so a 382 MB window does not look like a hang.

**Two things the Vietnamese changed beyond words.** Column headers lost
`text-transform: uppercase` — Vietnamese headers carry stacked diacritics that
capitalisation cramps and makes easy to misread — and body type went 14px→15px with
tighter leading relaxed, because ế/ộ/ữ lose their marks first at small sizes.

---

## Phase 6 — Before a hostname exists ✅ (2026-08-20, with the hosting-side halves named)

Scheduled because the target is cloud. None of it is optional once the system is
reachable. Every item below is done to the boundary of what this repository can
do; where a half belongs to the host (an offsite backup target, an alert route, a
real certificate), that half is **named in the status** rather than absorbed into
a ✅ it did not earn. Nothing in this phase touches `src/` — the golden gate had
nothing to say and was run anyway (structural gate green, manifest untouched).

| | Task | Register | Status |
|---|---|---|---|
| 6.1 | TLS and ingress; `serverActions.allowedOrigins`; a pinned proxy for `X-Forwarded-For` | **C4** | ✅ `--profile ingress`: nginx terminates TLS and is the pinned proxy (X-Forwarded-For is SET, never appended); `RECON_ALLOWED_ORIGINS` is a web build arg wired into `serverActions.allowedOrigins` (build-time by Next's design — an image built for a hostname is built FOR it); `deploy/ingress/make-self-signed.sh` mints the cert pair until a real one exists, into the out-of-tree certs dir. Config rendered and `nginx -t`-verified inside `nginx:1.27-alpine` with the real cert pair; **not yet exercised with a browser through it** — same standing limit as every UI surface (2.8) |
| 6.2 | **Postgres backup and a restore drill** — there is none, and the database is the whole audit trail | **C3** | ✅ `--profile backup` runs a nightly `pg_dump -Fc` into the `db-backups` volume (14 kept); `tools/db_restore_drill.sh` dumps, restores into a scratch database and compares **exact per-table row counts**. **The drill was performed, not just written** (2026-08-20, `recon_dev`: 30 tables, 1,037 rows, every count identical — docs/09 keeps the log). The hosting half, stated: a copy that leaves the machine |
| 6.3 | Observability: structured logging, metrics, error reporting, alerting | **C5** | ✅ logging + metrics; alerting is the host's half. `service/obs.py` emits one JSON object per line on stdout (api and worker, uvicorn's lines included) with a content rule stricter than the run log's: identifiers, counts and durations — never a store name, filename or figure. Worker events: `job_claimed`/`job_finished` (status, duration), `reclaimed_expired_leases` (WARNING — a worker died mid-job), `retention_sweep`, `migrations_applied`. `GET /metrics` (viewer role — every route names one) serves queue depth by state, 24h run outcomes, worker liveness, oldest-queued age. A `"level":"ERROR"` line is the alert hook; building an alerter inside the service would be config nobody wires |
| 6.4 | A worker-liveness signal in `/healthz` — "queued with no worker" is currently indistinguishable from "queued a second ago" | **C6** | ✅ `worker_heartbeats` (migration `017`), beaten from BOTH cadences — each idle loop turn and each lease extension, so a worker deep in a 269-second run stays visible. `/healthz` now answers `workers_alive` / `workers_known` / `worker_last_seen_seconds` on a 60s threshold; a stale row is kept, because *when it was last seen* is the diagnostic fact |
| 6.5 | Upload size limit — `service/api.py:848` reads the whole body into memory against 382 MB windows | **C7** | ✅ `RECON_MAX_UPLOAD_MB` (default 512 — measured: the largest export any platform has produced is 184 MB, July Shopee Lashe income, so ~2.8× headroom), enforced by a **bounded read** (`read(limit+1)`), refused 413 naming the limit before the sanitizer or store see a byte; the ingress mirrors it as `client_max_body_size` |
| 6.6 | Advisory lock around migrate-on-start | **C8** | ✅ `pg_advisory_lock` taken before the applied-list is read, released in `finally` (after a rollback, so a failed migration cannot leave the lock wedged). Pinned by a two-thread race test and a lock-release test (`tests/service/test_migrate_lock.py`) |
| 6.7 | Rate limiting beyond sign-in, at the BFF | **C9** | ✅ at the ingress, which is where the real client address is: per-IP zones for everything (30 r/s), for mutations (keyed on the Next-Action header — sign-in, queueing, uploads, config; page loads never touch it) and a tighter one on `/login`. The api's per-USERNAME throttle stays as the backstop it declares itself to be (`service/ratelimit.py`) |
| 6.8 | Read audit for workbook downloads | **C12** | ✅ `artifact_downloads` (migration `018`), appended after the digest check and before a byte streams — in the request path deliberately, an audit row allowed to fail silently is the gap repainted. A refused (tampered) download records nothing. Read via `python -m service.admin audit downloads [--run N]` |
| 6.9 | Secrets out of the OneDrive-synced tree | **C13** | ✅ the real `deploy/.env` was **moved** to `%LOCALAPPDATA%\recon-deploy\.env` (2026-08-20); compose is started with `--env-file`, `.env.example`'s header and docs/09 say so, and the ingress cert pair follows the same rule (`%LOCALAPPDATA%\recon-deploy\certs`) |
| 6.10 | Decide the git half of the config audit, which silently no-ops in containers — accept the database as the audit record and say so in the UI, or push to a remote | **C11** | ✅ decided as **[D60](06-DECISIONS.md#d60)**: the `config_versions` row is the audit record; git is a developer-checkout convenience. The apply response names the version row (`audit_record`) and explains a null commit (`git_note`); the config page explains it instead of a bare "not a git checkout". Pushing to a remote was rejected — a writable git credential in every api container is C13's shape |
| 6.11 | Retention for scratch, rejected uploads, log lines and sessions | **C10** | ✅ `service/retention.py`: aged `job-*` scratch (kept-on-failure dirs age out after the 14-day diagnosis window) and orphaned incoming files; `run_log_lines` of runs finished >90 days (the DB *mirror* — run_log.txt in the artifact store is the durable copy, and the runs row is never touched); sessions dead >30 days (live ones are never candidates). Plus a disk-free warning below 5 GB. Swept hourly by the worker and on demand via `service.admin retention sweep [--dry-run]`. Uploads in bucket mode stay owned by the MinIO lifecycle rule — a bucket rule cannot silently stop running |

---

## Not in this programme

- ~~**The exception queue** (**D1**) and the **window index on the board** (**D2**) — real workflow holes; the obvious block after Phase 5.~~ Done 2026-08-21, together with A4's remainder and D3 — see their rows above.
- **Staging the July raw exports.** Phase 3.7 depends on them. A data task, not engineering — raise it as soon as Phase 3 starts.
- ~~**`VAT_RATES` as a closed tuple** and the invoice bucket lists (**A14**). Phase 1's tables make it tractable; doing it in the same pass would mix a migration with a semantic change.~~ Done 2026-08-20, as its own change exactly as this bullet asked — see A14 above.
- **Reconciling the two brand mappings** (**D12**) — Phase 1.3 deliberately kept them apart. Its own commit, with the 28-store delta stated in advance.
- **Answering the open questions** (**E4**) — business decisions. But #1 (external ground truth), #2 (sign-off protocol) and #3 (rollback story) gate whether anything can be booked from at all.
- **Backfilling git history** (**E1**). Nothing recovers it; the discipline applies from here.

---

## How this was found, and what it does not cover

Found by reading the code — the pipeline, the service, the web app, the deployment and the docs — on 2026-08-18. Every entry cites a location, and the claims that could be checked by running something were.

**Not covered, and the omissions matter:**

- **Nobody used the system.** This is a code audit, not a usability study. The B-series says the register is wrong for a finance audience; only a finance user can say whether the *workflow* is. That test has still never been run.
- **Nothing was checked against a real deployment.** A11 (the masters mount path) and C11 (the git no-op in containers) are read off the Dockerfile and compose file and should be confirmed against a running stack before being treated as certain.
- **The money math is out of scope here.** It is covered by [07-VERIFICATION](07-VERIFICATION.md) and [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md), and nothing in this register contradicts them.
- **This is not a security review.** The auth posture was examined and is strong; a genuine review would test it rather than read it.
- **One claim made during Phase 1.1 was mis-attributed and is corrected here.** After the dead-code deletion the fast suite's `tests/goldens` was cited as proof that nothing moved. It is not: those tests compare committed digests against *static* golden cellsets and never execute `pipeline.run`, so they could not have detected a behaviour change either way. The claim happens to be true, and the evidence is the Phase 1.4 golden re-run (8 windows, 0 moved) which ran after the deletions — but the test named at the time did not test it. Recorded rather than quietly fixed, because "which test proves this" is exactly the kind of thing this project cannot afford to be loose about.

**The unifying observation from [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md#the-unifying-observation) still governs.** Both the manual process and this one have been operating without a functioning automated end-to-end control, and everything held because a person was comparing numbers. A3 is that observation restated as a defect: the product removed the person's ability to do the comparison at all.
