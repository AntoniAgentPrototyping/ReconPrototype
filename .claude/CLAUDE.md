# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Vietnamese e-commerce settlement reconciliation pipeline (TikTok Shop, Shopee, Lazada → classify → VAT/revenue calculation → tie-out → an Excel finance file the team invoices from). Every rule was reverse-engineered from the finance team's own Power Query / worksheet formulas and verified row-by-row against their outputs — **never invented**. That provenance is the project's entire value; see `docs/07-VERIFICATION.md` for the per-platform evidence and `docs/05-DOMAIN-RULES.md` for the rules themselves.

Money is VND (no minor unit). Tolerances are as tight as 10 VND.

## Environment

One venv, outside the repo (this folder is OneDrive-synced and `settings.yaml:31-34` documents sync contention causing 45+ minute runs):

| Venv | Contents | Purpose |
|---|---|---|
| `%LOCALAPPDATA%\recon-venv` | Python 3.12.10, pandas 2.3.3, fastapi/uvicorn/psycopg | Everything: pipeline, suite, golden generation, service |

**Postgres for the M4 service** lives at `%LOCALAPPDATA%\recon-pg` — the EDB *binaries zip*, not the MSI (which needs elevation and installs a Windows service). It runs as the current user on port **55432**, password in `%LOCALAPPDATA%\recon-pg\pgpass.txt`, databases `recon_dev` and `recon_test`. Start/stop:

```bash
PG="$LOCALAPPDATA/recon-pg/pgsql/bin"; DATA="$LOCALAPPDATA/recon-pg/data"
"$PG/pg_ctl.exe" -D "$DATA" -l "$LOCALAPPDATA/recon-pg/server.log" start   # or: stop
"$PG/pg_isready.exe" -h 127.0.0.1 -p 55432 -U recon
```

Delete the folder to undo it entirely. `fsync = off` is set — it is a test cluster, not a place to keep anything.

`pandas>=2.2,<3` in `pyproject.toml` is a **control, not laziness** — pandas 3.0 makes Copy-on-Write default, which changes whether `finance_template.py:159`'s in-place mutation is visible to its caller. `requires-python<3.14` is different: a **temporary** artifact of pandas 2.x having no cp314 wheels, and the bound to lift when pandas ships 3.14 support.

There was a second venv (`recon-polars-venv`) for an engine migration that was **descheduled 2026-08-12** — do not recreate it, and do not run the suite "in both venvs".

## Commands

```bash
PY="$LOCALAPPDATA/recon-venv/Scripts/python.exe"

# Tests
"$PY" -m pytest                                    # full suite

# OPT-IN Excel read cache for the inner loop. Content-addressed Parquet, keyed on
# sha256(file bytes) + sheet + header_row + engine, so an edited export or a changed
# reading rule MISSES. Measured on the two tests that are 67% of the suite:
# 357s -> 210s. It is OFF by default and must stay off for any run you intend to
# cite — a run served from a cache is a weaker run (tests/io_cache.py).
export RECON_TEST_IO_CACHE=1
"$PY" -c "import sys; sys.path.insert(0,'tests'); import io_cache; io_cache.clear()"
"$PY" -m pytest tests/goldens -q                   # workbook golden gate
"$PY" -m pytest tests/test_io_boundary.py -q       # I/O-boundary lint
"$PY" -m pytest tests/test_tieout_blindness.py::test_clean_data_passes_all_checks
"$PY" -m pytest -k rounding                        # by keyword

# Service tests need a Postgres and create/drop their own database inside it.
# NEVER point this at RECON_DATABASE_URL.
export RECON_TEST_DATABASE_URL="postgresql://recon:$(cat "$LOCALAPPDATA/recon-pg/pgpass.txt")@127.0.0.1:55432/recon_test"
"$PY" -m pytest tests/service -q

# A run, for a DEVELOPER. Users go through the browser since M6 — this is for
# regenerating a golden or debugging a window staged by hand.
"$PY" tools/devrun.py --platform tiktok --period 2026-05_w1 [--refs refs.json]
"$PY" tools/devrun.py --platform tiktok --period 2026-05_w1 --partial-roster

# The service (a deletable wrapper — see below)
export RECON_DATABASE_URL="postgresql://recon:<pw>@127.0.0.1:55432/recon_dev"
"$PY" -m service.api                      # 127.0.0.1:8080, migrates on start
"$PY" -m service.worker --once            # claim one job and exit

# Auth: the FIRST identity cannot come from the api (creating one needs an admin
# credential), so it comes from here — issuing an identity needs more access than
# using one. `reset-password` is BREAK-GLASS and is why this CLI cannot be deleted
# once the admin UI exists.
"$PY" -m service.admin user create --username you@ada --role admin
"$PY" -m service.admin user list
"$PY" -m service.admin user reset-password --username you@ada
"$PY" -m service.admin config pins         # which windows are frozen to which config

# The queue, and the way out of a stuck one (M8/4.5). `leased` alone cannot tell
# "running normally" from "the worker is gone", so `job list` prints the LEASE.
# `reclaim` closes out expired ones and, at the default max_attempts=1, does NOT
# re-run them — an automatic retry is a second write of the same money (D30).
"$PY" -m service.admin job list [--state leased]
"$PY" -m service.admin job reclaim

# Configuration lives in Postgres since M8 (docs/14). settings.yaml is the SEED and
# the CLI's input; an unpinned run renders its contract from the config_* tables, so
# an edit reaches the worker rather than only the api's own container. Since M8/1.6
# the tables are also what the EDITOR writes — `service/config_rows.py`, two ops on
# eleven tables. `service/api.build_app` seeds them on first boot; this is the same
# thing by hand, and the only way to do it for a bare `create_app` (a test double).
"$PY" -m service.config_import                    # seed the tables from config/
"$PY" -m service.admin config export              # tables -> settings.yaml (CLI path)

# The synthetic demo window: three platforms, deterministic, its own pinned config.
# Admins seed it from the browser (POST /demo/seed); this is the on-disk form.
"$PY" -m service.sampledata --out .scratch/demo

# How much non-NFC text is in the values hashed as exception identities.
# Read-only; reports COUNTS ONLY, because identity columns include store names.
"$PY" -m service.nfc_audit [--database-url "$RECON_DATABASE_URL"]

# The web app (M5). Node 26; node_modules must NOT sync to OneDrive.
cd web && npm install && npm run build && npm run dev      # localhost:3000

# The whole stack in containers: db + MinIO + api + worker + web.
# Brought up and exercised end to end on Docker 29.7.2, 2026-08-17.
# There is NO input mount any more — exports arrive through the browser.
cd deploy && cp .env.example .env && docker compose up --build

# The month-end master (M8 Phase 3). Windows come from the DATABASE in the
# service and from a directory listing in the CLI — never a hardcoded list.
# --check re-derives every column total from the window it was read from.
"$PY" tools/build_master_summary.py --month 2026-07 --out out.xlsx --check

# Compare our master against the team's, tab by tab. Their .xlsx is
# sensitivity-labelled and unopenable, so this reads a PER-TAB CSV export of it
# (input/master/*.csv, cp1252 — the Vietnamese did not survive Excel's export).
"$PY" tools/compare_master.py --month 2026-07

# Workbook goldens. Moving a baseline REQUIRES --rebaseline --reason.
# The flag must MATCH the committed provenance or the refusal is about the flag,
# not a moved cell: --partial-roster for the shopee/tiktok windows, NOT for the
# four lazada ones (they are `partial_roster: false` in the manifest).
# Staging DERIVES the window from each export's settlement dates. Plans by
# default; --apply copies. --period forces one named window (deliberate subset).
"$PY" tools/stage_exports.py --platform shopee            # plan only
"$PY" tools/stage_exports.py --platform shopee --apply
"$PY" tools/make_golden.py   --platform lazada --period 2026-05_l1
"$PY" tools/make_golden.py   --platform tiktok --period 2026-05_w1 --partial-roster

# Engine-port trigger dashboard
"$PY" tools/metrics_report.py --month 2026-05 --container-mb 4096

# Row-level verification against the team's own intermediary files
"$PY" tools/calc_verify.py           # TikTok  (each has a usage header)
"$PY" tools/calc_verify_shopee.py
"$PY" tools/calc_verify_lazada.py

# Synthetic self-test, needs no real data
"$PY" tools/smoke_test.py
```

Expected today: **`840 passed, 3 skipped, 4 deselected`** in ~7:00 with `RECON_TEST_DATABASE_URL` set and `RECON_REQUIRE_CLIENT_DATA=1` (measured 2026-08-19 after M8 Phase 3, `-m "not slow"`); the ~570 service tests skip loudly with no database. This line said `630` from 2026-08-17, `731` until 1.6 replaced the config-editor tests, `741` until Phases 2/4/5, and `816` until Phase 3 added `date_formats` (which fans out the config-parametrized tests), `tests/test_master_summary.py` (8) and `tests/service/test_month_master.py` (8) — treat a mismatch as a stale doc first and a regression second, but check.

**`tests/goldens/` in the fast suite is a STRUCTURAL gate: it compares committed digests against the *static* golden cellsets in `%LOCALAPPDATA%\recon-goldens` and nothing there calls `pipeline.run`.** So a green fast suite is **not** evidence that output is unchanged — do not cite it as one.

That sentence used to read "the fast suite does not re-run the pipeline on real data", which is false of the suite as a whole and was corrected on 2026-08-18 after measuring. Three service tests *do* run the real pipeline over real windows, because that is the only way to prove their claim, and two of them dominate the wall clock (`--durations=25`, 730 tests, 706s):

| Test | Time |
|---|---|
| `test_a_sanitized_renamed_window_produces_the_committed_golden[2026-05_s1-shopee]` | 269s |
| `…[2026-05_w1-tiktok]` | 201s |
| `test_golden_matches_committed_digest[2026-05_s1/shopee]` | 11.6s |
| every other test | under 5s |

Two tests are 67% of the suite. `RECON_TEST_IO_CACHE=1` roughly halves them (see the commands above); it is **off by default** and must stay off for anything you intend to believe. The money gate is `pytest tests/service/test_config_render.py::test_config_render_produces_the_committed_goldens` (~11 min), which re-runs all eight windows and compares fresh workbook digests, or `tools/make_golden.py` per window. The three unconditional skips are polars imports in `test_rounding_modes.py`, kept from the descheduled port. One test is
marked `slow` — it reads every raw export (~195s) to prove staging derives the
same windows a human staged by hand. It is **not** deselected by default; use
`-m "not slow"` for the fast inner loop (~13s). There are no strict xfails left — every pinned control gap is closed, so an empty list is the correct state, not a missing one; a newly found gap needs a newly pinned test (and the `control_gap` marker re-declared in `pyproject.toml`, removed with the last of them). The `pytest.importorskip("pandas")` guards are vestigial — they existed so collection survived a pandas-free runtime.

## Architecture

**Two callers, one pipeline.** `tools/devrun.py` (developer CLI) and `service/worker.py` both do exactly: `build_context` → `run` → `log_result` → `write_artifacts`. The worker prefixes one step since M6 — `materialize_window`, which downloads the window's uploads into scratch — and that step is in `service/`, so `run()` still writes nothing. All four live in `src/pipeline.py` *because* there are two callers — they moved out of the CLI in M4 since the worker's copy is the one that would drift, starting with the `_vat_sku` back-channel ([D28](../docs/06-DECISIONS.md#d28)). `tools/full_run.py` became `tools/devrun.py` in M6 (users are browser-only now); `recon.py` and the unverified placeholder path were **deleted in M1**; deleting them left every golden manifest unchanged, which is the proof they were unreachable.

**The seam is the structure to preserve.** `src/pipeline.py`: `run(ctx) -> RunResult` reads inputs and **writes nothing**; `write_artifacts(result)` is the only writer in the codebase. `tests/test_io_boundary.py` enforces this per-function — do not add a write inside `run()` or a `_run_*` platform function. `RunResult.findings` is one ORDERED list, not two: the interleaving is committed inside `variances.json`'s digest.

**`service/` is a deletable wrapper, and three tests keep it that way.** `service/` imports `src/`; `src/` and `tools/` must never import `service/`, and `service/` must never import `tools/` (the container ships `src/` + `service/` only). `tests/service/test_service_is_deletable.py` also denies `import service` at the interpreter level and runs the pipeline anyway, which catches the lazy in-function import a lint cannot. Do not put a web framework or a DB driver in `src/` — the I/O-boundary lint scopes to `src/**` and exempting it expires the deferred-engine-port bet ([D25](../docs/06-DECISIONS.md#d25)).

**The worker adds no compute and no formatting.** It writes artifacts through the same `write_artifacts` into a scratch dir, then an `ArtifactStore` takes the files — never its own writer, because that would be a second unverified implementation of the deliverable ([D31](../docs/06-DECISIONS.md#d31)). `QueueRunLog` *subclasses* `RunLog` so `run_log.txt` cannot drift from the CLI's ([D34](../docs/06-DECISIONS.md#d34)). Job state and run status are separate axes: a hard stop is `state=done, status=hard_stop`, and `max_attempts` defaults to 1 because retrying a settlement run is a second write of the same money ([D30](../docs/06-DECISIONS.md#d30)).

**Three platform paths, two shapes.** TikTok and Shopee share `ingest → stitch → classify → calculate` (orders + income files). Lazada is a self-contained vertical in `src/lazada.py` (a fee-event ledger, no order files). Its column maps, sheet names and filename regex were hardcoded there until **M8/1.7**, and are now `column_maps.lazada` / `sheet_names.lazada` / `store_from_filename.lazada` like every other platform's; `lazada.column_map()`, `sheet_name()` and `store_pattern()` read them and hard-stop rather than falling back to a copy. Its invoice BUCKET names are still code (`REVENUE_BUCKET`, `PROMO_BUCKETS`) — that is A14.

**`config/settings.yaml` is the contract** — column maps per platform, store rosters and aliases, VAT default (the 8%→10% revert is one line), tolerances. Its **in-line comments are the audit trail**: they cite the verifying script, row counts, order-ID-overlap proofs, and the specific broken `<dimension>` tag behind a reader-engine choice. Any tooling that round-trips this file must preserve comments (`ruamel.yaml`, not `PyYAML`).

**Store identity comes from the filename, not a column** (`ingest.store_from_filename`, `lazada.py:99`). TikTok/Shopee exports carry no store column. This is the single most invasive thing to change if inputs ever become API responses — and since M6 it is also why `service/naming.py` exists: uploads are renamed to a uniform scheme, and `validate_roundtrip` re-runs *that same function* on every generated name rather than a copy of it. Measured 73/73 on the real tree, and gated by the golden comparison on all three platforms.

**Export layering.** `src/finance_template.py` is the live export and is a *second compute layer* (~24 grouped aggregations, the invoice rounding model at `:171-178`, ~500 of its 695 lines are computation). Its frame column order **is** the Excel header row. `src/export.py` writes only `exceptions.xlsx`, wired in since M2. `src/export_platforms.py` was deleted in M1.

**Masters.** `config/Lib & VAT rate.xlsb` is a team-owned live file (fee-type buckets + per-SKU VAT exceptions) read at runtime, with `config/lazada_*.csv` as snapshot fallbacks and drift reported every run.

Data lives in gitignored `input/<period>/<platform>/...` — TikTok/Shopee use `{orders,income}/`, Lazada uses `{Weekly,Daily}/`. Window naming: `2026-05_w1..w5` (TikTok), `_s1..s4` plus sub-batches like `s2x`/`s3k` (Shopee), `_l1..l5` (Lazada).

## Current work: M6 is complete — what to do next is in `docs/10-ROADMAP.md`

`~/.claude/plans/want-you-to-explore-tingly-wolf.md` is the **M1** plan and `M6-PLAN.md` is the **M6** plan; both are now history. The roadmap in `docs/10-ROADMAP.md` is authoritative for what comes next, and `docs/12-CHANGE-HISTORY.md` records where each plan turned out to be wrong — M6-PLAN in particular asserted a leaked password that did not exist, and claimed removing a commented config item takes its comment with it, which holds only for an EOL comment. **The polars migration was descheduled 2026-08-12** ([D25](../docs/06-DECISIONS.md#d25)) — do not propose it, do not write polars, do not reinstate a pandas "oracle" framing. pandas stays; a port is trigger-gated on instrumented thresholds in `docs/10-ROADMAP.md`.

**M4 is complete (2026-08-13).** `service/` is FastAPI + worker + Postgres: job queue on `FOR UPDATE SKIP LOCKED`, run record, run log streamed to the DB mid-run and polled by `?after_seq=N`, artifact index. Verified against a real PostgreSQL 17.10, and the gate that mattered: a worker's `2026-05_l1` workbook matches the committed golden digest across all 12 tabs and 2,193 cells at zero tolerance. The three `src/pipeline.py` moves were proven output-identical by regenerating **all eight** golden windows with zero refusals.

**M5 is complete (2026-08-14).** Authentication (bearer tokens, three roles, fails closed), period-versioned config, a config editor that writes, the upload/PII boundary, and a Next.js BFF in a container. Defects 2.1, 2.2, 2.3 and 2.5 are closed.

**M6 is complete (2026-08-17).** The system is browser-only for users. Passwords + opaque sessions replaced pasted tokens; uploads go to a MinIO/S3 bucket under a uniform naming scheme and the worker materialises each window into its own scratch at run time; the per-run `partial_roster` checkbox became a per-window declaration with a mandatory reason; the config editor became sections with purpose-built widgets, each field carrying its own comment block as evidence; applying a goldens-affecting config change now measures whether a cell actually moved; and `service/sampledata.py` generates a deterministic three-platform demo window. **No phase moved a committed golden digest.** Defects **2.4** and **2.7** are closed, **2.8** is closed by inspection, and **[open question 13](../docs/11-OPEN-QUESTIONS.md) is answered**.

**Do not "discover" these — they are named in `docs/08-KNOWN-DEFECTS.md` Part 1b:** the config verification run is **synchronous and checks one window**, so a change that moves cells only elsewhere still reports `verified` (2.11); the run log in Postgres names stores (2.6, accepted); `tieout.py` still keys its coverage reference on `order_id` alone (**2.9** — renumbered in M6, there used to be two 2.7s); SSO itself is still unbuilt, and `docs/13-ENTRA-SETUP.md` is the permissions escalation for it.

**Also do not re-discover:** the web UI still has **no browser automation**. 2.8 is closed because the *old* screens were exercised by hand — that session is the origin of M6's scope — but the screens M6 *adds* have the same limit.

**M8 Phase 2 is complete (2026-08-18)** — the correctness work. A user can supply the team's reference totals (`window_references`, migration `011`), so a browser-driven run can be **verified at all**; every one since M6 reported UNVERIFIED because no screen ever sent figures. Materialised objects are digest-checked — against **`object_sha256`** (migration `010`), *not* `uploads.sha256`, which digests the original upload while the store holds the sanitized rewrite; comparing those two fails every healthy window and is why [D52](../docs/06-DECISIONS.md#d52) exists. The roster check reads orders files too (measured first: orders and income derive identical store sets on all four rostered golden windows). `drop_unmapped_columns` now defaults to **True** in `src/ingest.py`, so the fail-open direction is no longer the PII-leaking one. **The golden gate was re-run over all eight windows at zero tolerance: no cell moved.**

**M8 Phase 3 is complete (2026-08-19)** — the month-end master, and the first tie against a July month-end file. `src/master_summary.py` is pure compute plus the workbook build (unwritten `Workbook`, no new I/O grant); `service/month_master.py` supplies its inputs the way `materialize_window` does; `tools/build_master_summary.py` is a thin CLI over both. **The window list comes from `M5Repository.month_windows`** — a union of runs, roster declarations and uploads — which kills register item A5 by construction: the hardcoded `WINDOWS` dict silently omitted the real sub-batch windows `s2x`/`s3k` *and* its own tie check re-read the same dict. A finished window enqueues `kind='month_master'` (migration `013`) with its own run, log and artifacts ([D55](../docs/06-DECISIONS.md#d55)); a master that does not cover its month reports `UNVERIFIED` and names the gaps on a `Coverage` tab ([D56](../docs/06-DECISIONS.md#d56)).

**July is staged and run: 14 windows, 1,051 files, 9.8 GB.** `input/2026-07_{l1..l5,s1..s4,w1..w5}`. Lazada and Shopee derive their windows automatically; **TikTok must be staged per folder with an explicit `--period`**, because two genuine mis-pulls and a one-day boundary overlap make its folder spans overlap, so derivation collapses all five into one window.

**The July tie found real things — read `docs/08-KNOWN-DEFECTS.md` 2.12 before re-deriving any of it.** Lazada reproduces the team's master **exactly** on all five windows (17 of 18 storefronts to the dong; one differs by 1 VND, which is display rounding in their CSV). TikTok reproduces 119 of 125 store-window cells; the six that differ total 2,417,721,642 VND and are all one cause — **a window's order export does not cover the orders it settles**, and for `purite` and `mondelez kinh do` the w2 order file is byte-identical to w1's. **Do not "fix" that by pooling the month's order files**: measured, pooling takes w2 to 183B against a reference of 40B, because the same order line appears in several exports and `dedupe_rows: false` is deliberate.

**The reference master is this system's OWN output, and that is recorded rather than assumed.** Its CSV tabs carry `master_summary`'s f-string titles, `norm_store`-lowercased row labels and the `UNMAPPED (kept as storefront)` fall-through; `docs/07-VERIFICATION.md` already said July "has not been externally tied — none existed at run time". So 3.7 is a **reproduction** gate, not independent ground truth, and [open question 1](../docs/11-OPEN-QUESTIONS.md) is still open. The user confirmed the file is legitimate and received, and it is used on that basis.

**`date_formats.<platform>.<kind>` replaced date inference ([D54](../docs/06-DECISIONS.md#d54)) — the fix the previous note said was deferred.** It is no longer deferred and **must not be reverted to `dayfirst`**. Every value was measured against real May *and* July exports (100% of non-blank cells; 80 files for Lazada alone), and **all eight golden windows regenerate with no cell moved**. Two things the measurement found: TikTok income really is `%Y/%m/%d` against `dayfirst.tiktok: true`, which on July data derives a 1–7 July window as `2026-01-07..2026-09-07`; and **Lazada's two variants disagree with each other** — weekly is `%d-%b-%Y`, daily is `%d %b %Y` — so the ledger is parsed per variant. One format over the concatenated frame would `NaT` every row of whichever variant lost, and the 25th-to-month-end Daily week is a permanent monthly fixture.

**`window_settlement_bounds` has a trap that is now commented in `settings.yaml`: do NOT add `2026-07_w5`.** The 29-31 July folder's `16. Income Curel 07.xlsx` spans the whole month and looks exactly like the w2 mis-pull. It is the **only** Curel income file in the entire July dump, and its 772 early rows carry 230,946,293 VND that appears in no other window. The w2 bound is correct and was re-verified: of the 24,555 rows it drops, **24,546 of 24,546 distinct order ids are already in w1** — zero unique.

**July Shopee needed roster maintenance, which is normal monthly drift.** Three new storefronts (`Tolpa`, `pepsicofoods`, `xa_kho_gia_tot`) and one alias (`AHC` → `Unilever AHC`, because the order files abbreviate what the income file spells out). All four Shopee windows hard-stopped on the unexpected-store check until these landed — the check working, not failing.

**Dates are counted now, and the counter already found something.** `ingest.parse_dates` / `report_undated` close the date half of defect 1.6 — an unreadable date was `errors="coerce"` with no counter while an unreadable *amount* has hard-stopped since M2.5. Default is `warn` (a blank settlement date is legitimate), `date_coercion: hard_stop` is available ([D53](../docs/06-DECISIONS.md#d53)). The pandas warning fired when a file's format contradicts `dayfirst` is now captured, and **it fires on real TikTok income**: `%Y/%m/%d` against `dayfirst.tiktok: true`. The dates are right today only because that column's first value is unambiguous — pandas infers the format from the first element and overrides the flag. **Do not "fix" this by flipping `dayfirst`**; the fix is explicit `date_formats.<platform>.<kind>`, and it **has shipped** — see the D54 paragraph above, which supersedes the "deferred to its own commit" note that used to end this line. Lazada's undated counts are reported per *variant* under the format that actually parsed them, because one collapsed `lazada/ledger` line named `dayfirst=False`, a setting Lazada does not consume.

**M8 Phase 4 is complete (2026-08-18)** — making failure legible. `service/failures.py` turns a worker crash into a sentence on `runs.error` and puts the traceback in the run log; a `ReconHardStop` message passes through untouched because `docs/09-OPERATIONS.md` quotes those strings. The web app gained `error.tsx` / `global-error.tsx` / `not-found.tsx` / `loading.tsx` and segment 404s; the run page refreshes itself while in flight and stops when it settles; re-run and cancel are on the run page (re-run confirmed, cancel not); the role `<select>` no longer commits on `onChange`. **The unstick path exists**: `python -m service.admin job list|reclaim`, `POST /jobs/reclaim` (admin), a board button shown only when something is showing as running, a worker `HEALTHCHECK` reading a heartbeat file, and `stop_grace_period: 400s` against Docker's 10s default.

**`service/failures.py` translates a worker crash into a sentence — but `ReconHardStop` and `MaterializationError` pass through UNTRANSLATED** (`_OWN_MESSAGE`). Their messages are written for this reader and name the store or the file; `docs/09-OPERATIONS.md` quotes the first verbatim and three materialize tests assert on the second. The rule is "is this text written for this reader", not "is this an exception we recognise".

**`reclaim_expired` returns `{"requeued", "dead"}` — not `"failed"`.** Reading the wrong key is a *silent* no-op: the sweep runs, rows change, the caller reports "nothing to reclaim". That bug was written and caught in the same phase.

**Still true after Phase 4: there is no browser automation.** Every M6 and Phase 4 screen was reasoned about and typechecked, and none was exercised by a person or a test. Do not read a green suite as evidence that a screen works.

**Two files in the data tree are encrypted by a Microsoft sensitivity label, and neither was diagnosed correctly until 2026-08-19.** `input/master/ADA marketplace MASTER July 2026.xlsx` and one Lazada weekly export carry the **same label id and tenant** (`method="Privileged"`). Both are genuine `.xlsx` files. The register called the first "a legacy `.xls` with the wrong extension" and four documents called the second "password-protected" — the `D0 CF 11 E0` signature is the *encryption wrapper*, not a legacy workbook, and `xlrd` finds no workbook stream inside. **A container signature identifies the container, not what is inside it.** `ingest.rights_protected` now detects it from bytes (stdlib only, 8 bytes for a healthy file) and gives three different answers for three different causes. **There is no password to ask for** — hosting this system requires the label to grant rights to the identity it runs as, which is register item **C15** and `docs/13-ENTRA-SETUP.md`, not something engineering can build around.

**M8 Phase 5 is complete (2026-08-19)** — the vocabulary and Vietnamese. `web/lib/words.ts` is the whole i18n layer: one dictionary, no framework, ~60 entries. `<html lang>` follows the reader; the default is the browser's `Accept-Language` **falling back to Vietnamese**, with a header toggle. `tests/test_ui_vocabulary.py` fails if `Peak RSS`, `exit code`, `DataFrame`, `openpyxl`, `calamine`, `SHA-256`, `service.admin` or `hard stop` reaches anything the browser paints — comments and wire-format names are exempt, so the record of *why* a term went survives the lint.

**The four run verdicts use the TEAM's own words in Vietnamese** — `ok có thể xuất HD` and `Cần check lại số có vấn đề`, lifted verbatim from `finance_template.VERDICT_OK` / `VERDICT_BAD`. A test asserts the screen and the workbook never diverge. **Do not "improve" these into more standard Vietnamese**; the point is that the interface and the file it produces speak one language.

**Translated on the finance path only.** Board, run, period, upload, the team's figures, sign-in and the shell. The **rules editor and accounts screen stay English on purpose**: the rules editor's load-bearing content is the per-row evidence text, which is English in `settings.yaml` because that is where it was written and verified, and translating the chrome around untranslated evidence would look finished and be worse.

**Invariants worth not eroding.**
- Every endpoint names a role, and `tests/service/test_auth.py::test_the_required_role_of_every_route_is_declared` walks the router against an **explicit route→role table** asserted in both directions. The old single-sided "every mutating route needs at least operator" check would have passed an ADMIN route silently dropping to USER.
- `requested_by`, `uploaded_by`, `declared_by`, `proposed_by` come from the **session**, never the body.
- `requires(role)` must keep `role` as a closed-over `Role` object — the router walk reads it out of `dependency.__closure__`. Rewriting it as `Annotated[...]` or decorator-level `dependencies=[...]` makes those tests pass **vacuously**.
- `service/config_store.py` uses **`ruamel.yaml`, never `PyYAML`** — the round trip is byte-identical on the real `settings.yaml` and PyYAML would silently discard every comment ([D2](../docs/06-DECISIONS.md#d2)).
- Config **evidence is read from the file's text**, not from ruamel's `.ca` — measured, that attaches a block to the key *preceding* the one it documents, so `.ca` would caption nearly every field with the previous field's justification ([D42](../docs/06-DECISIONS.md#d42)). That is now the **importer's** rule: once a key is a row, its justification is the row's `evidence` column and the editor serves it from there.
- Config is edited as **rows**, not as paths into a file. `service/config_rows.py` holds the closed registry of eleven tables and two operations (`upsert`, `delete`); `service/config_edits.py` and `service/config_schema.py` were **deleted in M8/1.6**. A table that refuses a new row says why in a sentence and the refusal quotes it. `invalidates_goldens` is a column read off the row — never inferred from a path — and `verification.verify` takes the answer rather than computing it.
- The upload sanitizer strips using the pipeline's own column map **and preserves the file's shape** (band rows, junk rows, multi-sheet income), because `read_parts` applies those to whatever it writes. `test_a_sanitized_renamed_window_produces_the_committed_golden` — now **all three platforms**, and covering the rename too — is what makes rewriting an export before the verified pipeline safe. It covered Lazada alone through M5, which is exactly why the shape bug survived that long.
- `service/` must never import `tools/`, and `src/`/`tools/` must never import `service/`. Materialisation lives in `service/` for that reason: `run()` still writes nothing and `tests/test_io_boundary.py` needed no new grant.

**M1 and M2 are complete** (2026-08-13). M1 built the seam, metrics and I/O lint; M2 rebuilt the tie-out checks so they can fail, consumed the result, armed Shopee's roster, split the exit codes 0/1/2/3, wrote `exceptions.xlsx`, and NFC-normalized headers. The `PARITY:` markers in `src/pipeline.py` are gone.

**M1, M2 and M2.5 are all complete (2026-08-13).** M2.5 closed: the four remaining pinned defects (silent VAT default, `order_id` fan-out ×2, silent numeric coercion), **Shopee's money crossing** (the team's June `Net revenue` formula, once their file arrived), **golden coverage** (3 windows → 8, two stores on each platform's primary window), and the **staging normalizer** — `tools/stage_exports.py` derives each window from its exports' settlement dates and `tools/stage_july.ps1` is deleted.

**Staging is where the remaining pipeline-side sharp edges are**, and they are named rather than latent: order-ID-level cross-window overlap is still manual, and one Lazada weekly export is **encrypted by a Microsoft sensitivity label** and cannot be staged (it was recorded as "password-protected" until 2026-08-19 — there is no password to ask for; see `ingest.rights_protected`). (`--partial-roster` closed the third one in M4 — a single-store run no longer needs a config edit. It survives on `tools/devrun.py` and `tools/make_golden.py` as **developer** tooling; the user-facing per-run toggle is gone, replaced by the per-window declaration ([D46](../docs/06-DECISIONS.md#d46)).)

**Instrumentation gotcha worth not repeating:** `metrics.StageKind` has THREE values. `build_workbook` is `serialize` (openpyxl materialization, engine-independent), not `compute`. Tagging it `compute` reports a 31% compute share and fires the engine-port trigger; correctly tagged it is ~2%.

The governing principle survives the descheduling: **the committed tree's authority is provenance, not correctness.**

- **Never mix a refactor with a semantic fix.** A structural change must be output-identical; behaviour changes get their own commit with the expected delta stated in advance.
- Goldens live **outside the repo** (`%LOCALAPPDATA%\recon-goldens\<period>\<platform>\`); only digests are committed, in `tests/goldens/manifest.json`. The gate runs at **zero tolerance** — same engine, so bit-exact is achievable. If a re-run diffs, something is genuinely non-deterministic; that is a finding, never a reason to widen a tolerance.
- **Never re-baseline a golden to make a suite green.** `make_golden.py` refuses unless given `--rebaseline --reason "..."`. Diff first, understand the moved cell, then re-baseline deliberately.
- Never hash `.xlsx` bytes — openpyxl stamps timestamps into `docProps/core.xml`. Compare content via the cellset module.

## Known-broken things (verified, do not "discover" them again)

- **Shopee's money crossing is CLOSED (2026-08-13)** and is not TikTok-shaped: `SHOPEE_MONEY` stays `None` because no single income column conserves (settlement is net of platform fees). Its crossing is a derived pair built by `tieout.revenue_crossing_shopee` from the team's own June `Net revenue` formula — `SUM(amount_with_vat − discount_allocated) == gross_revenue + shopee_product_subsidy` — measured exact on four windows before being asserted. Refund orders and zero-quantity lines are **held out and named**, never absorbed into a tolerance. **Still do not "fix" anything here by picking a column and widening a tolerance**; that is how the original checks became worthless.
- **~21% of TikTok GOOD settlement has no matching order lines** (11,765 orders, 3.45B VND) and is excluded from invoicing. Expected, matches the team's VLOOKUP, reported every run as a `RECONCILING` line. Investigate a large *change* in it, not its existence.
- **Lazada's revenue conservation nets promo.** `check_with_vat ≈ credits + promo`; comparing against credits alone reports a false ~72M VND breach.
- **The VAT master matches nothing.** Its 660 SKUs cover **0** of the SKUs traded in every sampled window on all three platforms, so the per-SKU override has never fired and everything invoices at the 1.08 default. Fixed in M2.5 to the extent code can: the fall-through is counted and logged every run (`masters.resolve_vat_factors`, warns loudly at 0% coverage). Whether the master is *meant* to cover these stores is a human question ([open question 9](../docs/11-OPEN-QUESTIONS.md)) — do not "fix" it by inventing a mapping.
- Joins key on `(store, order_id)` since M2.5 — but `tieout.py` still keys its coverage reference on `order_id` alone, a named residual.

`tests/test_tieout_blindness.py` and `tests/test_silent_failures.py` used to pin 11 of these as `xfail(strict=True, raises=AssertionError)`; **all 11 are now closed** (8 in M2, 3 in M2.5) and the markers are gone. **`strict=True` is a drift detector, not a TODO list** — while a gap is pinned it must stay xfail, and an unexpected XPASS means behaviour changed unintentionally. When closing one, never flip marker and behaviour in the same commit: fix first (the XPASS is the evidence), remove the marker second. The tests themselves stay after the fix — they are what stops the behaviour drifting back.

## Data handling

Raw exports contain customer PII — `settings.yaml:285` records `Recipient`, `Phone #`, `Detail Address`; Shopee adds `Tên Người nhận`, `Số điện thoại`, `Địa chỉ nhận hàng`. `drop_unmapped_columns: true` strips these at read time. **Never print cell values** when probing real files — report schemas, column names and counts only. Fingerprints hash store names (`store_h`); committed manifests carry only integer counts and digests, and `tests/goldens/test_manifest_integrity.py` enforces both.

Since M4 the run log also lands in Postgres (`run_log_lines`). It carries store names and counts and **no cell values** — the same exposure `run_log.txt` already has on disk, in a database with no authentication in front of it yet (defect 2.1/2.6). `artifacts/` and `.scratch/` are gitignored for the same reason `output/` is.

## Documentation

`docs/` is the authoritative set, written for both humans and agents. `README.md` is the index.

| Need | Doc |
|---|---|
| Business context, vocabulary, mental model | `docs/01-ORIENTATION.md` |
| Module map, I/O boundary, entry points | `docs/02-ARCHITECTURE.md` |
| Stages and per-platform differences | `docs/03-PIPELINE.md` |
| File conventions, staging, masters, PII | `docs/04-DATA-FLOW.md` |
| The money math | `docs/05-DOMAIN-RULES.md` |
| Why something is the way it is (stable `#d1`… anchors) | `docs/06-DECISIONS.md` |
| Evidence and honest limits | `docs/07-VERIFICATION.md` |
| Verified defects — **read before trusting a green run** | `docs/08-KNOWN-DEFECTS.md` |
| Commands, cadence, troubleshooting | `docs/09-OPERATIONS.md` |
| Milestones and next actions | `docs/10-ROADMAP.md` |
| Decisions needing a human | `docs/11-OPEN-QUESTIONS.md` |
| Monthly format drift + milestone history | `docs/12-CHANGE-HISTORY.md` |
| Entra ID / Azure access — the permissions to escalate for | `docs/13-ENTRA-SETUP.md` |
| What blocks real users — the production-readiness register | `docs/14-PRODUCTION-READINESS.md` |
| The **prototype** Azure hosting ask — synthetic data only; draft, unsent | `docs/15-AZURE-ACCESS-REQUEST.md` |
| What the finance team must supply before the month-end master can be checked | `docs/16-DATA-REQUEST-MONTH-MASTER.md` |
| **For users, not maintainers** — Vietnamese first | `docs/17-USER-GUIDE.md` |

`ARCHITECTURE_POSITION.md` is a stakeholder-facing position document (leadership audience, not maintainers).

**Retired 2026-08-12** — do not look for or cite these: `HANDOFF.md`, `COMPLETION_REPORT.md`, `REVIEW_PACKAGE.md`, `EVALUATION_DOSSIER/*`. Their content was migrated into `docs/`; the originals are recoverable from git history. `REVIEW_PACKAGE.md` is regenerable via `tools/build_review_package.py`.

When you change behaviour, update the matching doc in the same commit — particularly `docs/08-KNOWN-DEFECTS.md` (status flags) and `docs/12-CHANGE-HISTORY.md` (drift log).
