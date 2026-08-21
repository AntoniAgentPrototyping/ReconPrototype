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
| Aug 2026 | One Lazada weekly export is **encrypted by a sensitivity label** | Cannot be staged or read. Recorded as "password-protected" until 2026-08-19, when the bytes were examined: it is a Microsoft Purview label (`method="Privileged"`), the same label id and tenant as the month-end master. No password exists to ask for; the label has to be removed or extended. The stager names it in its refusal rather than crashing; the window (`2026-05_l5`, settled 05-25..31) has no golden |

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
- Defect [1.9](08-KNOWN-DEFECTS.md#19-shared-mutable-state-via-the-config-dict--fixed-2026-08-19) went from theoretical to *contained*. A worker is the first thing that runs two windows in one process, so "two runs sharing a settings dict" stopped being hypothetical and is now pinned by a test asserting the two `id(settings)` values differ.

**A design choice worth recording because the obvious alternative is wrong.** "The worker streams artifacts to object storage while the CLI writes to disk" reads like the worker needs its own writer. It must not: that writer would be a second implementation of the code path producing the deliverable the team invoices from, and since goldens are generated through the CLI, the service's copy would be the *unverified* one. Write-then-upload makes the bytes identical by construction ([D31](06-DECISIONS.md#d31)).

**Shipped unverified, deliberately and labelled.** `deploy/Dockerfile` and `deploy/docker-compose.yml` have never been built — there is no Docker on the development machine — so they are a reviewable deployment contract and are recorded as [defect 2.2](08-KNOWN-DEFECTS.md#22-deploydockerfile-and-deploydocker-composeyml-have-never-been-built--fixed-m5-2026-08-14) rather than described as done. The Postgres layer underneath them *is* verified, against a real 17.10 server: `FOR UPDATE SKIP LOCKED` is a statement about what two transactions do to each other and cannot be proven against a substitute.

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

Refusals, each mapped to a failure that actually happened: unclassifiable or unreadable files (an unreadable export is now *named* — the one this refers to turned out to be encrypted by a sensitivity label, not a password; see 2026-08-19 below), identical content bound for two windows or already staged elsewhere (the double-pull class, 5.97B VND when it last happened), an export whose range starts before its siblings' (the shape that mis-pull took), and data-less exports (Summary-only Shopee files, removed by hand ten times in July). Each staged window now carries a `staging.json` provenance record; staging previously left none.

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
- **One Lazada week could not be staged at all — the export is encrypted.** The stager now names it in its existing "refusing to stage a partial tree" refusal instead of dying on a traceback that named no file. *(Called "password-protected" here until 2026-08-19; it is a Microsoft sensitivity label (corrected 2026-08-19 by reading the container's streams — same label id and tenant as the month-end master; `ingest.rights_protected` now names it).)*

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

## M8 — production readiness (in progress, from 2026-08-18)

The register in [14-PRODUCTION-READINESS](14-PRODUCTION-READINESS.md) is the working
record; this notes what has landed and where a claim about it should be checked.

**The defect register was audited against the code (2026-08-19), and about a third of
what it called open had already been fixed.** Worth recording as drift in its own
right: the page whose job is to stop rediscovery had itself become a source of it.
Corrected in place with the wrong text quoted — 1.6's date residual and the 1.10 date
bullet (`date_formats` had shipped), 1.7's "atomic write is M2" (M2 closed without it;
D10 is the record), the 1.10 provenance bullet (three claims, three different answers,
and its "parity fingerprints are the first step" sentence was simply false — zero
references in `service/`), 2.3's description of `POST /uploads/{id}/stage`, deleted in
M6, 2.5's "no way to pin ahead of time" (there is, since M8), register A5 (fixed on
both paths by Phase 3) and A9's line citations. Two entries mattered more than
tidiness:

- **2.4's digest half described a fix that was measured to fail.** It said the stored
  `uploads.sha256` "could be checked"; doing exactly that failed every healthy window,
  because that digest is of the original upload while the store holds the sanitized
  rewrite ([D52](06-DECISIONS.md#d52)). Left standing, it invited a reimplementation of
  the version that does not work.
- **2.12 cited D14 for "byte-identical order lines are legitimate."** That is D5. D14 is
  "compare stored artifacts, not live processes".

One gap was found *by* the audit rather than corrected in it: **artifact downloads are
never digest-checked** — the same shape as 2.10, opposite direction, digest already
stored. Also fixed in the same pass: Lazada's undated-date count was reported as one
`lazada/ledger` line naming `dayfirst=False`, a setting Lazada does not consume and
which migration 009 deliberately never emitted; it now reports per variant under the
format that actually parsed it. And `src/export.py`'s docstring still said it was "not
yet wired into production", citing `tools/full_run.py` — wired since M2, and that file
became `tools/devrun.py` in M6.

**What the audit then fixed in code is recorded separately**, under *The defect-register
fix pass* at the end of this section — nine items, one commit, no golden moved. The split
is deliberate: this paragraph is about the register being wrong, that one is about the
system being wrong.

**Phase 1 — configuration into the database.** Config was fragmented across seven
files, two of which nothing read; five settings keys were dead; seven tolerances
were read by `src/tieout.py` and never configured, so code literals were the source
of truth by accident; and two flags had code defaults that were the **opposite** of
their configured values. Behind all of that sat **A1**: a config change made in the
browser never reached the worker, because `config/` is baked into each image and no
volume joins them, so applying a proposal wrote `settings.yaml` into the api
container's writable layer and was lost on the next restart.

The seam that made this safe was already there. `build_context(settings_text=…)`
takes a YAML **string**, so the fix is normalized tables → renderer → YAML text →
the existing `config_versions` snapshot → the existing per-window pin. `src/` never
learns about Postgres, the I/O-boundary allowlist is unchanged, and `service/` stays
deletable. It reverses [D2](06-DECISIONS.md#d2), and that reversal is argued in
`service/migrations/007_config_normalized.sql`'s header rather than waved through:
evidence becomes a **column**, which is strictly stronger than a comment — queryable,
attributed, dated, and impossible to orphan by editing a neighbour.

**The gate that mattered: all eight golden windows re-run under DB-rendered config —
`matched 8 | moved 0`.** That is the test to run before believing any claim here
(`tests/service/test_config_render.py::test_config_render_produces_the_committed_goldens`,
~11 minutes). The fast suite is a *structural* gate and never re-runs the pipeline.

**1.6 made the tables the thing that is edited**, not just the thing that is
rendered. `service/config_edits.py` and `service/config_schema.py` are deleted;
`service/config_rows.py` replaces both with eleven tables and two operations. Three
things that were awkward became impossible rather than handled:

- **Orphaned evidence.** Removing a commented list item left its justification
  captioning the item below it — measured, and the worst available outcome: evidence
  that looks authoritative and is attached to the wrong thing. A row deletes its own
  evidence, so `OrphanedEvidence` and `comment_disposition` are gone.
- **Per-entry evidence.** A file can only caption a top-level key, so the editor
  showed the roster's justification against all 42 storefronts in it. Each row now
  answers for itself.
- **Inferring what can move a cell.** `invalidates_goldens` is a column on every
  table (migration `008`), so `verification.verify` is *given* the answer instead of
  resolving a dotted path back to a declared field. Two tables are argued down to
  false and two were tightened; migration 008's header argues all four.

**1.7 put Lazada in the contract.** Its column maps, sheet names and filename regex
were module constants in `src/lazada.py`, imported directly by two modules in
`service/`, one in `tools/` and a handful of tests — the last part of the domain
contract that could only be changed by editing Python. They are now
`column_maps.lazada`, `sheet_names.lazada` and `store_from_filename.lazada`, and the
accessors that read them **hard-stop** rather than falling back to a copy, because a
fallback would re-create the two-definitions problem the move removed. Four Lazada
golden windows re-ran unmoved. `check_stores` is wired into `_run_lazada`, which
never called it at all; `expected_stores.lazada` is still empty because that is a
business question, and the check self-skips until it is answered.

One thing 1.7 refused to do quietly: a Lazada platform row would have emitted
`dayfirst.lazada: false` into the contract, and `read_ledger` calls `pd.to_datetime`
with no `dayfirst` at all. Emitting it would have added a key nothing reads — what
1.1 deleted five of — and honouring it would have shifted Lazada dates by up to
eleven days inside an output-identical refactor. Migration `009` makes the column
nullable and null means "this reader does not consume it".

**1.8 found a control that had never run in production.** A11 said the live
VAT/fee master is mounted at `/app/config/masters` while `src/masters.py` looks in
`/app/config`. Confirmed **in a running container**, not read off the Dockerfile:
with the file mounted and present, the pre-fix image reports `masters file 'Lib &
VAT rate.xlsb' not found — using CSV snapshots`. Every containerised run had been on
point-in-time snapshots. Both locations are searched now, the log names which
answered, and the fallback is a **finding** rather than one `log.warn` in a log
nobody reads after a green run. Measured honestly: the live master currently matches
the snapshots exactly, so no number was wrong *yet* — the failure was that nothing
would have said so the day the team edited the file.

**A measurement that corrected a doc claim.** `--durations=25` over the 706s fast
suite: two tests are 67% of it, both real-pipeline runs over real windows. So
"the fast suite never re-runs the pipeline on real data" was true of
`tests/goldens/` and false of the suite, and is corrected in `CLAUDE.md` and
`docs/14` with the numbers. An opt-in Parquet read cache
(`tests/io_cache.py`, `RECON_TEST_IO_CACHE=1`) halves those two — 357s to 210s
measured — keyed on `sha256(bytes) + sheet + header_row + engine` so an edited
export or a changed reading rule misses. **Off by default**, because a run served
from a cache is a weaker run and the run that matters is the one nobody would have
remembered to disable it for.

**D6 closed as a consequence**, not as separate work: the roster "optional" flag and
the settlement-bounds date pickers had both been described in help text and were
absent from the schema, and normalising made each of them a column.

**One correction worth recording.** During 1.1 the fast suite's `tests/goldens` was
cited as proof that deleting dead config moved nothing. It is not: those tests
compare committed digests against *static* golden cellsets and never execute
`pipeline.run`. The claim happens to be true and the evidence is the 1.4 golden
re-run, which ran afterwards — but the test named at the time could not have
detected a behaviour change either way.

**Phase 2 — the rest of the correctness work (2026-08-18).** Seven items, all
landed, golden gate re-run over all eight windows at zero tolerance with **no cell
moved**. The two worth remembering are the ones where the register was wrong.

*2.5 was specified against the wrong column.* The register said "`uploads.sha256`
sits unused ten lines from the download". Implementing exactly that failed **every
healthy window** on the first run — `sha256` digests the original upload while the
object store holds the sanitized rewrite, so the two were never comparable. Migration
`010` adds `object_sha256` and [D52](06-DECISIONS.md#d52) records why no backfill is
attempted. This is the pattern worth noticing: the register is a set of hypotheses
written from reading code, and two of the seven did not survive contact with a run.

*2.3's premise had to be measured before it could be landed*, because adding the
roster check to orders files can only ever ADD hard stops. Measured across all four
rostered golden windows: the store set derived from orders is **identical** to the
one derived from income on every one of them. Zero windows newly stop. Had that come
out differently the correct move was to report the list, not to land it and see.

*The date counter was not a register item* and was added at the user's request after
they challenged a claim in `CLAUDE.md` about what the fast suite proves. It closes
the date half of [defect 1.6](08-KNOWN-DEFECTS.md) and immediately found something
real: TikTok income is `%Y/%m/%d` while `dayfirst.tiktok` is `true`, so the file and
the contract disagree on every run. The dates are correct today only because that
column's first value is unambiguous — pandas infers the format from it and overrides
the setting. A column whose first value has a day ≤ 12 would transpose silently.
[D53](06-DECISIONS.md#d53) records why declaring the formats is a separate commit.

*A test-hygiene leak, caught by the tests that were already there.*
`window_references` was missing from the per-test `truncate` list, so figures
supplied in one test file changed the status of three worker tests in another —
`UNVERIFIED` became `VARIANCE`. It was unmissable precisely because those tests
assert a status rather than a count. `windows` was added alongside it: same key,
same shape of leak, and every worker test reuses one synthetic window name.

**Phase 4 — making failure legible (2026-08-18).** Seven items, web-layer plus the
service halves that back them. Three things are worth carrying forward.

*The traceback fix is legibility, not security, and the register said so carefully
enough that it survived implementation.* `GET /runs/{id}` and `GET /runs/{id}/log`
are both `VIEWER`, so moving a stack trace from one to the other restricts nothing.
What changed is what a finance user sees first on a failed run. The tempting
implementation — interpolate `str(exc)` into a friendly wrapper — would have put
connection strings and file paths on screen, so `failures.humanise` falls back to a
fixed sentence instead, and a test asserts a fake password does not survive it.

*The translation layer was too eager on its first pass, and the existing tests set
it straight.* `MaterializationError` was given a canned sentence; three materialize
tests failed because they assert on its real text, which names the file. The rule
turned out to be "is this message written for this reader", not "is this an
exception we recognise" — `ReconHardStop` and `MaterializationError` both pass
through, everything else is translated.

*A silent bug in my own new code, found by a test that asserted the outcome rather
than the call.* `reclaim_expired` returns `{"requeued": [...], "dead": [...]}`; both
the new endpoint and the new CLI command read `result["failed"]`. Reading a missing
key from a dict returns the default, so the sweep ran, changed rows, and reported
"nothing to reclaim". `assert response.status_code == 200` would have passed. What
caught it was `assert job.id in body["failed"]`.

*The worker healthcheck is a file, and the number in it comes from the lease.* The
worker has no HTTP server and giving it one to answer a healthcheck would add a port
and a framework to a process whose whole job is to hold one settlement run. It
touches `scratch/worker.alive` each loop turn. The threshold is 1200s because the
heartbeat is only touched *between* jobs — a worker inside a 269-second Shopee run is
legitimately silent, and a tighter threshold would restart healthy workers
mid-settlement. `stop_grace_period` was the same class of mistake already shipped:
Docker's 10-second default against a 171-second run had been SIGKILLing workers
partway through `build_workbook` on every redeploy.

*The limit that did not move.* There is still no browser automation. Every screen in
this phase was reasoned about and typechecked; none was exercised by a person or a
test. That is [defect 2.8](08-KNOWN-DEFECTS.md)'s gap, it applies to these screens
exactly as it applies to M6's, and Phase 4 does not close it.

**A wrong diagnosis corrected, 2026-08-19 — and it was wrong in both directions.**

Two files in the data tree refuse to open. The month-end master had been written up
in the register as "not an `.xlsx`: an OLE2 compound file, i.e. a legacy Excel
97–2003 `.xls` with the wrong extension". One Lazada weekly export had been recorded
since August, in four documents, as **"password-protected"**. Neither was true.

Both are genuine `.xlsx` files encrypted by a **Microsoft Purview sensitivity
label** — the same label id, the same tenant, `method="Privileged"` on each. The
`D0 CF 11 E0` signature that produced the first diagnosis is the encryption wrapper;
`xlrd` opens the container and finds no workbook stream in it, which is what gave it
away. The container's own `LabelInfo` stream names the label.

*The mistake worth remembering:* **a container signature identifies the container,
not what is inside it.** The magic-byte check was correct and the conclusion drawn
from it was not, and it survived because the conclusion was plausible and nobody had
a reason to look further.

*Why the distinction is not pedantry.* "Password-protected" points at a fix that does
not exist — there is no password to ask anyone for. A sensitivity label is org policy:
the file opens only for an identity the label grants rights to, no re-save touches it,
and **it will apply to every labelled file the team ever sends**. That makes it a
constraint on hosting this system at all, not a per-file nuisance, and it now sits in
[13-ENTRA-SETUP](13-ENTRA-SETUP.md) beside the role assignments and as register item
**C15**.

*What was built:* `src/ingest.rights_protected` — stdlib only, keyed on bytes,
distinguishing three cases that need three different answers (a sensitivity label:
re-saving will not help; plain encryption: ask for an unprotected copy; a genuine
legacy `.xls`: re-saving IS the fix). Wired into `read_excel_sheet` and the upload
door, so the failure stops being `File is not a zip file`. A healthy export costs 8
bytes, because a real `.xlsx` is a ZIP and the signature check answers immediately.

*The lint did its job on the way in:* adding a `open()` to `src/ingest.py` failed
`test_io_boundary.py` until it was declared in the `ALLOWED` table. Declared, not
exempted — widening that table is meant to be a visible act.

**Phase 5 — the vocabulary and Vietnamese (2026-08-19).** Five items; the two worth
carrying forward are both decisions rather than code.

*The Vietnamese came from the team, not from a translator.* `src/finance_template.py`
already held `VERDICT_OK = "ok có thể xuất HD"` and
`VERDICT_BAD = "Cần check lại số có vấn đề"` — the phrases the finance team writes in
their own workbooks. Those are now the run verdicts on screen, verbatim. Writing a
more standard Vietnamese rendering of *variance* would have been fluent and would
have made the interface read as a different system from the file it produces. A test
asserts the two never diverge, because the tempting future edit is to "fix" the
grammar.

*The default language is the browser's, falling back to Vietnamese.* The app shipped
`<html lang="en">` and zero Vietnamese words because nobody had got to it, not
because anyone decided. `Accept-Language` gives a Vietnamese browser Vietnamese with
nothing configured and a maintainer's browser English; Vietnamese wins ties, because
a finance user seeing English is worse than a maintainer seeing Vietnamese — the
maintainer can find the toggle.

*Two typography changes the language forced.* Column headers lost
`text-transform: uppercase`, because Vietnamese headers carry stacked diacritics that
capitalisation cramps; and the 12px floor went to 13px with body at 15px, because
ế/ộ/ữ lose their marks first at small sizes. Neither was on the register — they came
out of doing the translation.

*What is deliberately not done, and labelled:* byte-level upload progress (a server
action gives the browser no progress events, and an XHR rewrite would cost the
per-file refusal handling that matters at month end), and the rules editor and
accounts screens, which stay English because the rules editor's load-bearing content
is per-row evidence text written and verified in English in `settings.yaml`.

*The limit that still has not moved:* `tests/test_ui_vocabulary.py` lints the source.
It cannot tell anyone whether the Vietnamese reads naturally, because there is still
no browser automation and nobody has used these screens.

**The defect-register fix pass (2026-08-19).** The audit noted at the top of this
section corrected the register's *text*; this is what it changed in *code*. Nine items,
none of which had a milestone owner — M8's only remaining phase is deployment
hardening, so 2.9, D10, 1.9, the Lazada `dropna` divergence and the unpin audit gap
belonged to nobody. **The gate held: all eight golden windows re-run at zero tolerance,
no cell moved**, and the fast suite went `840 → 874 passed, 3 skipped, 4 deselected`
with every delta accounted for (+27 tests, +7 file-parametrized lint cases over five
new files). With the order index and the upload date door that follow, the suite stood
at **899 passed, 3 skipped, 4 deselected** (~6:21, read cache off), the money gate
passes over all eight windows at zero tolerance, `tests/goldens/manifest.json` is
untouched, and `tools/smoke_test.py` is 13/13. (It is **982** as of 2026-08-20 — C13's
backfill took it to 930, the borrow-path fix below added 7, B1's falsification tests
added 3, A14 added 16, and M8 Phase 6 added 25.)

*Recorded as a deviation:* all of it landed in **one** commit (`675f844`), because the
working tree was already dirty in 123 files before the work started, so the per-commit
separation this project requires was not achievable. The pin-fix-unpin ritual was still
followed *within* the work — each 2.9 gap was pinned `xfail(strict)`, seen to XPASS, and
unpinned — but the commit boundaries that normally record that are absent. Read the
tests, not the history, for that evidence.

**Defect 2.9 was three faults, not one, and two of them were not on the register.**
`tieout.py` differenced bare order-id sets. Under a collision the coverage check went
**blind** (another store's id made an unrepresented order look covered); `partition`
filed that settlement as *matched*, which **shrank the ~21% reconciling item** a
reviewer is told to watch for changes in; and per-order conservation summed two stores'
rebuilt revenue against one store's settlement, **manufacturing a variance from correct
data**. The second is the quietest and the worst — money left the invoice through a door
reporting less traffic than it carried. Fixed by one identity function, `tieout.pairs`,
used by *both* sides of every comparison, which also closed a stringification asymmetry
(`str(k)` on one side, `.astype(str)` on the other) that would have produced phantom
breaches. `tools/measure_order_id_collisions.py` is **committed rather than ad-hoc** —
the M2.5 measurement was a scratch script nobody kept, which is precisely why this claim
had to be re-derived three milestones later. It reports **0 collisions across 8,399,255
distinct order ids**, so the change is output-identical on today's data and bites only
the case it exists for.

*A technique worth reusing: the **discriminator**.* Each of the three regression tests
asserts, permanently, that the *pre-fix* comparison would still have passed. An `xfail`
marker proves that only until it is removed; a discriminator keeps the proof of the
original blindness in the suite forever, and fails loudly if a future fixture stops
exercising the collision it was built for. One of these was caught by its own
discriminator during development: the first attempt let a store vanish entirely, so
*store* coverage caught it as an accidental backstop and the test XPASSed for the wrong
reason.

**Per-store order coverage ships as INFO, and the measurement is why.** The plan called
for a *failable* check with a leave-one-out tolerance. Measuring first killed that: on
`2026-05_w1` — a golden window that reproduces the team's own figures — the entire
documented ~21% unmatched belongs to **one** storefront (Unilever Homecare 21.2%, Mars
0.0%). Any threshold calibrated to catch July's understatement would breach on
known-good data. So the *level* separates nothing, and what an operator gets instead is
an identity and a month-over-month comparison: `tieout.coverage_by_store`, an INFO row
naming the worst stores, and an `order_coverage` exception sheet keyed on `("store",)`
so "this store's coverage changed" is a `run_exceptions` recurrence query. The alarming
check moves to the one signal with **zero** legitimate traffic — "these lines exist in an
*earlier* window's export" — because the legitimate class has lines in **no** window.
Shopee also gained an `unmatched_orders` sheet; it had none, on the platform carrying
July's worst single cell.

**Two writes that could corrupt a deliverable, both closed.** All four artifact writers
now go through `pipeline._write_atomically` (sibling `.tmp`, then `os.replace`) — D10,
scoped to M2 and never landed. It buys: no truncated `finance_file.xlsx` can sit at the
final path, which matters because a truncated one still *opens* in Excel and looks
current. It does **not** buy writing over a file Excel holds open; `os.replace` raises
`PermissionError` there too, and both halves are pinned in `tests/test_atomic_writes.py`
so the limit is asserted rather than merely described. Separately, artifact **downloads**
were never digest-checked — the same shape as 2.10, opposite direction, with the digest
already stored since M6. That gap was found *by* the audit rather than corrected in it.
`sha256_of_chunks` verifies a streamed artifact in constant memory, a mismatch is a 502
naming both digests, and a `NULL` recorded digest is **refused, not backfilled**:
recomputing it from the store certifies the store against itself ([D26](06-DECISIONS.md#d26)).

**Both settings back-channels are gone (1.9), and the second was unregistered.** The
register named `settings["_vat_sku"]`; `_masters_source` and `_masters_searched` were
travelling the same way and nobody had written them down. Three fields on the frozen
`RunContext` plus an explicit `vat_sku` parameter on both `compute_sku_columns_*`.
**Lazada already had the right shape** — the M2.5 situation again, where the correct
pattern was already in the codebase for one platform of three. The one-job-per-process
constraint stays; only its *justification* was reworded, because it no longer rests on a
back-channel.

**`_blank_repeats` now copies, and the hazard is sharper than the register said.** It was
recorded as ordinary in-place mutation. The real risk is aliasing: `"Source.Name"` and
`"Source.Name non repeat"` are both built from `df["store"]`, so under a no-copy
constructor (pandas 3.0's Copy-on-Write default) blanking the repeat column would
**silently empty the store column on invoice tabs**. The `pandas<3` pin stays until a
measured pandas-3 golden run; the copy removes the sharpest edge behind it.

**Unpinning a window's config now leaves a record.** It bare-deleted the `period_config`
row: no trace that the window was ever pinned, to what, or why — in the system whose
entire M5/M6 rationale is the audit trail. Migration `014_pin_events.sql` is append-only;
pin, auto-pin and unpin each write an event **in the same transaction** as the change,
unpin requires a reason (422 without one), and the actor comes from the session, never
the body. `GET /config/pins` and `service.admin config pins` print the history. One bug
was written and caught in the same pass, worth naming because it is silent: reading the
wrong key off `reclaim_expired` makes a sweep that changed rows report "nothing to
reclaim".

**Lazada's promo pairing was measured before it was changed, and the measurement decided
the commit's class.** `revenue_lines` grouped promo without `dropna=False` while the
revenue side beside it had it — a dropped promo row means `price_ka` too high and an
**overstated** invoice. Probing all nine Lazada windows found **0 null `sku_id` and 0
null `product_name`**, so no cell could move and the fix is output-identical on today's
data. The orphan class it exposes is real but separate: genuinely unpaired promo of
−30,845 VND (July `l2`) and −22,486 (`l3`), now reported in a warning that no longer
reads the same for a null-key drop and a legitimate unpaired charge. This is also the
first unit coverage `revenue_lines` has ever had.

**The upload door now reads settlement dates, and the order index exists (2026-08-19).**
This is 2.3's residual closed and 2.12's detection half built. `POST /uploads` validated
`period` for character safety and nothing else, so a file could be addressed to any
window and nothing looked at what it settled — while `tools/stage_exports.py` had derived
the window from settlement dates since M2.5. Three cases, three different answers
([D57](06-DECISIONS.md#d57)): a window-defining file whose span does not **intersect** its
window's month is refused before anything durable is written; a file starting earlier than
every sibling is **warned** and accepted; order exports are **not checked at all**. Each
"weaker" choice is load-bearing — containment instead of intersection would refuse
Lazada's month-lapping weekly every month, refusing the outlier shape would block the
first upload into every window, and date-checking order exports would flag every healthy
TikTok folder, which is the same over-broad check staging already had to narrow.

Migration `015_order_index.sql` adds `upload_order_index(upload_id, store, order_id)` and
`service/order_index.py --backfill` clears the pre-door backlog. The rule it is built
under, and the answer to the "why not move the reconciliation into SQL" question that
prompted it: **the database may know where every number came from; it may never compute
one** ([D58](06-DECISIONS.md#d58)). Every column is an identifier or a count — a test
asserts that structurally, so a future migration adding an amount fails a test rather than
a review. The expensive-sounding half of the proposal turned out to be already paid for:
"have these exact bytes arrived before" was already answered by `uploads.sha256`,
`object_sha256` and `staging.json`.

*The backfill is deliberately not the [D26](06-DECISIONS.md#d26) trap.* It reads bytes out
of the object store, so it first **checks** the digest recorded independently at the door —
never derives it. A NULL digest is skipped and named ("re-upload to index"); a mismatch is
refused and exits non-zero, so a scheduled sweep cannot report success while a store
serves altered bytes. The index it writes is never an integrity reference for anything.

*What the cross-window query buys, in one sentence:* `w2` settles an order whose SKU lines
exist only in `w1`'s export, and that is now findable — the signal with **zero** legitimate
traffic, because the legitimate ~21% unmatched class has lines in no window at all. A
seeded two-window fixture asserts it, including that the query is predecessor-only so
re-running an early window cannot change once a later one arrives.

**And it is now visible in three places before a run, not after a month-end tie.** The
worker logs it (`_report_order_coverage`, immediately after materialisation),
`GET /windows/{platform}/{period}/order-coverage` serves it to a viewer, and the window
page shows it beside the roster preview. Three properties of that surface are deliberate:

* **The worker adds no compute** ([D31](06-DECISIONS.md#d31)) — these are log lines, and
  the authoritative per-store coverage still comes out of the run itself. The cross-window
  half is the only part that *cannot* come from the run, because a run opens one window's
  folder and the answer lives outside it.
* **The two halves differ in tone on purpose.** Orders missing from every window are the
  documented reconciling class and are logged as counts; orders whose lines sit in an
  earlier window's export get a warning naming the window, the file and the upload id,
  because that is a sentence somebody can act on.
* **"Not indexed" is never rendered as "all covered".** An empty result from a window whose
  uploads predate the index looks identical to perfect coverage, so `indexed: false` is
  carried through to the screen and says so. The UI also names the actual remedy — ask the
  platform to re-export — and warns against the pooling anti-fix, because that is the
  intuitive wrong answer and it over-counts 4.5×.

**The borrowing mechanism now exists and runs in `report` mode (2026-08-19).**
`src/backfill.py` answers "which earlier window exported this order's lines?" and can
hand them to the explode; `cross_window_order_backfill: off | report | apply` decides
whether it does. Detection and fix are deliberately the **same code path**, so the thing
that eventually moves money is the thing that has been reporting in production first —
rather than a second implementation written on the day it is trusted.

*Report mode's inertness is proven, not argued.* Its only outputs are a tie-out INFO row,
an exceptions sheet and log lines, and none of those is a field of the golden manifest:
INFO rows never become variances (`consume_tieout` only promotes a `BREACH`), and
`stage_row_counts` comes from an explicit list of fingerprinted functions that this does
not touch. **The money gate re-ran all eight windows under `report` at zero tolerance and
no cell moved.** A measured bonus: Shopee's own-window order coverage is 100% on the
golden windows, so `s2` and `s3` reported "every settled order has lines in this window"
and never opened a predecessor's files at all — the common path costs nothing.

*Two things were extracted rather than copied, and that is the point.* `ingest.read_files`
and `ingest.export_files` came out of `read_parts` (whose name, signature and behaviour
are unchanged) so borrowing reads through the **same** reading rules every verified number
was produced under — the broken-`<dimension>` fallback, NFC headers, the sheet regex, the
junk-row skip, the PII drop. And `backfill.predecessor_labels` is one rule over two
sources of candidates: directories on the CLI, window labels out of Postgres in
`materialize_predecessor_orders`. Two spellings of "which window is earlier" is exactly
the drift this defect is made of.

*The worker had to learn to fetch them.* On the CLI every window is already a sibling
directory; on the service the input root is a scratch dir holding only this window, so
report mode would have found nothing — and finding nothing looks like good news.
`materialize_predecessor_orders` downloads earlier windows' **order** files only, digest-
checked exactly as the window's own are, and **only when the resolved config asks**: a
window pinned before the flag existed behaves precisely as it did then.

*Still not done:* the flip to `apply`. The recovery it would produce is already measured
per window, and the two mis-pull cells recover nothing because their lines were never
exported.

**The borrow path was reading through only half of those rules — corrected 2026-08-20.**
The extraction above stopped one function short. `read_files` was shared; everything
`read_parts` does *after* it was not, so borrowed frames got no `store_aliases` and no
numeric coercion. The alias half was live in `report` mode and silent: `needed` holds
canonical store names while a filename holds what the platform exported, and
`settings.yaml` maps `"Pediasure" -> "Abbott Pediasure"` because the order files drop the
"Abbott" — so w1's Abbott files were skipped before anything read them and **941,081,056
VND of July's measured recovery reported as zero.** `2026-07_w2` now reports 942,869,056
VND against ~1,788,000 before, and `2026-07_s2` 173,429; both figures now equal
`tools/measure_order_coverage.py`'s, and that agreement is the closure evidence, because
the disagreement *was* the defect — the tool read through `read_parts` and the pipeline
did not, and nothing compared them.

The fix finishes the extraction rather than patching the caller: `ingest.normalize_parts`
now holds the post-read rules (numeric coercion, dates, strip, aliases) with `read_parts`
delegating to it, and `ingest.canonical_store` absorbed the copy that was living in
`service/materialize.py` so the roster preview, the pipeline's reads and the borrow's file
prefilter cannot drift on what a store is called. Proven output-identical by the money gate
before the behaviour change went in, and again after: eight windows, zero tolerance, no
cell moved either time — no golden window opens a predecessor, so none could.

*Three things this pass declined to do, each checked rather than assumed.* Borrowed rows
are **not** branded: the explode's groupby keeps five key columns and the SKU frame takes
brand from the income side, so `derive_brand` there would be a call in the money path that
changes nothing. The fan-out guard became a `ReconHardStop` instead of an `assert` (`python
-O` deletes asserts, and it guards the one over-count that inflates an existing SKU line
invisibly) but has **no test** — it is unreachable by construction, and driving it would
assert a mock. And the `REQUIRED_COLUMNS` check stayed in `read_parts` as a window's own
contract; `normalize_parts` guarantees only the two identity columns it touches.

*One policy replaced three disagreeing ones.* An unreadable predecessor warns and skips
under `report`, refuses under `apply` — same rule in `src/backfill.py` and
`service/materialize.py`. Previously a digest mismatch hard-stopped a report-mode run
while an unnameable file and a missing object only warned, and two predecessor uploads
sharing a filename were silently last-one-wins where the window's own files refuse it.
The worker's predecessor download also now asks `backfill.mode_of` instead of its own
`!= "off"` test, which had accepted `"OFF"` and downloaded files before a typo could
hard-stop inside `run()`.

**The safety property became a test before it became a default (2026-08-20).** D59 and
the register had asserted since 2026-08-19 that a predecessor export whose quantities
drifted *breaches* a check rather than being invoiced — the whole argument for why
borrowing is not pooling with extra steps — and nothing drove it. Three tests now do,
through the real `explode_to_sku_tiktok`, the real `compute_sku_columns_tiktok` and the
real `run_checks_tiktok` rather than a re-implementation: a borrowed order reaches the SKU
frame and ties to its settlement; the same fixture with one drifted quantity **BREACHES**
per-order conservation; and an order the window already covers is not doubled by `apply`
— asserted on the quantity *inside* the SKU bucket, because that is where the 4.5x
over-count would hide rather than in a row count.

**`cross_window_order_backfill: apply` is the default (2026-08-20).** The money move, and
it moved no golden cell — which was **stated in advance, not discovered**: `needed` comes
from `read_parts` frames on both sides, so no golden window borrows anything at all
(`2026-05_w1`/`_s1` are first of their month; `_s2`/`_s3` have 100% own-window order
coverage; the four Lazada windows have no order files). The money gate re-ran all eight at
zero tolerance after the flip, `tests/goldens/manifest.json` is untouched, and **no golden
was re-baselined for this change** — the one commit in this whole sequence that was
entitled to move a baseline did not need to.

What it does on real data: `2026-07_w2` applies **942,869,056 VND** across 825 orders
(abbott 941,081,056 + similac 1,788,000), and per-order settlement conservation is
**exact** — variance 0.00 against a 38,609,498,443 VND rebuilt total, at a 1 VND
tolerance, with the borrowed lines in the frame. Its reconciling "settlement with no
matching order lines" fell from ~2,394,101,094 to 1,451,232,038, of which Purite alone is
1,444,139,986 — the byte-identical mis-pull that needs a platform re-pull, not code.
`s2` applies 173,429 and `s4` 1,390,095,674. A **pinned** service window keeps the mode it
was pinned to, so re-running July through the worker is a deliberate repin, not a
side effect of this line changing.

*The doc sweep that followed (2026-08-20).* Switching a default on makes every sentence
that describes the old default wrong, and there were more of them than expected: 2.12's own
header still said `OPEN` and its body still said the fix "is running in `report` mode" and
that `apply` "has not been switched on"; `docs/03`'s stage 3b named `report` as current;
`docs/09`'s runbook row explained the `NOT applied` message and advised *considering*
`apply`; `docs/10`'s milestone table and `docs/14`'s 3.7 row both described the defect as
open; `docs/07` carried a "never been run against real data" paragraph. All corrected in
place, with the superseded text kept where it earns its place — `docs/07`'s paragraph is
quoted verbatim before being superseded, because the check it insisted on ("agreement
between the two is a check worth making at the flip") is what found the alias defect a day
later. **Renaming 2.12's header also broke its own inbound anchors**, which is a cost of
putting status in a heading; those were repaired, and a sweep found and fixed **11 more
pre-existing** broken cross-doc anchors from earlier milestones' `OPEN`→`FIXED` renames.
`docs/16` gained **Ask 3** — the two order exports that need re-pulling, stated as two
different asks because `purite`'s file is the *wrong* file and `masan`'s looks *truncated*.

*The one that was nearly misread, recorded because the next reader will hit it too.*
`2026-07_s4` under `apply` BREACHES Shopee's revenue crossing — per-order worst 893,000
VND, total −95,928,999 — which reads exactly like the fix breaking a control. It is the
reverse. The same window under `report`, same flags, breaches at total −2,111,292,476 with
a per-order worst of 3,276,000; `apply` improves that crossing by **2,015,363,477 VND**
and takes Masan's unmatched share from 66.8% to 3.0%. The breach is pre-existing in both
modes: the check's own detail line reads "729 of 40,299 orders deviate, 729 absent from the
SKU frame entirely" — orders with no lines in *any* export, which cannot tie by
construction and need a platform re-pull.

Two things that made the difference between diagnosing and guessing. The check names how
many deviating orders are **absent** rather than only printing a variance, which is what
separated "broke it" from "improved a pre-existing breach". And the baseline was
**measured** — settings flipped back to `report`, the window re-run, the numbers compared —
rather than reasoned about from the code. The first attempt to check this had grepped a
pattern that did not match the `Check Revenue conservation` lines at all, so the earlier
report-mode run appeared clean when it was not; a filter that silently excludes the lines
you are looking for reads exactly like good news.

*The July measurement that will pre-state the fix's effect:* cross-window recoverable
settlement is **942,869,056 VND** in `w2` from `w1` (exactly abbott 941,081,056 +
similac 1,788,000), **1,390,095,674** in `s4` from `s3`, and 173,429 in `s2` from `s1`.
`purite` and `mondelez kinh do` recover **nothing** — their `w2` order files are the
byte-identical mis-pulls, so those lines were never exported and no code can recover
them. That residual's remedy is a platform re-pull.

**A14 — the last bucket lists joined the contract (2026-08-20).** The tail of M8/1.7's
move, done as its own change exactly as docs/14's deferral bullet asked. What moved, values
verbatim: `finance_template.VAT_RATES` → `vat_factors.rates` (a `config_scalars` row);
the three invoice-bucket lists → `invoice_buckets.<platform>` (`match` + `default`,
walk order preserved as `sort_order`); `lazada.REVENUE_BUCKET`/`PROMO_BUCKETS` →
`fee_buckets.lazada` (one `revenue` row, five `promo` rows). Migration `016` adds the two
row tables; both keep `invalidates_goldens` **true** — a bucket row decides which tab a
store's money lands on. Everything is read through hard-stop accessors with no code
fallback (`finance_template.vat_rates`/`invoice_buckets`, `lazada.revenue_bucket`/
`promo_buckets`), and `revenue_lines` now takes `settings`. The bare `1.08` that filled a
zero-pre-VAT group's VAT factor in `_sku_pivot` now reads `vat_factors.default` —
value-identical today, and deliberately *semantically live*: a future default change now
moves it too, instead of leaving a literal behind.

Three edges stated rather than left latent. **The workbook TAB layout stays code** —
which bucket gets a tab, in what order, the control-block cell positions are template
geometry pinned to the team's own files (the TikTok file itself is internally
inconsistent about order: tabs go Merries-then-KAO while its PV-sum side block goes
KAO-then-Merries, so no single configured order could drive both). A configured bucket
the template has no tab for hard-stops, never leaks into a pivot-drift breach. **Shopee's
and Lazada's control-row geometry hard-wires the 1.05/1.08/1.10 trio** (PV-sum side
block, Summary rows, per-rate tab pairs), so those builders hard-stop on any other
`vat_factors.rates`; TikTok's layout is fully enumerated from the list. **Editing
`vat_factors.rates` in the browser cannot silently stringify it** — `_coerce_scalar_value`
now preserves numeric-list element types, because `"1.05"` compares equal to no
`vat_factor.round(2)` and would have zeroed every per-rate row (the `dedupe_rows`-
becoming-`"false"` failure shape, one level down).

One consequence for old pins, same as M8/1.7's: a window **pinned to a pre-A14 config**
hard-stops on re-run with the message naming the missing key; the remedy is the audited
unpin/repin, not a fallback. **The gate: all eight golden windows re-ran under the
rendered contract at zero tolerance — no cell moved, no golden re-baselined.**

**M8 Phase 6 — deployment hardening (2026-08-20).** The last phase: the eleven
"breaks when deployed for real" items (register C3–C13), each done to the boundary of
what the repository can do, with the hosting-side halves *named* in docs/14's Phase 6
table rather than absorbed into a claim. Nothing touches `src/` — no golden question
existed, and the structural gate was run anyway.

In one paragraph each, with the number that matters:

- **Migrations serialize on `pg_advisory_lock`** (C8) — first boot was api-vs-worker
  coin flip; the loser crashed on `schema_migrations`' primary key and
  `restart: unless-stopped` hid it. Race-tested with two threads; the unlock lives in
  `finally` after a rollback so a failed migration cannot wedge the lock.
- **Uploads are capped** (C7) — `RECON_MAX_UPLOAD_MB`, default 512 against a measured
  184 MB largest-ever real export, enforced by a bounded `read(limit+1)`, refused 413
  before the sanitizer or object store see a byte.
- **`/healthz` answers "does a worker exist"** (C6's second half) — `worker_heartbeats`
  (migration `017`) beaten each idle turn and each lease extension, so a worker deep in
  a 269-second run stays visible; `queued: 3, workers_alive: 0` is now a readable state.
- **Workbook downloads are audited** (C12) — `artifact_downloads` (migration `018`),
  written after the digest check, before a byte streams, in the request path
  deliberately; a refused tampered download records nothing.
  `service.admin audit downloads` reads it.
- **Retention exists** (C10) — `service/retention.py`: scratch 14d (kept-on-failure job
  dirs age out after their diagnosis window), the run-log DB mirror 90d (run_log.txt in
  the artifact store is the durable copy; the runs row is never touched), dead sessions
  30d (live ones are never candidates), plus a disk-free warning. Hourly in the worker,
  on demand with `--dry-run` in the CLI.
- **The service can be seen** (C5) — `service/obs.py` emits one JSON object per line
  (api + worker, uvicorn included) under a content rule stricter than the run log's:
  identifiers, counts, durations — never a store name or a figure. `GET /metrics`
  (viewer role) serves the counters. Alerting is the host's half; an ERROR line is the
  hook.
- **The config audit is decided, not implicit** (C11 → [D60](06-DECISIONS.md#d60)) —
  the `config_versions` row is the audit record; git is a developer-checkout
  convenience; the api response and the config page say so instead of an unexplained
  `committed: false`. Pushing to a remote was rejected as a new credential surface.
- **Backups exist and the restore drill was PERFORMED** (C3) — nightly `pg_dump -Fc`
  under `--profile backup`; `tools/db_restore_drill.sh` restored `recon_dev` into a
  scratch database on 2026-08-20: 30 tables, 1,037 rows, every per-table count
  identical. docs/09 keeps the drill log. Offsite is the hosting half, stated.
- **Secrets left the synced tree** (C13) — the real `deploy/.env` moved to
  `%LOCALAPPDATA%\recon-deploy\`; compose runs with `--env-file`; the ingress cert
  pair follows the same rule.
- **A front door exists before the hostname does** (C4 + C9) — an nginx profile:
  TLS (self-signed via `deploy/ingress/make-self-signed.sh` until a real one exists),
  the PINNED X-Forwarded-For proxy, per-IP rate limits (general / Next-Action
  mutations / `/login`), `client_max_body_size` mirroring C7's cap, and
  `RECON_ALLOWED_ORIGINS` wired into `serverActions.allowedOrigins` as a web build
  arg. The nginx config was rendered and `nginx -t`-verified inside the real
  `nginx:1.27-alpine` image with the real cert pair; **it has not yet been browsed
  through** — the same standing no-browser-automation limit as every UI surface (2.8).

Two sharp edges found while doing it, recorded so they are not re-found: Git Bash
rewrites `-subj "/CN=..."` into a Windows path unless `MSYS_NO_PATHCONV=1` (the cert
script carries it now), and the nginx image's envsubst substitutes every `$VAR`
defined in the container's environment — `$host` survives only because no `HOST`
variable is set, which the template's header now warns about.

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

## 2026-08-19 — July 2026 format and roster drift, found by staging and running the month

The first full month staged and run since May, and it needed maintenance in four
places. Recorded here because "rules that are month-shape-dependent have needed
maintenance every single month so far" is a claim this project makes, and July is
another data point for it.

**1. TikTok income dates (the expensive one).** `dayfirst.tiktok: true` against a
`%Y/%m/%d` income column inverted day and month, deriving a 1–7 July window as
`2026-01-07..2026-09-07`. `tools/stage_exports.py` could not derive a single TikTok
window from a 3.7 GB dump, and every folder's prior-month order re-pull then
collided as a false double-pull. Fixed by declaring formats explicitly
([D54](06-DECISIONS.md#d54)). **This had been latent since May and cost nothing
then**, because May's first date value happened to be unambiguous so pandas'
inference silently overrode the flag.

**2. Lazada's two variants disagree with each other.** Weekly writes `03-Jul-2026`,
Daily writes `29 Jul 2026`. Never noticed because inference handled both; it
matters now that formats are declared, so the ledger parses per variant.

**3. Shopee roster.** Three new storefronts — `Tolpa`, `pepsicofoods`,
`xa_kho_gia_tot` — and one alias: the order files call Unilever AHC `AHC` while its
income file spells it out. All four July Shopee windows hard-stopped on the
unexpected-store check until these landed. The check was working; the config was
stale.

**4. TikTok folder spans overlap, so its windows cannot be derived.** Two genuine
mis-pulls (a U food income file carrying w1's block, a Curel file carrying the
whole month) plus a one-day boundary overlap at 7 July. TikTok is staged per folder
with an explicit `--period`; Lazada and Shopee still derive automatically.

**Three exports declare themselves empty in a way that used to block a window.**
TikTok emits one all-blank row for a store that settled nothing —
`19. Income Merries 29-31.xlsx`, `23. Income Reckit 29-31.xlsx`,
`22. income Nutifood-Varna-Life.xlsx` (22-28). One row, 65 columns, not a single
non-blank cell. They reported "no parseable dates", which reads as a broken export.
They now join Shopee's zero-revenue "part 2" exports in the self-declared-empty
class. The blank test had to tolerate **whitespace**: the row blocking `w4` held a
single space in every cell, so an `== ""` test called it data.

**Staging's cross-window duplicate check was too broad and was narrowed.** TikTok
ships each store's prior-month order re-pull in *every* weekly folder,
byte-identical, because the cross-period stitch needs it. Twenty-three such files
were reported as double-pulls in one dump. Cross-window duplicates are now only
reported for the kinds that DEFINE a window (income / Weekly / Daily); the
same-window check is unchanged for every kind, because the same bytes twice in one
window really does double-count.

## 2026-08-21 — the workflow holes: A4's remainder, D1, D2, D3

Four register items in one pass, each to its own boundary. Nothing here touches money
math; the only golden movement was D3's stamp, pre-stated and matched exactly.

**A4 — and the register row was stale.** "No API route, no screen; not downloadable"
had been false since Phase 3: the month master is a first-class run whose
`month_master.xlsx` goes through the same `write_artifacts`/`ArtifactStore` and the
same digest-checked, download-audited artifact route as every settlement workbook.
What was genuinely missing: nobody could *request* a master (the only creation path
was the automatic post-run chain; `POST /jobs` rejects `platform='all'` and has no
`kind`), the chain's outcome lived only on worker stdout, and the run page
breadcrumbed a master to `/windows/all/{month}`, which 404s. Built:
`POST /months/{month}/master` (user role, 409 on an active master),
`service.admin job enqueue-master --month` as the break-glass twin, a board form,
`runs.chained` (migration `019` — the run log is already stored when the chain runs,
and `test_the_stored_log_and_the_database_log_are_the_same_log` is why the log was
not an option), a WARNING service-log event on a failed chain, and a breadcrumb that
points a master at the board's month view.

**D1 — exception dispositions ([D61](06-DECISIONS.md#d61)), M6's original scope,
finally.** Migration `020`, the pin/pin-events two-table shape. Decided and worth
repeating: **a disposition annotates, never hides** — the fingerprint hashes identity
columns, not amounts, so suppressing recurrences would hide exactly the grown
variance a reviewer must see; `open_only` is an explicit filter, the default answer
is always whole. `recon.user` dispositions with a mandatory 8-character reason;
clears require their own reason and record what they released. One test-infrastructure
lesson: the new tables key on the fingerprint (deliberately no FK), so no cascade
truncates them — they had to join the conftest truncate list, found as cross-test
state leakage on the first full run.

**D2 — the board unions evidence, not jobs.** `board()` was `FROM jobs`, so a window
whose exports were uploaded but never queued was invisible. It now starts from the
same known-window union as `month_windows` (jobs ∪ roster declarations ∪ live
uploads), with the uploads-evidence condition (`platform is not null and state <>
'rejected'`) extracted to ONE spelling shared by both queries. Upload-only windows
render "not yet run" with a file count and link to the window page; `GET /months`
feeds a real month picker; the queue form suggests known windows per platform and
keeps free text for a brand-new period.

**D3 — the roster declaration names its stores ([D62](06-DECISIONS.md#d62)), is
re-evaluated, and the workbook says it is partial ([D63](06-DECISIONS.md#d63)).**
Migration `021` adds `declared_absent_stores`; `apply_partial_roster` grew an
`optional_stores` parameter whose `None` is the old blanket **verbatim** — that is
what the CLI's `--partial-roster` bool produces and the mode all four partial-roster
goldens regenerate in, pinned by `tests/test_partial_roster.py` before anything else
was built. A named list relaxes only those stores, so `check_stores`' unchanged
arithmetic hard-stops a forgotten store again; an unknown declared name hard-stops
naming itself (membership checks at run time against the run's own — possibly
pinned — roster, which is why the API door deliberately does not check it). A stale
declaration (declared-absent store now has files; or blanket on a complete window)
is a run-log warning plus an amber window-page notice, never a hard stop and never a
finding: the figures are included either way, only the record is wrong.

**The stamp, and its golden procedure.** The four non-partial lazada windows
regenerated first with zero refusals (the identity proof for the non-partial path);
`2026-05_w1 --partial-roster` without `--rebaseline` then refused with `fields that
changed: workbook` — fingerprint and variances digests untouched — and the cellset
delta was exactly two cells (`PV sum` 25 → 27, both in row 1). The four
`partial_roster: true` windows were rebaselined with `--rebaseline --reason` naming
D63. The stamp is Vietnamese (the workbook's own language), names the absent stores
rather than counting them, and is deliberately not a `checks` entry (that would move
`fingerprint_digest`).

**Found on the way, recorded rather than fixed: defect 2.13.** The stamp was first
placed below the PV sum side block and never appeared: `_Tab.emit` silently drops
buffered control cells at any row ≥ `data_start_row` on tabs that emit data. The
committed goldens confirm the loss is pre-existing — TikTok's `PV sum` has never
carried its side-block totals and G8/H8 verdicts, Shopee's loses rows 5–11 including
the J11 "working verdict". The verdicts survive in `checks`/run log, so this is a
rendering gap, not a lost control — and fixing `emit` moves cells on all eight
goldens, so it needs its own pre-stated pass ([08-KNOWN-DEFECTS 2.13](08-KNOWN-DEFECTS.md)).
The stamp lives at A1/B1, which is both the safe region and where a caveat belongs.

## 2026-08-21 — A9: a code default may not disagree with the contract

One register row, and it turned out to name a class with two live members rather than
the single line it cited. Nothing touches the money math; **the golden gate re-ran all
eight windows at zero tolerance with no cell moved**, which was the delta stated
before running it.

**The named instance.** `settings.get("dedupe_rows", True)` against a contract that
says `false`. Byte-identical order lines are legitimate — duplicated gift SKUs, where
the team's quantity of 2 became 1 under deduping — so a settings dict that forgot the
key understated revenue with nothing to show for it.

**The instance nobody had named, found while planning the fix.**
`settings.get("cross_window_order_backfill", "off")` (`src/backfill.py:120`) against a
contract that says `apply`. Absence silently reverted defect 2.12's fix: 942,869,056
VND on `2026-07_w2`, 1,390,095,674 on `_s4`, 173,429 on `_s2`. **That default was
correct and safe on the day it was written** — the mode still was `off` — and stopped
being safe when the mode was flipped later the *same day*. `mode_of` guarded the typo
case with a comment saying "unset and misspelled are different mistakes", and left the
more likely of the two unguarded. `tests/test_backfill.py` asserted the wrong
behaviour and its own comment explains why: it cited "today's behaviour, the one every
committed golden was produced under", which is a claim about the world, not about the
code.

**So the fix is the class, not the line.** `src/config.py` gains `REQUIRED_SETTINGS`
(three keys: `dedupe_rows`, `drop_unmapped_columns`, `cross_window_order_backfill`)
and `require`, which hard-stops naming the key, what the value decides, and where to
set it — and refuses a quoted `"false"`, truthy in Python and a silent inversion the
service already guards at the row (M8/1.6) on the one path that has no rows.
`vat_factors.default`'s three bare `1.08` fallbacks — the last A14 left — become
`config.vat_default`, so the 8%→10% revert cannot be outlived by a literal.

**`service/config_render.DANGEROUS_DEFAULTS` is now `src.config.REQUIRED_SETTINGS`,
imported rather than restated.** Its old local copy is the third stale claim this pass
found: it described `cross_window_order_backfill`'s absence as "the SAFE direction
(today's behaviour, and every golden was produced under it)" in the justification of
the control meant to catch it. Render-time refusal stays — a run that hard-stops has
already been queued, materialised and started — but both layers now read one set of
keys.

**`tests/test_config_defaults.py` is the part that outlives this.** An AST walk over
`src/**` pairs every `settings.get(<key>, <literal>)` with the configured value and
fails on disagreement; a key nothing configures needs an entry in
`CODE_LITERAL_IS_THE_CONTRACT` with its reason (`date_coercion` is the only one, and
D53 is the reason). It carries a negative control that parses both inversions as
source text, because a walk that silently matched nothing would make the whole file
pass. Six defaulted reads survive in `src/` and all six agree.

Recorded rather than fixed: `(settings.get("dayfirst") or {}).get(platform, False)` is
a real inversion (`dayfirst.tiktok: true`) and a dead one — `date_formats` takes
precedence for both platforms that read it ([D54](06-DECISIONS.md#d54)) — so the walk
cannot compare it and does not pretend to.

## 2026-08-21 — D12: one storefront→brand mapping, in the database

Register D12, decided as [D65](06-DECISIONS.md#d65). **All eight goldens re-ran at
zero tolerance with no cell moved**, which was the delta stated before running it.

**What there was.** `store_to_brand` in the contract, `{}` — so `ingest.derive_brand`
fell back to the store name for every store and warned on every run — plus
`config/brand_map.csv`, 60 rows read only by the month-end master through
`master_summary.parse_brand_map`. `config_store_brands.in_pipeline_contract`
(migration `007`) marked which of the two was in force, and existed so the config
migration could avoid a behaviour change.

**What the measurement changed about the plan.** The register and migration 007 both
said rendering the CSV's rows would "change the brand of 28 stores". At the pipeline
it was **2**: `derive_brand` used an exact `df["store"].map()`, the CSV's keys were
`norm_store` spellings (`abbott grow`, `ufood store`) and the pipeline's stores are
roster spellings (`Abbott grow`, `ufood_store`) — TikTok 0/25, Shopee 2/17, Lazada
0/18. Flipping the flag would have rebranded two storefronts and silently left forty
unbranded, with the contract looking fully populated. **That is worse than the empty
map it replaced**, because an empty map warns about every store and a near-miss map
warns only about the ones it missed. So the fix is the matching rule, not the flag.

**What there is now.** One table, one rendered key, two projections:
`ingest.store_brands(settings, platform)` for a run and `ingest.brand_map(settings)`
for the master, both resolving through `norm_store` on **both** sides. Under it all
25 TikTok and all 17 Shopee rows match the roster. The rendered key is per platform
and each entry carries `brand` / `confidence` / `note`, because the master's "Brand
mapping" tab is the surface the team reviews the mapping on. `brand_map.csv`,
`parse_brand_map`, `month_master.brand_map` and `in_pipeline_contract` (migration
`022`) are gone. `norm_store` moved `src/pipeline.py` → `src/ingest.py` as a pure
re-exported move, because `pipeline` imports `ingest` and `derive_brand` needed it.

**The shape change was free exactly now.** The key was `{}`, so every window pinned
to a pre-change config renders an empty map and no pinned behaviour depends on the
old flat shape. Once it has rows, changing it means repinning.

**Three things found on the way, none of them D12 as written.**

1. **The month-master worker read `load_settings(config_dir)`** — the container's own
   baked copy of `config/`. Harmless until today and load-bearing the moment the
   brand mapping became contract: a brand corrected in the editor would never have
   reached the master while every settlement run used the new one. That is **A1 one
   layer over**. It resolves through `_resolve_config` now, unpinned, because a
   master is a month-level artifact that pins to no window.
2. **`confidence` had two vocabularies** — the CSV's `high` against
   `config_store_brands`' `confirmed` / `needs_confirmation` CHECK constraint. The
   master flagged anything that was not `high` in red, so once the CSV became the
   contract every row would have printed red and taught the team to ignore the
   column. The database's words win.
3. **Three Shopee storefronts have no brand row, deliberately** — `Tolpa`,
   `pepsicofoods`, `xa_kho_gia_tot`, the July onboardings, which the CSV had already
   missed. They fall back to their own name, the run reports it, and the master flags
   them UNMAPPED. `tests/test_brand_mapping.py` asserts those three **by name**, so a
   fourth unmapped storefront fails a test rather than joining a tolerated count.
   Inventing a brand in a mapping the month-end master invoices from is not
   engineering's call.

**New refusals.** A bare `store_to_brand.tiktok.Kao: "KAO"` is a hard stop — reading
it would mean inventing a `confidence`, and that field exists so nobody has to guess
whether a mapping was reviewed. A brand row must name a roster storefront, the same
rule `config_store_aliases` already carries and matched through `norm_store` too, so
a person may type `ufood store` where the roster says `ufood_store`; Lazada is exempt
and reported rather than refused, because `expected_stores.lazada` is empty (register
A6) and an empty roster means "nobody has said".

Also corrected while in the files: `docs/05-DOMAIN-RULES.md` still claimed "there is
no config file for" invoice buckets, which **A14 had already made false** on
2026-08-20 — fixed in place rather than quietly, since which layer owns a rule is
what that document is for.

**The master was measured too, because its risk is the mirror image of the
pipeline's.** The golden gate proves no settlement cell moved; nothing gates the
month-end master, and it had 60 **working** brand rows before this change — so the
question there was not "did anything move" but "did anything stop resolving". July's
master was built twice over the same 14 finance files, once with the deleted CSV
(recovered with `git show`, keyed exactly as `parse_brand_map` did) and once from the
contract:

| | Before | After |
|---|---|---|
| `By brand` rows | 30 | 30, none added, removed or moved |
| with-VAT total across the tab | 261,663,086,591.88 | 261,663,086,591.88 (diff 0.00) |
| `Brand mapping` rows whose **brand** changed | — | **0 of 63** |
| rows newly resolved by `norm_store` matching | — | **0** |

All 60 changed rows changed in the `confidence` column only (`high` → `confirmed`,
`needs-confirmation` → `needs_confirmation`), and the three rows that carried
`needs-confirmation` still carry it — so the review request the tab exists to make
survived the vocabulary change. The `note` column also changed on the three UNMAPPED
rows, which named the deleted file; that column was not part of this comparison.

**Zero newly resolved is the useful result, and it corrects how D12 was described.**
The master's storefront labels are already `norm_store`-lowercased
(`master_summary.per_store`), and the CSV's keys were written in that same spelling —
so the old exact lookup was **correct for the master on all 60 rows**. The
exact-match failure was only ever a *pipeline* problem, where stores arrive in roster
spelling. The register's "two mappings that disagree" was right about both halves and
named neither the key nor the reader that made them disagree.

## 2026-08-21 — D7: the store correction becomes reachable

Register D7, decided as [D66](06-DECISIONS.md#d66). **No `src/` change, no contract
change, so no golden could move and the money gate was not run** — stated rather than
run, which is the honest form of "not applicable".

**The defect was a three-sided contract missing one side.** `POST /uploads` accepted
an optional `store` — confirm or correct what the filename pattern found, recording
both — since M6. `web/app/actions.ts` posted `store:<filename>` per file since M6.
Nothing rendered an input, for the whole of M6, M8 and five register phases, so the
only way to fix a misparsed filename was to rename the file on disk and upload again.
Both existing sides were correct, which is why nothing noticed.

**What was built.** `POST /uploads/store-preview` takes *filenames only* — the cost
being avoided is a 184 MB export landing on the wrong storefront, and sending the
bytes to find out would cost exactly that. It answers per file with the derived
store, its canonical form after aliases, the uniform name the file will carry,
whether that storefront is on **this window's** roster (`_domain_for_window`, so a
pinned window previews against its own), and the refusal sentence when a name cannot
be read. The upload form renders one row per chosen file with a correction picklist,
posting into the field name that was already being read.

**Three things decided rather than defaulted:**

- **The derivation stays server-side.** Store identity comes from the filename
  ([D6](06-DECISIONS.md#d6)); a regex in the browser would be a second definition of
  the rule that decides whose revenue a file becomes.
  `tests/test_upload_store_control.py` asserts no `.tsx` file reads
  `store_from_filename` outside a comment.
- **Picklist where a roster exists, free text where none does.** An off-roster store
  is refused at the door and the fix is a config proposal, so free text would produce
  the same refusal more slowly. Lazada has no roster (A6), where a picklist would be
  empty. *Measured, so the picklist cannot offer an option the door will refuse:* all
  45 roster storefronts survive `validate_roundtrip` on every kind their platform
  has.
- **A failed preview does not block the upload.** The door performs the same checks
  with the same sentences; the preview finds out early and is not a new control. A
  preview outage must not stop month-end.

**Reporting, not refusing, per file.** An unreadable name or an off-roster store is a
state on that row while every other row still answers — a 422 for the batch would
hide the answers for the files that were fine.

**The test that would have caught this in 2026-08-17.**
`tests/test_upload_store_control.py` joins the three sides: the action reads
`store:<filename>`, *some* screen renders an input with that name, and the API takes
the field. Text-level, the same technique `test_ui_vocabulary.py` uses and for the
same reason — **there is still no browser automation** (register E2), so nothing here
proves the screen works. It proves the field name is one string in three places,
which is the side that went missing.

## 2026-08-21 — D5: format drift becomes visible where it happens

Register D5, decided as [D67](06-DECISIONS.md#d67). The last item of the
A9/D12/D7/D5 plan. **No `src/` change and no contract change, so no golden could
move and the money gate was not run** — stated rather than run.

**What the drift log at the top of this file was really recording.** Eighteen events
in three months, every one absorbed by a developer: an export renamed a column, the
run hard-stopped ~200 seconds in with *"income data is missing required columns after
header mapping: ['net_revenue']. Update column_maps.income in settings.yaml"*, and
somebody who knew what a column map was edited YAML. The rules editor could always
express the fix. Nothing helped a person find it.

**The evidence half.** Migration `023` adds `uploads.kept_columns`: the headers the
contract recognised. With `pii_columns_dropped` — which held *every* dropped header
all along, despite its name — that is the file's original header row, and the
sanitized object in the bucket no longer contains the dropped ones, so the door is
the only chance to record it. Raw headers, not the fields they mapped to: the mapping
belongs to the contract, which is versioned and pinned per window, so a stored
mapping would freeze one reading of the file. The upload response and the window
screen report the unrecognised ones **with `KNOWN_PII` subtracted** — a list
containing `Recipient` and `Phone #` fires on every healthy file, and an operator
learns to ignore it.

**The step half.** The window screen maps an unknown header to a canonical field as
a config proposal, reusing the existing `proposeEdits`. It is not a new control:
approve/apply, `invalidates_goldens` and the verification canary all still stand
between that form and a settlement run.

**The decision that shaped it: the required-field check is per window-and-kind,
never per file.** `ingest.read_parts` concatenates a kind's parts and checks the
*result*, so a "part 2" export with fewer columns is legitimate — July produced nine
of them. A per-file check at the door would have refused healthy windows to catch a
fault the union does not have. So `uploads.missing_fields` takes the union and lives
on `/uploads/plan`, appending to `problems`, which makes `ready` false: a window a
run will refuse must not look ready. Both directions are pinned in
`test_a_part_file_with_fewer_columns_is_not_a_drift_report`, including that each part
*alone* would look broken.

**Three smaller things worth not re-deriving:**

- **`store` is excluded from the required set.** It is in `REQUIRED_COLUMNS` and
  comes from the *filename* ([D6](06-DECISIONS.md#d6)); `read_files` adds it before
  `read_parts` checks. Testing a file's headers for it would fail every export ever
  written, so `PIPELINE_SUPPLIED_FIELDS` names the exception instead of leaving it a
  subtraction somebody rediscovers.
- **`checked: false` means unmeasured, not clean.** Lazada's kinds have no entry in
  `REQUIRED_COLUMNS` (a fee-event ledger, read by `lazada.read_ledger`), and a file
  uploaded before migration `023` recorded no headers. Rendering either as "no drift
  found" is the shape of most of what this register has had to fix.
- **Canonical field names render untranslated** on a translated page.
  `unit_price_gross` is the identifier the rules editor's own dropdown shows;
  inventing a Vietnamese label would create a second name for one thing.

**No suggested mapping, decided with the user.** The screen shows the unknown headers
and the fields nothing supplies; the human pairs them. That is [D7](06-DECISIONS.md#d7)
applied one layer up — evidence decides, not similarity. Suggesting the pairing is
the first thing in this system that would call a model and it needs to read cell
**values**, which are PII-bearing; it is a future pass and its constraints are
written down in [10-ROADMAP](10-ROADMAP.md#where-ai-does-and-does-not-belong).

**Not browser-verified.** Register E2 stands: the drift panel was typechecked and
reasoned about. What is tested is the arithmetic (six unit cases), the door's
recording, and the plan's report end to end — including a real TikTok orders export
with the price header renamed, which produces `missing_fields: ["unit_price_gross"]`
and `ready: false`.
