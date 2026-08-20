# 07 — Verification

What is proven, how, and — importantly — what is not. Nothing below was verified by "looks right"; every claim names its comparison target.

Harnesses: `tools/calc_verify*.py` (row-level), `tools/devrun.py --refs` (totals against the team's own figures), `tests/goldens/` + `tools/make_golden.py` (workbook regression).

## Methodology — three escalating modes

1. **Row-level porting proof (May 2026).** For each platform, the pipeline's per-row calculated columns were compared against the team's own intermediary workbooks for the same window — every column, every row, exact match required. Stores were chosen to cover the cleanest *and* messiest cases: multi-SKU stress, returns at scale, only-zero-settlement stores.
2. **External tie (June 2026).** The pipeline ran on unseen June data; totals were tied against the team's **own month-end files** — an independent output produced by their manual process, not by this tool.
3. **Independent run (July 2026).** The pipeline ran *first*, before any team output existed. Its internal checks and coverage analyses produced findings that were then confirmed with the team.

Tolerances used are the team's own, read from their formulas — see [05-DOMAIN-RULES](05-DOMAIN-RULES.md#tie-out-checks-and-tolerances).

## May 2026 — row-level (~288,000 rows)

- **TikTok** — four stores × both windows row-verified: one small store, the messiest (73,689 + 62,177 rows), one with 6,487 Total Returns at scale, one averaging 4.9 SKU lines/order. ~236K rows, **all columns exact**; take-out and OK-good pivots tie exactly. The VAT question was resolved with evidence: all TikTok is 1.08, and the template's 1.05/1.10 cells are provably dead.
- **Shopee** — 2 stores × 3 windows (~51K rows) row-exact, including the only-zero-settlement store. Classification rules were derived from data patterns and then verified against the team's manual XLOOKUP list: **11/11, 40/40, 178/178 — zero missing, zero extra.**
- **Lazada** — two stores across both Weekly and Daily schema windows: 1,306 revenue lines plus every fee bucket exact. The Price KA formula reproduced to the đồng, including gift-line zero-outs and promo netting.
- **Full-platform runs** — every window of every platform tied per-store and grand against team references: TikTok 2/2, Shopee 5/5 (including sub-batches), Lazada 5/5. **Not a single store failed to tie.**

## June 2026 — external tie

- **Lazada** — all windows tie to the VND (one window off by 2 VND rounding).
- **TikTok** — window 2 grand total ties **exactly** (53,207,809,124; diff 0). Window 1's gap decomposes exactly into two named exclusions: a Vietnamese-header export set aside pending a header mapping (confirmed out of the team's total too), and one store that ties against its own separate file.
- **Shopee** — order-level reconciliation of **427,917 / 427,917 orders = 100.0000% to the VND** across all three windows, zero unexplained, after deriving the team's June "Net revenue" formula from their own file. The only exclusions are two named, team-confirmed items: 676 orders the team's own file missed (their missed download), and 258 orders outside our window in a long-spanning file.
  - **That formula is now in the code as a control** (2026-08-13). Their consolidated June file was supplied, its `Net revenue` cell read directly, and reproducing it from its six components matched their cached values on **all 82,714 rows at max deviation 0.000000 VND**. Rearranged into an order-file-vs-income-file statement it ties at **0.00 VND on 17.5B across 80,239 May orders** — so the June tie stopped being a one-off analysis and became a check that runs every window ([08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md#shopees-money-crossing--closed-2026-08-13)).
- The team's period convention was **derived** from their six month-tag files rather than guessed, and an explicit hold was kept until that derivation was evidence-backed.

## July 2026 — independent run (14 windows)

- All 14 windows generated; every TikTok window passes all three ported checks with variance 0.00.
- **Coverage proof:** on the settlement axis every July day is covered exactly once per platform; a new Shopee window split partitions perfectly (zero shared orders across 507,904).
- **The material catch:** one TikTok export was mis-pulled and also contained an entire prior settlement block — **18,352 orders / 5,973,070,353 VND (3.98% of TikTok July) would have been double-invoiced.** Found by cross-window order-ID analysis, root-caused to the pull (May baseline 220 overlaps = a normal multi-payout tail; the affected pair showed 24,851, of which 23,427 byte-identical), fixed as a config-declared settlement boundary with per-day drop logging, and verified back to **0 VND** cross-window duplication. Genuine second-payout rows were preserved (1,180 rows).
- The template-shaped export was itself verified against the team's real invoicing files: identical tab sets, grand totals tie exactly, brand-tab totals tie or land within a few VND (a different rounding path), the partial-return total equals their own cell exactly, and the pivot rounding drift matches theirs.

## Golden-file regression gate

Built as a cross-engine parity gate for a polars migration; **repurposed on 2026-08-12** as a workbook regression gate when that migration was descheduled ([D25](06-DECISIONS.md#d25), [D26](06-DECISIONS.md#d26)). It now closes a gap this project had documented as open — no regression coverage over the settlement-bounds logic or the template exporter.

**How it works.** A *golden* is generated per window: a canonical cell-by-cell record of the workbook (`cellset.jsonl`) plus the variance list. Only digests are committed, in `tests/goldens/manifest.json`. Any change to `src/` or `config/` that moves a cell fails the gate; moving the baseline requires `make_golden.py --rebaseline --reason "..."`, so the re-baseline is a reviewable commit rather than a side effect.

**Compared exactly:** sheet set and order (the monthly master reads Lazada's 12 tabs positionally), dimensions, cell *type* (`"1000"` vs `1000` is a bug that number formats hide), all strings including the Vietnamese verdicts, `None` vs `0.0`, and number formats — accounting vs plain changes how a negative reads to finance and no value diff catches it.

**Zero tolerance.** Same engine means the same reduction order, so bit-exact is achievable ([D17](06-DECISIONS.md#d17)). If a re-run diffs at all, something is genuinely non-deterministic and that is a finding, not a reason to widen an epsilon.

**Never compares `.xlsx` bytes** — openpyxl stamps timestamps into `docProps/core.xml` and zip entry order varies, so identical data yields different files ([D16](06-DECISIONS.md#d16)).

**The differ is self-tested before any golden is trusted.** The self-test proves it catches a 1-VND change, a number-stored-as-text flip, a blank-where-zero-belongs, a sheet reorder, a missing/extra sheet, an extra row, a number-format change and a verdict-string change — and that two byte-different but content-identical files compare equal. Same philosophy as `test_tieout_blindness.py`: a check nobody has tried to fool is not a check.

### Current gate status

Broadened 2026-08-13 from 3 windows to **8**, and from one store per platform to two on each platform's primary window:

| Window | Coverage | Workbook | Notes |
|---|---|---|---|
| `2026-05_l1` Lazada | **2 stores**, 1,965 ledger rows | 12 tabs, 2,193 cells | settled 05-01..03 |
| `2026-05_l2` Lazada | 1 store, 859 ledger rows | 12 tabs, 1,767 cells | settled 05-04..10 |
| `2026-05_l3` Lazada | 1 store, 287 ledger rows | 12 tabs, 979 cells | settled 05-11..17 |
| `2026-05_l4` Lazada | 1 store, 266 ledger rows | 12 tabs, 855 cells | settled 05-18..24 |
| `2026-05_s1` Shopee | **2 stores**, 242,172 + 119,382 rows | 12 tabs, 955,342 cells | settled 05-01..10, `partial_roster: true` |
| `2026-05_s2` Shopee | 1 store, 76,375 + 39,691 rows | 12 tabs, 331,721 cells | settled 05-11..20, exercises the partial-return split |
| `2026-05_s3` Shopee | 1 store, 94,761 + 44,890 rows | 12 tabs, 373,211 cells | settled 05-21..31, exercises the partial-return split |
| `2026-05_w1` TikTok | **2 stores**, 126,358 + 65,551 rows | 6 tabs, 743,970 cells | settled 05-01..17, `partial_roster: true` |

Three things the broadening bought beyond raw volume:

- **The per-store conservation check is no longer trivially satisfiable.** With one store it could only restate the grand total; on two TikTok stores it ties at 0.00 VND against a 14,096,939,407 VND reference.
- **The Shopee "Return 1 phần" split is now covered** — `_s2` and `_s3` carry 12 and 8 partial-return orders that must be invoiced, against the tightest tolerance in the system (10 VND). The old single-window gate never exercised it.
- **Four consecutive Lazada weeks** instead of one, so the weekly window boundary is regression-covered.

**Read `partial_roster: false` on the Lazada rows as "not checked".** Lazada has no configured roster, so `check_stores` never runs for it ([defect 1.3](08-KNOWN-DEFECTS.md#13-the-store-roster-is-checked-for-tiktok-income-only--fixed-m2-2026-08-13)); the flag records that no relaxation was requested, not that the roster was complete.

Proven properties:

- Regeneration is **bit-stable** — digests unchanged across re-runs.
- A mutated input **moves** the manifest: dropping 183 of 1,826 ledger rows changed both the workbook manifest and the stage row counts.
- The gate **refuses to move itself.** `make_golden.py` will not overwrite a differing baseline without `--rebaseline --reason`; `tests/goldens/test_rebaseline_guard.py` proves the refusal fires on every digest field and leaves the old baseline untouched.
- It caught nothing during the M1 seam refactor, which is the point — all three manifests came out byte-identical across a restructure of the entire call graph, and again across the deletion of the placeholder path.

### The M4 service gate

M4 put a queue, a worker, a substituted logger and an artifact store between the operator and the pipeline. The question that matters is whether any of that moved a cell, and it is answered the same way the rest of this page is — against the committed goldens, which were generated through the developer CLI (`tools/full_run.py` at the time, `tools/devrun.py` since M6 — the same code, renamed when users moved to the browser).

`tests/service/test_worker_matches_the_cli.py` enqueues `2026-05_l1` Lazada, lets a real worker execute it, reads the workbook back out of the artifact store, and compares its cellset manifest against the committed digest **sheet by sheet, then whole**. It passes: all **12 tabs, 2,193 cells** identical, at zero tolerance, and the run's findings match the committed `variance_count`. So the service is a different way to invoke the same computation rather than a second implementation of it.

Why that test is cheap enough to keep in the default suite: Lazada `_l1` runs in ~0.5s. A gate on a 171-second Shopee window would get marked `slow` and then deselected, which is how a gate stops being one.

The queue's own claims are verified against a **real PostgreSQL 17.10**, not a substitute — `FOR UPDATE SKIP LOCKED` is a statement about what two transactions do to each other, and lease expiry is a statement about `now()`. Eight threads racing on five jobs claim each exactly once. The tests create and drop their own database and skip (loudly) when `RECON_TEST_DATABASE_URL` is unset.

**What the M4 gate does not cover:** the container images, which have never been built ([defect 2.2](08-KNOWN-DEFECTS.md#22-deploydockerfile-and-deploydocker-composeyml-have-never-been-built--open)); anything about authentication, because there is none ([2.1](08-KNOWN-DEFECTS.md#21-the-api-is-unauthenticated--open-m5)); and behaviour under a genuinely concurrent *pipeline* load — the worker runs one job at a time by design and multi-process concurrency is proven at the queue, not at the pipeline.

### The M5 gate

M5 added authentication, an upload boundary, config versioning, a config editor that **writes**, and a web app. Three of those touch the money path, and each is gated rather than argued:

**The upload sanitizer does not move a cell.** Stripping PII at the boundary means rewriting an export *before* the verified pipeline reads it — a transformation inserted into the path that produced every verified number. `tests/service/test_uploads.py::test_a_sanitized_window_produces_the_committed_golden` sanitizes both of `2026-05_l1`'s real exports, runs the pipeline over the sanitized copies, and matches the committed digest across all 12 tabs at zero tolerance. It also asserts that columns were actually dropped, so a no-op sanitizer cannot pass it.

**The config editor preserves the audit trail.** `ruamel.yaml`'s round trip is **byte-identical** on the real 300-line `settings.yaml` — comments, Vietnamese quoted keys, blank lines and key order — so a one-value edit produces a one-line diff. Pinned by a test against the real file, because a canary against a fixture would prove nothing about the contract that matters.

**The two `src/` additions are output-identical.** `config.parse_settings` and `build_context(settings_text=...)` exist so a run can use a *pinned* config. All eight golden windows regenerate with **zero refusals**.

**The containers were built and run.** `db` + `api` + `worker` + `web` came up; the api answered `/healthz` on the private network, `/board` without a token returned **401**, a token minted through `service.admin` authenticated as `recon.admin`, and the containerised worker claimed a job and executed it. Defect 2.2 is closed.

**What the M5 gate does not cover:** the web UI has never been opened in a browser ([defect 2.8](08-KNOWN-DEFECTS.md#28-the-web-app-has-never-been-opened-in-a-browser--open)) — it type-checks under `strict` and its container serves, and that is all. Nor does anything here verify Entra ID SSO, which is not built.

Suite baseline: **`435 passed, 3 skipped, 0 xfailed`** with Postgres available (`167` before M4, `319` after it; the 240 service tests skip without `RECON_TEST_DATABASE_URL`). The xfail count is the number worth watching — it was 12 through M0/M1 and is now zero, because every pinned control gap has been closed ([08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md)). An empty drift detector detects nothing, so a newly discovered gap needs a newly pinned test. The second venv and the "run in both runtimes" rule are retired ([D21](06-DECISIONS.md#d21)).

**What this gate is and is not.** It proves the output has not *changed*. It says nothing about whether the output is *right* — that is what the three verification modes above are for, and the gate's job is to stop a refactor silently undoing them.

## What verification does NOT cover — honest limits

- **Only three months of data.** Rules that are month-shape-dependent (window labelling, file naming) have needed maintenance **every single month so far** — see [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md).
- **Non-1.08 VAT paths remain live-unexercised**, and the reason is now measured rather than assumed. The master's 660 VAT SKUs match **0** of the SKUs at TikTok's two stores, Shopee's two, and Lazada's Unilever 2 — but **41–50%** at Lazada's KAO, so the lookup itself works and the gap is store coverage, not mechanism. Every SKU it has matched so far is itself 1.08, so no override has changed a number yet ([defect 1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13)).
- **July has not been externally tied** against team month-end files — none existed at run time. That comparison is still open.
- **Goldens cover 8 windows and at most two stores each** — not a full roster. TikTok is 2 of 25 configured stores and Shopee 2 of 17, so both still run `--partial-roster`. Broadened from 3 windows / 1 store on 2026-08-13; going further needs raw exports for the remaining stores, which is a data question rather than a tooling one.
- **Faithful ≠ correct.** Row-verification against the team's workbooks proves faithful *reproduction of their process*. It inherits any systematic error they make — and their workbooks were found to contain broken checks. There is **no external ground truth** in the system today: everything ties to the team's own files. Tying to bank settlements or platform statements of account remains unbuilt and is arguably the highest-value verification work outstanding.
- **Production booking is not authorised.** That blessing is a human decision and has not been formally given. (The `PLACEHOLDER_FORMULAS` flag that used to advertise this was removed in M1 along with the placeholder math — see [D10](06-DECISIONS.md#d10). The gate it named still stands; only the misleading signal is gone.)

## Suggested falsification exercises

For anyone auditing this rather than reading about it:

1. Pick 20 random orders from a raw export; hand-compute their invoice lines from [05-DOMAIN-RULES](05-DOMAIN-RULES.md); compare to the generated file.
2. Deliberately mis-stage one file into the wrong window and observe whether any guard catches it.
3. Edit one amount by 1,000,000 VND and verify which check catches it, and how it is reported. (Read [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md) first, then predict the answer — it is instructive.)
4. Re-run the same window twice and confirm the content is identical (note: **not** the bytes — see [D16](06-DECISIONS.md#d16)).
5. Diff the team's original invoicing file against the pipeline's re-issue of the same window, cell region by cell region, and satisfy yourself that every difference is one of the documented deliberate ones.

## The M6 gate — the sanitizer and the rename, on all three platforms

M5's strongest test sanitized a real window and demanded the resulting workbook match the committed golden cell for cell. It covered **Lazada only**, and that is precisely why nobody noticed that the sanitizer flattened Shopee income's two band rows and TikTok orders' junk row: it wrote the header on row 1 with pandas' defaults, which is correct for Lazada and wrong for the other two.

M6 widened it twice over — to all three platforms, and to the **rename** as well as the strip. That combination is what makes `service/naming.py` safe to trust, because store identity is derived from the filename ([D6](06-DECISIONS.md#d6)) and a rename that got it wrong would silently reassign a storefront's revenue.

`tests/service/test_uploads.py::test_a_sanitized_renamed_window_produces_the_committed_golden`, parametrised over three windows:

| Window | Platform | Files | Renamed | Columns dropped | Result |
|---|---|---|---|---|---|
| `2026-05_l1` | lazada | 2 | 2 | 28 | **cell-for-cell identical** |
| `2026-05_w1` | tiktok | 5 | 5 | 237 | **cell-for-cell identical** |
| `2026-05_s1` | shopee | 39 | 39 | 2,099 | **cell-for-cell identical** |

46 files, every one renamed *and* stripped, 2,364 columns removed, and not one cell moved — at zero tolerance, against digests generated through the developer CLI. Two independent claims are proved together: that the strip drops only columns and preserves the shape the config describes, and that the rename is a fixed point of the pipeline's own store parser.

The gate skips silently without local client data, which is correct and is also how a regression could go unnoticed on the machine that *has* the data. `RECON_REQUIRE_CLIENT_DATA=1` makes a missing window a failure there.

### The naming scheme, measured before it was written

Over the whole real `input/` tree: `derive(uniform(derive(x))) == derive(x)` for **73/73** real exports across all eight committed golden windows, `sorted(new)` preserved `sorted(old)` in **12/12** folders, and every filename was already NFC.

The remaining 10 files in `input/` are **refused, correctly**: they are the legacy synthetic window `2026-06_p1`, whose parts are named `part_1.csv` and carry a `Shop Name` *column*. `read_parts` only consults the filename when `store` is absent from the frame, so that window exercises a path production never takes — which became a requirement on the new generator rather than a gap in the namer.

## The M6 gate — the stack, brought up and exercised

The M4 compose file carried a header saying it had never been brought up; `CLAUDE.md` claimed the whole stack was built and verified. Both were partly wrong. On **2026-08-17**, Docker **29.7.2**:

* `db`, `minio`, `api`, `worker`, `web` all healthy; `minio-init` created both buckets, applied the 30-day lifecycle rule to `recon-uploads`, enabled versioning on `recon-artifacts`, and attached a **scoped** service account so neither app holds root credentials.
* `GET /healthz` reported `migrations: 4`.
* Bootstrap admin created through `python -m service.admin user create` — the first identity cannot come from the api, because creating one needs an admin credential.
* Sign-in returned `must_change_password: true`; `/board` answered **403** with `code: password_change_required`; rotating the password made it **200**.
* An export uploaded through `POST /uploads` was stripped, its store resolved at the door by the pipeline's own regex, and stored under a content-addressed key. **1 object** in `recon-uploads`; nothing left on disk.
* `GET /uploads/plan` showed the uniform name the run would use (`1_SmokeStore.xlsx` → `001_SmokeStore.xlsx`).
* The worker materialised the window from the bucket — no input mount — ran it, and produced artifacts addressed `s3://…`.
* `GET /runs/1/artifacts/finance_file.xlsx` returned **200, 14,924 bytes**, matching the recorded artifact size, and the downloaded file opened as a valid **12-tab** workbook. That is [defect 2.4](08-KNOWN-DEFECTS.md) closed by demonstration rather than by assertion.
* The run log named the uniform filename and said which mode the input came from; the upload was attributed to the run that read it.

**What this does not cover:** nobody clicked a button. The new screens — login, accounts, the window/upload page, the sectioned config editor — are covered by tests and by the API pass above, but there is still no browser automation, so claims about rendering are claims about code that compiles ([defect 2.8](08-KNOWN-DEFECTS.md)).

## The M6 gate — the demo window

`service/sampledata.py` is verified by determinism, not by inspection: two generations of all 10 files compared by **cellset digest** — never file bytes, since openpyxl stamps timestamps ([D16](06-DECISIONS.md#d16)) — are identical.

Measured outcome when run through the verified pipeline under its own pinned config: Lazada **VARIANCE** with 2 unmapped fees, TikTok **UNVERIFIED** with 4 unmatched ghost orders, Shopee **UNVERIFIED** with the revenue crossing tying exactly. An empty exception queue teaches nothing, and this one is not empty.

The generator's first run produced a **real** tie-out breach — off by exactly the two Shopee subsidies, because income's product price is net of both while the crossing adds only the Shopee-funded part back. Distinguishing that from a manufactured breach required reading the check's source, which is why the demo ships no deliberate breach.

## The M6 audit — NFC

`service/nfc_audit.py`, read-only and reporting counts only:

| Source | Non-NFC |
|---|---|
| `settings.yaml` store names (roster, optional, aliases) | 0 |
| `lazada_fee_types.csv` `fee_name` | 0 of 118 |
| live `Lib & VAT rate.xlsb` `fee_name` | 0 of 118 |
| stored `run_exceptions` identity values | 0 rows |

So normalising `exceptions._norm` moved **no** existing fingerprint, and `006_exception_nfc.sql` is a recorded no-op that says so. The M6 plan's "0 impact" claim had been measured over *filenames*, which said nothing about `fee_name` — the value that actually comes from Vietnamese Lazada exports. It was audited separately and came back clean.

## The M8 Phase 3 gate — dates, and the July month-end tie (2026-08-19)

### The date formats were measured, not assumed

`date_formats.<platform>.<kind>` ([D54](06-DECISIONS.md#d54)) replaced format
inference. Every value in it was probed against real exports first, and the bar
was 100% of non-blank cells in every date column of that platform/kind, in **both**
month-sets:

| platform / kind | format | files probed (May + July) |
|---|---|---|
| tiktok / orders | `%d/%m/%Y %H:%M:%S` | 13 |
| tiktok / income | `%Y/%m/%d` | 12 |
| shopee / orders | `%Y-%m-%d %H:%M` | 20 |
| shopee / income | `%Y-%m-%d` | 17 |
| lazada / weekly | `%d-%b-%Y` | 66 |
| lazada / daily | `%d %b %Y` | 14 |

Two findings came out of the measurement rather than out of reasoning. TikTok
income is `%Y/%m/%d` against `dayfirst.tiktok: true`, and on **July** data that
inverts day and month: a 1–7 July window derived as `2026-01-07..2026-09-07`. And
Lazada's two variants do not agree with each other — the separator differs — which
nothing had ever noticed because inference handled both.

**The gate: all eight golden windows were regenerated with the change in place and
no cell moved** (`tools/make_golden.py`, zero tolerance, `exit=0` on every window).
Explicit parsing is stricter than inference, so this was the only thing that made
the change safe to land.

*Invocation note, because it cost a cycle:* the goldens carry `partial_roster` in
their provenance — `True` for the Shopee and TikTok windows (they are two-store
subsets), `False` for the four Lazada ones. Regenerating with the wrong flag
refuses with `fields that changed: partial_roster`, which is the gate working, not
a moved cell.

### July, tied against the team's month-end master

The blocker recorded above — "July has not been externally tied; none existed at
run time" — is closed for **Lazada** and open for the rest at the time of writing.

The team's `ADA marketplace MASTER July 2026.xlsx` is encrypted by a Microsoft
Purview sensitivity label and cannot be opened here ([16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md)).
The comparison is made against a **per-tab CSV export** of it, supplied 2026-08-19,
via `tools/compare_master.py`.

**Lazada: exact.** All five windows reproduce the reference to the dong:

| window | ours (with VAT) | reference | diff |
|---|---|---|---|
| l1 | 542,192,057 | 542,192,057 | 0 |
| l2 | 2,983,994,880 | 2,983,994,880 | 0 |
| l3 | 2,322,957,905 | 2,322,957,905 | 0 |
| l4 | 1,798,489,504 | 1,798,489,504 | 0 |
| l5 | 1,528,688,537 | 1,528,688,537 | 0 |

Per storefront, 17 of 18 match exactly and one differs by **1 VND**
(`unilever ahc`), which is display rounding in the reference export.

**Two limits on this comparison, stated rather than buried.**

- **The reference CSVs are cp1252 and lost their Vietnamese.**
  `Unilever Chăm Sóc Vẻ Đẹp` exported as `Unilever Ch?m S�c V? ??p`. Those
  characters were destroyed when the file was written, not when it is read.
  `compare_master._skeleton` matches a mangled label to our intact one by reducing
  every non-ASCII character to `?` on both sides. It deliberately does **not**
  strip diacritics, because `thuan phat` and `thuận phát` are two different rows
  in the team's own file and folding them would invent agreement.
- **Window columns are matched by ORDER, not by name.** The team heads columns
  with day ranges (`01-07`) and we use window ids (`w1`); the only mapping between
  them is the hardcoded table this phase deleted. Both sides list a platform's
  windows in settlement order, so the nth is compared with the nth — and the tool
  refuses to compare a platform at all if the two sides disagree on how many
  windows it has.

## The cross-window order gate (2026-08-19)

`cross_window_order_backfill: report` shipped as the default, so every run now measures
whether an order it settles had its SKU lines exported with an *earlier* window
([defect 2.12](08-KNOWN-DEFECTS.md), [D59](06-DECISIONS.md#d59)). The claim that needed
proving is that measuring changes nothing.

**The gate: all eight golden windows re-run under `report` at zero tolerance — no cell
moved, and `tests/goldens/manifest.json` is byte-unchanged.** Run as
`pytest tests/service/test_config_render.py::test_config_render_produces_the_committed_goldens`,
which renders the contract from the config tables and regenerates every window, so it
also proves the new key survives the table round trip.

That result is not luck; report mode is manifest-neutral **by construction**, and the
two reasons are worth stating because a future change could break either:

* An INFO row is not a variance. `RunResult.consume_tieout` promotes only a `BREACH`
  into `findings`, so `variances.json` — and therefore `variances_digest` — cannot see
  the new row. Adding a *failable* check here would move that digest.
* `stage_row_counts` comes from `tools/make_golden.STAGE_TARGETS`, an explicit list of
  fingerprinted functions. Borrowing reads through `ingest.read_files`, which is **not**
  on that list, while `read_parts` — which is — was left with its name, signature and
  behaviour unchanged. Had borrowing called `read_parts`, every window with a
  predecessor would have gained a fingerprint entry and the digest would have moved.

**What the measurement found on the golden windows, which is why they could not move:**

| Window | Predecessors | What report mode did |
|---|---|---|
| `2026-05_w1` / tiktok | none (first of its month) | nothing to compare against |
| `2026-05_s1` / shopee | none | nothing to compare against |
| `2026-05_s2`, `_s3` / shopee | s1; s2+s1 | *"every settled order has lines in this window"* — Shopee's own-window order coverage is 100%, so **no predecessor file was opened at all** |
| `2026-05_l1..l4` / lazada | n/a | Lazada is a fee-event ledger with no order files; the step is not wired into `_run_lazada` |

So the common path costs no extra I/O, which matters because order exports are the
largest inputs in the tree.

**Not verified, and named as such:** `apply` mode has never been run against real data.
Its expected effect is measured (`w2` +942,869,056 VND from `w1`; `s4` +1,390,095,674
from `s3`; `s2` +173,429) but measured by `tools/measure_order_coverage.py`, which is a
*different* implementation of the same idea from the one that would execute. Agreement
between the two is a check worth making at the flip, not an assumption to carry into it.

**That check was made on 2026-08-20, and the two did not agree.** The tool reads through
`ingest.read_parts`; the pipeline's borrow read through `read_files` only, so it never
applied `store_aliases` — and `needed` holds canonical store names while a filename holds
what the platform exported. `settings.yaml` maps `"Pediasure" -> "Abbott Pediasure"`
because the order files drop the "Abbott", so `w1`'s Abbott files were skipped before
anything read them: `2026-07_w2` reported **~1,788,000 VND** where the tool said
942,869,056. The fix routes borrowed frames through the extracted `ingest.normalize_parts`
(and canonicalises the filename prefilter), after which:

| Window | Pipeline, report mode | `measure_order_coverage.py` | Agrees |
|---|---|---|---|
| `2026-07_w2` / tiktok | 942,869,056 VND, 825 orders from `w1` | 942,869,056 | yes |
| `2026-07_s2` / shopee | 173,429 VND, 6 orders from `s1` | 173,429 | yes |
| `2026-07_s4` / shopee | **not measurable this way** | 1,390,095,674 | — |

`s4` cannot be confirmed by a CLI run: it hard-stops on the roster check
(`check_stores`, which runs *before* the cross-window stage) because
`xa_kho_gia_tot` and `Reckitt Sức Khỏe Sắc Đẹp` have order files in `s3` and none in
`s4`. That is a pre-existing window-level roster gap, independent of this change — and
itself an instance of 2.12's shape that the roster happens to catch loudly, where 2.12's
own cases pass the roster and leak silently. Its figure remains the tool's.

So the agreement is now evidence on two of the three windows, and the third is blocked on
roster maintenance rather than on this mechanism. `apply` is still unrun.
