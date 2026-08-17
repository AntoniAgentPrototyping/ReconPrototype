# 08 — Known Defects

Verified, reproducible, with locations. **Do not re-discover these.** Two sections: defects in this pipeline, and defects found in the team's own source files.

Status key: `OPEN` · `PINNED` (an `xfail(strict)` test asserts the broken behaviour) · `SCHEDULED` (a milestone owns it).

**As of 2026-08-13 there are no `PINNED` entries left.** The eleven strict xfails that had pinned 1.1–1.6 since M0 are all gone — closed in M2 (1.1, 1.2, 1.3) and M2.5 (1.4, 1.5, 1.6). What remains open below is either a smaller item in 1.10, the date half of 1.6, or a residual named inside a fixed entry. The suite baseline is now `91 passed, 3 skipped` with **no xfails**, which means the drift detector is empty rather than quiet: a new gap needs a new pinned test.

---

## Part 1 — Defects in this pipeline

### 1.1 The tie-out checks cannot fail — **FIXED (M2, 2026-08-13)**

The most important entry on this page, and the reason the project's control audit happened at all. Kept in full because the *shape* of the failure is the reusable lesson.

`src/tieout.py:84-93` computes **both sides of all three TikTok checks from the same frame**, so each reduces to an identity:

| Check | Compares | Reduces to |
|---|---|---|
| PV sum | pre-VAT per store vs per VAT bucket | `sum(x)` vs `sum(x)` |
| PV xuat HD | pre-VAT line total vs SKU-pivot total | `sum(x)` vs `sum(x)` |
| Xuat HD bt | with-VAT lines vs VAT-bucket recombination | `sum(p·f)` vs `sum(p·f)` |

A real production run reports:

```
Check PV sum:      PASS (expected 11,891,178,238.89, actual 11,891,178,238.89, variance 0.00)
Check Xuat HD bt:  PASS (expected 12,842,472,498.00, actual 12,842,472,498.00, variance 0.00)
Check PV xuat HD:  PASS (expected 11,891,178,238.89, actual 11,891,178,238.89, variance 0.00)
```

Variance **exactly** 0.00 across 55,494 SKU lines and 11.9B VND — not "within tolerance", identical to the cent, because both sides are the same number.

A falsification harness confirms six revenue-loss mutations pass undetected — dropping 30%, 50%, 90% of rows, deleting an entire store, halving every amount, and **zeroing all revenue**. Only one corruption is caught: deliberately breaking the relationship between two derived columns, which production code cannot produce (`calculate.py:142` defines one as the product of the other).

**Why this happened, and why it is not a coding error.** In the manual process these checks are load-bearing: between each pivot and each worksheet there is a human copy-paste, and the check catches a row dropped in that step. The formulas were ported faithfully. What could not be ported is the *manual step they were watching*. The control's value lived in the process, not the arithmetic.

Three compounding problems:

- **The result is discarded.** `src/pipeline.py` calls `run_checks_tiktok(...)` and throws the return value away — the checks cannot affect the exit code even in principle. Preserved deliberately through the M1 refactor and marked `PARITY:` at the call site.
- **Shopee and Lazada never call `tieout` at all.**
- **The only real verification is `--refs`, and it is optional.** With it absent, every store emits `"no team reference found"` through the *same channel* as a genuine numeric variance, which trains operators to ignore the list.

**Fixed in M2.** `src/tieout.py` was rebuilt around a `SourceReference` captured from the **income** export before the money math runs, while the checks run against the SKU frame rebuilt from the **order** export — two different files, so agreement is evidence rather than arithmetic. Order coverage, store coverage and per-order/per-store settlement conservation now run; the result is consumed (`RunResult.consume_tieout`), all three platforms call it, and breaches become variances that set the exit code.

Evidence the fix is real: all six revenue-loss mutations now BREACH. They had been `xfail(strict)` since M0 and were removed only after XPASS, in a separate commit from the fix ([D22](06-DECISIONS.md#d22)). On real data the TikTok conservation check ties to **0.00 VND variance on 12,842,472,498** with a 1 VND tolerance.

Two things the rebuild surfaced:

- **21% of GOOD settlement had no matching order lines.** 11,765 of 55,894 TikTok orders, 3,453,805,299 VND, dropped by the inner join in `explode_to_sku_tiktok` and never reported. Believed correct — the team's own VLOOKUP behaves the same and June tied exactly — but a control silent about a fifth of settlement is not doing its job. It is now a named `INFO` reconciling item logged every run.
- ~~**Shopee has no verified money crossing.**~~ **Closed 2026-08-13** — see below. Applying TikTok's relation there had breached on correct data: `amount_with_vat` carries a proportional discount allocation, and the closest income column deviated on 14,104 of 36,162 orders. Rather than invent a tolerance wide enough to pass, Shopee ran coverage checks only and the gap was logged every run ([D1](06-DECISIONS.md#d1)). Holding that line is what made the eventual fix evidence-based rather than a fitted tolerance.

#### Shopee's money crossing — closed 2026-08-13

What closed it was the missing **artifact**, not more engineering: the team's June consolidated file, whose `Net revenue` column (sheet "Doanh Thu", column J) reads

```
Net revenue = Giá sản phẩm + Sản phẩm được trợ giá từ Shopee
            + Mã ưu đãi do Người Bán chịu + Mã hoàn xu do Người Bán chịu
            + Mã ưu đãi Đồng Tài Trợ do Người Bán chịu
            + Mã hoàn xu Đồng Tài Trợ do Người Bán chịu
```

Reproducing it from its components matched their cached values on **all 82,714 rows, max deviation 0.000000 VND**. The last four terms are the seller-borne discount pool; moving them to the other side leaves a pure order-file-vs-income-file statement, which is what `tieout.RevenueCrossing` asserts:

```
SUM(amount_with_vat − discount_allocated)  ==  gross_revenue + shopee_product_subsidy
└─────── rebuilt from the ORDER export ──┘      └──── read from the INCOME export ───┘
```

**Measured before being asserted**, on four independent windows: **0** deviating orders of 48,535 / 14,831 / 16,873 (May `_s1`/`_s2`/`_s3`) and 1 of 81,232 (their June file). Hence a 1 VND tolerance. On real data all three May windows now tie at **0.00 VND on 17.5B VND across 80,239 orders**.

Two classes are **held out and named**, not tolerated:

- **Refund orders** — the order export keeps the full ordered quantity while income is reduced for returned units, so they cannot tie. Every non-tying order in all four windows was one, and the deviation was always positive. Reported as a reconciling item with its count and amount (603 / 158 / 207 orders in May).
- **Zero-quantity SKU lines** — the pre-VAT unit price divides by quantity, so such a line cannot carry its own revenue. None occur in any window measured, but `calculate.py` explicitly guards against qty 0, so the case is real.

The rebuild is deliberately **row-additive** rather than reading the pre-computed `order_gross_sale` column. Both forms are algebraically identical and measured identical to 0.0000 VND, but only the additive one moves when a SKU line is dropped from an order that still has siblings — which is a mutation in the harness.

*The falsification harness found a hole in this check before it shipped.* The first version inner-joined the two sides, so **deleting an entire store went undetected** — its orders simply left the comparison and everything remaining still tied. The reference now defines the population and an absent order counts as a shortfall of its whole value. Seven revenue-loss mutations must BREACH, including the sibling-line drop and the store deletion.

### 1.2 Vietnamese headers arrive in Unicode NFD — **FIXED (M2, 2026-08-13)**

`config/settings.yaml` keys are NFC (precomposed); Shopee **order** exports deliver NFD (decomposed). Visually identical, byte-unequal:

```
config key : 'Được Shopee trợ giá'   U+1EE3 (ợ) … U+00E1 (á)      precomposed
export col : 'Được Shopee trợ giá'   U+01A1 U+0323 … U+0061 U+0301  decomposed
raw equal: False        equal after NFC: True
```

`src/ingest.py:158` only `.strip()`s column names — no normalization — so `shopee_subsidy` silently fails to map. **9 of 63 headers** in a real Shopee order file are non-NFC; only one is currently mapped, but any future mapping of the other eight fails the same silent way. Shopee income and both TikTok files are fully NFC.

Runtime effect, confirmed across all 28 order parts:

```
Order Masan part 1.xlsx: 6123 rows (headers not found: ['Được Shopee trợ giá'])
...
WARNING: orders have no 'shopee_subsidy' column (export version drift) — treating as 0
```

The pipeline warns, then treats the column as 0. The warning also misdiagnoses the cause as version drift; the column is present.

**Correction — this did NOT overstate revenue.** Earlier revisions of this page said it did. Tracing the consumers before fixing it showed `shopee_subsidy` is aggregated (`calculate.py:151`) and then **referenced by no money formula**: the Shopee chain uses `seller_subsidy` (which maps fine, being NFC), the four voucher columns, and the proportional allocation. The mapping failure was real; its monetary impact was nil.

The risk was prospective rather than actual: 9 of 63 headers in a real Shopee order file are non-NFC, and any future mapping of the other eight would have failed the same silent way.

**Fixed in M2** — `ingest.py` NFC-normalizes headers before matching, the treatment `norm_store` had always applied to store names. The delta was stated in advance and confirmed exactly: **workbook digests unchanged, `fingerprint_digest` moved** (the column's sum went from 0 to a real value), nothing else.

### 1.3 The store roster is checked for TikTok income only — **FIXED (M2, 2026-08-13)**

`ingest.check_stores` is called in exactly one place (`src/pipeline.py`, the TikTok runner; the Shopee runner carries a `PARITY:` note where the call is absent). Shopee has a **17-store roster configured that is never checked**, and Lazada has none. Confirmed in practice: a Shopee run with 1 of 17 expected stores completed without complaint.

A configured control that never executes is the same category of problem as 1.1.

**Fixed in M2**: `check_stores` now runs for Shopee income. It fired immediately — the single-store Shopee golden window hard-stopped with 16 of 17 stores missing, which is the control working. That window now has to declare `--partial-roster`, and its manifest coverage flag flipped `false → true`: it had always been a partial roster, and nothing had been armed to say so ([D23](06-DECISIONS.md#d23)).

Lazada still has no roster configured — there is nothing to check against, which is a config gap rather than a code one.

### 1.4 Unmapped SKU silently receives the default VAT factor — **FIXED (M2.5, 2026-08-13)**

*Was:* `src/masters.py` used `.map(vat_sku).fillna(default)`, which cannot distinguish "this SKU is standard-rated" from "this SKU is absent from the master". The second is a **wrong-tax** path with no warning — and it is precisely the branch verification never exercised, since non-1.08 SKUs barely traded.

**Master coverage is store-dependent, and mostly zero.** Measured across every window now staged:

| Store (window) | rows | matched a master SKU |
|---|---|---|
| Unilever Homecare + Mars (`_w1` TikTok) | 63,612 | **0** |
| Masan + Xmenforboss (`_s1` Shopee) | 70,203 | **0** |
| Unilever 2 (`_l1` Lazada) | 1,826 | **0** |
| **KAO** (`_l2`/`_l3`/`_l4` Lazada) | 859 / 287 / 266 | **355 / 145 / 120** (41–50%) |

*Correction to an earlier reading of this page.* On the first three stores the coverage is zero, and that was initially written up as "the mechanism is inert" — a claim about the mechanism. Staging KAO disproved it: the lookup fires normally there, matching 41–50% of ledger rows. The master is **populated for a different store set**, not broken and not namespaced differently. What is true is narrower and still worth acting on: for the largest stores in this data, *every* line invoices at the 1.08 default by fall-through, so a non-default-rate SKU trading at one of them would be taxed wrongly. Every matched SKU seen so far is itself 1.08, so **the non-1.08 path remains live-unexercised** even now that the lookup demonstrably works.

Who maintains the master and which stores it is meant to cover is [open question 9](11-OPEN-QUESTIONS.md).

**Fixed** by splitting the decision in two: `masters.vat_factor_for` returns **NaN** where the master says nothing, and `masters.resolve_vat_factors` applies the team's default fall-through — the row-verified behaviour, unchanged ([D1](06-DECISIONS.md#d1)) — while counting it and logging coverage every run, with a warning when coverage is zero. All three platforms use it. **No number moved**: all three golden manifests were regenerated byte-identical.

### 1.5 Joins key on `order_id` alone — **FIXED (M2.5, 2026-08-13)**

*Was:* `src/stitch.py` and both `explode_to_sku_*` in `src/calculate.py` merged on `order_id` while the left side is keyed by (store, brand, order_id). Two effects if an ID ever collides across stores:

- `calculate.py` explodes many-to-many, **inflating revenue**
- `stitch.py` takes `min(order_created_at)` across stores, so one store's income can be dated by another store's order — deciding the period revenue lands in

Per-store input folders hid this; multi-tenant API pulls would remove that accident. `src/lazada.py:208` already did it correctly, so the right pattern was already in the codebase.

**Fixed** — the stitch merge, both SKU explodes, and the four per-order `transform` aggregations inside `compute_sku_columns_*` all key on `(store, order_id)`. The per-order transforms are ports of the team's `SUMIF`, which keys on order id alone *within one store's sheet*; the composite is the faithful equivalent in a frame that holds every store.

**Measured zero-delta, not assumed:** across both May windows, 0 order_ids appear in more than one store, both merges return identical row counts either way (58,131 TikTok / 125,595 Shopee), and no `(store, order_id)` group's minimum creation date differs from its global minimum. All three golden manifests came out unchanged.

*Residual, deliberately not changed:* `tieout.py` still groups per order and keys `SourceReference.order_ids` on `order_id` alone. Under a collision its coverage check could count a store's order as present because another store has that ID. Closing it means keying the reference composite too, which is a change to a control and wants its own measured pass.

### 1.6 Silent numeric and date coercion — **FIXED (M2.5, 2026-08-13)** (numeric; date part still open)

*Was:* `ingest.to_number` used `to_numeric(errors="coerce")` and downstream code applies `.fillna(0)` in ~33 places on money columns. A blank cell and genuine garbage both became NaN and then 0 — **indistinguishable**, uncounted, unwarned. A settlement export never legitimately contains an unparseable amount.

**What the data turned out to hold.** One column was already exercising this path in production: a real Shopee income export writes the Excel accounting dash `-` in **46,972 of 83,134 rows** of `seller_ship_support`, which feeds `total_discount` → the proportional allocation → the pre-VAT unit price. It was being coerced to NaN and zeroed on every run. That is the correct *value* — the dash means zero, which is why May still row-verified exact — but it was arriving there through the path that cannot tell a zero from a garbled amount.

**Fixed** — blank and accounting-dash cells (`-`, `‐`, `–`, `—`) parse to **0.0**; NaN now means only "could not be parsed at all". `ingest.read_parts` and `lazada.read_ledger` count those per column and **hard-stop** with the column named ([D3](06-DECISIONS.md#d3)), overridable with `numeric_coercion: warn` in `settings.yaml`. After the fix, both May windows contain **zero** unparseable money cells, so the hard stop is armed against real drift rather than firing on current data.

Delta was stated in advance and confirmed exactly: **workbook digests unchanged** (the stored cellsets still hash to the committed workbook manifest), `fingerprint_digest` moved on TikTok and Shopee because money-column null counts collapsed to zero, and Lazada did not move at all. Column sums use `skipna`, so no sum changed.

*Still open — the date half of this entry.* `pd.to_datetime(errors="coerce")` has the same shape and is untouched, along with `to_number` handling only two literal formats (a currency symbol or an unexpected separator now hard-stops rather than becoming 0, which is the improvement, but the format list itself has not grown).

### 1.7 No error handling in the production driver — **PARTLY FIXED (M1)** `SCHEDULED`

*Was:* `tools/full_run.py` had no `try/except`. A mid-stage failure propagated as a raw traceback and `log.write(...)` never ran — so a failed run produced **no audit log at all**, while a previous run's `finance_file.xlsx` sat there looking current.

*Fixed in M1:* `pipeline.run()` returns a `RunResult` in every case, recording the exception on `result.error` and setting `RunStatus.HARD_STOP`. `write_artifacts()` always runs, so the log is always written. Verified by the smoke test, which asserts a hard stop still produces a log and leaves no partial workbook.

*Still open:* the write itself is not atomic, so a `PermissionError` on `finance_file.xlsx` — a finance file left open in Excel, which is routine operator behaviour, not an edge case — still fails the write. It is now reported rather than silent. Atomic write-and-rename is M2.

### 1.8 Two VAT sources of truth — **FIXED (M1)**

**Resolved in M1.** `recon.py:67` read `settings["vat_rate"]` (`0.08`) while `masters.py:116` reads `vat_factors.default` (`1.08`) — different keys, different representations. Only the second was ever used in production, and `recon.py` was deleted, so one source of truth remains. `masters.py:80` still carries a literal `!= 1.08` used only for a log count.

### 1.9 Shared mutable state via the config dict — `OPEN` `CONTAINED (M4)`

`settings["_vat_sku"]` is injected by `pipeline.build_context` (it moved out of `tools/full_run.py` in M4) and read inside `calculate.py` — the config dict used as a data channel. Two concurrent runs sharing one settings dict would cross-contaminate VAT rates.

*Contained, not fixed, in M4.* M4 is the milestone that made this reachable: a worker is the first thing that runs more than one window in one process. Three things hold it:

- the worker calls `build_context` **per job**, so each run gets its own dict — pinned by `tests/service/test_worker.py::test_each_job_gets_its_own_settings_dict`, which asserts the two `id(settings)` values differ;
- one job at a time per worker process, stated in `service/worker.py`'s docstring and in the Dockerfile — concurrency comes from more processes, which is what `FOR UPDATE SKIP LOCKED` is for;
- nothing caches a settings dict at module scope.

The back-channel itself is unchanged, so this stays `OPEN`. What changed is that the way you would hit it now fails a test.

### 1.10 Smaller items

- ~~**`exceptions.xlsx` is never written in production**~~ — fixed in M2; `pipeline.write_artifacts` writes it whenever any exception sheet is populated.
- **In-place mutation of a passed-in frame** — `finance_template.py:159` does `df.loc[dup, c] = None`. Currently safe because callers pass locally-built frames, but it is latent aliasing and the reason for the `pandas<3` pin.
- **Lazada config is code, not YAML** — column maps, sheet names, filename regex and bucket names live in `src/lazada.py:58-99` while TikTok/Shopee use `settings.yaml`.
- **A group-by inconsistency inside Lazada** — one promo aggregation omits the null-key handling its sibling has, so null-product promo rows are dropped from the promo pool while the revenue side keeps them.
- **Date-format inconsistency** — one reader is config-driven `dayfirst`, another parses month-first. Real TikTok files arrive as `%Y/%m/%d` while config documents `dd/mm/yyyy`; pandas coped silently, but a stricter parser will not.
- **No run provenance** — `run_log.txt` is free text with no input hashes, config version or per-row lineage. "Why did this row get this number?" is unanswerable after the fact. The parity fingerprints are the first step toward fixing this.

---

## Part 1b — Open gaps in the M4 service (new, 2026-08-13)

These are not pipeline defects. They are the parts of the service skeleton that are deliberately unfinished or deliberately unverified, written here rather than left as comments in source files because a comment in a source file is not where an operator looks.

### 2.1 The api is unauthenticated — **FIXED (M5, 2026-08-14)**

*Was:* anyone who could reach the port could queue a settlement run, cancel one, read every store's revenue and download the invoicing workbook. The only mitigations were procedural — a `127.0.0.1` default and a printed warning — and **a default is not a control.**

*Fixed:* every endpoint now resolves a `Principal` and names the role it needs (`service/auth.py`). Three roles, least to most: `recon.viewer` reads, `recon.operator` runs work and uploads, `recon.admin` changes the rules. (Renamed `recon.user` in M6, when `api_tokens` was dropped and pasted tokens became password sessions.) Tokens are 32 bytes of `os.urandom`, stored only as SHA-256, and revocation takes effect on the next request rather than when a cache expires.

Three things worth knowing about the shape:

- **It fails closed.** `auth_enabled` is on unless `RECON_AUTH_DISABLED` is *present* — presence, not truthiness, so `RECON_AUTH_DISABLED=false` cannot become a trap. And `ServiceSettings.check_safe_to_serve()` **refuses to start** when a non-loopback host is bound with auth off, rather than warning.
- **`requested_by` is now evidence.** It comes from the authenticated subject; a request body that says otherwise is ignored. Before M5 it was a caller-supplied string, which made "who asked for this settlement run" unanswerable.
- **The seam is Entra-shaped.** Role strings are exactly the ones an Entra `roles` claim will carry, so SSO substitutes for the token lookup rather than replacing the model. The tenant app registration is a human/permissions task — [13-ENTRA-SETUP](13-ENTRA-SETUP.md).

*What is still open:* SSO itself. Bearer tokens are appropriate for an internal prototype and are not a substitute for directory-managed identity — every token is issued and revoked by hand, and there is no central "this person has left" event.

### 2.2 `deploy/Dockerfile` and `deploy/docker-compose.yml` have never been built — **FIXED (M5, 2026-08-14)**

Docker became available on the development machine, so all three images were built and the full stack brought up: `db` + `api` + `worker` + `web`. The api answered `/healthz` over the private network, an unauthenticated `/board` returned **401**, a token minted through `service.admin` authenticated, and the containerised worker claimed a job and executed it — concluding `hard_stop` because no window was staged, which is the correct answer and exercises both axes ([07-VERIFICATION](07-VERIFICATION.md#the-m5-gate)).

One real change came out of building it: the Dockerfile copied `pyproject.toml` before the sources to get a cacheable dependency layer, which cannot work — `pip install .` builds a wheel and hatchling needs the packages to exist. Sources are now copied first. That is exactly the class of error a file that has never been built accumulates.

### 2.3 There is no upload or staging endpoint — **FIXED (M5, 2026-08-14)**

`POST /uploads` accepts a raw export, strips it to the configured columns, quarantines the result, and `POST /uploads/{id}/stage` moves it into the window folder the CLI reads. Two properties carried over from existing controls rather than invented:

- **The PII strip is the pipeline's own allowlist.** `ingest.read_parts` keeps exactly the columns in that platform's column map; the uploader applies the same map from the same config, so there is no second list of PII column names to maintain and go stale. The unstripped original never outlives the request.
- **A byte-identical re-upload is refused** by a unique constraint on the content hash — the double-pull class, one instance of which carried 5.97B VND of double-invoicing risk ([D9](06-DECISIONS.md#d9)).

Rewriting an export before the verified pipeline reads it is a real risk, and it is gated rather than assumed: `test_a_sanitized_window_produces_the_committed_golden` sanitizes a real window and demands the workbook match the committed digest cell for cell.

*Still open:* the mis-pull class itself. Uploading is now possible; **deciding which window an export belongs to is still `tools/stage_exports.py`'s job**, and the api takes the period as a parameter rather than deriving it from settlement dates.

### 2.4 Artifacts are local-filesystem only — **FIXED (M6, 2026-08-17)**

*Was:* `ArtifactStore` was a Protocol with one implementation. "The worker streams to object storage" was a claim about a class that did not exist, and the api returned **501** for any URI its store could not open.

*Fixed:* `service/objects.py` provides `ObjectStore` with two real implementations — `S3Objects` (MinIO/S3/R2 via boto3) and `LocalDirObjects` — plus `S3ArtifactStore` and one new `ArtifactStore` method, `stream()`. `build_artifact_store(settings)` is called by both `api.build_app` and `worker.build_worker`, so the two cannot end up writing and reading different stores, which is exactly the failure this defect described.

*Why it mattered more than it looked:* `api` and `worker` were already separate containers, and the target deployment allows **one volume per service with no cross-service mounting** (verified in Railway's own docs). So the 501 was not a gap to fill later — it was a path that would have failed in production while passing every local test ([D43](06-DECISIONS.md#d43)).

*Verified end to end, not asserted:* through the compose stack on Docker 29.7.2, an uploaded window ran in the worker, its artifacts landed under `s3://recon-artifacts/...`, and `GET /runs/1/artifacts/finance_file.xlsx` returned **14,924 bytes** — a valid 12-tab workbook — streamed through the api's own authorization. Deliberately **not** a presigned URL: that is a credential in a query string `service/auth.py` never sees.

*Still open, and named:* `ObjectStore` has no retry/backoff beyond botocore's three attempts, and nothing verifies a downloaded object's digest against the recorded `sha256` before the pipeline reads it. The digest is stored and could be checked; today a silently truncated download would be caught only by the tie-out.

### 2.5 Config is not yet period-versioned, and there is no config audit trail — **FIXED (M5, 2026-08-14)**

*Was:* the service read whatever was on the worker's disk, so changing a VAT rate in August changed what a **re-run of May** produced.

*Fixed:* `config_versions` stores the full text of every config a run has used — the whole file, comments included, because the comments are the audit trail ([D2](06-DECISIONS.md#d2)) and a parsed structure would discard exactly the part carrying the evidence. `period_config` pins a window to one version, and a window is pinned by **the first run that produces a workbook** — so an ordinary first run behaves exactly as it did before, and only a re-run is protected. `runs.config_version_id` records what each run actually used, and `config_was_pinned` distinguishes "read from disk at the time" from "frozen".

A hard stop pins nothing: a run that produced no workbook should not freeze the rules, because the fix for it may well be a config change.

*Still open:* the pin is automatic and per-window. There is no way to say "this month runs under version 7" ahead of time, and unpinning is an admin action with a warning rather than a workflow.

### 2.6 The run log in Postgres contains store names — `ACCEPTED`

`run_log_lines` holds the pipeline's audit log verbatim, which names stores and counts rows. It contains **no cell values** — the pipeline never logs them and `drop_unmapped_columns` strips the PII columns at read time — so this is the same exposure `output/<window>/<platform>/run_log.txt` already carries on disk. Since M5 there is authentication in front of it, which was the standing objection.

### 2.7 Config approval policy is a setting, not a decision — **FIXED (M6, 2026-08-17)**

*Was:* `RECON_CONFIG_APPROVAL` was a deployment choice with three modes, because **who may approve a rate change had not been decided by anyone** ([open question 13](11-OPEN-QUESTIONS.md)).

*Fixed:* the question is answered. `recon.user` and `recon.admin` propose; `recon.viewer` cannot; only `recon.admin` approves, rejects or applies. `ApprovalPolicy`, `ApprovalDenied` and the environment variable are **deleted** — with an answer in hand, a configurable policy is only a way for a deployment to weaken it, so the rule moved to where every other authorization rule lives: the role on the route, walked by `test_auth.py::test_the_required_role_of_every_route_is_declared`. Setting the old variable now raises a `ConfigError` naming its replacement rather than being silently ignored.

*Self-approval is permitted and recorded, not forbidden.* M5's strict default did not produce a second reviewer in a single-admin deployment — it produced a hand-edit of `settings.yaml` with no proposal, no diff and no audit row at all. `config_proposals.self_approved` is a **generated** column, so it cannot be set to a convenient value.

*The honest caveat, which is the whole reason this closes rather than vanishes:* this is **recorded evidence, not separation of duties**. A single-admin deployment has one person on both ends of the config write path, and no schema can invent a second. What changed is that the fact is now a queryable column instead of an unwritten assumption ([D47](06-DECISIONS.md#d47)).

### 2.8 The web app had never been opened in a browser — **CLOSED (2026-08-17)**

*Was:* `web/` type-checked and built, its container ran, the login page served — and **nothing had clicked a button.**

*Closed by inspection, and that inspection is the origin of M6.* The UI was opened and exercised by hand on 2026-08-17. What that session found — a config editor that required knowing a dotted YAML path, pasted API tokens, a per-run checkbox that relaxed a hard stop with no reason recorded — is the *scope* of this milestone, not a task still outstanding.

*What M6 adds, and its equivalent inspection:* login, the accounts screen, the window/upload screen, and the sectioned config editor did not exist when 2.8 was written. They are covered by 479 service tests and by an end-to-end pass through the compose stack (bootstrap → sign in → temp-password gate → rotate → upload → queue → run → download a valid workbook), but **the same limit applies to their screens**: there is still no browser automation, so claims about rendering are claims about code that compiles and an API that answers. Treat the first real session on the new screens the way the first session on the old ones went.

### 2.9 `tieout.py` still keys its coverage reference on `order_id` alone — `OPEN` (unchanged by M4, M5 or M6)

Carried forward from M2.5, restated here only so it is not read as something a later milestone introduced or fixed. Joins key on `(store, order_id)`; the tie-out's coverage reference does not.

*Renumbered from 2.7 in M6* — there were two defects numbered 2.7, which made "defect 2.7" ambiguous in exactly the register that exists to remove ambiguity.

### 2.10 Materialised objects are not digest-checked before the pipeline reads them — `OPEN` (new in M6)

`uploads.sha256` records the bytes that were accepted, and `service/materialize.py` downloads by key without comparing. A silently truncated or replaced object would reach `read_parts` and be caught only by a tie-out — or not at all, if it happened to still parse.

Cheap to fix (the digest is already stored and already computed on `put`); named here rather than done because it belongs with a retry/verify policy for the object store, not bolted onto the download call.

### 2.11 The config verification run is synchronous and single-window — `ACCEPTED` (new in M6)

Applying a goldens-affecting config change runs one canary window inline, in the api handler ([D45](06-DECISIONS.md#d45)). Two consequences, both deliberate:

* The apply request takes as long as the canary run (~3s for the Lazada window, longer for TikTok or Shopee). Queuing it would put the answer on the board where nobody connects it to the edit, so it is inline on purpose.
* **One** window is checked, not all eight. A change that moves cells only in a window the canary is not is reported as `verified`. That is a real limit of the claim, which is why the stored result names the window it used.

---

## Part 2 — Defects found in the team's own files

Reported back with evidence. These matter for two reasons: they are the reason "faithful reproduction" is not the same as "correct", and they show the manual process had no working end-to-end check either.

1. **A missed income download.** One store's 01–10 consolidation contained zero rows for a storefront that was present in the raw download and in their own adjacent windows — 676 orders / 74,230,000 VND gross. Confirmed by the team as their missed download.
2. **Broken template checks.** One platform's line-tab control block sums a `#REF!` and multiplies a brand bucket by the wrong VAT factor; another's PV-sum verdict reads a blank cell and therefore **always says "OK"**; both platforms' "PV xuat HD" checks compare mismatched quantities and permanently fail. All were silently ignored in practice. The rebuilt template computes every block from the engine instead.
3. **A hard-coded range bug.** One workbook's own `SUBTOTAL` range is too short and under-counts its own sheet by 45,258,030 VND.
4. **Stale stamps.** One template's per-VAT-rate tabs carried January labels into May files.
5. **A double-pull.** One weekly export also contained the whole previous settlement block — 5.97B VND of double-count risk. The single most material catch to date; see [07-VERIFICATION](07-VERIFICATION.md).

---

## The unifying observation

Both processes — theirs and this one — have been operating without a functioning automated end-to-end control. Everything held because **a person was comparing numbers**. That is the load-bearing control today, and it is exactly what automation removes. Any plan that adds layers between the data and a human must replace that control before it deletes it.
