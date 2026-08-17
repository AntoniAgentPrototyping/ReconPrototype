# 12 — Change History

Two things are recorded here: **format drift absorbed from the platforms** (the recurring operational cost), and **milestone history** (what was built when).

The drift log is the most operationally useful page in these docs. It is the evidence that maintenance is a certainty, not a risk — every month tested has needed a config change.

## Format drift absorbed

| Month | Drift | How it was handled |
|---|---|---|
| Jun 2026 | TikTok order `.xlsx` with a broken `<dimension>` tag, unreadable by the default engine even after resets | Switched reader engine to `calamine`, config-selectable per platform/kind |
| Jun 2026 | Income headers renamed (`Order\adjustment ID` → `Order/Adjustment ID`, `Type` → `Transaction type`) | All spellings kept as **parallel** column-map entries — old spellings still resolve |
| Jun 2026 | One store's window exported with Vietnamese-localized headers | Set aside honestly: excluded from totals, named in the tie, pending a VN-header mapping. **Not** silently parsed |
| Jun 2026 | Shopee big-3 stores auto-split across batch files with overlap | Overlap measured at order level first, then confirmed with the team; deduped by order ID to an exact figure |
| Jul 2026 | Weekly TikTok cadence began; 3 new filename suffix styles, one file with no separator before the date token | Filename regex extended, under the rule that store names must **never** be truncated |
| Jul 2026 | 7 stores onboarded; one window renamed most stores, including typos | Store-roster guard hard-stopped by design; aliases added only after order-ID-overlap proof (100% / 99.8%). One store that looked like an alias was proven **genuinely new** (zero overlap) |
| Jul 2026 | 9 Shopee "part 2" income exports with no revenue sheet | Each self-declares total 0 in its own Summary → removed from staging only; the raw dump was left untouched |
| Jul 2026 | A Lazada window exported in the Daily schema under a weekly-named folder | Dual-schema support already existed; restaged manually. Fixed properly later by detecting the schema from **sheet content** rather than folder name |
| Jul 2026 | Order-less Lazada revenue rows (compensation for lost/damaged inventory) mapped by the team's own master to a revenue bucket but with no order or SKU | Surfaced as **named reconciling rows** in the control blocks and kept in the sale-report figure. The earlier plain exporter had been silently zeroing them |
| Jul 2026 | **A weekly TikTok export also contained the entire previous settlement block** | The most material catch to date — 5.97B VND of double-invoicing risk. Fixed as a config-declared settlement boundary with per-day drop logging, verified back to 0 VND cross-window duplication |
| Aug 2026 | Shopee reads took 45+ minutes per window once cloud sync began competing for the disk | Forced `calamine` for Shopee orders and income; totals tie-checked after the engine change |
| Aug 2026 | Shopee **order** headers found to arrive in Unicode NFD while config keys are NFC | **Fixed in M2** — `ingest` NFC-normalizes headers before matching ([defect 1.2](08-KNOWN-DEFECTS.md#12-vietnamese-headers-arrive-in-unicode-nfd--fixed-m2-2026-08-13)). The mapping failure was real; tracing the consumers first **disproved** the claim that it overstated revenue |
| Aug 2026 | TikTok export dates arrive as `%Y/%m/%d` while config documents `dd/mm/yyyy` | Benign under pandas (year-first is unambiguous), but a stricter parser will need an explicit format |
| Aug 2026 | Shopee income writes the Excel **accounting dash** (`-`) for zero — 46,972 of 83,134 rows of `seller_ship_support`, which feeds the discount allocation | **Fixed in M2.5** — the dash and blanks parse to `0.0`; only genuinely unparseable cells stay NaN, and those hard-stop with the column named ([defect 1.6](08-KNOWN-DEFECTS.md#16-silent-numeric-and-date-coercion--fixed-m25-2026-08-13-numeric-date-part-still-open)). The value was already right; the path was not |
| Aug 2026 | The team's VAT master matches **no SKU** at the largest stores (0 of 295 TikTok / 650 Shopee), while matching 41–50% at another | Not drift so much as a standing gap made visible: the override fires where the master covers the store and nowhere else, and every SKU it has matched is 1.08. Coverage is reported every run; the policy question is [open question 9](11-OPEN-QUESTIONS.md) |
| Aug 2026 | Shopee sub-window income files carry the settlement range in the name (`… 1-10.xlsx`, `… 21-end.xlsx`); Lazada weekly exports arrive browser-numbered (`2_KAO (1).xlsx` … `(4)`) | Both parsed as distinct stores. Handled as optional trailing tokens in the existing filename patterns — never by truncating the name |
| Aug 2026 | One Lazada weekly export is **password-protected** | Cannot be staged or read. The stager names it in its refusal rather than crashing; the window (`2026-05_l5`, settled 05-25..31) has no golden |

**Pattern:** drift is roughly linear in platforms × stores × export variants. Each event needed a human to notice, diagnose, and hand-edit config. This is why schema-drift assistance is the one place AI earns its keep ([10-ROADMAP](10-ROADMAP.md#where-ai-does-and-does-not-belong)).

## Durability gaps that were predicted

Recorded in July as "expected to bite in August". Status as of 2026-08-12:

| Gap | Status |
|---|---|
| No automated cross-window overlap check in the run path | **Partly fixed (M2.5)** — staging refuses on duplicate content by SHA-256 and flags an export whose settlement range starts before its siblings', which is the shape the July double-pull took. Order-ID-level comparison is still manual |
| Lazada schema detected by folder name, not file content | **Fixed** in the staging tool (`stage_exports.py` reads sheet names) |
| Empty Summary-only Shopee exports hard-stop the run | **Detected at staging (M2.5)** — an export with no matching data sheet is named and refused before it reaches a run |
| Staging is a hand-written per-month script | **Fixed (M2.5)** — `tools/stage_exports.py` derives the window from each export's settlement dates; `stage_july.ps1` is deleted |
| No regression tests over settlement bounds and the template exporter | **Being closed.** The workbook differ built for engine parity is repurposed as a golden-file regression gate ([D26](06-DECISIONS.md#d26)) — which covers exactly these two, since both terminate in workbook cells |
| New stores hard-stop until a human edits the roster | Still open, deliberately ([D3](06-DECISIONS.md#d3)) |

## Milestone history

### M5 — web app v1 (2026-08-14)

All five surfaces the milestone named, plus the two defects that had to close before any of them was defensible. Suite went `319` → **`435 passed, 3 skipped, 0 xfailed`**; `service/` roughly doubled and a `web/` package appeared.

**Authentication came first, and not as a screen.** M5's own justification is that it builds the thing the roadmap's gate warns about — a green badge a finance user reads in a browser — so shipping it on top of an unauthenticated api, with a config editor that *writes*, was not an option. Entra ID SSO is the destination and turned out to be blocked on a tenant app registration needing directory permissions nobody here has, so this ships **bearer tokens** with the seam already Entra-shaped: role strings are Entra's own and the substitution changes who vouches for a role, not what a role means ([D35](06-DECISIONS.md#d35)).

Two consequences worth recording:

- The api now **refuses to start** on a routable address with auth disabled, rather than warning. M4 had exactly that as a printed warning and defect 2.1 recorded that a default is not a control.
- `requested_by` stopped being a caller-supplied string. For a system that produces invoices, "who asked for this run" has to be a fact.

**Config became period-versioned** ([D37](06-DECISIONS.md#d37)) — defect 2.5. A window is pinned to the config its first *successful* run used, so an August rate change cannot alter a re-run of May. The full file text is stored, comments included, because a parsed structure would discard exactly the evidence that makes the file worth anything. A hard stop pins nothing: the fix for it may well be a config change.

**The config editor writes, under a policy nobody has chosen yet.** One path, one value, one stated reason → a one-line diff → approve → apply and commit. The property it rests on was checked before anything was built on it: `ruamel.yaml` round-trips the real 300-line `settings.yaml` **byte-identically**. Who may approve is `RECON_CONFIG_APPROVAL`, defaulting to the strict mode, because [open question 13](11-OPEN-QUESTIONS.md) is unanswered and a one-admin deployment should have to *choose* self-approval rather than inherit it ([D39](06-DECISIONS.md#d39)).

**The upload boundary landed with its own gate** ([D40](06-DECISIONS.md#d40)) — defect 2.3. PII is stripped using the pipeline's own column map, so there is no second denylist to go stale, and the unstripped original never outlives the request. The risk this creates is real — it rewrites an export *before* the verified pipeline reads it — so it is gated rather than argued: a real window is sanitized and its workbook matched against the committed golden across all 12 tabs at zero tolerance.

**Defect 2.2 closed as a side effect.** Docker arrived on the development machine mid-milestone, so `deploy/` was built and run for the first time: `db` + `api` + `worker` + `web`, an unauthenticated `/board` returning 401, and a containerised worker claiming a job and executing it. Building it immediately found a real error the file had accumulated while unbuilt — it copied `pyproject.toml` before the sources to get a cacheable dependency layer, which cannot work, because `pip install .` builds a wheel and hatchling needs the packages to exist.

**The web app is a container, not Vercel** ([D41](06-DECISIONS.md#d41)). The workload is wrong for serverless — 171 seconds of CPU-bound pandas at 832 MB peak RSS holding a 900-second lease — so Vercel would only ever have hosted the front end, meaning two vendors for a prototype. As a BFF beside the api it also keeps the bearer token server-side, in an httpOnly cookie, which a browser-side client could not do.

**Two `src/` additions, both default-off and golden-gated:** `config.parse_settings` and `build_context(settings_text=...)`, so a run can be handed a pinned config. All eight golden windows regenerated with zero refusals.

**Shipped honestly unfinished:** the web UI has **never been opened in a browser** ([defect 2.8](08-KNOWN-DEFECTS.md)). It type-checks under `strict`, builds, and its container serves the login page — and nothing has clicked a button. The API underneath it has 240 tests; the UI has none.

**Environment note.** Node 26 and Docker Desktop (WSL2) became available on the development machine. `web/` adds 27 npm packages — no UI framework, no state library, no generated client — deliberately small against the standing single-maintainer risk. `ruamel.yaml` and `python-multipart` joined the `service` extra; the core dependency list is still untouched.

### M4 — the service skeleton (2026-08-13)

A wrapper around the pipeline: FastAPI, a worker, and Postgres holding the queue, the run record, the log and the artifact index. `service/` is a new top-level package, ~1,200 lines, with 135 tests. Suite went `167 passed` → **`319 passed, 3 skipped, 0 xfailed`** (the service tests skip without `RECON_TEST_DATABASE_URL`; the rest of the growth is two lints now parametrizing over `service/`).

**What was gated, and how.** Two claims mattered, and neither was taken on trust:

- *The service does not move a cell.* `tests/service/test_worker_matches_the_cli.py` runs a real worker over `2026-05_l1` and compares the workbook it stored against the committed golden digest — 12 tabs, 2,193 cells, zero tolerance. It matches, so the service is a different way to invoke the same computation rather than a second implementation.
- *The three helpers that moved into `src/pipeline.py` are output-identical.* All **eight** golden windows regenerated with **zero refusals** and zero re-baselines.

**Three things moved out of `tools/full_run.py`** and into the seam — `build_context`, `EXIT_CODES`, and the `RESULT` log section — each because it acquired a second caller and the worker's copy is the one that would have drifted. What would have drifted first is the `settings["_vat_sku"]` back-channel, which silently changes numbers ([D28](06-DECISIONS.md#d28)). `full_run.py` is now argument parsing plus reading one JSON file.

**Two things fell out of the work rather than being planned:**

- `tools/full_run.py --partial-roster`. The service needed roster relaxation as a job property, and once `build_context` owned it, the CLI got it for free — closing the M2.5 sharp edge where a genuine single-store production run needed a config edit.
- Defect [1.9](08-KNOWN-DEFECTS.md#19-shared-mutable-state-via-the-config-dict--open-contained-m4) went from theoretical to *contained*. A worker is the first thing that runs two windows in one process, so "two runs sharing a settings dict" stopped being hypothetical and is now pinned by a test asserting the two `id(settings)` values differ.

**A design choice worth recording because the obvious alternative is wrong.** "The worker streams artifacts to object storage while the CLI writes to disk" reads like the worker needs its own writer. It must not: that writer would be a second implementation of the code path producing the deliverable the team invoices from, and since goldens are generated through the CLI, the service's copy would be the *unverified* one. Write-then-upload makes the bytes identical by construction ([D31](06-DECISIONS.md#d31)).

**Shipped unverified, deliberately and labelled.** `deploy/Dockerfile` and `deploy/docker-compose.yml` have never been built — there is no Docker on the development machine — so they are a reviewable deployment contract and are recorded as [defect 2.2](08-KNOWN-DEFECTS.md#22-deploydockerfile-and-deploydocker-composeyml-have-never-been-built--open) rather than described as done. The Postgres layer underneath them *is* verified, against a real 17.10 server: `FOR UPDATE SKIP LOCKED` is a statement about what two transactions do to each other and cannot be proven against a substitute.

**Environment note.** PostgreSQL 17.10 was installed from the EDB *binaries zip* into `%LOCALAPPDATA%\recon-pg`, running as the current user on port **55432** — not the MSI installer, which needs elevation, and not a Windows service. Reversible by stopping `pg_ctl` and deleting the folder. Three new packages in a new `service` **extra**: fastapi, uvicorn, psycopg. The core dependency list is untouched, and a test asserts it stays that way.

### Phase 1 — build and verification (through Aug 2026)

Post-download automation: given the exports the team already downloads, produce the invoicing files automatically with every rule explicit in code and config. Three real months processed — May (row-level verification, ~288K rows), June (external tie against the team's own outputs), July (independent run on unseen data that produced findings the team confirmed).

Delivered beyond the original scope, in response to team feedback: finance files in the team's own invoicing-template shape (replacing an earlier plain format), and a monthly cross-platform master with a storefront→brand mapping table for review.

Phase 2 (API extraction) and Phase 3 (D365 posting) were assessed but not started.

### Aug 2026 — control audit and migration groundwork

- **Control audit.** Found that the tie-out checks are algebraic identities that cannot fail, that the production driver discards their result, that two of three platforms never call them, and that the only real verification is optional. Demonstrated with a falsification harness in which zeroing 100% of revenue still reports ALL PASS. Also found the NFD header defect, the silent VAT default, the `order_id` fan-out risk, and the missing error handling. See [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md).
- **A stakeholder position document** (`ARCHITECTURE_POSITION.md`) responding to a proposed four-layer target architecture: endorsing platform APIs and a transaction store, arguing that controls must be rebuilt before automation, and placing AI at the edges rather than on the money path.
- **M0 — falsification harness.** Packaging (`pyproject.toml`), a test suite pinning 11 control gaps as strict xfails, and the environment split (a pinned oracle venv, both venvs outside the synced folder).
- **M0.5 — parity gate.** A canonical workbook differ with a self-test proving it catches every defect class it claims; per-stage fingerprints for localizing a divergence; a golden generator that instruments the real driver without modifying it; a deterministic staging tool. Goldens now exist for all three platforms, regeneration is bit-stable, and a mutated input provably moves the manifest.
- **Rounding semantics verified rather than assumed.** The plan had assumed polars defaulted to half-away-from-zero and that a helper would be needed to avoid flipping tie-out verdicts. Measurement showed polars already defaults to half-to-even, matching pandas — so that risk class does not exist, and Excel's `ROUND` reduces to a single native call, verified bit-identical across 123,976 values.

### Engine migration descheduled (2026-08-12)

A polars rewrite — newest Python and libraries, pandas retained as a frozen diff oracle — was planned in detail and then **cancelled before any polars was written**. The prompt was a scale question: *is this system unnecessarily complex, or is that industry standard?*

Measuring the repo answered it. ~1,955 lines of pipeline, against ~1,205 lines of migration scaffolding — a harness 60% the size of the thing it protected. And the migration's premise did not survive checking:

- The largest month is 427,917 order rows; pandas is comfortable an order of magnitude above that.
- The documented performance pain (45+ minute runs) was **cloud-sync contention on Excel reads**, fixed by the `calamine` engine. Polars touches neither the sync nor openpyxl.
- Goldens were keyed to `oracle_rev`, which hashes `src/` + `config/`. Config changed in *every month tested*, so the harness would have been rebuilt whenever a port actually began — it decayed rather than compounding.
- The port's cost scales with the compute layer, and the web-app milestones don't grow it. Later ≈ the same price.

**What replaced it** ([D25](06-DECISIONS.md#d25)–[D27](06-DECISIONS.md#d27)): the *option* instead of the migration — a pipeline seam, an I/O-boundary lint, and instrumentation that separates compute from I/O so the port decision becomes a threshold rather than an opinion.

Two second-order corrections came out of the same review, both against the first instinct:

- **Deleting the whole harness was wrong.** Most of it is not engine-specific, and this very page listed "no regression tests over settlement bounds and the template exporter" as open. `cellset.py`, `diff.py`, the self-test and the manifests were **repurposed** as a golden-file regression gate; only the cross-engine parts (`fingerprint.py`, `oracle_rev`, the second venv) were deleted. ~350 lines out, ~865 kept.
- **The gate got stricter, not looser.** The 0.5 VND tolerance existed only because cross-engine float reduction order differs. One engine ⇒ bit-exact is achievable ⇒ zero tolerance, and the knife-edge allowlist machinery deleted with it ([D17](06-DECISIONS.md#d17)).

Recorded at this length because the *reasoning* is the reusable part: the same "will it scale?" pressure will recur, and the answer that held here was to instrument the question rather than pre-pay for the answer.

### M2.5 — the staging normalizer (2026-08-13)

The step this system was most exposed on now has a tool instead of a monthly rewrite. `tools/stage_july.ps1` — absolute paths from one developer's machine, a hand-maintained folder-name → window table, routing on `rder`/`ncome`, no validation — is **deleted**.

**The insight is that a settlement window is a fact about the data, not about a folder.** Every income and ledger export states the date range it was paid out for, so the window is derived: group the files, merge groups whose spans overlap, sort chronologically, index. Order exports never define one — they deliberately reach into earlier months for the cross-period stitch — so they inherit their group's.

The acceptance test is that deriving reproduces the labels a human had already assigned by hand for all eight staged windows. It does, on all three platforms.

Refusals, each mapped to a failure that actually happened: unclassifiable or unreadable files (a password-protected export is now *named*), identical content bound for two windows or already staged elsewhere (the double-pull class, 5.97B VND when it last happened), an export whose range starts before its siblings' (the shape that mis-pull took), and data-less exports (Summary-only Shopee files, removed by hand ten times in July). Each staged window now carries a `staging.json` provenance record; staging previously left none.

**Three bugs the tool had, all found by pointing it at real dumps rather than by reading it:**

- **It could not find its own source directory.** Moving from `tools/parity/` to `tools/` in M1 left `parents[2]` unchanged, so every path it computed pointed one level above the repo. The "deterministic stager" the goldens were supposed to be reproducible through had not run since that move.
- **It silently staged nothing from nested folders.** A flat `iterdir()` missed the per-sub-window directories real Shopee dumps arrive in — the worst possible stager failure, because the run afterwards looks fine and is simply missing a store.
- **Its first dedupe pass flagged every file as its own duplicate**, because `input/original exports/` lives *inside* `input/` and the scan for already-staged content reached the raw dump.

And two design corrections that only surfaced against real data: grouping on the immediate parent folder orphaned the order files (a Shopee dump splits `Doanh Thu/` and `Order New/` under one window folder), and numbering by group made two stores of the *same* window into `s1` and `s2` — fixed by merging groups whose spans overlap, while keeping adjacent-but-not-overlapping spans separate so Lazada's four consecutive weeks stay four windows.

### Shopee's money crossing closed (2026-08-13)

The last platform without a conservation control has one. It was **blocked on an artifact, not on effort** — and the thing that unblocked it was the team handing over their June consolidated file, whose `Net revenue` column carries the formula the June tie had used and this repo had only ever recorded the *result* of.

The formula reproduces from its six components to **0.000000 VND across all 82,714 rows**. Four of the six are the seller-borne discount pool; moved to the other side, what remains is a statement with the order export on one side and the income export on the other — which is the only kind of check worth having here. It ties at **0.00 VND on 17.5B VND across 80,239 May orders**. Detail in [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md#shopees-money-crossing--closed-2026-08-13).

Four things worth carrying forward:

- **Refusing to fake it was the right call.** M2 could have shipped a Shopee check with a tolerance wide enough to pass and it would have looked identical in a green run log. Because the gap was left open and logged instead, the arrival of one file turned it into a **measured** control rather than a fitted one.
- **The falsification harness found a hole before the check shipped.** The first version inner-joined the two sides, so deleting an entire store went undetected — the orders left the comparison and the remainder still tied. Written up because it is the same failure shape as defect 1.1: a check that only ever restates what it was given.
- **Two forms, both exact, one better.** Reading the pre-computed `order_gross_sale` column and summing `amount_with_vat − discount_allocated` are algebraically identical and measured identical to 0.0000 VND, but only the second moves when a SKU line is dropped from an order that still has siblings. Measured both, then chose on control strength.
- **Named holdouts, not tolerances.** Refund orders cannot tie by construction (the order export keeps the full ordered quantity); they are reported with their count and amount. Zero-quantity lines get the same treatment even though none exist in any window measured.

### Golden coverage broadened, 3 windows → 8 (2026-08-13)

Raw exports for more stores arrived, closing roadmap item 2. Coverage table in [07-VERIFICATION](07-VERIFICATION.md#current-gate-status). Five things came out of staging them, none of which were about the pipeline:

- **`tools/stage_exports.py` had been broken since M1.** It kept `parents[2]` after moving from `tools/parity/` to `tools/`, so every path it computed pointed one level above the repo and it could not find `input/original exports` at all. The deterministic stager the goldens are supposed to be reproducible through had not run since the move.
- **It also staged nothing from nested folders.** Real Shopee exports arrive as per-sub-window directories (`1_10/`, `11_20 - Xmen/Doanh Thu/`); a flat `iterdir()` silently skipped them — the worst stager failure mode, because the run that follows looks fine and is simply missing a store. Now recursive, with a `--pattern` flag because one raw folder legitimately holds several settlement windows.
- **Two store-identity hazards, both the [D6](06-DECISIONS.md#d6) filename problem.** Shopee sub-window income files carry the settlement range (`Income. Xmenforboss 1-10.xlsx` → store `Xmenforboss 1-10`), and a store's five weekly Lazada exports downloaded in one browser session arrive as `2_KAO.xlsx`, `2_KAO (1).xlsx` … (→ five distinct "stores"). Both fixed as optional trailing tokens in the existing patterns, under the standing rule that a store name must never be truncated.
- **A double-pull was ruled out before staging, not after.** The Mars store shipped two order exports; they share **1 order in 24,408** and each alone covers only ~half the income orders, so they are complementary parts. The July 5.97B VND catch is why this gets checked every time.
- **One Lazada week could not be staged at all — the export is password-protected.** The stager now names it in its existing "refusing to stage a partial tree" refusal instead of dying on a traceback that named no file.

**And it corrected a claim made earlier the same day.** Defect 1.4's write-up had concluded from three stores that the VAT master matches nothing and the per-SKU override mechanism is inert. The KAO windows match 41–50% of their ledger rows: the lookup works, and the real finding is narrower — the master is populated for a different store set than the largest stores in this data. Every SKU it matches is still 1.08, so the non-1.08 path stays live-unexercised.

### M2.5 — the last pinned defects (2026-08-13)

The four gaps M2 left pinned are closed, which empties the strict-xfail list for the first time since M0: `83 passed, 3 skipped, 4 xfailed` → **`91 passed, 3 skipped, 0 xfailed`**. Each was a pair — fix, XPASS as the evidence, marker second ([D22](06-DECISIONS.md#d22)) — and each stated its golden delta before the gate ran. All three predictions held.

- **[1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13) silent VAT default.** `vat_factor_for` now returns NaN where the master says nothing; `resolve_vat_factors` applies the team's default fall-through and *counts* it. Manifests byte-identical.
- **[1.5](08-KNOWN-DEFECTS.md#15-joins-key-on-order_id-alone--fixed-m25-2026-08-13) `order_id` fan-out.** Stitch, both explodes and four per-order transforms key on `(store, order_id)`. Manifests byte-identical.
- **[1.6](08-KNOWN-DEFECTS.md#16-silent-numeric-and-date-coercion--fixed-m25-2026-08-13-numeric-date-part-still-open) silent coercion.** Blank and accounting-dash cells parse to `0.0`; unparseable ones hard-stop. Workbook unchanged, `fingerprint_digest` re-baselined on two windows for null counts alone.

**The pattern worth keeping — measuring a defect before fixing it changed what the defect was, twice:**

- 1.4 was documented as "an unmapped SKU silently defaults", a rare-event risk. Measurement says the master matches **0 of 224 TikTok, 0 of 461 Shopee and 0 of 92 Lazada SKUs** — the per-SKU override has never fired at all. The fix as written (report the fall-through) is right either way, but the finding is about the *master*, not the code, and it belongs to a human ([open question 9](11-OPEN-QUESTIONS.md)).
- 1.6 was documented as a latent risk. It was live: 46,972 of 83,134 rows of a Shopee income column that feeds revenue were being coerced to NaN and zeroed on every run. The value was correct — the dash *means* zero — which is exactly why nobody caught it: the path was wrong and the answer was right.
- 1.5 went the other way. Measurement showed 0 colliding order IDs, identical merge row counts and no borrowed dates, so the fix is provably output-identical **on the data that exists** and buys protection for the multi-tenant case it was written for. Worth stating plainly: this one is insurance, not a repair.

**A fourth bug, in the gate itself.** Re-running a window *after* re-baselining it silently **erased the reason the baseline moved**: an ordinary run produces an entry with no `rebaselined` block, and it overwrote the one that had it. Digests matched, so the guard stayed quiet — the audit trail [D26](06-DECISIONS.md#d26) promises (`git diff` on the manifest) was being deleted by the next regeneration. `merge_manifest` now carries non-output fields forward. `test_the_rebaseline_stamp_does_not_itself_trip_the_guard` already walked this exact path and only asserted "does not raise", which is why it never caught it; the new test asserts the reason survives.

*Residual, named rather than quietly left:* `tieout.py` still keys its coverage reference on `order_id` alone, and the date half of 1.6 (`to_datetime(errors="coerce")`) is untouched.

### M2 — the controls actually work now (2026-08-13)

The milestone the whole project was ordered around: the tie-out checks that
[could not fail](08-KNOWN-DEFECTS.md#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13) now can.

**The design rule that fixed it:** a check must cross a boundary. Every check compares the SKU frame — rebuilt from the *order* export — against a `SourceReference` captured from the *income* export before the money math ran. Two different files, so agreement is evidence rather than arithmetic. Evidence the fix is real: the six revenue-loss mutations, `xfail(strict)` since M0, all XPASS. Markers came off in a separate commit ([D22](06-DECISIONS.md#d22)).

Class A moved no cells; Class B moved one non-money digest, predicted in advance.

**Three findings from doing it, all from measuring before asserting ([D1](06-DECISIONS.md#d1)):**

- **21% of TikTok GOOD settlement never reaches invoicing.** 11,765 of 55,894 orders, 3,453,805,299 VND, dropped by the inner join and reported nowhere. Believed correct — the team's VLOOKUP does the same and June tied exactly — but silence about a fifth of settlement is not a working control. Now a named `INFO` reconciling item.
- **Shopee has no verified money crossing.** Applying TikTok's relation there breached on *correct* data. Rather than widen a tolerance until it passed — the exact move that produced the original broken checks — Shopee runs coverage only and the gap is logged explicitly every run.
- **A documented defect was overstated.** Defect 1.2 claimed the NFD header failure overstated revenue. Tracing the consumers showed `shopee_subsidy` feeds no money formula: the mapping failure was real, its monetary impact nil. The fix's delta was predicted (workbook unchanged, fingerprint moves) and confirmed exactly.

Two of my own errors, both caught by the gate rather than by review: the Lazada check compared against ledger credits without netting promo and reported a false 72M VND breach while the template's own controls read 0.00; and a Shopee baseline was moved while its checks were breaching, then restored.

Suite: `74 passed, 3 skipped, 12 xfailed` → `83 passed, 3 skipped, 4 xfailed`. Eight gaps closed, four left pinned.

### M1 — seam, instrumentation and boundary lint (2026-08-13)

Four things landed, gated throughout on the workbook goldens at zero tolerance.

- **The seam.** `src/pipeline.py` — `run(ctx) -> RunResult` reads inputs and **writes nothing**; `write_artifacts(result)` is the only writer. `tools/full_run.py` fell from 233 lines to ~100 of argument parsing. **All three manifests came out byte-identical**, which is what a refactor gate is for.
- **Instrumentation.** `src/metrics.py` plus `tools/metrics_report.py`, the engine-port trigger dashboard.
- **Boundary lint.** `tests/test_io_boundary.py` — the I/O boundary is now enforced rather than merely documented, including a per-function check that `run()` writes nothing, and a dead-grant check so the permission table cannot rot.
- **The old M0.6 folded in.** Deleted the unverified placeholder path and `recon.py`; manifests unchanged, which proves it was unreachable.

**Two silent-zero bugs, both found by looking at the output rather than the tests:**

- Peak RSS reported **0 MB on every run**. `GetCurrentProcess()` returns a pseudo-handle that ctypes truncated to 32 bits; psapi rejected it and the exception path returned the fallback. A metric that is always zero never fires a trigger and never looks broken.
- `build_workbook` was tagged `compute`, which pushed the measured compute share to **31% — over the 25% engine-port trigger**. But openpyxl materialization is engine-independent. Retagged as a third `serialize` kind, real compute is ~2%.

The second one matters beyond the bug: a mis-tagged stage had inverted the verdict on the project's largest open architectural question. The instrumentation was measuring, reporting, and wrong.

**What the corrected numbers say** (May 2026, three windows): DataFrame compute is **1.3%–3.8% of wall time** — 2.3–2.5 seconds of a 120–171 second run. Excel reading is ~66%, workbook materialization ~32%. Peak RSS 832 MB against a 4 GB container. The polars descheduling ([D25](06-DECISIONS.md#d25)) now rests on measurement rather than argument: a 5× faster engine would save about two seconds.

`tools/smoke_test.py` was rewritten. It used to drive the deleted `recon.py`, so it had been proving the machine could run code production never called; it now runs `pipeline.run()` on a synthetic Lazada window and checks 13 properties including that `run()` wrote nothing.

Suite: `36 passed, 3 skipped, 11 xfailed` → `74 passed, 3 skipped, 12 xfailed`. All 11 control-gap xfails intact ([D22](06-DECISIONS.md#d22)).

### Documentation restructure (2026-08-12)

The `docs/` set replaced a scattered collection of overlapping documents. Retired: `HANDOFF.md`, `COMPLETION_REPORT.md`, `REVIEW_PACKAGE.md` (auto-generated) and the six-file `EVALUATION_DOSSIER/`. Their content was migrated here — verification evidence into [07](07-VERIFICATION.md), rules into [05](05-DOMAIN-RULES.md), drift history into this file. The old `README.md` was scaffold-era and actively misleading (it described column maps as placeholders and Lazada as out of scope, long after both were false); it is now a navigation index.

## M6 — browser-only: passwords, bucket input, a revamped config editor (2026-08-17)

The milestone that turned a verified pipeline with a service wrapper into an application. Five things changed — auth, input, the roster control, config, and sample data — and the verification apparatus stayed, because it is the reason anyone believes the numbers. It just stopped being something a *user* touches.

**The governing constraint held: no phase moved a committed golden digest.** That was the sharpest available gate on a change this size, and it is the reason the sanitizer/rename work can be trusted.

### What changed

| Area | Before | After |
|---|---|---|
| Credentials | API tokens pasted from a terminal | Username + password (Argon2id), opaque server-side sessions, three roles |
| Input | A human copies exports into `input/<period>/<platform>/` | Browser upload → MinIO bucket → materialised into the worker's scratch at run time |
| Filenames | Whatever the download named them | A uniform scheme, with the rename proved a fixed point of the pipeline's own parser |
| Artifacts | A shared volume, 501 for anything else | `s3://recon-artifacts/...`, streamed through the api's authorization |
| Roster override | A per-run checkbox | A per-window declaration with a mandatory reason and a named author |
| Config editing | A dotted YAML path and a value, in two text boxes | Sections with purpose-built widgets, each carrying its own comment block as evidence |
| Config safety | Nothing measured whether an edit moved a number | An automatic verification run against a committed golden, with five distinguishable outcomes |
| Sample data | A deleted generator whose files carried a `Shop Name` column | `service/sampledata.py` — deterministic, three platforms, store-bearing filenames |
| CLI | `tools/full_run.py`, the primary human entry point | `tools/devrun.py`, developer tooling |

### Things that were measured rather than assumed, and came back differently than expected

These are the findings, and they are the part of M6 worth re-reading:

* **`.dockerignore` was anchoring at the context root**, so `deploy/input/` — 382 MB of real client Shopee exports — was inside the build context while `input/` was excluded. Proved with a mimic build, not inferred. All five data patterns were affected, not just one. Git was unaffected because git patterns match at any depth, which is exactly what made the asymmetry easy to miss.
* **The upload sanitizer only ever worked for Lazada.** It wrote the header on row 1 with pandas' defaults, which flattens Shopee income's two band rows (`header_rows: 3`) and TikTok orders' junk row (`skip_rows_after_header: 1`). Nobody noticed because the golden gate covered **Lazada alone**. It now preserves the file's shape and the gate runs per platform — 46 files across three windows, sanitized *and* renamed, all cell-for-cell identical to the committed digests.
* **ruamel attaches a comment block to the key *preceding* the one it documents.** Rendering `.ca` directly would have captioned nearly every config field with the previous field's justification — authoritative-looking and wrong. Evidence is read from the text instead ([D42](06-DECISIONS.md#d42)).
* **The M6 plan's claim that removing a commented item takes its comment with it was false** for a comment *block*: removing `"Merries"` left its July-w5 justification captioning a different store. Now the operator is asked which it was ([D49](06-DECISIONS.md#d49)).
* **Appending to a list whose trailing comment introduces the next key** put the new item under that comment — a new TikTok store rendered beneath a comment announcing Shopee's roster. Found by the test written for the previous finding.
* **`request.client.host` is not reliably an IP.** Starlette's TestClient reports the literal `"testclient"`, which Postgres' `inet` type rejects — so every sign-in 500'd on a field that exists only for an audit trail.
* **The demo generator's first run produced a real tie-out breach**, off by exactly the two Shopee subsidies, because income's `Giá sản phẩm` is net of both and the crossing adds only the Shopee-funded part back. Telling that apart from a manufactured breach required reading the check's source — which is why the demo now ships **no** deliberate breach ([sampledata](../service/sampledata.py)).
* **`dayfirst.shopee` carries an unresolved `TODO verify`.** The demo abstains and emits ISO dates rather than assert an answer nobody has. Raised as [open question 16](11-OPEN-QUESTIONS.md).
* **NFC: 0 non-NFC identity values anywhere** — 0 of 118 live `.xlsb` fee names, 0 of 118 in the CSV snapshot, 0 store names, 0 stored exception rows (`service/nfc_audit.py`). The plan's "0 impact" claim had been measured over *filenames*, which said nothing about `fee_name`; audited separately and clean. `_norm` normalises anyway, and `006_exception_nfc.sql` is a recorded no-op that says so.

### Corrections to earlier notes

* The plan asserted a **leaked Postgres password** in the repo. Verified false: `deploy/` is entirely untracked, `.env.example` contains `change-me`, and the password appears in no tracked file.
* The plan said the PII in the build context was "baked into an image layer". Overstated — the Dockerfile has no `COPY . .`, so it was transferred to and cached by the daemon, which is bad but different.
* `docker-compose.yml` carried an M4 header saying it had **never been brought up**, while `CLAUDE.md` claimed the whole stack was built and verified. Both were partly wrong; the stack was brought up on Docker 29.7.2 on 2026-08-17 and the header now says what was actually done.
* There were **two defects numbered 2.7**. Renumbered.

### Deliberately not done

* **No `ROSTER:` stamp in the finance workbook's control block.** It is the stronger version of the roster caveat and it moves workbook cells, so it needs its own commit and a deliberate rebaseline rather than being smuggled into a change that must be output-identical.
* **No deliberate tie-out breach in the demo**, and no planted duplicate row — see the generator's own docstring for why each would teach the wrong lesson.
* **`drop_unmapped_columns` stays uneditable**, against the literal request. It is the PII control in two places and its diff reads as an ordinary boolean flip.
