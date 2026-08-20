# 08 — Known Defects

Verified, reproducible, with locations. **Do not re-discover these.** Two sections: defects in this pipeline, and defects found in the team's own source files.

Status key: `OPEN` · `PINNED` (an `xfail(strict)` test asserts the broken behaviour) · `SCHEDULED` (a milestone owns it).

**This page was audited against the code on 2026-08-19**, and roughly a third of what it called open had already been fixed. Corrections are written in place, with the wrong text quoted, rather than deleted — a register that silently rewrites itself is not an audit trail, and the same discipline already applied to the sensitivity-labelled files (diagnosed wrong twice, both recorded). Corrected: 1.6's date residual and the 1.10 date bullet (`date_formats` shipped), 1.7's scheduling clause (M2 closed without it; the record is D10), the 1.10 provenance bullet (three claims, three different answers), 2.3's description of a route deleted in M6, 2.4's digest half (**closed, and the fix it proposed was measured to fail**), 2.5's "no ahead-of-time pinning" clause, and 2.12's decision citation. One gap was found *by* the audit and added: artifact downloads are never digest-checked.

**As of 2026-08-13 there are no `PINNED` entries left.** The eleven strict xfails that had pinned 1.1–1.6 since M0 are all gone — closed in M2 (1.1, 1.2, 1.3) and M2.5 (1.4, 1.5, 1.6). What remains open below is either a smaller item in 1.10 or a residual named inside a fixed entry. The date half of 1.6 was closed in M8 (2026-08-18), leaving its own named residual: date formats are inferred rather than declared. The suite baseline is now `91 passed, 3 skipped` with **no xfails**, which means the drift detector is empty rather than quiet: a new gap needs a new pinned test.

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

*Residual — **CLOSED 2026-08-19**, see [2.9](#29-tieoutpy-still-keys-its-coverage-reference-on-order_id-alone--fixed-2026-08-19).* `tieout.py` kept grouping per order and keying `SourceReference.order_ids` on `order_id` alone. It got the measured pass this paragraph asked for, and the pass found two more faults of the same origin than the one recorded here — including one that *understated* the reconciling item rather than missing a breach.

### 1.6 Silent numeric and date coercion — **FIXED (M2.5, 2026-08-13)** (numeric; date part still open)

*Was:* `ingest.to_number` used `to_numeric(errors="coerce")` and downstream code applies `.fillna(0)` in ~33 places on money columns. A blank cell and genuine garbage both became NaN and then 0 — **indistinguishable**, uncounted, unwarned. A settlement export never legitimately contains an unparseable amount.

**What the data turned out to hold.** One column was already exercising this path in production: a real Shopee income export writes the Excel accounting dash `-` in **46,972 of 83,134 rows** of `seller_ship_support`, which feeds `total_discount` → the proportional allocation → the pre-VAT unit price. It was being coerced to NaN and zeroed on every run. That is the correct *value* — the dash means zero, which is why May still row-verified exact — but it was arriving there through the path that cannot tell a zero from a garbled amount.

**Fixed** — blank and accounting-dash cells (`-`, `‐`, `–`, `—`) parse to **0.0**; NaN now means only "could not be parsed at all". `ingest.read_parts` and `lazada.read_ledger` count those per column and **hard-stop** with the column named ([D3](06-DECISIONS.md#d3)), overridable with `numeric_coercion: warn` in `settings.yaml`. After the fix, both May windows contain **zero** unparseable money cells, so the hard stop is armed against real drift rather than firing on current data.

Delta was stated in advance and confirmed exactly: **workbook digests unchanged** (the stored cellsets still hash to the committed workbook manifest), `fingerprint_digest` moved on TikTok and Shopee because money-column null counts collapsed to zero, and Lazada did not move at all. Column sums use `skipna`, so no sum changed.

*The date half — **FIXED (M8, 2026-08-18)**.* `pd.to_datetime(errors="coerce")` had the same shape as the numeric bug and no counter at all. It is arguably the worse of the two: an unreadable amount becomes a wrong number, an unreadable date becomes a **missing** one, because `finance_template` groups on `.dt.month` and pandas drops a `NaN` group key by default — so the row's money leaves the invoice with nothing said.

`ingest.report_undated` now counts unreadable dates per column and reports them, mirroring `report_unparseable` but defaulting to `warn` rather than `hard_stop`: a blank settlement date is legitimate (`apply_settlement_bounds` already keeps and reports undated rows), so stopping on one would refuse windows that are fine. `date_coercion: hard_stop` is available for an operator who has decided otherwise.

`ingest.parse_dates` also captures the pandas warning that fires when a file's own format contradicts the configured `dayfirst` — previously written to stderr and lost. **It fires on real data today:** TikTok income is `%Y/%m/%d` while `dayfirst.tiktok` is `true`, measured on `2026-05_w1`, both date columns, zero unreadable rows. pandas detects the year-first format and overrides the setting, so the dates are right — but only because the first row of that column is unambiguous. A column whose first value has a day ≤ 12 takes the other branch and transposes silently.

*The residual — **CLOSED (M8 Phase 3, 2026-08-19)**.* The durable fix named here was an explicit `date_formats.<platform>.<kind>` in the contract, and it landed ([D54](06-DECISIONS.md#d54)): `config/settings.yaml:160-171` carries all seven platform/kind pairs, `ingest.date_format` (`src/ingest.py:177-184`) is the single accessor for `src/`, `service/` and `tools/`, `parse_dates` skips inference entirely when given a format, and Lazada parses **per variant** because weekly (`%d-%b-%Y`) and daily (`%d %b %Y`) genuinely disagree (`src/lazada.py:182-192`). Every value was measured against real May *and* July exports and all eight golden windows regenerated with no cell moved. `dayfirst` survives in the config but is consulted only where `date_formats` has no entry — which is now nowhere on a live read path; the one open thing about it is [open question 16](11-OPEN-QUESTIONS.md) (`dayfirst.shopee` carries an unresolved *"TODO verify"*), which is a question about a setting nothing reads rather than a defect.

*What is still open from this entry, restated precisely:* `to_number` handles two literal number styles (`"standard"`, `"vietnamese"`, `src/ingest.py:84-99`) selected by a single **global** `settings["number_style"]`. The asymmetry with `date_formats` is the point — a month where one platform exports Vietnamese-styled amounts and another exports standard cannot be expressed at all. The failure mode is at least loud: unparseable amounts hard-stop through `report_unparseable`.

*This paragraph said "still open" until 2026-08-19, after the fix had shipped.* Corrected here rather than deleted, because a register that quietly rewrites itself is not an audit trail.

### 1.7 No error handling in the production driver — **FIXED**

*Was:* `tools/full_run.py` had no `try/except`. A mid-stage failure propagated as a raw traceback and `log.write(...)` never ran — so a failed run produced **no audit log at all**, while a previous run's `finance_file.xlsx` sat there looking current.

*Fixed in M1:* `pipeline.run()` returns a `RunResult` in every case, recording the exception on `result.error` and setting `RunStatus.HARD_STOP`. `write_artifacts()` always runs, so the log is always written. Verified by the smoke test, which asserts a hard stop still produces a log and leaves no partial workbook.

*The residual — **FIXED (2026-08-19)**, register item D10.* All four writers in `write_artifacts` now go through `pipeline._write_atomically`: a sibling `<name>.tmp`, then `os.replace`. The temp file is a sibling deliberately — `os.replace` is only atomic within one filesystem, so a temp dir elsewhere would silently degrade to copy-then-delete. Each writer (`finance_template.write_workbook`, `export.write_exceptions_file`, `RunLog.write`, and the metrics JSON) takes a `write_to` override so **composing** writes stays `write_artifacts`'s job and no writer invents a temp path of its own; `QueueRunLog.write` passes it through, keeping the subclass substitutable ([D34](06-DECISIONS.md#d34)).

**What it buys, and what it does not.** It buys: a crash, a full disk or a killed worker mid-write can no longer leave a truncated `finance_file.xlsx` — and a truncated one still *opens* in Excel, so the failure being removed is a finance file that looks current and is short some tabs. It does **not** buy writing over a file Excel holds open: on Windows `os.replace` raises `PermissionError` in that case too. What changed there is that the previous artifact now survives intact and the failure is reported (`service/failures.py` already translates `PermissionError` into a sentence naming the file). `tests/test_atomic_writes.py` asserts both halves, including the locked-destination case, so the limit is pinned rather than merely described.

*The scheduling clause in this entry was also wrong.* It read "Atomic write-and-rename is M2." M2 closed without it; **D10** in [14-PRODUCTION-READINESS](14-PRODUCTION-READINESS.md) recorded that correctly (*"Scoped to M2, never landed"*) and is where the status lives. An entry should not claim a milestone that has closed.

### 1.8 Two VAT sources of truth — **FIXED (M1)**

**Resolved in M1.** `recon.py:67` read `settings["vat_rate"]` (`0.08`) while `masters.py:116` reads `vat_factors.default` (`1.08`) — different keys, different representations. Only the second was ever used in production, and `recon.py` was deleted, so one source of truth remains. `masters.py:80` still carries a literal `!= 1.08` used only for a log count.

### 1.9 Shared mutable state via the config dict — **FIXED (2026-08-19)**

`settings["_vat_sku"]` was injected by `pipeline.build_context` (it moved out of `tools/full_run.py` in M4) and read inside `calculate.py` — the config dict used as a data channel. Two concurrent runs sharing one settings dict would cross-contaminate VAT rates.

*Contained in M4, fixed in M8.* M4 made it reachable — a worker is the first thing that runs more than one window in one process — and contained it three ways: `build_context` per job, one job at a time per worker process, and no module-scope caching of a settings dict.

**Containment was the only thing standing between the pattern and wrong tax on an invoice, and it was doing that job invisibly.** A leading underscore in a dict is not a signature: it cannot be type-checked, it does not appear in `compute_sku_columns_tiktok`'s parameter list, and a reader of that function had no way to know the single most consequential input to the VAT calculation arrived out of band. The proof that the shape spreads is that **it had already been copied**: `_masters_source` and `_masters_searched` were added later by the same reasoning, and this entry never mentioned them.

*Fixed* by three fields on the frozen `RunContext` — `vat_sku`, `masters_source`, `masters_searched` — and an explicit `vat_sku` parameter on both `compute_sku_columns_*`. **Lazada already had the right shape** (`pipeline.py` passed its VAT map as an argument), so this is the M2.5 situation again: the correct pattern was already in the codebase for one of three platforms.

*One-job-per-process stays*, but its stated justification changed, because it was resting on this defect. The reasons now are memory (a window's frames peak well into the GBs, and peak RSS is the binding container constraint) and the settings dict still being per-run mutable state (`apply_partial_roster` writes into it). `service/worker.py`, `deploy/Dockerfile` and `build_context`'s docstring were updated so none of them argues from a channel that no longer exists.

`test_each_job_gets_its_own_settings_dict` keeps its `id()` assertion and gains the ones that stop a silent revert: no key beginning with `_` may appear in a run's settings, and the VAT map must arrive as a field. A future `settings["_vat_sku"] = ...` would restore the channel while every other test still passed.

### 1.10 Smaller items

- ~~**`exceptions.xlsx` is never written in production**~~ — fixed in M2; `pipeline.write_artifacts` writes it whenever any exception sheet is populated.
- ~~**In-place mutation of a passed-in frame**~~ — **fixed (2026-08-19).** `_blank_repeats` copies before blanking. **The hazard was sharper than this entry said**, and worth recording because it changes the item from cosmetic to load-bearing: in each caller the blanked column is a *duplicate of a sibling column in the same frame* — `"Source.Name"` and `"Source.Name non repeat"` are both `df["store"]`. Under pandas 2.x the dict-of-Series constructor copies, so they are distinct arrays; under a no-copy construction they would share storage and blanking the "non repeat" column would **empty the real store column on invoice tabs**. That is the concrete thing the `pandas<3` pin stands in front of. The pin **stays**: Copy-on-Write changes far more than one function, and lifting it still needs its own measured golden run. No cell moved (copy semantics are identical under pandas 2.x).
- ~~**Lazada config is code, not YAML**~~ — **fixed in M8/1.7 (2026-08-18).** `WEEKLY_MAP`, `DAILY_MAP`, `SHEETS` and `STORE_PATTERN` moved into `column_maps.lazada`, `sheet_names.lazada` and `store_from_filename.lazada`; `src/lazada.py` reads them through `column_map()` / `sheet_name()` / `store_pattern()`, which hard-stop rather than falling back to a copy. The four modules in `service/` and `tools/` that imported the constants now go through the contract, and the four Lazada golden windows re-ran unmoved. **The bucket names are still code** — `REVENUE_BUCKET` and `PROMO_BUCKETS` in `src/lazada.py`, alongside `finance_template.py`'s invoice buckets, which is [A14](14-PRODUCTION-READINESS.md) and its own commit.
- ~~**A group-by inconsistency inside Lazada**~~ — **fixed (2026-08-19).** The promo aggregation in `lazada.revenue_lines` lacked the `dropna=False` its revenue-side sibling twelve lines above had, so a promo row with a null product name (or SKU) left the pool while its revenue counterpart stayed. **The direction is what made this the priority among the smaller items:** promo amounts are credits that *reduce* the invoiced unit price (`price_ka = (credits + promo) / units / VAT`), so a dropped promo row makes `price_ka` too **high** — an over-statement, billing the client too much. Every other open defect in this area under-states.

  *Measured before the edit:* **0 null `sku_id` and 0 null `product_name` promo rows across all nine staged Lazada windows** (May `l1`–`l4`, July `l1`–`l5`), so the divergence was latent and no cell moved — the four Lazada goldens regenerate unchanged. Closed while the direction of the error was known rather than after an export arrives with a blank product name.

  *The warning was part of the fix.* `revenue_lines` warns when promo does not fully match, and its text attributed the remainder to "the team's pairing" — the same sentence for a genuinely orphaned promo charge and for a key the grouping had thrown away, which is [1.1](#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13)'s shape at small scale. Now that both sides handle nulls identically the remainder really is the orphan class, and the message says so. The orphan class is real: **−30,845 VND on July `l2` and −22,486 VND on `l3`**, both with zero null keys. `tests/test_lazada_promo_pairing.py` covers all of it — `revenue_lines` had no unit coverage at all before, being reachable only through four windows of workbook goldens.
- ~~**Date-format inconsistency**~~ — **fixed (M8 Phase 3, 2026-08-19).** No reader parses month-first any more; every raw-text date parse in `src/` goes through `parse_dates` with an explicit measured format from `date_formats` ([D54](06-DECISIONS.md#d54)). The remaining bare `pd.to_datetime` calls (`src/finance_template.py:240,354,355,395,396`; `src/pipeline.py:374`) re-parse already-typed columns and are harmless. See 1.6 above.
- **No run provenance** — three separate claims, and they now have three different answers. Split so nobody closes the wrong one:
  - *`run_log.txt` is unstructured* — **open, both paths.** `src/runlog.py:7-41` is a `list[str]`; `service/runlog.py:50-57` subclasses it deliberately so the text cannot drift from the CLI's, adding a `seq` and a DB mirror rather than structure.
  - *No input hashes or config version* — **open for a CLI run, closed for a service run.** A service run records `runs.config_version_id` and `config_was_pinned`, logs the version and its digest into the run log (`service/worker.py:435-436`), stores the full config text content-addressed in `config_versions`, writes a per-file `materialized.json` with `sha256`/`object_key`/`upload_id`, digest-verifies every materialised input before it is read, and records a `sha256` per artifact. A `tools/devrun.py` run records none of it: `build_context` reads `settings.yaml` off disk and no digest is taken. **That asymmetry is itself the finding** — the developer path is the one with no provenance.
  - *No per-row lineage* — **open, and the real remaining gap.** Nothing associates an output cell with the input rows that produced it. The closest thing is `source_file`, carried on Lazada ledger rows into the workbook, which is file-level. Owned by the unified transaction store in [10-ROADMAP](10-ROADMAP.md); the upload order index added for [2.12](#212-a-windows-order-export-does-not-cover-the-orders-it-settles--open-found-2026-08-19-july-month-end-tie) is its first additive piece.

  *Struck from this entry 2026-08-19:* "The parity fingerprints are the first step toward fixing this." They were not. `tools/fingerprint.py` is golden-gate machinery with **zero** references in `service/`; what became run provenance was the config-version table and the upload digest chain, by an unrelated route. (`run_exceptions.fingerprint` is a different concept — a stable exception identity.)

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

`POST /uploads` accepts a raw export, strips it to the configured columns, and stores the sanitized result in the object bucket. Two properties carried over from existing controls rather than invented:

- **The PII strip is the pipeline's own allowlist.** `ingest.read_parts` keeps exactly the columns in that platform's column map; the uploader applies the same map from the same config, so there is no second list of PII column names to maintain and go stale. The unstripped original never outlives the request.
- **A byte-identical re-upload is refused** by a unique constraint on the content hash — the double-pull class, one instance of which carried 5.97B VND of double-invoicing risk ([D9](06-DECISIONS.md#d9)).

Rewriting an export before the verified pipeline reads it is a real risk, and it is gated rather than assumed: `test_a_sanitized_window_produces_the_committed_golden` sanitizes a real window and demands the workbook match the committed digest cell for cell.

*This entry described a staging endpoint that no longer exists.* `POST /uploads/{id}/stage` was **deleted in M6** — the bucket is the window, so there is no staging step, and `service/materialize.py` assembles the scratch tree at run time instead (`service/api.py:1094-1095`). An operator reading the old text would look for a route that is gone. The M6 shape is better than what this entry claimed; the claim was simply not updated.

*The residual — **FIXED (2026-08-19)**, [D57](06-DECISIONS.md#d57).* It read: *"deciding which window an export belongs to is still `tools/stage_exports.py`'s job", and `POST /uploads` takes `period` as a form field validated for character safety only (`_safe_period`) — no settlement date is ever read.* That was true, and expensive: **[2.12](#212-a-windows-order-export-does-not-cover-the-orders-it-settles--open-found-2026-08-19-july-month-end-tie) is the price tag** at 4,527,401,608 VND of July understatement, two instances of which are confirmed byte-identical mis-pulled order files.

The door now reads the settlement span out of the pass the sanitizer already makes — no second read, and dates parsed through `ingest.date_format` rather than a second spelling ([D54](06-DECISIONS.md#d54)). It does **three different things** with the answer, and the differences are the point:

| Case | Answer | Why not something stricter |
|---|---|---|
| Window-defining file (income / weekly / daily) whose span does not **intersect** its window's month | **Refused** (422), before the object is stored and before the row is inserted | Intersect rather than contain: a Lazada weekly legitimately laps into the next month, and the 25th-to-month-end Daily week is a permanent fixture. Containment would refuse the healthy case every month |
| A file starting earlier than **every** sibling already in the window | **Warned**, accepted | The first upload has no siblings; [D9](06-DECISIONS.md#d9) owns the hard control at run time; a door that refuses on suspicion at month end teaches operators to fight it |
| Order exports | **Not checked** | An order created in June legitimately settles in July, and TikTok re-ships each store's prior-month pull in every weekly folder. Checking them would flag every healthy window — the same over-broad check staging had to narrow |
| No parseable date column | **Reported as not checked** | A Lazada ledger carries no `statement_date`. Silence is a legitimate answer, and saying so beats guessing |

What is **not** claimed: this catches a mis-pull *labelled for the right month*, which July's two were — `11. Order Purite 14.7.xlsx` is byte-identical to w1's file and both sit inside July. Those are caught by the cross-window order index instead ([D58](06-DECISIONS.md#d58)), and the lines they should have contained were never exported at all, so no control recovers them; a platform re-pull does.

### 2.4 Artifacts are local-filesystem only — **FIXED (M6, 2026-08-17)**

*Was:* `ArtifactStore` was a Protocol with one implementation. "The worker streams to object storage" was a claim about a class that did not exist, and the api returned **501** for any URI its store could not open.

*Fixed:* `service/objects.py` provides `ObjectStore` with two real implementations — `S3Objects` (MinIO/S3/R2 via boto3) and `LocalDirObjects` — plus `S3ArtifactStore` and one new `ArtifactStore` method, `stream()`. `build_artifact_store(settings)` is called by both `api.build_app` and `worker.build_worker`, so the two cannot end up writing and reading different stores, which is exactly the failure this defect described.

*Why it mattered more than it looked:* `api` and `worker` were already separate containers, and the target deployment allows **one volume per service with no cross-service mounting** (verified in Railway's own docs). So the 501 was not a gap to fill later — it was a path that would have failed in production while passing every local test ([D43](06-DECISIONS.md#d43)).

*Verified end to end, not asserted:* through the compose stack on Docker 29.7.2, an uploaded window ran in the worker, its artifacts landed under `s3://recon-artifacts/...`, and `GET /runs/1/artifacts/finance_file.xlsx` returned **14,924 bytes** — a valid 12-tab workbook — streamed through the api's own authorization. Deliberately **not** a presigned URL: that is a credential in a query string `service/auth.py` never sees.

*The retry half — `ACCEPTED`.* `service/objects.py:212-219` configures `retries={"max_attempts": 3, "mode": "standard"}` explicitly, with the rationale in place: *"Three tries, then fail loudly. A settlement run must not hang for minutes on a store that is simply down."* The register's original phrasing was factually right — that is what botocore would have done anyway — but the value is now a decision rather than an accident, and the reasoning argues *against* raising it. Remaining resilience surface, unnamed elsewhere: no circuit breaker and no explicit connect/read timeouts, so botocore's 60s defaults apply.

*The digest half — **STRUCK. Closed by [2.10](#210-materialised-objects-are-not-digest-checked-before-the-pipeline-reads-them--fixed-m825-2026-08-18), and the fix it proposed was measured to FAIL.*** This paragraph used to say *"the digest is stored and could be checked"*, meaning `uploads.sha256`. Implementing exactly that failed **every healthy window** on the first run, because `uploads.sha256` digests the bytes the user handed over while the store holds the sanitized rewrite — different bytes, deliberately ([D52](06-DECISIONS.md#d52)). The working control is `materialize.verify_digest` against `object_sha256` (migration `010`), called on every materialised file before anything reads it. **Left standing, this text would send the next reader to reimplement the version that does not work.**

*The opposite direction — unnamed until 2026-08-19, and **FIXED** the same day.* The artifact **download** path: the worker records a `sha256` per artifact and nothing ever compared it, so a truncated or replaced workbook would reach a finance user looking authoritative. Same failure shape as 2.10, same digest already stored, opposite direction of travel.

`api.download_artifact` now verifies before serving, on both paths — `artifacts.sha256_of` for a local file, `artifacts.sha256_of_chunks` for a streamed object. A mismatch is **502** (the api did its job; storage returned something else) with both digests named, and there is no warning tier: a differing digest on the file the team invoices from has no benign cause.

Two deliberate details. **Verify-then-serve, not verify-while-serving:** the store is read twice rather than buffering a 30 MB workbook in the api, which would undo the reason `stream()` exists — and aborting mid-stream would leave the client holding partial bytes. **A NULL digest is refused, not backfilled:** hashing the stored file now would certify the object store against itself and pass even if the bytes had already been replaced ([D26](06-DECISIONS.md#d26), the argument `010_object_digest.sql` made for uploads). The cost is stated rather than hidden — artifacts from runs predating the digest column stop being downloadable, and a re-run regenerates them.

### 2.5 Config is not yet period-versioned, and there is no config audit trail — **FIXED (M5, 2026-08-14)**

*Was:* the service read whatever was on the worker's disk, so changing a VAT rate in August changed what a **re-run of May** produced.

*Fixed:* `config_versions` stores the full text of every config a run has used — the whole file, comments included, because the comments are the audit trail ([D2](06-DECISIONS.md#d2)) and a parsed structure would discard exactly the part carrying the evidence. `period_config` pins a window to one version, and a window is pinned by **the first run that produces a workbook** — so an ordinary first run behaves exactly as it did before, and only a re-run is protected. `runs.config_version_id` records what each run actually used, and `config_was_pinned` distinguishes "read from disk at the time" from "frozen".

A hard stop pins nothing: a run that produced no workbook should not freeze the rules, because the fix for it may well be a config change.

*The "no way to pin ahead of time" clause was **STALE**.* `POST /config/pins` (admin, `service/api.py:1360-1365`) takes `platform`, `period`, `config_version_id` and a `reason`, validates the version exists, and upserts — so it serves both as an ahead-of-time pin and as a re-pin, with a CLI equivalent at `service/admin.py:211-221` and a `GET` to list them. What remains true is narrower: the pin is **per-window**, so "this month runs under version 7" is one call per window (~14 a month), and `service/admin.py`'s explanatory line — *"a window is pinned by its first run that produces a workbook"* — describes only the automatic path.

*The sharper half — **FIXED (2026-08-19)**.* **Unpinning destroyed the evidence.** `DELETE /config/pins/{platform}/{period}` bare-deleted the `period_config` row, so afterwards nothing recorded that the window had been pinned, to which version, or why it was released — only a docstring warning and a line printed to stdout. In a system whose entire M5/M6 rationale is the audit trail, and which records `config_proposals.self_approved` as a *generated* column precisely so it cannot be set to a convenient value, the one act on the config path that leaves no trace was the consequential one: after an unpin, a re-run of that window may not reproduce the invoice it was booked from.

Migration `014_pin_events.sql` adds append-only `config_pin_events`. Both the automatic pin (`worker._settle_config`) and a manual one write an event **in the same transaction as the upsert**, so current state and its history cannot disagree. Unpinning requires a `reason` — 422 without one — reads the released version *inside* the transaction (after the delete there is nowhere left to look it up), and takes its actor from the session, never the body. `GET /config/pins` returns `events` alongside `pins` in one response on purpose: an unpinned window has no `pins` row at all, so a caller reading only current state cannot distinguish "never pinned" from "pinned and released", which is the whole question the history answers. `service.admin config pins` prints both.

The table is append-only *by construction* — no update or delete path is written against it anywhere in `service/`, and the `action` check keeps the vocabulary closed so a third verb needs a migration.

*The pin being automatic is deliberate and stays:* `service/worker.py:439-453` records the version on the run and pins only when the run produced a workbook — a hard stop pins nothing, because the fix for it may well be a config change.

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

*And again after M8 (2026-08-18).* Phases 2 and 4 added a reference-totals form, a verification-capability notice, error/not-found/loading boundaries, a self-refreshing run page, re-run and cancel controls, confirmation dialogs and a reclaim button. All of it type-checks and builds; the service halves are tested. **None of it has been clicked.** This entry stays closed because it names the *old* screens, but the limit it describes now covers three generations of UI and is not shrinking. A browser test harness is the thing that would actually retire it.

### 2.9 `tieout.py` still keys its coverage reference on `order_id` alone — **FIXED (2026-08-19)**

Carried forward from M2.5 through M4, M5 and M6. Joins keyed on `(store, order_id)`; the tie-out's coverage reference did not.

**It was three faults, not one, and they fail in opposite directions.** Only the first was on the register:

| Site | Under a collision |
|---|---|
| Order coverage (check 1) | differenced bare id sets, so an order with no lines of its own counted as covered because *another* store had that id — **blind** |
| `partition` | filed that order's settlement as "matched", inflating the reference total and **shrinking the ~21% reconciling item** a reviewer is told to watch for changes in |
| Per-order conservation (check 3) | `groupby("order_id")` with `settled=first()` summed both stores' rebuilt revenue against one store's settlement — **noisy**, a variance manufactured from correct data |

The second is the quietest and the worst: money left the invoice through a door that reported less traffic than it carried.

**Fixed** by one identity function, `tieout.pairs`, used by both sides of every comparison — which also closed a stringification asymmetry that would otherwise have produced phantom breaches (`per_store` keyed through `str(k)`, the SKU side through `.astype(str)`). `SourceReference.order_ids` became `order_keys`; `partition` takes `present_keys`; `from_income` now *requires* `store` rather than silently degrading to id-only keying; the dead `orders` field is derived from the key set. `pipeline.py`'s `unmatched_orders` exception sheet keys the same way, for the same reason — it had been omitting the row an operator needs most.

**The template was already in the same file.** `RevenueCrossing` (Shopee's money crossing) was built composite from the start, with the reference defining the population and an absent order counting as a shortfall of its whole value. Checks 2 and 4 were already store-keyed. Lazada needs nothing — it takes no `SourceReference`.

**Why this mattered more on Shopee than the original wording suggested:** `SHOPEE_MONEY` is `None`, so checks 3 and 4 never run there. Order coverage was Shopee's **only** order-population control, and this was the defect in it. On TikTok the `rebuilt total == referenced total` row is an accidental backstop — but it names no order and no store.

*Evidence.* `tools/measure_order_id_collisions.py` is committed rather than ad-hoc (the M2.5 measurement was a scratch script nobody kept, which is why this claim had to be re-derived): **0 collisions across 8,399,255 distinct order ids** — 522,201 over the four May golden windows and 7,877,054 over the nine July ones, orders and income, both platforms. So the change is output-identical on today's data and only bites the case it exists for. **The golden gate was re-run over all eight windows at zero tolerance: no cell moved.** Three regression tests in `tests/test_silent_failures.py` cover the three faults, each pinned first as `xfail(strict)` and unpinned only after XPASS ([D22](06-DECISIONS.md#d22)). Each also carries a **discriminator** — an assertion that the pre-fix comparison would still have passed — so the proof of the original blindness stays in the suite after the marker is gone.

*Named residual, deliberately not changed:* `finance_template.py:345` groups the TikTok return tab's per-order total on `order_id` alone. It writes workbook cells, so unlike everything above it is a golden-moving change and wants its own commit and its own measured delta.

*Renumbered from 2.7 in M6* — there were two defects numbered 2.7, which made "defect 2.7" ambiguous in exactly the register that exists to remove ambiguity.

### 2.10 Materialised objects are not digest-checked before the pipeline reads them — **FIXED (M8/2.5, 2026-08-18)**

`service/materialize.py` downloaded by key and compared nothing. A silently truncated or replaced object would reach `read_parts` and be caught only by a tie-out — or not at all, if it happened to still parse. Every claim the upload boundary makes, PII stripping included, was a claim about a file the run might not have been reading.

`materialize.verify_digest` now streams the sha256 of each materialised file and refuses a mismatch. A mismatch is a hard failure with no warning tier: an object store returning different bytes under one key, a truncated download and two windows colliding in scratch all arrive looking identical, and each is a reason to stop rather than to invoice.

**This entry said the wrong thing, and the fix is not what it described.** It read "`uploads.sha256` records the bytes that were accepted … cheap to fix (the digest is already stored)". Implementing exactly that failed every healthy window on the first run, because the two values were never comparable:

* `uploads.sha256` digests the bytes the **user handed over**. It is the provenance record and the unique constraint that refuses a byte-identical re-upload — the M2.5 double-pull control moved to the door.
* What is stored under `object_key` is the **sanitized rewrite**: PII columns removed, one sheet, written by openpyxl. Different bytes, deliberately.

Migration `010_object_digest.sql` adds `object_sha256`, recorded at upload from the sanitized bytes. **No backfill.** Recomputing it for existing rows means reading whatever is in the store today and writing that down as the expected value, which certifies the store against itself and would pass even if the bytes had already been replaced — the [D26](06-DECISIONS.md#d26) failure. `NULL` therefore means "uploaded before this check existed" and is refused, not trusted; such an upload has to be re-uploaded to be runnable.

### 2.11 The config verification run is synchronous and single-window — `ACCEPTED` (new in M6)

Applying a goldens-affecting config change runs one canary window inline, in the api handler ([D45](06-DECISIONS.md#d45)). Two consequences, both deliberate:

* The apply request takes as long as the canary run (~3s for the Lazada window, longer for TikTok or Shopee). Queuing it would put the answer on the board where nobody connects it to the edit, so it is inline on purpose.
* **One** window is checked, not all eight. A change that moves cells only in a window the canary is not is reported as `verified`. That is a real limit of the claim, which is why the stored result names the window it used.

*Three things have moved since this entry was written (recorded 2026-08-19, status unchanged):*

* **Whether the canary can run at all is now answered up front.** `verification.capability()` distinguishes `no_digests` from `no_inputs` because the fixes differ. This matters more than it sounds: the golden manifest lives under `tests/` and no container image ships it, so in every containerised deployment this system has produced, `UNAVAILABLE` was the *only* reachable verdict — 2.11's "one window, not eight" was really "zero windows" there. The window preference order is `2026-05_l1/lazada` → `w1/tiktok` → `s1/shopee` (Lazada first because it is the ~3s window), falling back to the synthetic demo window labelled `strong=False` rather than counted as equal.
* **Whether the canary runs is caller-decided, not inferred.** `invalidating` comes from the `invalidates_goldens` column on the edited config rows; an unknown counts as invalidating, and an empty list returns `NOT_APPLICABLE`. That mechanism decides coverage more than the window count does.
* **A failed canary is deliberately non-fatal.** By the time it runs, the config is already applied, so the verdict is `FAILED` with the exception named rather than a rollback — which means **"applied but unverified" is a state an operator can reach**, and it belongs in this entry's list of consequences.

---

### 2.12 A window's order export does not cover the orders it settles — `OPEN` (found 2026-08-19, July month-end tie)

**Found by the first external month-end comparison** ([07-VERIFICATION](07-VERIFICATION.md)),
which is the argument for doing that comparison at all. Eleven of 205 store-window
cells across July's TikTok and Shopee tabs disagree with the team's master, and
every one of them is this. **Lazada is unaffected** — it is a fee-event ledger with
no order files at all, and it reproduces the reference exactly.

`explode_to_sku_tiktok` joins a window's GOOD income to the ORDER files staged in
that window's folder. But an order settled in `w2` may have been *created* days
earlier, so its SKU lines live in the `01-07` folder's order export and not in
`08-14`'s. Those lines are simply absent, the income row matches nothing, and the
revenue leaves the invoice through the documented "~21% unmatched" door — quietly,
because that door is expected to have traffic.

Measured on July `w2`, order-id match rate of a store's own window income against
the order files staged with it, versus against the whole month's:

| store | own folder | whole month | money short vs the team's master |
|---|---|---|---|
| purite | 58.2% | 99.8% | 1,444,052,986 |
| abbott pediasure | 33.8% | 99.9% | 941,081,056 |
| mondelez kinh do | 85.4% | 100.0% | 6,992,600 |
| similac | 92.0% | 100.0% | 1,788,000 |

Also `curel` w5 (23,807,000) and `unilever homecare` w4 (73,000).

**Shopee has it too, and worse in one window.** `masan` in `s4` (29-31 July) matches
only **33.1%** of its income order-ids against the order files staged with it, and
**95.6%** against the month's — a 2,106,036,476 VND understatement in one cell. The
other four Shopee cells are small (`lashe` s2, `sanofi` s1/s3, `masan` s1).

Total understatement against the team's July master: **4,527,401,608 VND** —
2,417,721,642 on TikTok (~1.6% of its month) and 2,109,679,966 on Shopee (~2.0%).

**Two of these are a mis-pull in the raw data, confirmed by digest.**
`11. Order Purite 14.7.xlsx` (w2) is byte-identical to `12. Order Purite 7.7.xlsx`
(w1) — sha `9bd3750061f4` — and the w2 Mondelez folder holds a file still named
`7.7`, byte-identical to w1's (`f7e3dda0d99a`). The team exported the same order
file twice and labelled it for the later window. Nothing downstream can recover
from that; the lines were never pulled.

**Do not "fix" this by pooling the month's order files.** Measured: pooling every
July TikTok order export into `w2` takes the match rate to ~100% and the window
total to **183,102,704,362 VND** against a reference of 40,060,544,029 — a 4.5×
over-count. The same order line appears in several exports, `dedupe_rows: false`
is deliberate ([D5](06-DECISIONS.md#d5) — byte-identical order lines are
legitimate; this entry cited D14 until 2026-08-19, which is "compare stored
artifacts, not live processes" and has nothing to do with it), and the explode
sums quantity per `(store, order_id, sku_id, sku_name, unit_price_gross)` bucket,
so a second copy of a file **inflates quantity inside one SKU line** rather than
adding rows. Any dedupe therefore has to act on raw order rows *before* that
groupby, and a dedupe keyed on `(store, order_id, sku)` alone is **not safe**:
the same SKU legitimately appears twice in one order as a normal unit and a gift
variant (`docs/05-DOMAIN-RULES.md` — promo pairing must include product name),
and row content cannot distinguish that from a re-pulled copy. The discriminator
that exists is **file-level provenance** — `source_file` on every row, per-file
sha256 in `staging.json`, and `uploads.sha256` / `object_sha256` in Postgres.

**What is NOT the cause, checked and cleared:** the `2026-07_w2` settlement bound.
It drops 24,555 income rows, and **24,546 of 24,546 distinct order ids among them
are already present in w1** — 100%, zero unique. The bound is correct and removing
it would double-count.

**Detection has landed; the fix has not (2026-08-19).** The status stays `OPEN` because
no number has changed. What exists now:

* `tieout.coverage_by_store` reports the unmatched share **per storefront** rather than
  per window, as an `INFO` row naming the worst stores plus an `order_coverage`
  exception sheet keyed on `("store",)`. It is deliberately **not** a failable check:
  measured on `2026-05_w1` — a golden window that reproduces the team's figures — the
  whole ~21% belongs to one storefront (Unilever Homecare 21.2%, Mars 0.0%), so any
  threshold that would catch July's cells breaches on known-good data. The *level*
  separates nothing; the month-over-month **change** is the signal, which is what the
  store-keyed fingerprint makes queryable.
* Shopee gained an `unmatched_orders` exception sheet. It had none — on the platform
  holding the single worst July cell.
* **The cross-window question can now be asked and is reported before a run.** Migration
  `015_order_index.sql` records which uploaded file holds which `(store, order_id)`;
  the door indexes every arrival and `service.order_index --backfill` clears the
  backlog ([D58](06-DECISIONS.md#d58)). The worker logs it after materialisation,
  `GET /windows/{platform}/{period}/order-coverage` serves it, and the window page
  shows it. This is the check with **zero legitimate traffic** — the legitimate ~21%
  class has lines in **no** window, so lines sitting in a *sibling* window's export is
  not that class.
* **"Not indexed" is reported as its own state.** An empty coverage result from a window
  whose uploads predate the index is indistinguishable from perfect coverage, so it is
  never rendered as "covered".

**The fix exists and is running in `report` mode (2026-08-19).** `src/backfill.py`
borrows an order's SKU lines from the **nearest same-month predecessor window** that has
them — one window per order, all of that window's files, predecessors only, income never
re-read ([D59](06-DECISIONS.md#d59)). It is one mechanism with three modes,
`cross_window_order_backfill: off | report | apply`:

* **`off`** — byte-for-byte the behaviour every committed golden was produced under.
* **`report`** (the default now) — measures it and says so: a tie-out INFO row naming the
  recoverable settlement, a `Cross-window Orders` exceptions sheet carrying which order
  came from which window and file, and log lines. **No number changes.** Proven, not
  argued: the money gate re-ran all eight windows under `report` at zero tolerance.
* **`apply`** — concatenates the borrowed lines before the explode. This moves cells and
  has not been switched on.

*Why this is not pooling with extra steps.* Borrowed orders enter the tie-out's **matched**
population, so TikTok's per-order conservation (1 VND) and Shopee's revenue crossing run
*over the borrowed lines* — a predecessor re-export whose quantities drifted breaches a
check rather than silently mis-invoicing.

**The report mode that shipped on 2026-08-19 under-reported itself, fixed 2026-08-20.**
`borrow_order_lines` read predecessor files through `ingest.read_files` and then
hand-rolled two `.strip()` calls, so borrowed frames skipped everything `read_parts` does
afterwards — including `store_aliases`. `needed` is built from frames that *did* go
through `read_parts`, so it holds canonical store names, while the file prefilter compared
the raw name a filename yields. `settings.yaml` maps `"Pediasure" -> "Abbott Pediasure"`
because the order files drop the "Abbott", so **w1's Abbott files were skipped and
941,081,056 VND of recoverable July settlement reported as zero.** The same shape hid
Shopee's lowercase `lashe`. Measured after the fix: `2026-07_w2` reports 942,869,056 VND
(was ~1,788,000) and `2026-07_s2` reports 173,429 — both now equal to what
`tools/measure_order_coverage.py` had said all along, which is the point: the tool reads
through `read_parts` and the pipeline did not, and *nothing compared the two*.

Two things follow, and both are recorded rather than assumed. Borrowed frames now go
through `ingest.normalize_parts` — the extracted post-read block — so they also get the
numeric coercion the explode needs (it groups on `unit_price_gross` and SUMS `quantity`,
so raw text is both a wrong bucket and an unsummable column: an `apply`-mode fault that
had not yet been reachable). And no golden cell could move, because no golden window opens
a predecessor at all — `s2`/`s3` have 100% own-window coverage, `w1`/`s1` have no
predecessor, Lazada is not wired; re-verified at zero tolerance across all eight windows
after the fix.

*Why the tests did not catch it:* the synthetic fixtures configure no aliases and asserted
on **string** quantities — a test suite written against the buggy path. Those assertions
now read as numbers, and two new tests carry the alias shape with a discriminator.

**One failure policy across the seam, also 2026-08-20.** An unreadable predecessor now
warns and skips that window under `report` and refuses under `apply`, in both
`src/backfill.py` and `service/materialize.py`. Before, the two disagreed with themselves:
a digest mismatch hard-stopped a *report*-mode run (whose contract is that it changes
nothing) while an unnameable file and a missing object beside it only warned, and two
predecessor uploads sharing a filename were silently resolved last-one-wins where the
window's own files refuse the same collision.

**What is still open:** the flip to `apply`, with the measured per-window delta stated in
advance and the golden windows re-baselined deliberately. Expected recovery is already
measured: **942,869,056 VND** in `w2` from `w1`, **1,390,095,674** in `s4` from `s3`,
173,429 in `s2` from `s1`. The two mis-pull cells recover **nothing** — their lines were
never exported, and no mode can invent them.

Measured cross-window recoverable settlement, which pre-states what a fix can and cannot
return: **942,869,056 VND** in `w2` from `w1` (exactly abbott 941,081,056 + similac
1,788,000), **1,390,095,674** in `s4` from `s3`, 173,429 in `s2` from `s1`, and nothing in
`w3`/`w4`/`w5`/`s3`. `purite` and `mondelez kinh do` recover **nothing** — their `w2`
order files are the byte-identical mis-pulls named above, so the lines were never
exported. No code closes that residual; a platform re-pull does.

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
