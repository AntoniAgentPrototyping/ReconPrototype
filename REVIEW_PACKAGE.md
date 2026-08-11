# REVIEW PACKAGE — full documentation + source in one file
Generated 2026-08-11 from commit `c5ee2d0` by `tools/build_review_package.py`. Regenerate rather than edit.


# PART A — DOCUMENTATION


---

<!-- ===== HANDOFF.md ===== -->

# Handoff — E-commerce Reconciliation Pipeline (Phase 1)

For a technical reviewer who knows the original Excel/Power-Query process.
This package is **code + config only** — the real May 2026 input data lives
on the shared OneDrive (`VN handover/Platform reconciliation/…`), and the
repo's `.gitignore` keeps `input/` and `output/` out of version control.

## Setup (once)

1. Python 3.12 (user-scope install is fine; no admin needed).
2. `pip install -r requirements.txt` — four deps: pandas, openpyxl, PyYAML,
   duckdb (duckdb is unused so far; reserved from the original spec), plus
   `pip install pyxlsb` for the live master file (only needed when
   `config/Lib & VAT rate.xlsb` is present).

## Prove your machine works (no real data needed)

```
python tools/smoke_test.py
```

Generates synthetic multi-part inputs under `input/2026-06_p1/` (with baked-in
anomalies: overlapping parts, ghost income lines, an unknown SKU, refunds,
zero-revenue) and runs the legacy end-to-end pipeline for two platforms.
Expected output ends with `18/18 checks passed`. This exercises the sample
path (`tools/sample_config/`), not the real-platform rules.

## Running a real platform/window

1. Create one folder per settlement window and copy that window's downloads
   into it (never mix two windows' order exports — re-exports drift):
   - TikTok:  `input/<period>/tiktok/orders/` + `input/<period>/tiktok/income/`
   - Shopee:  `input/<period>/shopee/orders/` + `input/<period>/shopee/income/`
   - Lazada:  `input/<period>/lazada/Weekly/` and/or `.../Daily/`
   Period naming used for May 2026: `2026-05_w1|w2` (TikTok),
   `2026-05_s1|s2|s2x|s3|s3k` (Shopee incl. Xmen/Kao sub-batches),
   `2026-05_l1..l5` (Lazada).
2. Run all stores in the window:
   ```
   python tools/full_run.py --platform tiktok --period 2026-05_w1 [--refs refs.json]
   ```
   Outputs land in `output/<period>/<platform>/`: `finance_file.xlsx`
   (team's invoicing-file layout) and `run_log.txt` (audit trail, tie-out
   results, per-store ties when `--refs` is given). `--refs` is a JSON of
   team reference totals; see `COMPLETION_REPORT.md` §5 for how it is used
   in the parallel run.
3. Store-level row verification against the team's intermediary files (the
   deep evidence tool used throughout Phase 1):
   `tools/calc_verify.py` (TikTok), `tools/calc_verify_shopee.py`,
   `tools/calc_verify_lazada.py` — each documents its own usage header.

## Repo map

| Path | What it is |
|---|---|
| `recon.py` | Legacy single-command pipeline for the synthetic sample path (spec's original 6-stage flow) |
| `src/ingest.py` | Stage 1: multi-part reading, header maps, sheet patterns/header rows, store-from-filename + aliases, store-count hard stop |
| `src/stitch.py` | Cross-period order-date attribution (TikTok/Shopee orders+income model) |
| `src/classify.py` | Classification: TikTok M-code port (Good/Partial/Total Return/Payback + unclassified), Shopee derived rules (Return / 0 dong / ok), legacy sample rules |
| `src/calculate.py` | Yellow-column ports for TikTok and Shopee + per-formula verification state dicts (`*_FORMULA_STATUS`) |
| `src/lazada.py` | Lazada's dedicated ledger path (Weekly/Daily union, fee-bucket classification, promo-netted whole-VND Price KA) |
| `src/masters.py` | Team-owned live master (`Lib & VAT rate.xlsb`): fee-type map + per-SKU VAT exceptions, CSV snapshot fallback, drift reporting |
| `src/tieout.py` | The team's own named tolerance checks per platform |
| `src/export_platforms.py` | Finance-file layouts matched to the team's real invoicing files (TikTok Income tab; Shopee Income+Return with the 10-VND split; Lazada per-VAT-rate tabs + fee buckets) |
| `src/export.py`, `src/runlog.py`, `src/config.py`, `src/errors.py` | Legacy sample export, audit log, config loading, hard-stop type |
| `config/settings.yaml` | THE contract: column maps per platform (real export headers), store lists + aliases, VAT default (the 8%→10% revert is one line), tolerances, all with in-line evidence comments |
| `config/Lib & VAT rate.xlsb` | Team-owned master (Lib fee mapping + VAT-by-SKU), read live at runtime |
| `config/lazada_fee_types.csv`, `config/lazada_vat_sku.csv` | Point-in-time snapshots of the master (fallback; drift vs live is reported every run) |
| `config/brand_rules.yaml`, `config/sku_master.csv` | Spec-era config; superseded for the real platforms, still used by the sample path |
| `tools/full_run.py` | Full-platform window runs + export + total ties |
| `tools/calc_verify*.py` | Row-level verification harnesses per platform |
| `tools/stage1_probe.py` | Ingest-only diagnostics for a new window/store |
| `tools/make_sample_data.py`, `tools/smoke_test.py`, `tools/sample_config/` | Synthetic sample path |

## Where the findings live

`COMPLETION_REPORT.md` — the Phase 1 record: derived rules per platform with
file/cell evidence, the three-platform differences table, verification
results (~288K rows row-exact; all 12 windows' full-platform ties), defects
found in the team's own files, and the parallel-run plan. The README's
build-order section is the original scaffold-era plan, kept for history.

Note `PLACEHOLDER_FORMULAS = True` in `src/calculate.py`: a deliberate flag
stating the numbers are not yet blessed for production booking — it flips
only after the agreed criteria (stakeholder sign-off + one clean live
parallel cycle), not because the code is unverified.


---

<!-- ===== COMPLETION_REPORT.md ===== -->

# Phase 1 Completion Report — E-commerce Reconciliation Pipeline

**Scope:** May 2026 cycle, TikTok + Shopee + Lazada. Every rule below was
derived from the team's own files (Power Query M code embedded in their
workbooks, live Excel formulas, and pivot evidence) and verified row-by-row
against the team's computed outputs — never invented. Rohto is out of scope.

**Status:** all 12 reconciliation windows across the three platforms tie
against the team's Total/invoicing files. `PLACEHOLDER_FORMULAS` remains
`True` pending the agreed flip criteria (stakeholder sign-off + one clean
live parallel cycle).

---

## 1. Platform rules, with evidence

### TikTok (2 windows/month; orders + income)

- **Ingest**: per-store Seller Center exports; sheet `OrderSKUList` (orders,
  a description row under the header is skipped — their M code filters
  `"Current order status."`) and `Order details` (income). dd/mm/yyyy dates
  (their locale `en-GB`). No store column — store = file name.
- **Classification** (ported from the DataMashup M code in Thanh_recon
  V1/V2): income settlement lines are collapsed per (store, order, type,
  order-created time), then:
  `Good` = subtotal+refund-before ≠ 0, settlement ≥ 0, Type=Order;
  `Total Return` = subtotal+refund-before = 0; `Partial Return` = both ≠ 0 and
  refund ≠ 0; `Payback_Order` = not-Good and revenue < 0.
  `Final_Status`: OK = Good only; **take out = the three non-Good statuses**
  (proven exactly: Unilever W1 −55,421,041 −9,692,678 +8,035,197 =
  −57,078,522 = their take-out pivot). Reimbursement/adjustment lines
  (Logistics/Platform reimbursement etc.) fall through every branch — the
  team's pivots contain them in NEITHER bucket; the pipeline carries them as
  `unclassified` and routes them to exceptions (+44–51M VND/window at
  Unilever alone; the team books them outside the revenue invoice).
- **Calculation** (ported cell-by-cell from intermediary "Xuat HĐ", header
  row 3): SKU explode joins income→orders on Order ID with **Seller SKU**
  identity; gross = unit original price × qty; net = gross − SKU Seller
  Discount; pre-VAT unit = (net/qty)/VAT; per-order check V−F against the
  income subtotal. The income file is the *check*, not the revenue source.
- **Tie-outs**: the team's own named checks — PV sum |Δ|<12,000; Xuat HD bt
  |Δ|<2,000; PV xuat HD |Δ|<1,000 (the spec's "10,000 VND" figure matches
  none of them).

### Shopee (3 windows/month + Xmen/Kao invoicing-only sub-batch files)

- **Ingest**: income leaf headers on row 3 (their triple `PromoteHeaders`);
  data split across `Doanh thu - N` sheets at ~10K rows; lines typed
  Order/Sku — only Order rows count; the team's own M code strips PII
  columns at ingest (ours never maps them).
- **Classification**: their "return + 0dong" sheet is a manually curated
  XLOOKUP list; the derived membership rules verified exactly:
  `Return` = order refund sum ≠ 0 (Kate 11/11, Sanofi 178/178);
  `0 dong` = settlement ≤ 0 AND refund = 0 (Sanofi 40/40 — fully
  promo-covered orders whose residual settlement is fees); else `ok`.
- **Calculation** ("Xuat HĐ" formulas): gross = Giá gốc × Số lượng; net =
  gross − seller subsidy; total discount = seller voucher + shipping support
  + coin cashback + co-funded voucher; **proportional allocation**
  Z = (T/X)·Y; pre-VAT unit = ((T+Z)/qty)/VAT.
- **Return tab rule**: recompute the returned order's with-VAT total, add
  the (negative) refund; |sum| < **10 VND** → full return (skip invoice),
  else "Return 1 phan" → must invoice.
- **Tie-outs**: PV sum and Xuat HD checks at **2,000 VND** (not TikTok's
  12,000).

### Lazada (5 weekly windows/month; transaction LEDGER — no order files)

- **Ingest** (`src/lazada.py`, its own path): per-store ledgers, one row per
  (order item × fee event); Weekly schema (`Transaction Overview`) and Daily
  schema (`Income Overview`, the 25th–end week — a permanent fixture) are
  normalized and unioned exactly like their `FR_Total` query.
- **Classification is fee-typing**: fee name → bucket/status via the team's
  Lib master ("Item Price Credit"→"1.Doanh Thu", "Commission"→"6.CP co
  Invoice", …118 mappings; zero unmapped fee names in May). Refunds carry NO
  credit notes — reversal lines net into final sales through this mapping.
- **Revenue is gross-credited** (gifts credit full price); promo charges are
  separate ledger lines. Invoiced unit price:
  `Price KA = round((credits + per-(order, SKU, product) promo charges) / units / VAT)`
  — whole-VND rounding, promo *netting* (proven by gift lines zeroing
  exactly). Promo pairing must include product name: the same SKU can be
  both a normal unit and a gift variant within one order.
- **Per-SKU VAT is live here**: 660 SKUs mapped, 4 at 1.05 (MegRhythm).
- **Tie-outs**: sale-report cross-refs at 1,000 VND (1.05/1.08) / 2,000
  (1.10).

### VAT model (all platforms, confirmed by the team)

One default factor (`vat_factors.default: 1.08` — a temporary tax
concession; **reverting to 10% is that single config line**) plus per-SKU
exceptions from the team-owned master `config/Lib & VAT rate.xlsb` (Lib +
VAT sheets, additive-only, read live at runtime; the `lazada_*.csv` files
are fallback snapshots and live-vs-snapshot drift is reported on every run).
TikTok/Shopee's 1.05/1.10 template cells are vestigial (one multiplies an
empty cell; the other double-VATs inside a `#REF!`-broken block whose
failing verdict the team ignores).

---

## 2. Three-platform differences table

| Dimension | TikTok | Shopee | Lazada |
|---|---|---|---|
| Data model | Orders + income | Orders + income (Order/Sku typed) | Fee-event ledger, no orders |
| Windows/May | 2 | 3 (+ Xmen/Kao invoicing files) | 5 weekly (last = Daily schema) |
| Classification | PQ formulas (4 statuses) | Curated list (Return / 0 dong / ok) | Fee name → Lib bucket |
| Discounts | None (order-side rebuild) | Proportional allocation | Promo netting into unit price |
| VAT | 1.08 default | 1.08 default (buckets live, empty in May) | Per-SKU master, 1.05 live |
| Cross-period | Order-file join; income carries own order date | Order-file join | Transaction-date month only |
| Tolerances | 12,000 / 2,000 / 1,000 | 2,000 / 2,000 / 10 | 1,000 / 2,000 |
| Rounding | Float throughout | Float throughout | Whole-VND unit price |
| Finance file | Income tab | Income + Return tabs | Per-VAT-rate tabs + fee buckets |

---

## 3. Verification results

**Row-level (store-window verification against the team's own computed
rows):** ~288,000 rows reproduced exactly, every ported column —
TikTok: U food (149), Unilever Homecare (135,866), KAO (49,738), AHC
(49,894) across both windows; Shopee: nutifoodgpddvietnam (55) and Sanofi
(51,159 — the only store with 0-dong orders) across all three windows;
Lazada: Curel and Unilever-2 (1,306 revenue lines + all fee buckets) across
a Weekly and a Daily window.

**Full-platform runs (all stores, all windows, grand + per-store ties):**

| Platform | Windows | Result |
|---|---|---|
| TikTok | w1, w2 | All stores tie; grands match the For KA files to the VND |
| Shopee | s1, s2, s3, s2x (Xmen), s3k (Kao) | All tie — s1 32/32, s2 30/30, s3 32/32 store-metrics; grands exact |
| Lazada | l1–l5 | All stores, all buckets, per-rate grands tie |

**Not a single store fails to tie.** The only discrepancies found anywhere
are defects in the team's own files, all reported:
- Lazada l2 KA workbook: `'1.08'!H5` SUBTOTAL range is too short —
  under-counts its own sheet by 45,258,030 VND.
- TikTok For KA files: the "Return 1 phan" section formula is `#REF!`-broken
  in both windows; a standing "Có Ajinomoto, nhớ bỏ ra nha KA" manual note.
- Lazada Total files: the Daily query hard-codes a "Laz 26T04" path fragment
  (Period column broken for Daily data in May).
- Order/income file-name drift (store aliases now handle: RVeet typo,
  Pediasure, Varna casing, and "Reckitt VN Chăm sóc cá nhân" = source file
  #16, the Veet + Reckitt combined store).

**Formula status:** every entry in `TIKTOK_FORMULA_STATUS`,
`SHOPEE_FORMULA_STATUS`, `LAZADA_FORMULA_STATUS` is `verified` (one caveat
preserved in-code: non-1.08 VAT is formula-ported but the 1.05 SKUs did not
trade in May). `PLACEHOLDER_FORMULAS = True` until the flip criteria are
agreed.

---

## 4. Open items / caveats

1. Verification covers ONE month (May 2026). The realistic future risk is a
   platform export-format change; ingest hard-stops loudly on unmapped
   headers when that happens.
2. Non-1.08 VAT paths and a non-empty Shopee 1.05/1.10 bucket have never
   been exercised on real trades.
3. The "return + 0dong" list is manually curated — our derived rules
   reproduced May exactly, but a hand-edited list can drift from any rule;
   the parallel run is the guard.
4. TikTok reimbursement lines (`unclassified`) are excluded from the invoice
   exactly as the team does, and surfaced in exceptions; where they book in
   D365 is finance's process, outside this tool.

---

## 5. Parallel-run cycle (operational plan)

- **Nu** downloads seller-center exports exactly as today, into the agreed
  window folder layout (one folder per settlement window — never mix
  windows' order exports).
- **Pipeline operator** (Abdul or Hoang) drops each window's files into
  `input/<period>/<platform>/…` and runs `tools/full_run.py` per
  platform/window as they land (TikTok 2×, Shopee 3×+2, Lazada 5× in the
  month).
- **Hoang** runs the existing Excel/PQ process unchanged in parallel.
- **Comparison**: the pipeline's `run_log.txt` per window reports per-store
  and grand ties against Hoang's Total/For-KA files (same references used
  throughout this report); `finance_file.xlsx` diffs cell-structure against
  the For KA file. Any variance beyond the team's own tolerances
  (12,000/2,000/1,000 TikTok · 2,000/10 Shopee · 1,000/2,000 Lazada) is
  investigated before booking; **finance continues to book from Hoang's
  output** until a full cycle passes clean.
- **Masters**: the team keeps maintaining `Lib & VAT rate.xlsb`
  (additive-only); the pipeline reads it live and reports drift against its
  snapshots each run.
- **Exit criterion**: one full clean cycle → flip `PLACEHOLDER_FORMULAS`
  (per the criteria to be agreed) → finance books from the pipeline's
  finance file, Excel chain retired to fallback.


---

<!-- ===== README.md ===== -->

# E-commerce Reconciliation Pipeline (Phase 1)

Replaces the post-download Excel chain (Total file → calculation file → finance file) with a
local Python pipeline. The team keeps downloading files from seller centers as today; this tool
ingests the raw files, applies classification and calculation rules, runs tie-out checks, and
outputs a D365-ready finance file plus an exception report.

**Success criterion:** for one full cycle, pipeline output matches the team's manually produced
finance file to the cent (within the existing ~10,000 VND tolerance rule for split rounding).
No process change until a parallel run passes.

## Status

| Piece | State |
|---|---|
| Folder/CLI skeleton, config loading | Built |
| Stage 1 ingest: multi-part glob, .xlsx/.csv, header mapping, dedupe, store-count hard stop | Built |
| Stage 2 stitch: order-creation-date attribution, unmatched → exceptions | Built |
| Stage 3 classify: OK / WRITTEN / ZERO_REVENUE + brand rules from config | Built |
| Stage 4 calculate: SKU-level explode + yellow-column formulas | **PORT ZONE — placeholder formulas** |
| Stage 5 tie-outs: 3 checks, 10,000 VND split tolerance | Built (meaningful once Stage 4 is ported) |
| Stage 6 export: finance file, exceptions (4 tabs), run log | Built (**layout is a stand-in** until a real finance file arrives) |
| Column maps / expected stores / store→brand map in `config/settings.yaml` | **Placeholders — fill from real exports** |
| Synthetic sample data + smoke test (`tools/`) | Built |
| Lazada, API pulls, D365 writes | Out of scope (Phases 2–3) |

**PORT ZONE** means: the code structure is real but the numbers are not. Per the build spec, the
yellow-column logic must be translated line-by-line from the team's actual calculation file and
verified against its output — not reinvented. Every PORT ZONE placeholder is marked in
`src/calculate.py` and `src/export.py`, and every run stamps a warning into `run_log.txt` until
`PLACEHOLDER_FORMULAS` is flipped off after verification.

## Setup (once)

1. Install Python 3.12 from https://www.python.org/downloads/ (or the portable/embeddable zip
   under `%LOCALAPPDATA%` if admin rights are an issue — same pattern as the portable Node on
   this machine).
2. `pip install -r requirements.txt`

## Run

```
python recon.py --period 2026-06_p1 --platform tiktok
python recon.py --period 2026-06_p1 --platform shopee
```

Inputs are read from `input/<period>/<platform>/{orders,income}/` — drop every downloaded file
part there, including the prior-month order re-pull (orders folder). Outputs land in
`output/<period>/<platform>/`:

- `finance_file.xlsx` — D365-ready; TikTok one Income tab, Shopee Income + Return tabs
- `exceptions.xlsx` — Unmatched Orders · Unknown SKUs · Tie-out Breaches · Zero Revenue
- `run_log.txt` — file inventory, per-stage counts, totals, check results (the audit trail)

A tie-out breach does **not** stop the run — it flags, it doesn't hide. A store-count mismatch
or unmappable required column **does** stop the run (exit code 1, no finance file).

Smoke test on synthetic data (also the "does my machine work" check):

```
python tools/smoke_test.py
```

## Build order (once sample files arrive)

1. [ ] Fill `config/settings.yaml` column maps + expected stores from a real TikTok export
       (one brand) → replicate Pivot Income totals → verify against the real Total file
2. [ ] Verify stitch + classify tagged counts against the team's file
3. [ ] Port calculation logic line-by-line (`src/calculate.py` PORT ZONE) → verify net revenue
       to the cent → flip `PLACEHOLDER_FORMULAS = False`
4. [ ] Match `finance_file.xlsx` layout to the real finance file (`src/export.py`) → diff
       pipeline output vs. the team's finance file
5. [ ] Repeat for Shopee (3×/month cadence, multi-part files, Return tab, negative-discount
       adjustment)
6. [ ] Full parallel run for one live cycle with Huong/Dashaini

## Open questions for Huong/Dashaini (collect as we build)

- Exact column meanings in the Vietnamese calculation file (they offered to translate — take
  them up on it)
- Complete list of brands needing separate invoices (Keoh is the known one — any others?)
  → `config/brand_rules.yaml`
- How new SKUs get added to the master mapping today → `config/sku_master.csv`
- Tolerance rule: is 10,000 VND per split or cumulative? → `tolerances` in settings.yaml
- VAT rate: 8% vs 10%, per category? → `vat_rate` in settings.yaml
- Number format in raw exports: `1,234,567.89` or `1.234.567,89`? → `number_style`
- TikTok returned orders: confirmed they need no Return tab (settlement already nets them)?
- Do raw exports carry a store/shop column, or is store implied by the download account?
  (ingest currently expects a store column per file)

## Config is the contract

- `config/brand_rules.yaml` — per-brand invoicing rules; new brands are added here, no code change
- `config/sku_master.csv` — SKU ID → name; unknown SKUs are flagged, never fatal
- `config/settings.yaml` — tolerances, expected stores, store→brand map, column maps, VAT,
  number style

Data folders (`input/`, `output/`) are gitignored — order data never goes into version control.


---

<!-- ===== EVALUATION_DOSSIER/PROJECT_OVERVIEW.md ===== -->

# Project Overview — E-commerce Marketplace Reconciliation Pipeline

**Audience:** independent evaluator. This dossier is deliberately honest about
what is not done and what is uncertain — see `CHALLENGES_AND_FINDINGS.md` and
`OPEN_QUESTIONS_FOR_EVALUATOR.md`. It is an evaluation pack, not a pitch.

## What this reconciliation is, and why it exists

ADA operates official brand storefronts on three Vietnamese marketplaces —
TikTok Shop, Shopee, and Lazada — on behalf of client brands (Unilever, Abbott,
Kao, Masan, Reckitt, and others). Every month, each platform pays out settled
revenue net of its own fees, subsidies, vouchers, returns, and adjustments.

Finance must rebuild, from the platforms' raw exports, the **true invoiceable
revenue per client brand**: which orders count, at what net price, net of which
discounts, at which VAT rate — so that:

1. clients are billed correctly (the "KA" — key account — invoice files),
2. revenue is booked in D365 in the right period, and
3. VAT is calculated on the right base at the right rate per SKU.

None of the three platforms hands this over directly. TikTok's settlement file
does not carry per-SKU invoice lines; Shopee's income export mixes orders,
returns, and zero-settlement rows; Lazada provides only a transaction ledger of
fee lines with no order rows at all. The reconciliation is the process of
turning those exports into invoice-grade numbers, per settlement window (there
are 12–14 windows per month across the platforms).

## Source documents (in the shared cloud folder, next to the data)

Two ADA documents predate the build and are the requirements baseline this
project should be evaluated against — read them FIRST:

- **"Ecommerce Invoicing flow 30_06_2026.docx"** — the as-is process
  walkthrough (the manual TikTok/Shopee/Lazada flow, step by step, incl. the
  Total-file Power Query mechanics, the yellow-column calculation file, the
  brand splits, and the three tie-out checks). Phase 1 of this build is a
  faithful automation of THAT process; the pipeline's stages map 1:1 onto its
  steps.
- **"Ecommerce_Invoicing_Architecture_and_Roadmap.docx"** — the as-is/to-be
  architecture note (root causes, target architecture, AI use-case backlog,
  platform evaluation). Useful context for judging whether Phase 1's scope
  cut was the right one and what Phases 2-3 were envisioned to be.

Evaluator note: the flow doc says "26 TikTok stores". The May data contained
17-18; July contained 25 (7 onboarded mid-period). The pipeline's store
roster is config, updated monthly with evidence — the "26" is the team's
round number, not what the data showed in any single window.

## The old manual process (what this replaces)

- A ~36 GB Excel/Power Query workbook chain; a single refresh took ~15 minutes
  and had to be repeated per window, per platform.
- Manual copy-paste across 3–4 linked files per window to produce the final
  invoicing file (the "For KA" / "KA used" workbooks).
- Manual entry of results into D365.
- The rules — which columns, which formulas, which tolerances, which rows are
  excluded — lived partly in Power Query M code, partly in worksheet formulas,
  and partly in individual team members' heads (tribal knowledge). Several of
  the workbook's own consistency checks are broken (dead `#REF!` references,
  verdict cells reading blank cells) and are silently ignored in practice; the
  evidence is in `CHALLENGES_AND_FINDINGS.md`.

## Phased approach — what is and is not built

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Post-download automation: given the raw exports the team already downloads from each seller center, produce the finance/invoicing files automatically, with the team's own checks computed and every rule explicit in code/config. | **Built and verified** (this repo). Three real months processed: May (row-level verification), June (external tie against the team's own outputs), July (independent run on unseen data, incl. producing findings the team confirmed). |
| **Phase 2** | Automated data extraction via the platforms' official seller APIs (no manual downloads). | **Not started.** Feasibility assessed only: all three platforms expose suitable finance/order APIs; requires seller-account owners to register developer apps. |
| **Phase 3** | Automated D365 posting of the reconciled results. | **Not started.** Not designed beyond the phase label. |

Also built beyond the original Phase-1 spec, in response to team feedback:
- Finance files in the team's own invoicing-template shape (PV sum / Summary /
  brand tabs / control-block verdicts), replacing an earlier plain format.
- A monthly cross-platform master summary workbook (by window, by brand, by
  storefront) with a storefront→client-brand mapping table for team review.

Planned next (agreed in principle, not yet built at dossier time): a processed
Parquet data layer so each month's raw exports are parsed once, and a
self-service reporting portal for finance on top of it.

## Ground rules the project was built under

- **Evidence-first**: every calculation rule was extracted from the team's own
  artifacts (Power Query M code, worksheet formulas, pivot structures) and then
  verified row-level against the team's own outputs — never assumed or
  invented. Where evidence was missing, the question went to the team
  (documented as TODO-HUMAN items) instead of being guessed.
- **A variance is a finding, not an error to force away.** The pipeline flags
  and reports; it does not bend numbers to tie.
- **No client data in the repo.** Code and config only. Raw exports, staged
  inputs, and outputs live outside version control (see `HOW_TO_RUN.md`).
- **`PLACEHOLDER_FORMULAS`** is a deliberate governance flag stating the
  numbers are not yet blessed for production booking. It is still `True`:
  verification criteria were met in July, but the formal flip (and the
  accompanying doc updates) awaits the owner's go-ahead.

## People context

- **Nu** — extraction + D365 booking.
- **Hoang** — owns the reconciliation files; answered the rule-confirmation
  questions (store aliases, invoice splits, dedup decisions).
- **Huong / Dashaini** — finance stakeholders.
- The pipeline was built by Abdul Ashraff with an AI coding assistant; every
  ported rule cites its evidence (file, sheet, cell) in code comments.


---

<!-- ===== EVALUATION_DOSSIER/WHAT_WAS_BUILT.md ===== -->

# What Was Built — Architecture

## Pipeline stages

```
raw exports (per settlement window, per platform)
   │  input/<YYYY-MM_wN|sN|lN>/<platform>/...   (never mixed across windows)
   ▼
1. INGEST      src/ingest.py      multi-part xlsx → typed frames; column maps
   │                              from config; store from filename; PII columns
   │                              never mapped; per-file row counts logged;
   │                              settlement-window bounds applied where a raw
   │                              export was proven mis-pulled
   ▼
2. STITCH      src/stitch.py      income lines matched to true order-creation
   │                              dates via order files; unmatched → exceptions
   ▼
3. CLASSIFY    src/classify.py    TikTok: Good / Partial Return / Total Return /
   │                              Payback (M-code port; unclassified = 3rd
   │                              bucket → exceptions). Shopee: ok / Return /
   │                              0 dong (derived + verified rules)
   ▼
4. CALCULATE   src/calculate.py   the "yellow columns": SKU explode, net
   │           src/lazada.py      revenue, pre-VAT back-out, per-SKU VAT.
   │                              Lazada has its own path (fee ledger → Price
   │                              KA lines)
   ▼
5. TIE-OUT     src/tieout.py      the team's OWN checks, ported with their
   │                              tolerances; breaches named with amounts,
   │                              never hidden
   ▼
6. EXPORT      src/finance_template.py   invoicing workbooks in the team's
                                  template shape (PV sum / Summary / brand
                                  tabs / control blocks). The earlier plain
                                  exporter (src/export_platforms.py) is
                                  SUPERSEDED and no longer wired to any run.
```

Driver: `tools/full_run.py --platform <p> --period <window>` runs one window
end-to-end and, when reference JSONs are supplied, ties per-store and grand
totals against team numbers. `tools/build_master_summary.py` aggregates the
generated files into the monthly cross-platform master (its column totals are
asserted against each source file; it refuses to ship on a mismatch).

## Config-as-rules

`config/settings.yaml` is the contract. Everything month-specific or
platform-specific that can drift lives there, with evidence comments:
column maps (incl. all historical header spellings), store-from-filename
regexes, store aliases (each non-obvious one carries its verification
evidence), expected/optional store rosters (new stores hard-stop the run until
a human confirms them), tolerances per platform (the team's own values, not
the spec's assumption), VAT default-plus-exceptions (`vat_factors.default` is
the single line to revert the 8%→10% tax concession), reader-engine overrides,
and settlement-window bounds.

Masters: the team keeps maintaining their own `Lib & VAT rate.xlsb`
(fee-name→bucket, SKU→VAT). The pipeline reads it LIVE via pyxlsb, falls back
to committed CSV snapshots, and reports any drift between the two on every
run. The team's ownership of the rules is preserved; the pipeline just stops
being a second, silently-divergent copy of them.

## The three platforms are genuinely different

| | TikTok | Shopee | Lazada |
|---|---|---|---|
| Raw inputs | order export (SKU rows) + income export (order rows) | order export + income export (3-row grouped header, 10k-row split sheets) | transaction LEDGER only (fee lines; no order rows) |
| Revenue rebuild | **order-side rebuild**: SKU lines from the order file joined to OK income orders; income is the CHECK, not the source | income-side amounts with **proportional discount allocation** (Z=(T/X)·Y) across SKU lines | **Price KA** = round((credits + matched promo)/units/VAT) per (order, SKU, product name) |
| Status logic | M-code port: OK=Good; take-out={Total Return, Payback, Partial}; unclassified→exceptions | derived + verified: Return (refund≠0), 0 dong (settlement≤0 ∧ refund=0), ok | fee-name→bucket via the team's Lib master; refunds net inside the ledger |
| VAT | all 1.08 (the 1.05/1.10 template cells are dead — verified) | per-SKU exceptions exist (live in July: none traded in May) | per-SKU via VAT_SKU master; first live non-1.08 SKUs |
| Splits | invoice buckets KAO / Merries / Others | brand tabs Curel / KAO / Merries / Kate / Others; Xmen & Kao batch files | brand tabs Curel / KAO / Merries / Others |
| Quirks | dd/mm dates; description row under header; no store column (filename); multi-settlement orders | byte-identical duplicate rows are LEGITIMATE (gift SKUs — dedupe is forbidden); Return tab 10-VND full/partial split | Weekly AND Daily export schemas (both permanent); order-less revenue rows (compensations) handled as named reconciling items |

## How rules were derived (evidence-first)

- **Power Query M code** was extracted from the team's Total files' embedded
  DataMashup (UTF-16 customXml → base64 → length-framed inner zip →
  `Formulas/Section1.m`) and ported statement-by-statement. This is the source
  of the TikTok classification, the column removals (incl. PII columns), and
  the null→"Good" replacement rule.
- **Worksheet formulas** in the intermediary "Xuất HĐ" sheets were read
  formula-by-formula (header row 3, formulas from row 4) and ported as the
  calculation chain; each column in `src/calculate.py` cites its source cell.
- **Per-formula status dicts** (`TIKTOK_FORMULA_STATUS` etc.) record when each
  column was row-verified and by which harness; nothing was marked verified
  without a row-level match against the team's own file.
- Where the team's artifacts conflicted or were silent, the question was
  escalated (all TODO-HUMAN items were answered by Hoang and folded back into
  config with the answer documented).


---

<!-- ===== EVALUATION_DOSSIER/VERIFICATION_RECORD.md ===== -->

# Verification Record

Three months of real data, three escalating verification modes. Nothing below
was verified by "looks right" — every claim names its comparison target.
Re-run instructions are in `HOW_TO_RUN.md`; the harnesses are
`tools/calc_verify*.py` (row-level) and `tools/full_run.py --refs` (totals).

## Methodology

1. **Row-level porting proof (May).** For each platform, the pipeline's
   per-row calculated columns were compared against the team's own
   intermediary workbooks ("Xuất HĐ" sheets) for the same window — every
   column, every row, exact match required. Stores were chosen to cover the
   cleanest AND messiest cases (multi-SKU stress, returns at scale,
   only-zero-settlement stores).
2. **External tie (June).** The pipeline ran on unseen June data; its totals
   were tied against the TEAM'S OWN month-end Total/tong hop files — an
   independent output produced by their manual process, not by us.
3. **Independent run (July).** The pipeline ran first, before any team
   output existed; its internal checks and coverage analyses produced
   findings that were then confirmed against the team (see
   `CHALLENGES_AND_FINDINGS.md`).

Tolerances are the team's own, read from their formulas, not the build spec's
assumption: TikTok 12,000 / 2,000 / 1,000 VND; Shopee 2,000 / 2,000 / 10 VND
(plus their side-block 10,000); Lazada 1,000 / 2,000 VND.

## May 2026 — row-level verification (~288K rows)

- **TikTok**: four stores × both windows row-verified — U food, Unilever
  Homecare (messiest, 73,689 + 62,177 rows), KAO (6,487 Total Returns at
  scale), AHC (4.9 SKU lines/order) — ~236K rows, ALL columns exact; take-out
  and OK-good pivots tie exactly. VAT question resolved with evidence: all
  TikTok = 1.08; the template's 1.05/1.10 cells are dead (one multiplies an
  empty cell, one double-VATs inside a `#REF!` block).
- **Shopee**: 2 stores × 3 windows (~51K rows) row-exact, including the
  only-zero-settlement store; classification rules derived then verified
  against the team's manual XLOOKUP list.
- **Lazada**: Curel + Unilever-2 across Weekly AND Daily schema windows,
  1,306 lines + every fee bucket exact; Price KA formula reproduced to the
  đồng including gift-line zero-outs and promo netting.
- **Full-platform runs**: every window of every platform tied per-store and
  grand against team references — TikTok 2/2, Shopee 5/5 (incl. Xmen/Kao
  sub-batches), Lazada 5/5.

## June 2026 — external tie against the team's own outputs

- **Lazada**: all windows tie to the VND (one window off by 2 VND rounding).
- **TikTok**: window 2 grand total ties EXACTLY (53,207,809,124; diff 0).
  Window 1 gap decomposes exactly into two named exclusions: the Abbott
  Pediasure Vietnamese-export file (665,246,193 — set aside pending a
  VN-header mapping, confirmed out of the team's total too) and Kate
  (133,548,861 — ties against their separate Kate file).
- **Shopee**: order-level reconciliation of **427,917 / 427,917 orders =
  100.0000% to the VND** across all three windows, zero unexplained, after
  deriving the team's June "Net revenue" formula from their own file. The
  only exclusions are two named, team-confirmed items: 676 orders the team's
  own file missed (their missed download — confirmed by Hoang) and 258
  orders outside our window in their long-spanning xa_kho file.
- Along the way, the team's period convention was DERIVED from their six
  month-tag files (Finance_Month = newest order-creation cohort per
  settlement window) rather than guessed — an explicit hold was kept until
  the derivation was evidence-backed.

## July 2026 — independent run on unseen data (14 windows)

- All 14 windows generated (TikTok 5 weekly + Shopee 4 + Lazada 5); every
  TikTok window passes all three ported checks with variance 0.00.
- **Coverage proof**: on the settlement axis, every July day is covered
  exactly once per platform; Shopee's new 21-28/29-31 split partitions
  perfectly (zero shared orders across 507,904).
- **The material catch**: TikTok's 08-14 raw export was mis-pulled and also
  contained the entire 7-July settlement block — 18,352 orders /
  5,973,070,353 VND (3.98% of TikTok July) would have been double-invoiced.
  Detected by cross-window order-ID analysis, root-caused to the pull (May
  baseline: 220 overlaps = normal multi-payout tail; July w1∩w2: 24,851 with
  23,427 byte-identical), fixed as a config-declared settlement boundary
  with per-day drop logging, and verified back to 0 VND cross-window
  duplication. Genuine second-payout rows were preserved (1,180 rows).
- July totals (with VAT): TikTok 144,001,357,134 · Shopee 105,531,649,733 ·
  Lazada 9,176,322,882.
- The template-shaped export was itself verified against the team's real May
  invoicing files: identical tab sets, grand totals tie exactly, brand-tab
  totals tie or land within a few VND (different rounding path), Shopee's
  partial-return total equals their `return!Q5` exactly (35,572,127), and
  the pivot rounding drift matches theirs (ours 3,810/5,183 vs theirs
  3,836/5,193 at tolerances 12,000/10,000).

## What verification does NOT cover (honest limits)

- Only three months of data; rules that are month-shape-dependent (window
  labelling, file naming) have needed maintenance EVERY month so far.
- Non-1.08 VAT paths are formula-ported and structurally tested but had no
  live rows until July's small Lazada cases; TikTok/Shopee non-1.08 remains
  live-unexercised.
- The July outputs have NOT been externally tied against team month-end
  files (none existed at run time) — that comparison is open until the team
  produces their July totals.
- `PLACEHOLDER_FORMULAS = True` still: the production-booking blessing is a
  human decision that has not been formally given.


---

<!-- ===== EVALUATION_DOSSIER/CHALLENGES_AND_FINDINGS.md ===== -->

# Challenges and Findings — the honest hard parts

## Format drift the pipeline had to absorb (every month, so far)

| Month | Drift | Handling |
|---|---|---|
| June | TikTok order xlsx with broken `<dimension>` tags (unreadable by openpyxl even with resets) | switched reader engine to python-calamine, config-selectable per platform/kind |
| June | Income headers renamed ("Order\adjustment ID" → "Order/Adjustment ID", "Type" → "Transaction type") | all spellings kept as parallel column-map entries |
| June | Abbott Pediasure window exported with Vietnamese-localized headers | set aside honestly (excluded from totals, named in the tie), pending a VN-header mapping — NOT silently parsed |
| June | Shopee big-3 stores auto-split across batch files with overlap | overlap measured order-level first, then Hoang confirmed the split; Kao deduped by order ID = exactly 2,237,171,140 VND |
| July | Weekly TikTok cadence, 3 new filename suffix styles, one file with no separator before the date token | filename regex extended, with the rule that store names must never be truncated |
| July | 7 stores onboarded; the 29-31 window renamed most stores (incl. typos "Reckit", "Gluverna") | store roster guard hard-stopped (by design); aliases added only after order-ID-overlap proof (100% / 99.8%); Curel proven genuinely NEW (zero overlap), not aliased |
| July | 9 Shopee "part 2" income exports with no revenue sheet | each self-declares total 0 in its own Summary → removed from staging only, raw dump untouched |
| July | Lazada window exported in the Daily schema under a weekly-named folder | dual-schema support already existed; staged to Daily/ |
| July | Order-less Lazada revenue rows ("Lost & Damaged FBL Inventories" compensation, 27.97M + 9.14M VND) mapped by the team's own Lib to the revenue bucket but with no order/SKU | surfaced as a NAMED reconciling row in the control blocks; kept in the sale-report figure; never silently dropped (the old plain export had been silently zeroing them) |

## Findings handed to the team (defects in their process/files, evidence-backed)

1. **Missed income file (June, Shopee)**: the team's 01-10 consolidation
   contains zero rows for the Reckitt Sức Khỏe Sắc Đẹp store — 676 orders /
   74,230,000 VND gross present in the raw download and in their own 11-20 and
   21-30 files. Verified on our side: the pipeline sourced those orders from
   the income file (919 order-file-only IDs were correctly ignored). Hoang
   confirmed: their missed download.
2. **Broken template checks**: TikTok's line-tab control block sums a `#REF!`
   and multiplies the KAO bucket by 1.10; Shopee's PV-sum verdict reads a
   blank cell (always "OK"); both platforms' "PV xuat HD" checks compare
   mismatched quantities and permanently fail — all silently ignored in
   practice. The team's genuinely working checks are PV sum (TikTok),
   the side block (Shopee), and Summary (Lazada); the rebuilt template
   computes every block from the engine.
3. **Hard-coded range bug (Lazada, May)**: the team's l2 file's own SUBTOTAL
   range is too short and under-counts their own sheet by 45,258,030 VND.
4. **Stale stamps**: the Lazada template's 1.05/1.10 tabs carried January
   labels ("Laz T12", "8T1 to 14T1") into May files.
5. **The July w2 double-pull**: the 08-14 TikTok export also contained all of
   7 July (5.97B VND double-count risk). See VERIFICATION_RECORD.md — this is
   the single most material catch to date.

## Open items at dossier time (nothing hidden)

- **`PLACEHOLDER_FORMULAS = True`** — verification criteria met; the formal
  flip + doc updates await the owner's explicit go. Note: the flag gates only
  a warning line; the real safeguards are the per-formula status dicts and
  tie-outs.
- **July external tie not yet possible** — the team's July month-end files
  did not exist at run time.
- **Format sign-off**: the team's feedback asked for the invoicing-template
  shape (now the only export path) and a monthly master; the July re-issue in
  that shape was in progress as this dossier was written. Formal sign-off
  from Nu on the template details is still pending.
- **Brand mapping needs team confirmation**: `config/brand_map.csv` maps
  storefronts → client brands with confidence flags; the needs-confirmation
  rows (three Reckitt storefronts, Lazada "lactacyd" → Sanofi) await the team.
- **Durability gaps expected to bite in August** (identified, mostly not yet
  fixed):
  1. no automated cross-window overlap check in the run path (the July
     double-pull was caught by ad-hoc analysis; the settlement-bounds
     mechanism exists but detection is manual);
  2. Lazada schema detected by folder name, not file content (July l5
     required a manual restage);
  3. empty Summary-only Shopee exports hard-stop the run (July needed manual
     staging removal ×10);
  4. staging is a hand-written per-month script (zip layouts differ monthly);
  5. no regression tests over `apply_settlement_bounds` and the template
     exporter;
  6. new stores hard-stop until a human edits the roster (deliberate, but a
     monthly cost).
- **Phase 2 (API extraction) and Phase 3 (D365 posting)**: not started.
- **Processed data layer (Parquet) and finance self-service portal**: agreed
  direction, not yet built at dossier time.


---

<!-- ===== EVALUATION_DOSSIER/HOW_TO_RUN.md ===== -->

# How to Run

## Setup (once)

- Windows or any OS with Python 3.12+.
- Dependencies: `pip install pandas openpyxl python-calamine pyxlsb pyyaml numpy`
- No API keys, no internet access, no LLM at runtime — the pipeline is plain
  deterministic Python.

## Step 1 — zero-data plumbing check (do this first)

```
python tools/smoke_test.py
```

Generates synthetic sample data, runs the legacy single-platform driver end
to end for TikTok and Shopee, and asserts the outputs contain what the
baked-in anomalies predict. Requires NO real data. Note: the smoke test
exercises the original scaffold path (`recon.py`); the production path is
`tools/full_run.py` (next step). If the smoke test passes, your environment
is fine.

## Step 2 — real data

The repo is data-free by design. The real May/June/July source data (raw
platform exports, staged per-window inputs) and the generated outputs live
in the shared cloud folder:

    https://adaglobal-my.sharepoint.com/shared?id=%2Fpersonal%2Fklara%5Fgrintal%5Fadaglobal%5Fcom%2FDocuments%2FCustomer%20Experience%20Team%20Folder%2FADA%20Agent%20Initiatives%2FInternal%20Use%20Cases%2FFinance%2FEcommerce%20Invoicing%20and%20Reconcilation
    (ADA SharePoint — "Ecommerce Invoicing and Reconcilation" under the
    Customer Experience Team folder; ask Abdul for access if the link 403s)

Layout expected by the pipeline (already staged in the cloud copy):

```
input/<window>/<platform>/...       e.g. input/2026-07_w1/tiktok/{orders,income}/
                                         input/2026-07_l2/lazada/{Weekly|Daily}/
output/<window>/<platform>/         finance_file.xlsx + run_log.txt per window
```

Copy (or point the repo at) `input/`, then run one window end to end:

```
python tools/full_run.py --platform tiktok --period 2026-07_w1
python tools/full_run.py --platform shopee --period 2026-07_s1
python tools/full_run.py --platform lazada --period 2026-07_l2
```

Each run writes `output/<window>/<platform>/finance_file.xlsx` (the team's
invoicing-template shape) and a `run_log.txt` with every ingest count, check,
and warning. Exit code 1 with only "no team reference found" lines means the
run succeeded but had no reference JSON to tie against — supply one via
`--refs <json>` to tie per-store/grand totals (shape documented in the
script's docstring).

Monthly master (after all windows of a month exist):

```
python tools/build_master_summary.py --month 2026-07 --out "<path>.xlsx"
```

Row-level verification harnesses (what the "verified" claims rest on):
`tools/calc_verify.py` (TikTok), `tools/calc_verify_shopee.py`,
`tools/calc_verify_lazada.py` — each compares pipeline rows against a team
intermediary workbook; see their docstrings for inputs.

## Repo map

```
config/settings.yaml         THE rules contract (column maps, aliases, rosters,
                             tolerances, VAT, window bounds) — evidence in comments
config/Lib & VAT rate.xlsb   team-owned live master (fee buckets, per-SKU VAT)
config/lazada_*.csv          committed snapshots of the master (fallback + drift check)
config/brand_map.csv         storefront -> client brand (confidence-flagged, team-reviewable)
src/ingest.py                reading, typing, aliasing, window bounds
src/stitch.py                income -> order-date attribution
src/classify.py              TikTok + Shopee status rules (ported, cited)
src/calculate.py             calculation chains + PLACEHOLDER_FORMULAS governance flag
src/lazada.py                Lazada ledger path (fee buckets, VAT, Price KA)
src/masters.py               live xlsb read + snapshot drift report
src/tieout.py                the team's own checks, their tolerances
src/finance_template.py      the ONLY deliverable exporter (team template shape)
src/export_platforms.py      SUPERSEDED plain exporter (kept as layout evidence)
recon.py                     legacy scaffold driver (smoke test only)
tools/full_run.py            production driver (one window end to end)
tools/build_master_summary.py  monthly cross-platform master
tools/calc_verify*.py        row-level verification harnesses
tools/smoke_test.py          synthetic end-to-end test (no data needed)
tools/verify_july_aliases.py order-ID-overlap evidence for July store aliases
HANDOFF.md                   original reviewer tour
COMPLETION_REPORT.md         Phase-1 closing report (May state)
REVIEW_PACKAGE.md            all source inline in one document (May state)
EVALUATION_DOSSIER/          this pack
```

Suggested evaluation order: smoke test → read `config/settings.yaml` top to
bottom (it is the distilled rule set) → run one small window (Lazada l1 is
fastest) against the cloud data → pick one verified claim from
VERIFICATION_RECORD.md and independently re-verify it with the matching
harness → then read `OPEN_QUESTIONS_FOR_EVALUATOR.md` and go hunting.


---

<!-- ===== EVALUATION_DOSSIER/OPEN_QUESTIONS_FOR_EVALUATOR.md ===== -->

# Open Questions for the Evaluator

You are being asked for a critical, independent evaluation. The claims in
this dossier are evidence-backed but were produced by the same people who
built the tool — your job is to try to break them. Candid pointers on where
to push:

## 1. Is the evidence-first derivation actually sound?

- The calculation rules were ported from the team's M code and worksheet
  formulas, then verified against the team's own outputs. That proves
  **faithful reproduction of the team's process** — it does NOT prove the
  team's process is correct. Row-verification against a workbook whose own
  checks are broken (see CHALLENGES_AND_FINDINGS §2) inherits any systematic
  error the team makes. Question: is there any independent ground truth
  (platform statements? bank settlements? client acceptances?) the numbers
  could be tied to, beyond the team's own files?
- The Shopee status rules ("Return" / "0 dong" / "ok") were DERIVED from
  data patterns and verified against one manual XLOOKUP list. Would a rare
  fourth case (e.g. partial refund with positive settlement) classify
  correctly? Try to construct one from the raw data.
- The June Finance_Month convention was derived from six file tags. Is six
  data points enough? What happens in a month with an unusual settlement lag?

## 2. Which rules might not generalize beyond the three months tested?

- **Filename parsing**: the store-from-filename regexes have needed extension
  every single month. The store-name capture is only as safe as the suffix
  alternatives; a store whose name ends in a number-like token could be
  truncated. Audit the regex against hostile-but-plausible names.
- **Window labels/boundaries**: the settlement-bounds mechanism is
  config-per-window, added reactively when July's w2 was mis-pulled.
  Detection of NEW boundary overlaps is not yet automated — if August's
  exports overlap on a different day, nothing in the run path will catch it
  (identified gap, unfixed at dossier time).
- **VAT**: default-plus-exceptions rests on the team's VAT_SKU master being
  complete. Non-1.08 lines have barely been exercised live. What happens if
  a new SKU with 1.05 VAT trades before the master is updated? (Answer in
  code: silently defaults to 1.08 — is that acceptable?)
- **Brand buckets** (KAO/Merries/Others etc.) are substring matches on store
  names. A future store named e.g. "Kao Beauty Partner" would be swept into
  the KAO invoice bucket without warning.
- **Order-less revenue** (Lazada compensations) is handled; are there TikTok/
  Shopee analogues (platform compensations inside income exports) that would
  currently land in a classification bucket silently?

## 3. Where is the tool most fragile?

- **Monthly staging is manual** — a hand-adapted script per month. The most
  likely failure mode is human mis-staging (a file in the wrong window), and
  the defenses (store roster, coverage checks) are only partial.
- **Excel parsing edge cases**: three reader engines/configs exist because
  real exports were malformed in three different ways. The next malformed
  file may fail in a fourth way; the pipeline's posture is hard-stop rather
  than guess, which is safe but blocks the run.
- **In-process assumptions**: one machine, one operator, no tests around the
  newest code (settlement bounds, template exporter). A refactor could break
  the control-block arithmetic without anything failing loudly. (The
  template's checks would drift — would anyone notice a plausible-looking
  wrong verdict?)
- **The team's own template semantics**: the rebuilt invoicing workbook
  reproduces rounded-price pivot semantics (invoice view) vs exact line
  semantics deliberately. Confirm with finance that this distinction matches
  how they actually invoice clients — a wrong assumption here changes client
  bills by small amounts at scale.

## 4. Process questions worth asking the humans

- Who reviews the run logs each month? A pipeline that flags honestly is only
  as good as the person reading the flags.
- What is the sign-off protocol for flipping `PLACEHOLDER_FORMULAS` and for
  booking from these files instead of the manual chain?
- Is there a rollback story if a generated file is later found wrong after
  invoicing?
- The three Reckitt storefronts and the Lazada "lactacyd" mapping await
  business confirmation — who owns that decision?

## 5. Suggested falsification exercises

1. Pick 20 random orders from a raw July export; hand-compute their invoice
   lines from the documented rules; compare to the generated file.
2. Deliberately mis-stage one file (wrong window) and observe whether any
   guard catches it.
3. Feed the pipeline a copy of a window where you have edited one amount by
   1,000,000 VND; verify which check catches it and how it is reported.
4. Re-run the same window twice; confirm outputs are byte-identical
   (determinism claim).
5. Take the team's original May invoicing file and the pipeline's template
   re-issue of the same window; diff them cell-region by cell-region and
   satisfy yourself that every difference is one of the documented,
   deliberate ones.


# PART B — SOURCE


---

## `recon.py`

```python
"""E-commerce reconciliation pipeline — Phase 1.

Usage:
    python recon.py --period 2026-06_p1 --platform tiktok

Reads  input/<period>/<platform>/{orders,income}/
Writes output/<period>/<platform>/{finance_file.xlsx, exceptions.xlsx, run_log.txt}
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src import calculate, classify, config, export, ingest, stitch, tieout
from src.classify import STATUS_OK, STATUS_ZERO
from src.errors import ReconHardStop
from src.runlog import RunLog

ROOT = Path(__file__).resolve().parent


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="E-commerce reconciliation pipeline (Phase 1)")
    p.add_argument("--period", required=True, help="Reconciliation period folder, e.g. 2026-06_p1")
    p.add_argument("--platform", required=True, choices=["tiktok", "shopee"],
                   help="Platform to reconcile (Lazada arrives after TikTok + Shopee are verified)")
    p.add_argument("--input-root", default=str(ROOT / "input"))
    p.add_argument("--output-root", default=str(ROOT / "output"))
    p.add_argument("--config-dir", default=str(ROOT / "config"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log = RunLog()

    input_dir = Path(args.input_root) / args.period / args.platform
    output_dir = Path(args.output_root) / args.period / args.platform
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        config_dir = Path(args.config_dir)
        settings = config.load_settings(config_dir)
        brand_rules = config.load_brand_rules(config_dir)
        sku_master = config.load_sku_master(config_dir, log)

        log.section(f"STAGE 1 - INGEST - {args.platform} - {args.period}")
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, args.platform, "orders"),
                                   "orders", settings, log, args.platform)
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, args.platform, "income"),
                                   "income", settings, log, args.platform)
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        ingest.check_stores(orders, "orders", args.platform, settings, log)
        ingest.check_stores(income, "income", args.platform, settings, log)

        log.section("STAGE 2 - CROSS-PERIOD STITCH")
        income, unmatched = stitch.stitch(income, orders, log)

        log.section("STAGE 3 - CLASSIFY")
        income = classify.classify(income, brand_rules, log)

        log.section("STAGE 4 - CALCULATE")
        sku_level, unknown_skus = calculate.explode_to_sku(income, orders, sku_master, log)
        sku_level = calculate.compute_sku_columns(sku_level, float(settings.get("vat_rate", 0.08)), log)
        returns = calculate.build_return_lines(income, log)

        log.section("STAGE 6 - EXPORT (finance file first, so Check 3 ties against it)")
        finance_income_total, finance_return_total = export.write_finance_file(
            output_dir / "finance_file.xlsx", args.platform, sku_level, returns, log)

        log.section("STAGE 5 - TIE-OUT CHECKS")
        income_ok = income[income["status"] == STATUS_OK]
        checks = tieout.run_checks(income_ok, sku_level, finance_income_total, finance_return_total,
                                   settings, log)
        breaches = checks[checks["result"] == "BREACH"]

        exceptions = {
            "unmatched_orders": unmatched,
            "unknown_skus": unknown_skus,
            "tieout_breaches": breaches,
            "zero_revenue": income[income["status"] == STATUS_ZERO],
        }
        log.section("EXCEPTIONS")
        total_exceptions = export.write_exceptions_file(output_dir / "exceptions.xlsx", exceptions, log)

        log.section("SUMMARY")
        log.add(f"  finance file : {output_dir / 'finance_file.xlsx'}")
        log.add(f"  exceptions   : {output_dir / 'exceptions.xlsx'} ({total_exceptions} row(s))")
        log.add(f"  tie-out      : {len(checks) - len(breaches)}/{len(checks)} checks passed")
        log.write(output_dir / "run_log.txt")
        return 0

    except ReconHardStop as stop:
        log.section("HARD STOP — no finance file produced")
        log.add(str(stop))
        log.write(output_dir / "run_log.txt")
        return 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `src/__init__.py`

```python

```


---

## `src/calculate.py`

```python
"""Stage 4 — Calculate.

Explodes order-level income lines to SKU level via the order file (the
VLOOKUP the Power Query does today), then applies the yellow-column logic
from the team's calculation file.

=============================================================================
PORT ZONE
Everything between compute_sku_columns() and the end of this file must be
translated LINE-BY-LINE from the team's actual calculation file and verified
against its output — not reinvented. The bodies below are structural
placeholders (proportional allocation, flat VAT back-out) so the pipeline
runs end-to-end before the real file arrives. While PLACEHOLDER_FORMULAS is
True, every run stamps a warning into run_log.txt.
=============================================================================
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .classify import STATUS_OK, STATUS_WRITTEN
from .runlog import RunLog

PLACEHOLDER_FORMULAS = True

# Per-formula verification state for the TikTok chain ported from the team's
# intermediary file "Tiktok result Sample T5 - 1 to 17T5.xlsx", sheet
# "Xuat HĐ" (header row 3, formulas read from row 4). Flip an entry to
# "verified" ONLY after tools/calc_verify.py shows a row-by-row match against
# the team's file for that column. PLACEHOLDER_FORMULAS above stays True
# until every entry is verified AND the 1.05/1.10 VAT buckets are resolved.
TIKTOK_FORMULA_STATUS = {
    # Verified 2026-07-16 by tools/calc_verify.py: U food, May 2026, both
    # windows — 25/25 and 124/124 rows matched the team's intermediary file
    # ("Xuat HĐ") exactly; classification aggregates matched the Total files'
    # pivots to the VND. Verified means: for U food, one month, one store.
    # join income->order SKU lines on Order ID; SKU identity = Seller SKU
    # (evidence: "Xuat HĐ" col H header "Seller SKU"; col C join key)
    "sku_explode_join": "verified",
    "gross_rev": "verified",              # L4 = J4*K4  (unit original price x qty)
    "net_after_seller_discount": "verified",  # M4 = (J4*K4)-N4  (minus SKU Seller Discount)
    "order_gross_sale": "verified",       # P4 = SUMIF($C:$C, C4, $M:$M)
    "unit_price_pre_vat": "verified",     # R4 = (M4/K4)/Q4  (Q = VAT factor, 1.08)
    "amount_pre_vat": "verified",         # T4 = R4*S4  (S = qty)
    "amount_with_vat": "verified",        # U4 = T4*Q4
    "order_revenue_check": "verified",    # V4 = SUMIF(C:C, E4, U:U), W4 = V4-F4 (per-order semantics)
    # Classification (M code port, classify.py): all four branches exercised
    # and verified — U food (Good, Total Return) row-level; Unilever Homecare
    # (adds Partial Return + Payback_Order) via exact take-out pivot equality
    # in both windows. Final_Status rule: take out = not Good.
    "check_status": "verified",
    "final_status_take_out": "verified",
}


def explode_to_sku(
    income: pd.DataFrame, orders: pd.DataFrame, sku_master: dict[str, str], log: RunLog
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Income has one line per order; orders have one line per SKU.
    Returns (sku-level frame for OK lines, unknown-SKU exception frame)."""
    lines = orders[["order_id", "sku_id", "sku_name", "quantity", "unit_price_gross"]].copy()
    lines["quantity"] = lines["quantity"].fillna(0)
    lines["line_gross"] = lines["quantity"] * lines["unit_price_gross"].fillna(0)

    order_total = lines.groupby("order_id")["line_gross"].transform("sum")
    line_count = lines.groupby("order_id")["order_id"].transform("size")
    # Allocation weight per SKU line: share of order gross; equal split when
    # the order gross is zero (free items / vouchers covering full price).
    lines["weight"] = np.where(order_total > 0, lines["line_gross"] / order_total, 1.0 / line_count)

    ok = income[income["status"] == STATUS_OK]
    sku_level = ok.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(ok)} OK income lines exploded to {len(sku_level)} SKU lines")

    known = sku_level["sku_id"].astype(str).str.strip().isin(sku_master.keys())
    unknown = sku_level.loc[~known, ["order_id", "store", "brand", "sku_id", "sku_name"]].drop_duplicates()
    if len(unknown):
        log.warn(f"{len(unknown)} SKU line(s) not in sku_master.csv (-> exceptions, run continues)")
    return sku_level, unknown


def compute_sku_columns(sku_level: pd.DataFrame, vat_rate: float, log: RunLog) -> pd.DataFrame:
    """PORT ZONE — the yellow columns. Placeholder formulas:

    gross_revenue_sku   = order gross revenue × allocation weight
    discount_sku        = (order gross − order net) × allocation weight
    net_revenue_sku     = order net revenue × allocation weight
    net_pre_vat_sku     = net_revenue_sku ÷ (1 + vat_rate)
    vat_sku             = net_revenue_sku − net_pre_vat_sku

    Each of these must be replaced by the exact formula from the team's
    calculation file and ticked off against the real file's output.
    """
    df = sku_level.copy()
    df["gross_revenue_sku"] = df["gross_revenue"].fillna(0) * df["weight"]
    df["discount_sku"] = (df["gross_revenue"].fillna(0) - df["net_revenue"].fillna(0)) * df["weight"]
    df["net_revenue_sku"] = df["net_revenue"].fillna(0) * df["weight"]
    df["net_pre_vat_sku"] = df["net_revenue_sku"] / (1 + vat_rate)
    df["vat_sku"] = df["net_revenue_sku"] - df["net_pre_vat_sku"]

    if PLACEHOLDER_FORMULAS:
        log.warn(
            "Calculation formulas are PLACEHOLDERS (src/calculate.py PORT ZONE). "
            "Numbers are NOT finance-grade until ported from the real calculation file."
        )
    return df


def explode_to_sku_tiktok(income_ok: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """TikTok SKU explode, ported from 'Xuat HĐ': order SKU lines grouped by
    (Order ID, Seller SKU, Product Name, unit price) with Quantity and
    SKU Seller Discount summed, then joined to OK income orders on Order ID.
    Order-level income amounts repeat on every SKU line (no allocation —
    the team rebuilds revenue from the order side instead)."""
    lines = orders.groupby(
        ["order_id", "sku_id", "sku_name", "unit_price_gross"], as_index=False, dropna=False
    ).agg(quantity=("quantity", "sum"), sku_seller_discount=("sku_seller_discount", "sum"))

    sku_level = income_ok.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(income_ok)} OK income orders exploded to {len(sku_level)} SKU lines")
    return sku_level


def compute_sku_columns_tiktok(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The yellow columns, ported formula-by-formula from 'Xuat HĐ' row 4
    (see TIKTOK_FORMULA_STATUS for the cell evidence per column)."""
    df = sku_level.copy()
    # Default-plus-exceptions VAT (confirmed model): one default factor plus
    # per-SKU exceptions from the team's master file. Reverting the 8% tax
    # concession to 10% is the single vat_factors.default config line.
    from .masters import vat_factor_for
    df["vat_factor"] = vat_factor_for(df["sku_id"], settings, settings.get("_vat_sku") or {})  # Q

    qty = df["quantity"].fillna(0)
    df["gross_rev"] = df["unit_price_gross"].fillna(0) * qty                        # L = J*K
    df["net_after_seller_discount"] = df["gross_rev"] - df["sku_seller_discount"].fillna(0)  # M = (J*K)-N
    df["order_gross_sale"] = df.groupby("order_id")["net_after_seller_discount"].transform("sum")  # P
    df["unit_price_pre_vat"] = (df["net_after_seller_discount"] / qty.replace(0, pd.NA)) / df["vat_factor"]  # R
    df["amount_pre_vat"] = df["unit_price_pre_vat"] * qty                           # T = R*S
    df["amount_with_vat"] = df["amount_pre_vat"] * df["vat_factor"]                 # U = T*Q
    # The team computes V/W only on the FIRST row of each order (their SUMIF
    # keys on the blank-on-repeat "non repeat" order-id column E; D/E/F are
    # blank on 2nd+ SKU rows of an order — intermediary rows 38/39 evidence).
    # The pipeline carries the order-level value on every row instead;
    # per-order semantics are identical and verified by calc_verify.py.
    df["order_revenue_check"] = df.groupby("order_id")["amount_with_vat"].transform("sum")  # V
    df["order_check_diff"] = df["order_revenue_check"] - df["subtotal_after_seller_discounts"].fillna(0)  # W = V-F

    pending = [k for k, v in TIKTOK_FORMULA_STATUS.items() if v != "verified"]
    if pending:
        log.warn(f"TikTok formulas not yet row-verified: {pending}")
    return df


# =============================================================================
# SHOPEE PORT ZONE — chain ported from the Shopee intermediary "Xuat HĐ"
# (shopee result sample 01 to 10T05.xlsx, header row 3, formulas row 4).
# Each entry flips to "verified" only after tools/calc_verify_shopee.py shows
# a row-by-row match against the team's file for that column.
# =============================================================================
# Verified 2026-07-17 by tools/calc_verify_shopee.py, May 2026 all three
# windows: nutifoodgpddvietnam (cleanest, 55 rows) and Sanofi (messiest,
# 51,159 rows — the only store with 0-dong orders) — every column and the
# status tag row-exact against the team's "Xuat HĐ". Caveats: 0-dong only
# occurs at Sanofi; the 1.05/1.10 VAT buckets were EMPTY in May (all rows
# 1.08) so non-default VAT is still unexercised, as are the Xmen/Kao
# sub-batch files.
SHOPEE_FORMULA_STATUS = {
    "classification_return_0dong": "verified",  # derived rules (see classify.py)
    "sku_explode_join": "verified",     # join on Mã đơn hàng; SKU = "SKU phân loại hàng"
    "gross_rev": "verified",            # S4 = Q4*R4 (Giá gốc x Số lượng)
    "net_after_discount": "verified",   # T4 = (Q4*R4)-U4 (minus Người bán trợ giá)
    "total_discount": "verified",       # W4 = K4+L4+M4+H4 (voucher+ship support+coins+co-fund)
    "order_gross_sale": "verified",     # X4 = SUMIF($C:$C,C4,$T:$T)
    "discount_per_order": "verified",   # Y4 = SUMIF($C:$C,C4,$W:$W)
    "discount_allocated": "verified",   # Z4 = IFERROR((T4/X4)*Y4, 0)  — proportional!
    "unit_price_pre_vat": "verified",   # AB4 = ((T4+Z4)/R4)/AA4  (AA = VAT factor)
    "amount_pre_vat": "verified",       # AD4 = AB4*AC4
    "amount_with_vat": "verified",      # AE = AD*AA
}


def explode_to_sku_shopee(income_orders: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee SKU explode: order SKU lines grouped by (order, SKU, name,
    unit price) with quantity and both subsidies summed, joined to the
    classified income orders on order id. ALL classified orders join (the
    intermediary carries Return/0-dong rows too — invoicing filters later)."""
    orders = orders.copy()
    # Shopee order exports drift between stores/versions — some lack the
    # subsidy columns entirely. seller_subsidy feeds the T formula, so its
    # absence is worth a loud warning; shopee_subsidy is informational.
    for col in ("seller_subsidy", "shopee_subsidy"):
        if col not in orders.columns:
            log.warn(f"orders have no '{col}' column (export version drift) — treating as 0")
            orders[col] = 0.0
    lines = orders.groupby(
        ["order_id", "sku_id", "sku_name", "unit_price_gross"], as_index=False, dropna=False
    ).agg(quantity=("quantity", "sum"), seller_subsidy=("seller_subsidy", "sum"),
          shopee_subsidy=("shopee_subsidy", "sum"))
    sku_level = income_orders.merge(lines, on="order_id", how="inner")
    log.add(f"  {len(income_orders)} income orders exploded to {len(sku_level)} SKU lines")
    return sku_level


def compute_sku_columns_shopee(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The Shopee yellow columns (cell evidence in SHOPEE_FORMULA_STATUS)."""
    df = sku_level.copy()
    # Default-plus-exceptions VAT, same model as TikTok/Lazada (masters.py).
    from .masters import vat_factor_for
    df["vat_factor"] = vat_factor_for(df["sku_id"], settings, settings.get("_vat_sku") or {})  # AA

    qty = df["quantity"].fillna(0)
    df["gross_rev"] = df["unit_price_gross"].fillna(0) * qty                       # S = Q*R
    df["net_after_discount"] = df["gross_rev"] - df["seller_subsidy"].fillna(0)    # T = (Q*R)-U
    df["total_discount"] = (df["seller_voucher"].fillna(0) + df["seller_ship_support"].fillna(0)
                            + df["seller_coin_cashback"].fillna(0) + df["cofund_voucher"].fillna(0))  # W
    df["order_gross_sale"] = df.groupby("order_id")["net_after_discount"].transform("sum")   # X
    # W lives on order level (first row per order in their sheet); the SUMIF
    # over C picks it up once per order — groupby "max" of the constant works
    # because our merge repeats the order-level value on every SKU row.
    df["discount_per_order"] = df.groupby("order_id")["total_discount"].transform("max")     # Y
    ratio = (df["net_after_discount"] / df["order_gross_sale"].replace(0, pd.NA)).fillna(0)
    df["discount_allocated"] = ratio * df["discount_per_order"]                    # Z = IFERROR((T/X)*Y,0)
    df["unit_price_pre_vat"] = ((df["net_after_discount"] + df["discount_allocated"])
                                / qty.replace(0, pd.NA)).fillna(0) / df["vat_factor"]        # AB
    df["amount_pre_vat"] = df["unit_price_pre_vat"] * qty                          # AD = AB*AC
    df["amount_with_vat"] = df["amount_pre_vat"] * df["vat_factor"]                # AE

    pending = [k for k, v in SHOPEE_FORMULA_STATUS.items() if v != "verified"]
    if pending:
        log.warn(f"Shopee formulas not yet row-verified: {pending}")
    return df


def build_return_lines(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee Return tab: WRITTEN (returned) lines with the negative-discount
    adjustment. PORT ZONE — placeholder posts the refund as a negative amount;
    the exact adjustment must come from the team's file."""
    written = income[income["status"] == STATUS_WRITTEN].copy()
    written["return_amount"] = -written["actual_refund"].fillna(0).abs()
    log.add(f"  return lines: {len(written)}")
    return written

```


---

## `src/classify.py`

```python
"""Stage 3 — Classify.

Tags each income line OK / WRITTEN (returned) / ZERO_REVENUE per the team's
rules, and attaches the invoice grouping from brand_rules.yaml.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import invoice_grouping
from .runlog import RunLog

STATUS_OK = "OK"
STATUS_WRITTEN = "WRITTEN"
STATUS_ZERO = "ZERO_REVENUE"

# TikTok statuses ported from the team's Power Query (Income_Final query,
# Thanh_recon V1/V2 M code, steps "Added Custom3"/"Grouped Rows"):
CHECK_GOOD = "Good"
CHECK_PARTIAL_RETURN = "Partial Return"
CHECK_TOTAL_RETURN = "Total Return"
CHECK_PAYBACK = "Payback_Order"
FINAL_OK = "OK"
FINAL_TAKE_OUT = "take out"
# Lines that fall through every M-code branch: Logistics/Platform
# reimbursements, commission adjustments, and rare Order lines with negative
# settlement but no refund. The team's pivots include these in NEITHER the
# invoice (Good) NOR take-out — they must surface in exceptions, not vanish.
FINAL_UNCLASSIFIED = "unclassified"

# Shopee statuses, exactly as they appear in the intermediary "Xuat HĐ"
# column A (XLOOKUP against the curated "return + 0dong" sheet, default ok):
SHOPEE_OK = "ok"
SHOPEE_RETURN = "Return"
SHOPEE_ZERO_DONG = "0 dong"

# Income columns the M code sums when collapsing settlement lines per order.
TIKTOK_GROUP_SUM_COLUMNS = [
    "gross_revenue", "net_revenue", "actual_refund",
    "subtotal_after_seller_discounts", "subtotal_before_discounts",
    "refund_subtotal_after_sd", "refund_subtotal_before_sd",
]


def classify(income: pd.DataFrame, brand_rules: dict, log: RunLog) -> pd.DataFrame:
    df = income.copy()
    refund = df["actual_refund"].fillna(0)
    net = df["net_revenue"].fillna(0)
    df["status"] = np.where(refund != 0, STATUS_WRITTEN, np.where(net == 0, STATUS_ZERO, STATUS_OK))

    # Invoice grouping is config, not code: separate-invoice brands split out,
    # everything else lands in the combined group.
    df["invoice_group"] = [
        brand if invoice_grouping(brand, brand_rules) == "separate" else "combined"
        for brand in df["brand"]
    ]

    counts = df["status"].value_counts()
    for status in (STATUS_OK, STATUS_WRITTEN, STATUS_ZERO):
        log.add(f"  {status}: {int(counts.get(status, 0))}")
    return df


def classify_tiktok_income(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Line-by-line port of the team's TikTok income classification.

    Evidence: Power Query "Income_Final" embedded in Thanh_recon V1/V2
    (customXml DataMashup -> Formulas/Section1.m):
      - step "Grouped Rows": income collapsed per (Name, Order/adjustment ID,
        Type, Order created time), amount columns summed
      - step "Added Custom3":
          _OrderID_Pass           = subtotal_before + refund_before <> 0
                                    and settlement >= 0 and Type = "Order"
          _OrderID_Partial_Return = subtotal_before + refund_before <> 0
                                    and refund_before <> 0 and Type = "Order"
          _OrderID_Return_Total   = subtotal_before + refund_before = 0
                                    and Type = "Order"
          _OrderID_Refund_PayBack = not Pass and Total Revenue < 0
          _Check_Status: Payback_Order > Total Return > Partial Return > Good
      - step "Filtered Rows1": rows with null Type dropped

    Final_Status (OK / "take out") is a Power Pivot model column whose DAX is
    not extractable from the workbook. Empirically determined rule, verified
    exactly against the Total files' pivots for U food (Good/Total Return
    only) and Unilever Homecare (all four statuses, both windows):
      OK        = _Check_Status "Good" (the only lines that get invoiced)
      take out  = Total Return, Payback_Order AND Partial Return
                  (W1: -55,421,041 TR - 9,692,678 PB + 8,035,197 Partial
                   = -57,078,522 = V1 "Pivot Income" Unilever row, exact)
      unclassified = null _Check_Status (reimbursements/adjustments +
                  negative-settlement no-refund Orders; ~150/window/store,
                  +44-51M VND settlement for Unilever) — present in NEITHER
                  team pivot; route to exceptions, ask Hoang where they book.
    Partial Return orders are fully EXCLUDED from the window's invoicing
    (not partially adjusted) — the For KA file has a manual "Return 1 phan"
    section for them (broken #REF! formula in both windows' files).
    """
    df = income.copy()
    df = df[df["income_type"].notna()]

    group_keys = ["store", "brand", "order_id", "income_type", "income_order_created_at"]
    sum_cols = [c for c in TIKTOK_GROUP_SUM_COLUMNS if c in df.columns]
    before = len(df)
    df = df.groupby(group_keys, as_index=False, dropna=False).agg(
        {**{c: "sum" for c in sum_cols}, "statement_date": "min"})
    if len(df) != before:
        log.add(f"  income settlement lines collapsed per order: {before} -> {len(df)}")

    subtotal_net = df["subtotal_before_discounts"].fillna(0) + df["refund_subtotal_before_sd"].fillna(0)
    is_order = df["income_type"] == "Order"
    is_pass = (subtotal_net != 0) & (df["net_revenue"].fillna(0) >= 0) & is_order
    is_partial = (subtotal_net != 0) & (df["refund_subtotal_before_sd"].fillna(0) != 0) & is_order
    is_total_return = (subtotal_net == 0) & is_order
    is_payback = ~is_pass & (df["gross_revenue"].fillna(0) < 0)

    df["check_status"] = np.select(
        [is_payback, is_total_return, is_partial, is_pass],
        [CHECK_PAYBACK, CHECK_TOTAL_RETURN, CHECK_PARTIAL_RETURN, CHECK_GOOD],
        default=None,
    )
    df["final_status"] = np.select(
        [df["check_status"] == CHECK_GOOD, df["check_status"].notna()],
        [FINAL_OK, FINAL_TAKE_OUT],
        default=FINAL_UNCLASSIFIED,
    )

    for status, n in df["check_status"].value_counts(dropna=False).items():
        log.add(f"  check_status {status}: {n}")
    for status, n in df["final_status"].value_counts().items():
        log.add(f"  final_status {status}: {n}")
    return df


def classify_shopee_income(income: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Shopee order classification.

    The team's process tags each order via XLOOKUP into a manually curated
    "return + 0dong" sheet ('Xuat HĐ' col A, default "ok"). The membership
    rules were DERIVED from raw income and verified exactly:
      Return : order's refund ("Số tiền hoàn lại") sum != 0
               (Kate W1 11/11; Sanofi W1 178/178 — 0 missing, 0 extra)
      0 dong : order's settlement ("Tổng tiền đã thanh toán") sum <= 0
               AND refund == 0 — fully promo-covered orders where the
               remaining settlement is just fees, so nothing is invoiced
               (Sanofi W1 40/40 — 0 missing, 0 extra)
      ok     : everything else (invoiced)

    Only rows typed "Order" in "Đơn hàng / Sản phẩm" count (the team's M code
    filters the product-level "Sku" rows the same way).
    """
    df = income[income["income_type"] == "Order"].copy()
    dropped = len(income) - len(df)
    if dropped:
        log.add(f"  non-Order income lines dropped (Sku/product rows): {dropped}")

    group_keys = ["store", "brand", "order_id"]
    df = df.groupby(group_keys, as_index=False, dropna=False).agg(
        net_revenue=("net_revenue", "sum"),
        actual_refund=("actual_refund", "sum"),
        gross_revenue=("gross_revenue", "sum"),
        cofund_voucher=("cofund_voucher", "sum"),
        seller_voucher=("seller_voucher", "sum"),
        seller_coin_cashback=("seller_coin_cashback", "sum"),
        seller_ship_support=("seller_ship_support", "sum"),
        income_order_created_at=("income_order_created_at", "min"),
        statement_date=("statement_date", "min"),
    )
    refund = df["actual_refund"].fillna(0)
    net = df["net_revenue"].fillna(0)
    df["check_status"] = np.select(
        [refund != 0, net <= 0],
        [SHOPEE_RETURN, SHOPEE_ZERO_DONG],
        default=SHOPEE_OK,
    )
    df["final_status"] = np.where(df["check_status"] == SHOPEE_OK, FINAL_OK, FINAL_TAKE_OUT)
    for status, n in df["check_status"].value_counts().items():
        log.add(f"  check_status {status}: {n}")
    return df

```


---

## `src/config.py`

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .errors import ReconHardStop
from .runlog import RunLog


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ReconHardStop(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_dir: Path) -> dict:
    return load_yaml(config_dir / "settings.yaml")


def load_brand_rules(config_dir: Path) -> dict:
    return load_yaml(config_dir / "brand_rules.yaml")


def invoice_grouping(brand: str, brand_rules: dict) -> str:
    brands = brand_rules.get("brands") or {}
    rule = (brands.get(brand) or {}).get("invoice_grouping")
    return rule or (brand_rules.get("defaults") or {}).get("invoice_grouping", "combined")


def load_sku_master(config_dir: Path, log: RunLog) -> dict[str, str]:
    """SKU ID → name. Unknown SKUs are flagged downstream, never a hard stop."""
    path = config_dir / "sku_master.csv"
    if not path.exists():
        log.warn(f"sku_master.csv not found at {path} — all SKUs will be flagged as unknown")
        return {}
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    if "sku_id" not in df.columns:
        raise ReconHardStop(f"sku_master.csv must have a 'sku_id' column (found: {list(df.columns)})")
    df = df.dropna(subset=["sku_id"])
    return dict(zip(df["sku_id"].str.strip(), df.get("sku_name", pd.Series(dtype=str)).fillna("")))


def column_map(settings: dict, platform: str, kind: str) -> dict[str, str]:
    maps = settings.get("column_maps") or {}
    cmap = (maps.get(platform) or {}).get(kind)
    if not cmap:
        raise ReconHardStop(f"No column map configured for {platform}/{kind} in settings.yaml")
    return cmap

```


---

## `src/errors.py`

```python
class ReconHardStop(Exception):
    """Unrecoverable data problem — the run must not produce a finance file.

    Used for: missing input folders, unmappable required columns, store-count
    mismatch. Everything softer goes to exceptions.xlsx instead.
    """

```


---

## `src/export.py`

```python
"""Stage 6 — Export.

finance_file.xlsx in the layout finance books from (TikTok: one Income tab;
Shopee: Income + Return tabs), exceptions.xlsx with one tab per exception
type, run_log.txt as the audit trail.

PORT ZONE (layout): the exact column order/labels of the finance file must be
matched to the team's real finance file once a sample arrives. The canonical
layout below is a stand-in.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .runlog import RunLog

FINANCE_INCOME_COLUMNS = {
    "order_id": "Order ID",
    "order_created_at": "Order Created",
    "store": "Store",
    "brand": "Brand",
    "invoice_group": "Invoice Group",
    "sku_id": "SKU ID",
    "sku_name": "SKU Name",
    "quantity": "Quantity",
    "gross_revenue_sku": "Gross Revenue",
    "discount_sku": "Discount",
    "net_pre_vat_sku": "Net Revenue (pre-VAT)",
    "vat_sku": "VAT",
    "net_revenue_sku": "Net Revenue",
}

FINANCE_RETURN_COLUMNS = {
    "order_id": "Order ID",
    "order_created_at": "Order Created",
    "store": "Store",
    "brand": "Brand",
    "invoice_group": "Invoice Group",
    "actual_refund": "Actual Refund",
    "return_amount": "Return Amount (negative adjustment)",
}

EXCEPTION_TABS = [
    ("unmatched_orders", "Unmatched Orders"),
    ("unknown_skus", "Unknown SKUs"),
    ("tieout_breaches", "Tie-out Breaches"),
    ("zero_revenue", "Zero Revenue"),
]


def _tab(df: pd.DataFrame, columns: dict[str, str]) -> pd.DataFrame:
    out = df[[c for c in columns if c in df.columns]].rename(columns=columns)
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out


def write_finance_file(
    path: Path, platform: str, sku_level: pd.DataFrame, returns: pd.DataFrame, log: RunLog
) -> tuple[float, float]:
    """Returns (income tab total, return tab total) for tie-out Check 3."""
    income_tab = _tab(sku_level, FINANCE_INCOME_COLUMNS)
    income_total = float(sku_level["net_revenue_sku"].sum()) if len(sku_level) else 0.0
    return_total = 0.0

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        income_tab.to_excel(xw, sheet_name="Income", index=False)
        if platform == "shopee":
            _tab(returns, FINANCE_RETURN_COLUMNS).to_excel(xw, sheet_name="Return", index=False)
            return_total = float(returns["return_amount"].sum()) if len(returns) else 0.0

    log.add(f"  {path.name}: Income tab {len(income_tab)} rows (total {income_total:,.2f})"
            + (f", Return tab {len(returns)} rows (total {return_total:,.2f})" if platform == "shopee" else ""))
    return income_total, return_total


def write_exceptions_file(path: Path, exceptions: dict[str, pd.DataFrame], log: RunLog) -> int:
    """One tab per exception type, always all four tabs (empty = nothing to look at)."""
    total = 0
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for key, sheet in EXCEPTION_TABS:
            df = exceptions.get(key, pd.DataFrame())
            if df.empty and not len(df.columns):
                df = pd.DataFrame({"(no rows)": []})
            out = df.copy()
            for col in out.columns:
                if pd.api.types.is_datetime64_any_dtype(out[col]):
                    out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
            out.to_excel(xw, sheet_name=sheet, index=False)
            total += len(df)
            log.add(f"  exceptions - {sheet}: {len(df)} row(s)")
    return total

```


---

## `src/export_platforms.py`

```python
"""SUPERSEDED — NOT A DELIVERABLE FORMAT.

src/finance_template.py is the only export path (wired in tools/full_run.py
since Aug 2026): it produces the team's full invoicing-template shape
(PV sum / Summary / brand tabs / control block). This module's plain
Income-only workbooks were shipped for July by mistake and the team asked
for the template shape; keep this module only as reference for the original
column-layout evidence below. Do not wire it back into any run path.

Original docstring:
Finance-file exports matched to the team's real May invoicing files.

Layout evidence:
- TikTok : "Tiktok result * For KA.xlsx" sheet "Xuat HD bt" data region
           (header row 6) — single Income tab.
- Shopee : "shopee result Sample For KA *" — "Xuat HD bt" (Income) +
           "return" (Return tab: negative refunds, full-vs-partial split at
           |order total + refund| < 10 VND, 'return'!R7 evidence).
- Lazada : "Laz result KA used *" — one line tab per VAT rate (cols A..H of
           sheet "1.08") with whole-VND Price KA, plus a fee-bucket summary
           tab (Total file "SUM CP" layout).

Values come from the row-verified chains; the "non repeat" columns blank
order-level fields on 2nd+ SKU rows exactly like the team's sheets.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .runlog import RunLog


def _blank_repeats(df: pd.DataFrame, order_col: str, cols: list[str]) -> pd.DataFrame:
    """Blank order-level columns on repeated rows of the same order —
    the team's 'non repeat' convention."""
    dup = df.duplicated(subset=[order_col])
    for c in cols:
        df.loc[dup, c] = None
    return df


def finance_tiktok(sku_level: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Good rows -> Income tab in the 'Xuat HD bt' column layout."""
    df = sku_level.sort_values(["store", "order_id"]).copy()
    out = pd.DataFrame({
        "Month": pd.to_datetime(df["income_order_created_at"]).dt.month,
        "Source.Name": df["store"],
        "Mã đơn hàng": df["order_id"],
        "Source.Name non repeat": df["store"],
        "Mã đơn hàng non repeat": df["order_id"],
        "SKU phân loại hàng": df["sku_id"],
        "Tên sản phẩm": df["sku_name"],
        "VAT KA sử dụng": df["vat_factor"],
        "Đơn giá KA sử dụng trước VAT": df["unit_price_pre_vat"],
        "số lượng KA sử dụng trước VAT": df["quantity"],
        "Amount before VAT": df["amount_pre_vat"],
        "Check total": df["amount_with_vat"],
        "Số tiền hoàn trả cho Người mua (₫)": df["actual_refund"],
    })
    out = _blank_repeats(out, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])
    return {"Income": out}


def finance_shopee(sku_level: pd.DataFrame, return_tol_vnd: float = 10.0) -> dict[str, pd.DataFrame]:
    """ok rows -> Income tab; Return rows -> Return tab with negative refund
    and the 10-VND full/partial split ('return'!R7)."""
    df = sku_level.sort_values(["store", "order_id"]).copy()

    def base(sub: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "Diff": sub["check_status"],
            "Source.Name": sub["store"],
            "Mã đơn hàng": sub["order_id"],
            "Source.Name non repeat": sub["store"],
            "Mã đơn hàng non repeat": sub["order_id"],
            "Create_Order_Month": pd.to_datetime(sub["income_order_created_at"]).dt.month,
            "Finance_Month": pd.to_datetime(sub["statement_date"]).dt.month,
            "SKU phân loại hàng": sub["sku_id"],
            "Tên sản phẩm": sub["sku_name"],
            "VAT KA sử dụng": sub["vat_factor"],
            "Đơn giá KA sử dụng trước VAT": sub["unit_price_pre_vat"],
            "số lượng KA sử dụng trước VAT": sub["quantity"],
            "Cộng tiền hàng KA sử dụng trước VAT": sub["amount_pre_vat"],
            "Cộng tiền hàng KA sử dụng có VAT": sub["amount_with_vat"],
        })

    ok = df[df["check_status"] == "ok"]
    income = base(ok)
    income = _blank_repeats(income, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])

    ret = df[df["check_status"] == "Return"].copy()
    rtab = base(ret)
    rtab["Total by order"] = ret.groupby("order_id")["amount_with_vat"].transform("sum").values
    refund = ret["actual_refund"].fillna(0)
    rtab["Số tiền hoàn trả cho Người mua (₫)"] = np.where(refund > 0, -refund, refund)  # negative adjustment
    rtab["Check"] = rtab["Total by order"] + rtab["Số tiền hoàn trả cho Người mua (₫)"]
    rtab["Note"] = np.where(rtab["Check"].abs() < return_tol_vnd,
                            "Return full ko xuat HD", "Return 1 phan phai xuat HD")
    rtab = _blank_repeats(rtab, "Mã đơn hàng",
                          ["Source.Name non repeat", "Mã đơn hàng non repeat",
                           "Total by order", "Số tiền hoàn trả cho Người mua (₫)", "Check", "Note"])
    return {"Income": income, "Return": rtab}


def finance_lazada(revenue: pd.DataFrame, classified: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """One line tab per VAT rate ('1.08' layout, whole-VND Price KA) plus a
    'Fee buckets' tab in the Total file's 'SUM CP' shape."""
    tabs: dict[str, pd.DataFrame] = {}
    for rate, sub in revenue.sort_values(["store", "order_id"]).groupby("vat_rate"):
        tabs[f"{rate:.2f}".rstrip("0").rstrip(".")] = pd.DataFrame({
            "Source.Name": sub["store"],
            "Order No.": sub["order_id"],
            "Seller SKU": sub["sku_id"],
            "Details": sub["product_name"],
            "Sum of Price KA": sub["price_ka"],
            "Sum of Quantity": sub["quantity"],
            "Amount": sub["check_no_vat"],
            "Amount with VAT": sub["check_with_vat"],
        })
    buckets = (classified.pivot_table(index="store", columns="fee_bucket",
                                      values="amount_incl_vat", aggfunc="sum")
               .round(2).reset_index())
    tabs["Fee buckets"] = buckets
    return tabs


def write_finance(tabs: dict[str, pd.DataFrame], path: Path, log: RunLog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in tabs.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            log.add(f"  {path.name} · tab '{name}': {len(df)} rows")

```


---

## `src/finance_template.py`

```python
"""Finance workbooks in the team's invoicing-template shape — cleaned up.

Layout evidence (cell coordinates refer to the team's May files):
- TikTok : "Tiktok result 01 to 17T5 For KA.xlsx"
- Shopee : "shopee result Sample For KA 01 to 10T05.xlsx"
- Lazada : "Laz result KA used 26_04T5 to 10T5.xlsx"

Principle: same tabs, same column names/order, same header-row position and
total-cell positions as the template, so the team can read it without
retraining — but every check value is COMPUTED by the verified engine and
written as a static number.

Two families of numbers, deliberately kept distinct (the template mixes
them and several of its checks silently broke):

1. LINE tabs carry the engine's exact amounts. Their control blocks compare
   exact line totals to an exact per-VAT-rate recombination — a drift of
   even a few VND means a row was dropped or edited. Tolerance 2,000
   ('Xuat HD bt'!P5 / R5).
2. PIVOT tabs (brand / VAT / "KA used") are the client-facing invoice view:
   per-SKU rounded average price x quantity, so every displayed line
   satisfies qty x unit = amount. Recombining rounded prices drifts from
   the exact books by design; the PV-sum / Summary checks measure exactly
   that drift, at the tolerance the team's WORKING verdicts use
   (TikTok 'PV sum'!G8 12,000 · Shopee 'PV sum'!J11 10,000 ·
   Lazada Summary!F17 2,000).

Template defects fixed, not copied:
- TikTok 'Xuat HD bt' control: sums a #REF! (L3) and multiplies the KAO
  bucket by 1.10 (N4=M4*1.1). Rebuilt as exact per-VAT-rate rows.
- Shopee 'PV sum'!G3 verdict reads blank E2 (always "OK"); its stated 2,000
  tolerance never actually applied. Wired to the real diff at 10,000 — the
  team's functioning side-block tolerance for the same comparison.
- Shopee income control O3 reads ' 1.08 KA'!F2, which points at an empty
  column whenever the pivot is re-arranged. Values are written directly.
- 'PV xuat HD' checks compared mismatched quantities (always failing,
  ignored). Rebuilt as exact-vs-exact at 1,000.
- Lazada '1.05'/'1.10' tabs carried stale January stamps ("Laz T12",
  "8T1 to 14T1"). Stamps come from the current window.
- Lazada 'X KA used'!J1 used tolerance 1,000 on a pivot-rounding check
  whose natural drift is ~2,000 (their own May file fails it and is booked
  anyway); Summary uses 2,000 for the same comparison. Both now use 2,000.

Verdict wording is the team's own, diacritics included.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell

from .runlog import RunLog

# Accounting masks copied from the team's cells (probe: 'Xuat HD bt' row 7).
ACC0 = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'
ACC2 = '_(* #,##0.00_);_(* \\(#,##0.00\\);_(* "-"??_);_(@_)'
PLAIN0 = "#,##0_);(#,##0)"
PLAIN2 = "#,##0.00_);(#,##0.00)"

VERDICT_OK = "ok có thể xuất HD"
VERDICT_BAD = "Cần check lại số có vấn đề"
PVSUM_OK = "OK"
PVSUM_BAD = "check lai sai roi"
RETURN_FULL = "Return full ko xuat HD"
RETURN_PARTIAL = "Return 1 phan phai xuat HD"

VAT_RATES = (1.05, 1.08, 1.10)

TOL_PIVOT_DRIFT_SHOPEE = 10000.0   # 'PV sum'!J11 — the working verdict
TOL_PIVOT_DRIFT_LAZADA = 2000.0    # Summary!F17
TOL_SKU_PIVOT = 1000.0             # 'PV xuat HD'!I1 intent
TOL_LAZ_LINE = 1000.0              # '1.08'!I2


def _verdict(diff: float, tol: float, ok: str = VERDICT_OK, bad: str = VERDICT_BAD) -> str:
    return ok if abs(diff) < tol else bad


def _bucket(store: str, buckets: list[tuple[str, str]], default: str) -> str:
    s = str(store).lower()
    for needle, name in buckets:
        if needle in s:
            return name
    return default


TIKTOK_BUCKETS = [("kao", "KAO 8"), ("merries", "Merries 8")]
SHOPEE_BUCKETS = [("curel", "Curel"), ("kao", "KAO"), ("merries", "Merries"), ("kate", "Kate")]
LAZADA_BUCKETS = [("curel", "Curel.xlsx"), ("kao", "KAO.xlsx"), ("merries", "Merries.xlsx")]


class _Tab:
    """Sparse control rows + an optional streamed data region, emitted in
    row order to a write-only worksheet."""

    def __init__(self, wb: Workbook, name: str):
        self.ws = wb.create_sheet(name)
        self.cells: dict[int, dict[int, tuple]] = {}

    def put(self, ref: str, value, fmt: str | None = None) -> None:
        col = "".join(ch for ch in ref if ch.isalpha())
        row = int("".join(ch for ch in ref if ch.isdigit()))
        cidx = 0
        for ch in col:
            cidx = cidx * 26 + (ord(ch.upper()) - 64)
        self.cells.setdefault(row, {})[cidx] = (value, fmt)

    def label_row(self, row: int, start_col: int, labels: list[str]) -> None:
        for i, lab in enumerate(labels):
            self.cells.setdefault(row, {})[start_col + i] = (lab, None)

    def _make_row(self, rowcells: dict[int, tuple]) -> list:
        out = []
        for c in range(1, max(rowcells) + 1):
            if c in rowcells:
                v, fmt = rowcells[c]
                wc = WriteOnlyCell(self.ws, value=v)
                if fmt:
                    wc.number_format = fmt
                out.append(wc)
            else:
                out.append(None)
        return out

    def emit(self, data: pd.DataFrame | None = None, data_start_row: int | None = None,
             fmts: list[str | None] | None = None, widths: dict[str, float] | None = None,
             freeze: str | None = None) -> None:
        for ref, w in (widths or {}).items():
            self.ws.column_dimensions[ref].width = w
        if freeze:
            self.ws.freeze_panes = freeze
        last_control = max(self.cells) if self.cells else 0
        top = data_start_row - 1 if data_start_row else last_control
        for r in range(1, top + 1):
            rowcells = self.cells.get(r)
            self.ws.append(self._make_row(rowcells) if rowcells else [])
        if data is None:
            return
        fmts = fmts or [None] * data.shape[1]
        for tup in data.itertuples(index=False, name=None):
            row = []
            for v, fmt in zip(tup, fmts):
                if v is None or (not isinstance(v, str) and pd.isna(v)):
                    row.append(None)
                    continue
                wc = WriteOnlyCell(self.ws, value=v)
                if fmt:
                    wc.number_format = fmt
                row.append(wc)
            self.ws.append(row)


def _blank_repeats(df: pd.DataFrame, key: str, cols: list[str]) -> pd.DataFrame:
    dup = df.duplicated(subset=[key])
    for c in cols:
        df.loc[dup, c] = None
    return df


def _sku_pivot(df: pd.DataFrame, keys: list[str], recombine: bool) -> pd.DataFrame:
    """SKU pivot. recombine=True gives invoice semantics: rounded average
    unit price x quantity (every line internally consistent, totals drift
    from the exact books by the rounding — that drift is what the PV-sum
    checks measure). recombine=False keeps the engine's exact sums."""
    g = df.groupby(keys, as_index=False, dropna=False).agg(
        qty=("quantity", "sum"), pre_exact=("amount_pre_vat", "sum"),
        wv_exact=("amount_with_vat", "sum"))
    g["aveg"] = (g["pre_exact"] / g["qty"].replace(0, pd.NA)).fillna(0).round(0)
    factor = (g["wv_exact"] / g["pre_exact"].replace(0, pd.NA)).fillna(1.08)
    if recombine:
        g["pre"] = g["aveg"] * g["qty"]
        g["wv"] = (g["pre"] * factor).round(2)
    else:
        g["pre"] = g["pre_exact"]
        g["wv"] = g["wv_exact"]
    return g


# ---------------------------------------------------------------------------
# TikTok — tabs: PV sum, Xuat HD bt, Merries 8, KAO 8, Others 8, PV xuat HD
# ---------------------------------------------------------------------------

def build_tiktok(sku: pd.DataFrame, settings: dict, meta: dict, log: RunLog) -> tuple[Workbook, list[dict]]:
    tol = (settings.get("tolerances") or {}).get("tiktok") or {}
    tol_pv, tol_line, tol_pivot = (float(tol.get("pv_sum_vnd", 12000)),
                                   float(tol.get("xuat_hd_vnd", 2000)),
                                   float(tol.get("pv_xuat_hd_vnd", TOL_SKU_PIVOT)))
    wb = Workbook(write_only=True)
    checks: list[dict] = []
    df = sku.sort_values(["store", "order_id"]).copy()
    df["bucket"] = df["store"].map(lambda s: _bucket(s, TIKTOK_BUCKETS, "Others 8"))

    pre_total = float(df["amount_pre_vat"].sum())
    wv_total = float(df["amount_with_vat"].sum())
    by_rate_pre = {r: float(df.loc[df["vat_factor"].round(2) == r, "amount_pre_vat"].sum())
                   for r in VAT_RATES}
    recomb_wv = sum(v * r for r, v in by_rate_pre.items())

    # Brand pivots first (PV sum reads their recombined totals).
    brand_order = [("Merries 8", "Merries 8"), ("KAO 8", "KAO 8"), ("Others 8", "Others 8")]
    brand_piv = {b: _sku_pivot(df[df["bucket"] == b], ["sku_id", "sku_name"], recombine=True)
                 for _, b in brand_order}
    brand_pre = {b: float(p["pre"].sum()) for b, p in brand_piv.items()}
    brand_wv = {b: float(p["wv"].sum()) for b, p in brand_piv.items()}

    # --- PV sum ('PV sum'!A4 header; checks E2/E3 + side block F1:H8) ---
    t = _Tab(wb, "PV sum")
    pv = df.groupby(["store", "vat_factor"], as_index=False).agg(
        pre=("amount_pre_vat", "sum"), wv=("amount_with_vat", "sum"))
    side = [("Others 8%", "Others 8"), ("Kao 8%", "KAO 8"), ("Merries 8%", "Merries 8")]
    piv_pre_sum = float(sum(brand_pre.values()))
    piv_wv_sum = float(sum(brand_wv.values()))
    t.put("G1", "Sum of Amount before VAT"); t.put("H1", "Amount after VAT")
    t.put("D2", "Diff"); t.put("E2", piv_pre_sum - pre_total, ACC0)
    t.put("C3", pre_total, ACC0); t.put("D3", wv_total, ACC0)
    t.put("E3", _verdict(piv_pre_sum - pre_total, tol_pv, PVSUM_OK, PVSUM_BAD))
    for i, (label, b) in enumerate(side):
        r = 3 + i
        t.put(f"F{r}", label)
        t.put(f"G{r}", brand_pre[b], ACC0)
        t.put(f"H{r}", brand_wv[b], ACC0)
    t.put("G6", piv_pre_sum, ACC0); t.put("H6", piv_wv_sum, ACC0)
    t.put("F7", "Diff"); t.put("G7", pre_total - piv_pre_sum, ACC0)
    t.put("H7", wv_total - piv_wv_sum, ACC0)
    t.put("G8", _verdict(pre_total - piv_pre_sum, tol_pv, PVSUM_OK, PVSUM_BAD))
    t.put("H8", _verdict(wv_total - piv_wv_sum, tol_pv, PVSUM_OK, PVSUM_BAD))
    t.label_row(4, 1, ["Source.Name", "VAT KA sử dụng", "Sum of Amount before VAT", "Sum of Check total"])
    t.emit(pv[["store", "vat_factor", "pre", "wv"]], data_start_row=5,
           fmts=[None, ACC2, ACC0, ACC0], widths={"A": 34, "C": 20, "D": 20, "F": 12, "G": 18, "H": 18})
    checks.append({"tab": "PV sum", "check": "invoice pivots vs exact books (pre-VAT rounding drift)",
                   "diff": pre_total - piv_pre_sum, "tol": tol_pv,
                   "verdict": _verdict(pre_total - piv_pre_sum, tol_pv, PVSUM_OK, PVSUM_BAD)})

    # --- Xuat HD bt (header row 6; control rebuilt: exact per-VAT rows) ---
    t = _Tab(wb, "Xuat HD bt")
    out = pd.DataFrame({
        "Month": pd.to_datetime(df["income_order_created_at"]).dt.month,
        "Source.Name": df["store"],
        "Mã đơn hàng": df["order_id"],
        "Source.Name non repeat": df["store"],
        "Mã đơn hàng non repeat": df["order_id"],
        "SKU phân loại hàng": df["sku_id"],
        "Tên sản phẩm": df["sku_name"],
        "VAT KA sử dụng": df["vat_factor"],
        "Đơn giá KA sử dụng trước VAT": df["unit_price_pre_vat"],
        "số lượng KA sử dụng trước VAT": df["quantity"],
        "Amount before VAT": df["amount_pre_vat"],
        "Check total": df["amount_with_vat"],
        "Số tiền hoàn trả cho Người mua (₫)": df["actual_refund"].replace(0, pd.NA),
    })
    out = _blank_repeats(out, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])
    t.put("L1", "check"); t.put("M1", "no VAT"); t.put("N1", "with VAT"); t.put("O1", "check")
    for i, r in enumerate(VAT_RATES):
        t.put(f"K{2 + i}", r, ACC2)
        t.put(f"M{2 + i}", by_rate_pre[r], ACC0)
        t.put(f"N{2 + i}", by_rate_pre[r] * r, ACC0)
    line_diff = wv_total - recomb_wv
    t.put("K5", "total"); t.put("L5", wv_total, ACC0); t.put("N5", recomb_wv, ACC0)
    t.put("O5", line_diff, ACC0); t.put("P5", _verdict(line_diff, tol_line))
    t.label_row(6, 1, list(out.columns))
    t.emit(out, data_start_row=7,
           fmts=[None, None, None, None, None, None, None, ACC2, ACC0, ACC0, ACC0, ACC0, ACC0],
           widths={"B": 26, "C": 22, "G": 44, "I": 16, "K": 16, "L": 16, "M": 16},
           freeze="A7")
    checks.append({"tab": "Xuat HD bt", "check": "with-VAT lines vs exact VAT recombination",
                   "diff": line_diff, "tol": tol_line, "verdict": _verdict(line_diff, tol_line)})

    # --- Brand tabs (header row 4; totals at E2/F2 like the template) ---
    for tab_name, b in brand_order:
        t = _Tab(wb, tab_name)
        piv = brand_piv[b]
        sub = df[df["bucket"] == b]
        stores = sub["store"].unique()
        t.put("A1", "VAT KA sử dụng")
        t.put("B1", float(sub["vat_factor"].max()) if len(sub) else None, ACC2)
        t.put("A2", "Source.Name")
        t.put("B2", stores[0] if len(stores) == 1 else "(Multiple Items)")
        t.put("E2", brand_pre[b], ACC0)
        t.put("F2", brand_wv[b], ACC0)
        t.label_row(4, 1, ["SKU phân loại hàng", "Tên sản phẩm", "Sum of số lượng KA sử dụng trước VAT",
                           "Sum of Aveg Price KA sử dụng", "Sum of Check Amount No VAT", "Sum of Check total"])
        t.emit(piv[["sku_id", "sku_name", "qty", "aveg", "pre", "wv"]], data_start_row=5,
               fmts=[None, None, ACC0, ACC0, ACC0, ACC0],
               widths={"A": 22, "B": 46, "C": 14, "D": 16, "E": 18, "F": 18})

    # --- PV xuat HD (exact SKU pivot; drop-detection check at 1,000) ---
    t = _Tab(wb, "PV xuat HD")
    piv = _sku_pivot(df, ["store", "sku_id", "sku_name"], recombine=False)
    pivot_pre = float(piv["pre"].sum())
    t.put("A1", "VAT KA sử dụng"); t.put("B1", "(All)")
    t.put("E1", "Sale source data"); t.put("F1", pre_total, ACC0)
    t.put("G1", "Diff"); t.put("H1", pre_total - pivot_pre, ACC0)
    t.put("I1", _verdict(pre_total - pivot_pre, tol_pivot))
    t.put("F2", pivot_pre, ACC0); t.put("G2", float(piv["wv"].sum()), ACC0)
    t.label_row(3, 1, ["Source.Name", "SKU phân loại hàng", "Tên sản phẩm",
                       "Sum of số lượng KA sử dụng trước VAT", "Sum of Aveg Price KA sử dụng",
                       "Sum of Check Amount No VAT", "Sum of Check total"])
    t.emit(piv[["store", "sku_id", "sku_name", "qty", "aveg", "pre", "wv"]], data_start_row=4,
           fmts=[None, None, None, ACC0, ACC0, ACC0, ACC0],
           widths={"A": 30, "B": 22, "C": 46, "F": 18, "G": 18})
    checks.append({"tab": "PV xuat HD", "check": "pre-VAT lines vs exact SKU pivot",
                   "diff": pre_total - pivot_pre, "tol": tol_pivot,
                   "verdict": _verdict(pre_total - pivot_pre, tol_pivot)})
    return wb, checks


# ---------------------------------------------------------------------------
# Shopee — tabs: PV sum, Xuat HD bt, return, 1.05 KA, Curel, KAO, Merries,
#                Kate, Others, 1.08 KA, 1.10 KA, PV xuat HD (template order)
# ---------------------------------------------------------------------------

def build_shopee(sku: pd.DataFrame, settings: dict, meta: dict, log: RunLog) -> tuple[Workbook, list[dict]]:
    tol = (settings.get("tolerances") or {}).get("shopee") or {}
    tol_line = float(tol.get("xuat_hd_vnd", 2000))
    tol_ret = float(tol.get("return_full_vnd", 10))
    tol_drift = TOL_PIVOT_DRIFT_SHOPEE
    wb = Workbook(write_only=True)
    checks: list[dict] = []

    df = sku.sort_values(["store", "order_id"]).copy()
    ok = df[df["check_status"] == "ok"].copy()
    ret = df[df["check_status"] == "Return"].copy()
    ok["bucket"] = ok["store"].map(lambda s: _bucket(s, SHOPEE_BUCKETS, "Others"))

    pre_total = float(ok["amount_pre_vat"].sum())
    wv_total = float(ok["amount_with_vat"].sum())
    by_rate_pre = {r: float(ok.loc[ok["vat_factor"].round(2) == r, "amount_pre_vat"].sum())
                   for r in VAT_RATES}
    recomb_wv = sum(v * r for r, v in by_rate_pre.items())

    # Pivot frames first (PV sum reads their recombined totals).
    brand_names = ("Curel", "KAO", "Merries", "Kate", "Others")
    brand_piv = {b: _sku_pivot(ok[ok["bucket"] == b], ["sku_id", "sku_name"], recombine=True)
                 for b in brand_names}
    brand_pre = {b: float(p["pre"].sum()) for b, p in brand_piv.items()}
    vat_piv = {r: _sku_pivot(ok[ok["vat_factor"].round(2) == r],
                             ["store", "sku_id", "sku_name"], recombine=True)
               for r in VAT_RATES}
    vat_pre = {r: float(p["pre"].sum()) for r, p in vat_piv.items()}

    # Partial-return invoiceable total = the template's 'return'!Q5.
    per_order = ret.groupby("order_id", as_index=False).agg(
        total=("amount_with_vat", "sum"), refund=("actual_refund", "max"))
    per_order["refund_adj"] = per_order["refund"].fillna(0).map(lambda v: -v if v > 0 else v)
    per_order["check"] = per_order["total"] + per_order["refund_adj"]
    partial_total = float(per_order["check"].sum())

    # --- PV sum (verdict wired to the real diff; template's G3 read blank E2) ---
    t = _Tab(wb, "PV sum")
    pv = ok.copy()
    pv["com"] = pd.to_datetime(pv["income_order_created_at"]).dt.month
    pv["fm"] = pd.to_datetime(pv["statement_date"]).dt.month
    pvt = pv.groupby(["store", "com", "fm", "vat_factor"], as_index=False).agg(
        pre=("amount_pre_vat", "sum"), wv=("amount_with_vat", "sum"))
    side_rows = ([("5", None, vat_pre[1.05], 1.05)]
                 + [("8", b, brand_pre[b], 1.08) for b in ("Others", "Curel", "KAO", "Merries", "Kate")]
                 + [("10", None, vat_pre[1.10], 1.10)])
    j9 = sum(v for _, _, v, _ in side_rows)
    k9 = sum(v * f for _, _, v, f in side_rows)
    t.put("J1", "Sum of Amount before VAT"); t.put("K1", "Amount after VAT")
    t.put("F2", "Diff"); t.put("G2", j9 - pre_total, ACC0)
    t.put("E3", pre_total, ACC0); t.put("F3", wv_total, ACC0)
    t.put("G3", _verdict(j9 - pre_total, tol_drift, PVSUM_OK, PVSUM_BAD))
    for i, (rate_lab, name, val, factor) in enumerate(side_rows):
        r = 2 + i
        t.put(f"H{r}", rate_lab)
        if name:
            t.put(f"I{r}", name)
        t.put(f"J{r}", val, ACC0)
        t.put(f"K{r}", val * factor, ACC0)
    t.put("J9", j9, ACC0); t.put("K9", k9, ACC0)
    t.put("I10", "Diff"); t.put("J10", pre_total - j9, ACC0); t.put("K10", wv_total - k9, ACC0)
    t.put("J11", _verdict(pre_total - j9, tol_drift, PVSUM_OK, PVSUM_BAD))
    t.put("K11", _verdict(wv_total - k9, tol_drift, PVSUM_OK, PVSUM_BAD))
    t.label_row(4, 1, ["Source.Name", "Create_Order_Month", "Finance_Month", "VAT KA sử dụng",
                       "Sum of Amount before VAT", "Sum of Check total"])
    t.emit(pvt[["store", "com", "fm", "vat_factor", "pre", "wv"]], data_start_row=5,
           fmts=[None, None, None, ACC2, ACC0, ACC0],
           widths={"A": 34, "E": 20, "F": 20, "I": 10, "J": 18, "K": 18})
    checks.append({"tab": "PV sum", "check": "invoice pivots vs exact books (pre-VAT rounding drift)",
                   "diff": pre_total - j9, "tol": tol_drift,
                   "verdict": _verdict(pre_total - j9, tol_drift, PVSUM_OK, PVSUM_BAD)})

    # --- Xuat HD bt (15 cols incl refund; control block exact) ---
    t = _Tab(wb, "Xuat HD bt")
    out = pd.DataFrame({
        "Diff": ok["check_status"],
        "Source.Name": ok["store"],
        "Mã đơn hàng": ok["order_id"],
        "Source.Name non repeat": ok["store"],
        "Mã đơn hàng non repeat": ok["order_id"],
        "Create_Order_Month": pd.to_datetime(ok["income_order_created_at"]).dt.month,
        "Finance_Month": pd.to_datetime(ok["statement_date"]).dt.month,
        "SKU phân loại hàng": ok["sku_id"],
        "Tên sản phẩm": ok["sku_name"],
        "VAT KA sử dụng": ok["vat_factor"],
        "Đơn giá KA sử dụng trước VAT": ok["unit_price_pre_vat"],
        "số lượng KA sử dụng trước VAT": ok["quantity"],
        "Amount before VAT": ok["amount_pre_vat"],
        "Check total": ok["amount_with_vat"],
        "Số tiền hoàn trả cho Người mua (₫)": ok["actual_refund"].replace(0, pd.NA),
    })
    out = _blank_repeats(out, "Mã đơn hàng", ["Source.Name non repeat", "Mã đơn hàng non repeat"])
    t.put("N1", "check"); t.put("O1", "no VAT"); t.put("P1", "with VAT"); t.put("Q1", "check")
    t.put("M2", "Tổng xuất HD (lines + return 1 phần)"); t.put("N2", wv_total + partial_total, ACC0)
    t.put("M3", "Return 1 phần phải xuất HD"); t.put("N3", partial_total, ACC0)
    for i, r in enumerate(VAT_RATES):
        t.put(f"L{2 + i}", r, ACC2)
        t.put(f"O{2 + i}", by_rate_pre[r], ACC0)
        t.put(f"P{2 + i}", by_rate_pre[r] * r, ACC0)
    line_diff = wv_total - recomb_wv
    t.put("M5", "total"); t.put("N5", wv_total, ACC0); t.put("P5", recomb_wv, ACC0)
    t.put("Q5", line_diff, ACC0); t.put("R5", _verdict(line_diff, tol_line))
    t.label_row(6, 1, list(out.columns))
    t.emit(out, data_start_row=7,
           fmts=[None, None, None, None, None, None, None, None, None, ACC2, ACC0, ACC0, ACC0, ACC0, ACC0],
           widths={"B": 26, "C": 20, "I": 44, "K": 16, "M": 16, "N": 16}, freeze="A7")
    checks.append({"tab": "Xuat HD bt", "check": "with-VAT lines vs exact VAT recombination",
                   "diff": line_diff, "tol": tol_line, "verdict": _verdict(line_diff, tol_line)})

    # --- return (template geometry starts at col C with an unlabeled check
    #     column; cleaned: anchored at A, suffixed repeat headers, 'Check'
    #     labeled — column CONTENT and order unchanged) ---
    t = _Tab(wb, "return")
    rtab = pd.DataFrame({
        "Diff": ret["check_status"],
        "Source.Name": ret["store"],
        "Mã đơn hàng": ret["order_id"],
        "Source.Name non repeat": ret["store"],
        "Mã đơn hàng non repeat": ret["order_id"],
        "SKU phân loại hàng": ret["sku_id"],
        "Tên sản phẩm": ret["sku_name"],
        "VAT KA sử dụng": ret["vat_factor"],
        "Đơn giá KA sử dụng trước VAT": ret["unit_price_pre_vat"],
        "số lượng KA sử dụng trước VAT": ret["quantity"],
        "Cộng tiền hàng KA sử dụng trước VAT": ret["amount_pre_vat"],
        "Cộng tiền hàng KA sử dụng có VAT": ret["amount_with_vat"],
    })
    order_map = per_order.set_index("order_id")
    rtab["Total by order"] = ret["order_id"].map(order_map["total"]).values
    rtab["Số tiền hoàn trả cho Người mua (₫)"] = ret["order_id"].map(order_map["refund_adj"]).values
    rtab["Check"] = ret["order_id"].map(order_map["check"]).values
    rtab["Note"] = rtab["Check"].map(lambda v: RETURN_FULL if abs(v) < tol_ret else RETURN_PARTIAL)
    rtab = _blank_repeats(rtab, "Mã đơn hàng",
                          ["Source.Name non repeat", "Mã đơn hàng non repeat",
                           "Total by order", "Số tiền hoàn trả cho Người mua (₫)", "Check", "Note"])
    t.put("B2", "Return bang dung so tien mua")
    t.put("M5", float(per_order["total"].sum()), ACC0)
    t.put("N5", float(per_order["refund_adj"].sum()), ACC0)
    t.put("O5", partial_total, ACC0)
    t.label_row(6, 1, list(rtab.columns))
    t.emit(rtab, data_start_row=7,
           fmts=[None, None, None, None, None, None, None, ACC2, ACC0, ACC0, ACC0, ACC0, ACC0, ACC0, ACC0, None],
           widths={"B": 26, "C": 20, "G": 44, "M": 16, "N": 16, "O": 14, "P": 26}, freeze="A7")
    n_partial = int((per_order["check"].abs() >= tol_ret).sum())
    checks.append({"tab": "return", "check": f"partial returns to invoice: {n_partial} orders",
                   "diff": partial_total, "tol": tol_ret, "verdict": f"{partial_total:,.0f} VND"})

    # --- brand + VAT pivot tabs (recombined), in the template's tab order ---
    def brand_tab(name: str) -> None:
        t = _Tab(wb, name)
        piv = brand_piv[name]
        sub = ok[ok["bucket"] == name]
        stores = sub["store"].unique()
        t.put("A1", "VAT KA sử dụng")
        t.put("B1", float(sub["vat_factor"].max()) if len(sub) else None, ACC2)
        t.put("A2", "Source.Name"); t.put("B2", stores[0] if len(stores) == 1 else "(Multiple Items)")
        t.put("E3", brand_pre[name], ACC0)   # template total position: brand!E3
        t.label_row(4, 1, ["SKU phân loại hàng", "Tên sản phẩm", "Sum of số lượng KA sử dụng trước VAT",
                           "Sum of Aveg Price KA sử dụng", "Sum of Check Amount No VAT"])
        t.emit(piv[["sku_id", "sku_name", "qty", "aveg", "pre"]], data_start_row=5,
               fmts=[None, None, ACC0, ACC0, ACC0], widths={"A": 22, "B": 46, "E": 18})

    def vat_tab(name: str, rate: float) -> None:
        t = _Tab(wb, name)
        piv = vat_piv[rate]
        t.put("A1", "VAT KA sử dụng"); t.put("B1", rate if len(piv) else None, ACC2)
        t.put("F2", vat_pre[rate], ACC0)     # template total position: ' X KA'!F2
        t.label_row(3, 1, ["Source.Name", "SKU phân loại hàng", "Tên sản phẩm",
                           "Sum of số lượng KA sử dụng trước VAT", "Sum of Aveg Price KA sử dụng",
                           "Sum of Check Amount No VAT"])
        t.emit(piv[["store", "sku_id", "sku_name", "qty", "aveg", "pre"]], data_start_row=4,
               fmts=[None, None, None, ACC0, ACC0, ACC0], widths={"A": 30, "B": 22, "C": 46, "F": 18})

    vat_tab("1.05 KA", 1.05)
    for b in brand_names:
        brand_tab(b)
    vat_tab("1.08 KA", 1.08)
    vat_tab("1.10 KA", 1.10)

    # --- PV xuat HD (exact SKU pivot; drop-detection check at 1,000) ---
    t = _Tab(wb, "PV xuat HD")
    piv = _sku_pivot(ok, ["store", "sku_id", "sku_name"], recombine=False)
    pivot_pre = float(piv["pre"].sum())
    t.put("A1", "VAT KA sử dụng"); t.put("B1", "(All)")
    t.put("E1", "Sale source data"); t.put("F1", pre_total, ACC0)
    t.put("G1", "Diff"); t.put("H1", pre_total - pivot_pre, ACC0)
    t.put("I1", _verdict(pre_total - pivot_pre, TOL_SKU_PIVOT))
    t.put("E2", pivot_pre, ACC0); t.put("F2", float(piv["wv"].sum()), ACC0)
    t.label_row(3, 1, ["Source.Name", "SKU phân loại hàng", "Tên sản phẩm",
                       "Sum of số lượng KA sử dụng trước VAT", "Sum of Amount before VAT",
                       "Sum of Check total"])
    t.emit(piv[["store", "sku_id", "sku_name", "qty", "pre", "wv"]], data_start_row=4,
           fmts=[None, None, None, ACC0, ACC0, ACC0], widths={"A": 30, "B": 22, "C": 46, "E": 18, "F": 18})
    checks.append({"tab": "PV xuat HD", "check": "pre-VAT lines vs exact SKU pivot",
                   "diff": pre_total - pivot_pre, "tol": TOL_SKU_PIVOT,
                   "verdict": _verdict(pre_total - pivot_pre, TOL_SKU_PIVOT)})
    return wb, checks


# ---------------------------------------------------------------------------
# Lazada — tabs (cleaned order): Summary, then the 1.05/1.08/1.10 pairs
# ('X KA used' + 'X'), then brand tabs. The template scattered these.
# ---------------------------------------------------------------------------

def build_lazada(rev: pd.DataFrame, settings: dict, meta: dict, log: RunLog,
                 classified: pd.DataFrame | None = None) -> tuple[Workbook, list[dict]]:
    wb = Workbook(write_only=True)
    checks: list[dict] = []
    month_label = meta.get("month_label", "")
    period_label = meta.get("period_label", "")

    df = rev.sort_values(["store", "order_id"]).copy()
    df["bucket"] = df["store"].map(lambda s: _bucket(s, LAZADA_BUCKETS, "Others"))
    df["money"] = df["credits"].fillna(0) + df["promo"].fillna(0)   # actual settled VND incl VAT

    # Order-less revenue: ledger rows the team's Lib maps to 1.Doanh Thu but
    # that carry no order/SKU/quantity (July 2026: "Lost & Damaged FBL
    # Inventories" compensation). They are real settled money — kept in the
    # sale-report figure — but can never be invoice lines, so they appear as
    # a NAMED reconciling row in each control block instead of silently
    # distorting (old plain export) or failing (naive check) it.
    orderless_mask = (df["quantity"].fillna(0) <= 0) | df["order_id"].isin(["0", "", "nan", "None"])
    orderless = df[orderless_mask]
    if len(orderless):
        for _, r in orderless.iterrows():
            log.warn(f"order-less revenue kept as reconciling item, NOT an invoice line: "
                     f"{r['store']} {r['money']:,.0f} VND @ {r['vat_rate']}")
    df = df[~orderless_mask]

    def laz_pivot(sub: pd.DataFrame) -> pd.DataFrame:
        """Invoice-view SKU pivot: rounded average price x qty (per-SKU rate)."""
        g = sub.groupby(["sku_id", "product_name"], as_index=False, dropna=False).agg(
            qty=("quantity", "sum"), no_vat_exact=("check_no_vat", "sum"),
            rate=("vat_rate", "max"))
        g["aveg"] = (g["no_vat_exact"] / g["qty"].replace(0, pd.NA)).fillna(0).round(0)
        g["no_vat"] = g["aveg"] * g["qty"]
        g["wv"] = (g["no_vat"] * g["rate"]).round(2)
        return g

    def at(rate: float) -> pd.DataFrame:
        return df[df["vat_rate"].round(2) == rate]

    # Sale report = ALL Doanh Thu money incl. order-less items (matching the
    # team's bucket view); the invoiceable portion excludes them.
    orderless_rate = {r: float(orderless.loc[orderless["vat_rate"].round(2) == r, "money"].sum())
                      for r in VAT_RATES}
    sale_report = {r: float(at(r)["money"].sum()) + orderless_rate[r] for r in VAT_RATES}
    no_vat_exact = {r: float(at(r)["check_no_vat"].sum()) for r in VAT_RATES}
    rate_piv = {r: laz_pivot(at(r)) for r in VAT_RATES}
    brand_piv = {b: laz_pivot(df[df["bucket"] == b])
                 for b in ("Curel.xlsx", "KAO.xlsx", "Merries.xlsx", "Others")}
    brand_wv = {b: float(p["wv"].sum()) for b, p in brand_piv.items()}
    rate_wv = {r: float(p["wv"].sum()) for r, p in rate_piv.items()}
    rate_novat = {r: float(p["no_vat"].sum()) for r, p in rate_piv.items()}

    # --- Summary (template row positions D6:F17) ---
    t = _Tab(wb, "Summary")
    t.label_row(6, 4, ["Type", "VAT rate KA use", "Total order"])
    t.put("E7", 1.05, ACC2); t.put("F7", rate_wv[1.05], PLAIN2)
    for i, b in enumerate(["Others", "Curel.xlsx", "KAO.xlsx", "Merries.xlsx"]):
        r = 8 + i
        t.put(f"D{r}", b.replace(".xlsx", ""))
        t.put(f"E{r}", 1.08, ACC2)
        t.put(f"F{r}", brand_wv[b], PLAIN2)
    t.put("E12", 1.10, ACC2); t.put("F12", rate_wv[1.10], PLAIN2)
    total_wv = rate_wv[1.05] + float(sum(brand_wv.values())) + rate_wv[1.10]
    report_total = sum(sale_report.values())
    orderless_total = float(sum(orderless_rate.values()))
    prior_invoiced = 0.0   # "Order đã xuất HD trước đó" — manual field, defaults to 0
    summary_diff = report_total - orderless_total - prior_invoiced - total_wv
    t.put("D13", "Total"); t.put("F13", total_wv, PLAIN2)
    t.put("D14", "Per finance report"); t.put("F14", report_total, PLAIN2)
    t.put("D15", "Doanh thu không theo đơn hàng (không xuất HD dòng)")
    t.put("F15", orderless_total, PLAIN2)
    t.put("D16", "Order đã xuất HD trước đó"); t.put("F16", prior_invoiced, PLAIN2)
    t.put("D17", "Diff"); t.put("F17", summary_diff, PLAIN2)
    t.put("D18", "Check"); t.put("F18", _verdict(summary_diff, TOL_PIVOT_DRIFT_LAZADA))
    t.emit(widths={"D": 44, "E": 16, "F": 20})
    checks.append({"tab": "Summary", "check": "sale report vs invoice total (with VAT)",
                   "diff": summary_diff, "tol": TOL_PIVOT_DRIFT_LAZADA,
                   "verdict": _verdict(summary_diff, TOL_PIVOT_DRIFT_LAZADA)})

    # --- per-rate pair: 'X KA used' (SKU invoice view) + 'X' (exact lines) ---
    for rate in VAT_RATES:
        key = {1.05: "1.05", 1.08: "1.08", 1.10: "1.10"}[rate]
        sub = at(rate)
        piv = rate_piv[rate]

        t = _Tab(wb, f"{key} KA used")
        diff = rate_wv[rate] - (sale_report[rate] - orderless_rate[rate])
        t.put("A1", "Type"); t.put("B1", rate, ACC2)
        t.put("C1", "Month"); t.put("D1", month_label)
        t.put("F1", "số trên sale report"); t.put("G1", sale_report[rate], PLAIN2)
        t.put("H1", "Diff"); t.put("I1", diff, PLAIN2)
        t.put("J1", _verdict(diff, TOL_PIVOT_DRIFT_LAZADA))
        t.put("A2", "Period"); t.put("B2", period_label)
        if orderless_rate[rate]:
            t.put("F2", "trong đó: không theo đơn hàng")
            t.put("G2", orderless_rate[rate], PLAIN2)
        t.label_row(4, 1, ["KA Sử dụng cột này"] * 4 + ["check only", "check only"])
        t.put("E5", rate_novat[rate], PLAIN0); t.put("F5", rate_wv[rate], PLAIN2)
        t.label_row(6, 1, ["Seller SKU", "Details", "Sum of Sum of Quantity",
                           "Sum of Average Price KA used", "Sum of Check average amount",
                           "Sum of  check avg amount with VAT"])
        t.emit(piv[["sku_id", "product_name", "qty", "aveg", "no_vat", "wv"]], data_start_row=7,
               fmts=[None, None, PLAIN0, PLAIN0, PLAIN0, PLAIN2],
               widths={"A": 22, "B": 50, "D": 18, "E": 18, "F": 20})
        checks.append({"tab": f"{key} KA used", "check": "invoice pivot with-VAT vs sale report",
                       "diff": diff, "tol": TOL_PIVOT_DRIFT_LAZADA,
                       "verdict": _verdict(diff, TOL_PIVOT_DRIFT_LAZADA)})

        t = _Tab(wb, key)
        lines = pd.DataFrame({
            "Source.Name": sub["store"],
            "Order No.": sub["order_id"],
            "Seller SKU": sub["sku_id"],
            "Details": sub["product_name"],
            "Sum of Price KA": sub["price_ka"],
            "Sum of Quantity": sub["quantity"],
            "Đơn Vị Tính": None,
            "Amount": sub["check_no_vat"],
            "Xuất HD khách": None,
            "Note": None,
        })
        vat_amount = no_vat_exact[rate] * rate
        line_diff = sale_report[rate] - orderless_rate[rate] - vat_amount
        t.put("A1", "Type"); t.put("B1", rate, ACC2)
        t.put("C1", "Month"); t.put("D1", month_label)
        t.put("A2", "Period"); t.put("B2", period_label)
        t.put("G2", "Diff"); t.put("H2", line_diff, PLAIN2)
        t.put("I2", _verdict(line_diff, TOL_LAZ_LINE))
        t.put("G3", "Số trên sale report"); t.put("H3", sale_report[rate], PLAIN2)
        t.put("G4", "VAT"); t.put("H4", vat_amount, PLAIN2)
        t.put("G5", "no Vat"); t.put("H5", no_vat_exact[rate], PLAIN0)
        if orderless_rate[rate]:
            t.put("G6", "Không theo đơn hàng (không xuất HD dòng)")
            t.put("H6", orderless_rate[rate], PLAIN2)
        t.label_row(6, 1, list(lines.columns))
        t.emit(lines, data_start_row=7,
               fmts=[None, None, None, None, PLAIN0, PLAIN0, None, ACC0, None, None],
               widths={"A": 24, "B": 20, "C": 22, "D": 50, "E": 14, "H": 16}, freeze="A7")
        checks.append({"tab": key, "check": "sale report vs exact lines x VAT",
                       "diff": line_diff, "tol": TOL_LAZ_LINE,
                       "verdict": _verdict(line_diff, TOL_LAZ_LINE)})

    # --- brand tabs (template: totals at F2, header row 3) ---
    for b in ("Curel.xlsx", "KAO.xlsx", "Merries.xlsx", "Others"):
        t = _Tab(wb, b)
        piv = brand_piv[b]
        stores = df.loc[df["bucket"] == b, "store"].unique()
        t.put("A1", "Source.Name"); t.put("B1", stores[0] if len(stores) == 1 else "(Multiple Items)")
        t.put("F2", brand_wv[b], PLAIN2)
        t.label_row(3, 1, ["Seller SKU", "Details", "Sum of Sum of Quantity",
                           "Sum of Average Price KA used", "Sum of Check average amount",
                           "Sum of  check avg amount with VAT"])
        t.emit(piv[["sku_id", "product_name", "qty", "aveg", "no_vat", "wv"]], data_start_row=4,
               fmts=[None, None, PLAIN0, PLAIN0, PLAIN0, PLAIN2],
               widths={"A": 22, "B": 50, "E": 18, "F": 20})

    # Extra tab beyond the team's template: the fee-bucket overview the
    # plain export used to carry (the team's own fee view lives in their
    # Total file's SUM CP, which the invoicing template does not include).
    if classified is not None and len(classified):
        t = _Tab(wb, "Fee buckets")
        buckets = (classified.pivot_table(index="store", columns="fee_bucket",
                                          values="amount_incl_vat", aggfunc="sum")
                   .round(2).reset_index())
        t.label_row(1, 1, [str(c) for c in buckets.columns])
        t.emit(buckets, data_start_row=2,
               fmts=[None] + [PLAIN2] * (buckets.shape[1] - 1),
               widths={"A": 30})
    return wb, checks


def write_workbook(wb: Workbook, path: Path, checks: list[dict], log: RunLog) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    log.add(f"  wrote {path.name} ({len(wb.sheetnames)} tabs: {wb.sheetnames})")
    for c in checks:
        log.add(f"    check [{c['tab']}] {c['check']}: diff {c['diff']:,.2f} "
                f"(tol {c['tol']:,.0f}) -> {c['verdict']}")

```


---

## `src/ingest.py`

```python
"""Stage 1 — Ingest & validate.

Reads all file parts per platform (.xlsx and .csv), maps raw headers to
canonical names via settings.yaml column maps, dedupes across parts, and
enforces the team's store-count sanity check as a hard stop.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from .errors import ReconHardStop
from .runlog import RunLog

REQUIRED_COLUMNS = {
    "orders": ["order_id", "sku_id", "sku_name", "quantity", "unit_price_gross", "order_created_at", "store"],
    "income": ["order_id", "store", "gross_revenue", "actual_refund", "net_revenue", "statement_date"],
}

NUMERIC_COLUMNS = {
    "orders": ["quantity", "unit_price_gross", "sku_seller_discount", "order_refund_amount",
               "sku_subtotal_after_discount", "seller_subsidy", "shopee_subsidy"],
    "income": ["gross_revenue", "actual_refund", "net_revenue", "subtotal_after_seller_discounts",
               "subtotal_before_discounts", "refund_subtotal_after_sd", "refund_subtotal_before_sd",
               "cofund_voucher", "seller_voucher", "seller_coin_cashback", "seller_ship_support",
               "shopee_product_subsidy"],
}

DATE_COLUMNS = {
    "orders": ["order_created_at"],
    "income": ["statement_date", "income_order_created_at"],
}


def apply_settlement_bounds(income: pd.DataFrame, period: str, settings: dict,
                            log: RunLog) -> pd.DataFrame:
    """Drop income rows settled outside a window's own labelled date range.

    Only windows listed under settings['window_settlement_bounds'] are
    affected — used where a raw export was pulled with the wrong start/end
    date and carries the adjacent window's settlements (see the evidence
    comment beside each entry in settings.yaml). Rows with no settlement
    date are KEPT and reported: they cannot be attributed to a window, and
    dropping them silently would hide data.
    """
    bounds = (settings.get("window_settlement_bounds") or {}).get(period)
    if not bounds or "statement_date" not in income.columns:
        return income

    d = income["statement_date"]
    keep = pd.Series(True, index=income.index)
    if bounds.get("from"):
        keep &= (d >= pd.Timestamp(bounds["from"])) | d.isna()
    if bounds.get("to"):
        keep &= (d <= pd.Timestamp(bounds["to"])) | d.isna()

    dropped = int((~keep).sum())
    undated = int(d.isna().sum())
    log.add(f"  settlement bounds for {period} ({bounds}): dropped {dropped} "
            f"out-of-window income row(s) of {len(income)}")
    if dropped:
        for day, n in d[~keep].dt.date.value_counts().sort_index().items():
            log.add(f"    dropped settled {day}: {n} row(s)")
    if undated:
        log.warn(f"{undated} income row(s) have no settlement date and were KEPT "
                 f"(cannot be attributed to a window)")
    return income[keep].copy()


def to_number(series: pd.Series, style: str) -> pd.Series:
    """Parse amount strings. 'standard' = 1,234,567.89 · 'vietnamese' = 1.234.567,89"""
    t = series.astype(str).str.strip()
    t = t.replace({"": None, "nan": None, "None": None})
    if style == "vietnamese":
        t = t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        t = t.str.replace(",", "", regex=False)
    return pd.to_numeric(t, errors="coerce")


def _read_excel_sheet(path: Path, sheet, header_row: int, engine: str | None = None) -> pd.DataFrame:
    """Read one xlsx sheet as strings, header on `header_row` (1-based).

    Some exports (June 2026 TikTok order files) ship a broken `<dimension>`
    tag that makes the default openpyxl streaming reader — even after
    reset_dimensions — see only column A. `calamine` ignores the dimension
    and reads the real cells. Set reader_engine.<platform>.<kind>: calamine
    in settings for a source known to be broken (skips a wasted openpyxl
    read); otherwise the openpyxl fast path runs and calamine is a
    single-column safety-net fallback. Well-formed sources keep the exact
    openpyxl path, so verified May behaviour is untouched."""
    if engine == "calamine":
        return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                             engine="calamine")
    df = pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1)
    if df.shape[1] > 1:
        return df
    return pd.read_excel(path, dtype=str, sheet_name=sheet, header=header_row - 1,
                         engine="calamine")


def _store_from_filename(filename: str, pattern: str) -> str:
    m = re.match(pattern, filename, flags=re.IGNORECASE)
    if not m or not (m.group(1) or "").strip():
        raise ReconHardStop(
            f"Could not derive the store name from file name '{filename}' "
            f"(store_from_filename pattern: {pattern}). The export has no store "
            f"column, so the file name must identify the store."
        )
    return m.group(1).strip()


def read_parts(
    folder: Path, colmap: dict[str, str], kind: str, settings: dict, log: RunLog, platform: str
) -> pd.DataFrame:
    """Read every file part in `folder`, rename to canonical columns, dedupe."""
    if not folder.is_dir():
        raise ReconHardStop(f"Input folder not found: {folder}")

    suffixes = tuple(s.lower() for s in settings.get("file_formats", [".xlsx", ".csv"]))
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in suffixes)
    if not files:
        raise ReconHardStop(f"No {kind} files found in {folder} (expected {suffixes})")

    sheet = ((settings.get("sheet_names") or {}).get(platform) or {}).get(kind)
    # Regex alternative to sheet_names: platforms that split data across
    # numbered sheets (Shopee income: "Doanh thu", "Doanh thu - 1", ...) get
    # every matching sheet read and concatenated — same as the team's M code
    # (Text.Contains([Name.1], "Doanh thu")).
    sheet_regex = ((settings.get("sheet_patterns") or {}).get(platform) or {}).get(kind)
    # 1-based row that holds the real (leaf) headers. Shopee income has two
    # band/group rows above the leaf header row (triple PromoteHeaders in the
    # team's M code -> header_row 3).
    header_row = int(((settings.get("header_rows") or {}).get(platform) or {}).get(kind, 1))
    store_pattern = (settings.get("store_from_filename") or {}).get(platform)
    skip = ((settings.get("skip_rows_after_header") or {}).get(platform) or {}).get(kind, 0)
    engine = ((settings.get("reader_engine") or {}).get(platform) or {}).get(kind)

    frames: list[pd.DataFrame] = []
    for f in files:
        if f.suffix.lower() == ".csv":
            df = pd.read_csv(f, dtype=str, encoding="utf-8-sig")
        elif sheet_regex:
            xf = pd.ExcelFile(f, engine="calamine" if engine == "calamine" else None)
            matches = [s for s in xf.sheet_names if re.search(sheet_regex, s)]
            if not matches:
                raise ReconHardStop(
                    f"{f.name}: no sheet matching /{sheet_regex}/ (sheets: {xf.sheet_names})")
            df = pd.concat(
                [_read_excel_sheet(f, s, header_row, engine) for s in matches], ignore_index=True)
        else:
            df = _read_excel_sheet(f, sheet if sheet else 0, header_row, engine)
        if skip:
            df = df.iloc[skip:]
        df.columns = [str(c).strip() for c in df.columns]
        present = {src: dst for src, dst in colmap.items() if src in df.columns}
        missing = [src for src in colmap if src not in df.columns]
        df = df.rename(columns=present)
        # TikTok/Shopee exports carry no store column — the per-store download
        # file name is the store identity.
        if "store" not in df.columns and store_pattern:
            df["store"] = _store_from_filename(f.name, store_pattern)
        df["source_file"] = f.name
        # Real config drops unmapped raw columns at read time: nothing
        # downstream uses them, it strips PII columns immediately (the
        # team's own Shopee M code does the same), and it keeps full-
        # platform runs within memory.
        if settings.get("drop_unmapped_columns", False):
            keep = set(colmap.values()) | {"store", "source_file"}
            df = df[[c for c in df.columns if c in keep]]
        log.add(f"  {f.name}: {len(df)} rows" + (f" (headers not found: {missing})" if missing else ""))
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    before = len(combined)
    # Row dedupe is OFF for the real platforms: an order can legitimately
    # contain two byte-identical SKU lines (e.g. duplicated gift items —
    # Sanofi Shopee May), and the team's Power Query never dedupes; their
    # per-window folder discipline is the overlap protection. The synthetic
    # sample path (tools/sample_config) still sets dedupe_rows: true because
    # its generator bakes overlapping parts.
    if settings.get("dedupe_rows", True):
        data_cols = [c for c in combined.columns if c != "source_file"]
        combined = combined.drop_duplicates(subset=data_cols, ignore_index=True)
    dupes = before - len(combined)
    log.add(f"  {kind}: {len(files)} part(s), {before} rows, {dupes} duplicate rows dropped across parts")

    missing_required = [c for c in REQUIRED_COLUMNS[kind] if c not in combined.columns]
    if missing_required:
        raise ReconHardStop(
            f"{kind} data is missing required columns after header mapping: {missing_required}. "
            f"Update column_maps.{kind} in settings.yaml to match the real export headers."
        )

    style = settings.get("number_style", "standard")
    dayfirst = bool((settings.get("dayfirst") or {}).get(platform, False))
    for col in NUMERIC_COLUMNS[kind]:
        if col in combined.columns:
            combined[col] = to_number(combined[col], style)
    for col in DATE_COLUMNS[kind]:
        if col in combined.columns:
            combined[col] = pd.to_datetime(combined[col], errors="coerce", dayfirst=dayfirst)
    combined["store"] = combined["store"].astype(str).str.strip()
    combined["order_id"] = combined["order_id"].astype(str).str.strip()

    aliases = (settings.get("store_aliases") or {}).get(platform) or {}
    if aliases:
        stores_found = set(combined["store"])
        unresolved = [s for s, canon in aliases.items() if canon == "TODO-HUMAN" and s in stores_found]
        if unresolved:
            log.warn(f"Stores with UNRESOLVED alias (TODO-HUMAN in store_aliases): {unresolved}")
        combined["store"] = combined["store"].replace(
            {s: canon for s, canon in aliases.items() if canon != "TODO-HUMAN"})
    return combined


def derive_brand(df: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """Brand comes from the store→brand map; unmapped stores keep the store name."""
    mapping = settings.get("store_to_brand") or {}
    df = df.copy()
    df["brand"] = df["store"].map(mapping)
    unmapped = sorted(df.loc[df["brand"].isna(), "store"].unique())
    if unmapped:
        log.warn(f"Stores with no store_to_brand mapping (brand falls back to store name): {unmapped}")
        df["brand"] = df["brand"].fillna(df["store"])
    return df


def check_stores(df: pd.DataFrame, kind: str, platform: str, settings: dict, log: RunLog) -> None:
    """The team's existing sanity check, codified: stores in data must equal
    the expected list. Mismatch → hard stop with named stores."""
    expected = set((settings.get("expected_stores") or {}).get(platform) or [])
    if not expected:
        log.warn(f"expected_stores.{platform} not configured — store-count check SKIPPED for {kind}")
        return
    # Stores that legitimately appear only in some windows (e.g. TikTok's
    # "Nutifood Nutrition Store" onboarded mid-May): warn when absent
    # instead of hard-stopping.
    optional = set((settings.get("stores_optional") or {}).get(platform) or [])
    found = set(df["store"].dropna().unique())
    missing = sorted(expected - found - optional)
    missing_optional = sorted((expected & optional) - found)
    unexpected = sorted(found - expected)
    if missing or unexpected:
        raise ReconHardStop(
            f"Store-count check FAILED for {platform}/{kind}. "
            f"Missing stores: {missing or 'none'}. Unexpected stores: {unexpected or 'none'}."
        )
    if missing_optional:
        log.warn(f"optional store(s) absent from {kind} (allowed): {missing_optional}")
    log.add(f"  store check {kind}: OK ({len(found)}/{len(expected)} expected stores present)")

```


---

## `src/lazada.py`

```python
"""Lazada reconciliation path — a transaction LEDGER, not order+income.

Evidence (all from the team's own files, May 2026):
- Source = per-store ledger exports: Weekly files (sheet "Transaction
  Overview") or Daily files (sheet "Income Overview", different schema) —
  one row per (order item x fee event). There are NO order files.
  The Daily-format week (25th-end) is a PERMANENT monthly fixture, so
  dual-schema handling is standard, not an exception.
- The Total files' Power Query (DataMashup "Weekly"/"Daily"/"FR_Total")
  normalizes both variants to one table; FR_Total = Daily UNION Weekly.
- Per-row computed columns (FR_Total sheet, row 2 evidence):
    Fee Type   = Lib.xlsx fee-name -> bucket ("Item Price Credit" ->
                 "1.Doanh Thu", "Commission" -> "6.CP co Invoice", ...)
    Xuat HD    = bt/exp status via Lib
    VAT rate   = VAT_SKU.xlsx lookup by Seller SKU, default 1.08
                 (May master: 664 SKUs @ 1.08, 4 @ 1.05)
    Amount no VAT = Amount(Include Tax) / VAT rate
- Revenue = gross "Item Price Credit" lines (even free gifts credit full
  price); promotional charges are SEPARATE ledger lines in their own
  buckets — no discount allocation ever touches revenue lines.
- Refunds: NO credit notes (confirmed by the team) — refund/reversal fee
  lines net into final sales through the Lib bucket mapping, which is
  exactly what this module does. Nothing further to build there.
- Invoicing pivots group revenue lines per (store, order, Seller SKU):
  Price KA (pre-VAT) + quantity, split by VAT bucket, cross-checked against
  the sale report with |diff| < 1000 (1.05/1.08) or < 2000 (1.10).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .errors import ReconHardStop
from .ingest import to_number
from .runlog import RunLog

# Verified 2026-07-17 by tools/calc_verify_lazada.py against the Total files'
# "PV used" pivots and "SUM CP" bucket totals — Curel (cleanest) and
# "Unilever 2" Chăm Sóc Vẻ Đẹp (refund-heaviest), windows 11-17T5 (Weekly
# schema) AND 25-31T5 (Daily schema): 1,306 revenue lines + all fee buckets
# exact. Caveats: the 4 SKUs at VAT 1.05 did not trade in the verified
# windows (non-1.08 unexercised); Lib/VAT_SKU config CSVs are point-in-time
# ports of the team's masters and must be refreshed when those change.
LAZADA_FORMULA_STATUS = {
    "ledger_union_weekly_daily": "verified",
    "fee_bucket_classification": "verified",   # Lib.xlsx port, 0 unmapped in May
    "vat_rate_per_sku": "verified-1.08-only",  # VAT_SKU port; 1.05 SKUs didn't trade
    "amount_no_vat": "verified",               # amount / rate
    "price_ka_unit_promo_netted": "verified",  # round((credits+promo)/units/VAT)
    "check_totals": "verified",                # E*F and E*F*VAT vs 'PV used'
}

REVENUE_BUCKET = "1.Doanh Thu"
# Promotional buckets whose per-(order, SKU) charges net INTO the invoiced
# unit price (evidence: gift lines credit full price, their Flexi-Combo
# chargeback zeroes them in "PV used"; voucher/coin lines reduce the paired
# product's Price KA by exactly their allocated amount / VAT).
PROMO_BUCKETS = [
    "2.Promotional Charges Flexi-Combo",
    "3.Promotional Charges Vouchers",
    "3.1 Seller Funded Marketing Voucher",
    "4.1 LazCoints Discount",
    "5. Lazcoin discount",
]

# Canonical ledger columns (superset both export variants map into):
LEDGER_REQUIRED = ["store", "transaction_date", "fee_name", "amount_incl_vat",
                   "vat_amount", "order_id", "order_line_id", "sku_id"]

WEEKLY_MAP = {  # sheet "Transaction Overview", header row 1
    "Transaction Date": "transaction_date",
    "Fee Name": "fee_name",
    "Details": "product_name",
    "Seller SKU": "sku_id",
    "Lazada SKU": "lazada_sku",
    "Amount": "amount_incl_vat",
    "VAT in Amount": "vat_amount",
    "Order No.": "order_id",
    "Order Item No.": "order_line_id",
}
DAILY_MAP = {  # sheet "Income Overview" (the 25-31T05 window uses these)
    "Transaction Date": "transaction_date",
    "Fee Name": "fee_name",
    "Product Name": "product_name",
    "Seller SKU": "sku_id",
    "Lazada SKU": "lazada_sku",
    "Amount(Include Tax)": "amount_incl_vat",
    "VAT Amount": "vat_amount",
    "Order Number": "order_id",
    "Order Line ID": "order_line_id",
}
SHEETS = {"weekly": "Transaction Overview", "daily": "Income Overview"}

STORE_PATTERN = r"^\s*\d+_\s*(.+?)\s*\.xlsx$"  # "15_Masan.xlsx" -> "Masan"


def _read_ledger_file(f: Path, variant: str, log: RunLog) -> pd.DataFrame:
    cmap = WEEKLY_MAP if variant == "weekly" else DAILY_MAP
    df = pd.read_excel(f, dtype=str, sheet_name=SHEETS[variant])
    df.columns = [str(c).strip() for c in df.columns]
    missing = [src for src in cmap if src not in df.columns]
    df = df.rename(columns={s: d for s, d in cmap.items() if s in df.columns})
    m = re.match(STORE_PATTERN, f.name, flags=re.IGNORECASE)
    if not m:
        raise ReconHardStop(f"Cannot derive store from Lazada file name '{f.name}'")
    df["store"] = m.group(1).strip()
    df["source_file"] = f.name
    df["ledger_variant"] = variant
    log.add(f"  {f.name} [{variant}]: {len(df)} rows"
            + (f" (headers not found: {missing})" if missing else ""))
    return df


def read_ledger(period_dir: Path, settings: dict, log: RunLog) -> pd.DataFrame:
    """Read a window's Weekly/ and Daily/ folders (either may be empty) —
    the union mirrors the team's FR_Total query."""
    frames = []
    for variant in ("weekly", "daily"):
        folder = period_dir / variant.capitalize()
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.xlsx")):
            frames.append(_read_ledger_file(f, variant, log))
    if not frames:
        raise ReconHardStop(f"No Weekly/ or Daily/ ledger files under {period_dir}")
    df = pd.concat(frames, ignore_index=True)

    missing = [c for c in LEDGER_REQUIRED if c not in df.columns]
    if missing:
        raise ReconHardStop(f"Lazada ledger missing canonical columns: {missing}")
    style = settings.get("number_style", "standard")
    for col in ("amount_incl_vat", "vat_amount"):
        df[col] = to_number(df[col], style)
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    for col in ("order_id", "order_line_id", "sku_id", "fee_name", "store"):
        df[col] = df[col].astype(str).str.strip()
    log.add(f"  ledger: {len(df)} rows, {df['store'].nunique()} stores, "
            f"{df['order_id'].nunique()} orders")
    return df


def load_fee_type_map(config_dir: Path, log: RunLog, settings: dict | None = None) -> dict[str, dict]:
    """Fee name -> {bucket, status}. Reads the team-owned live master
    ("Lib & VAT rate.xlsb") when present, CSV snapshot otherwise — see
    src/masters.py. Unmapped fee names go to exceptions, never dropped."""
    from .masters import load_masters
    m = load_masters(config_dir, settings or {}, log)
    if not m["fee_types"]:
        raise ReconHardStop("No fee-type mapping available (master and snapshot both missing)")
    return m["fee_types"]


def load_vat_sku(config_dir: Path, log: RunLog, settings: dict | None = None) -> dict[str, float]:
    """SKU -> VAT factor from the live master / CSV snapshot (masters.py)."""
    from .masters import load_masters
    m = load_masters(config_dir, settings or {}, log)
    if not m["vat_sku"]:
        raise ReconHardStop("No VAT_SKU mapping available (master and snapshot both missing)")
    return m["vat_sku"]


def classify_ledger(df: pd.DataFrame, fee_types: dict, vat_sku: dict[str, float],
                    settings: dict, log: RunLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-row computed columns exactly as FR_Total: fee bucket + status via
    the Lib port, VAT rate via the VAT_SKU port, amount_no_vat = amount/rate.
    Returns (ledger with columns, unmapped-fee exception rows)."""
    df = df.copy()
    df["fee_bucket"] = df["fee_name"].map(lambda f: (fee_types.get(f) or {}).get("bucket"))
    df["xuat_hd"] = df["fee_name"].map(lambda f: (fee_types.get(f) or {}).get("status"))
    unmapped = df[df["fee_bucket"].isna()]
    if len(unmapped):
        log.warn(f"{len(unmapped)} ledger rows with fee names missing from the "
                 f"Lib port ({sorted(unmapped['fee_name'].unique())[:5]}) -> exceptions")

    default = float((settings.get("vat_factors") or {}).get("default", 1.08))
    df["vat_rate"] = df["sku_id"].map(vat_sku).fillna(default)
    df["amount_no_vat"] = df["amount_incl_vat"].fillna(0) / df["vat_rate"]

    for bucket, n in df["fee_bucket"].value_counts(dropna=False).head(8).items():
        log.add(f"  bucket {bucket}: {n} rows")
    return df, unmapped


def revenue_lines(df: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    """Invoicing base: revenue-bucket rows grouped per (store, order, SKU,
    product) — Price KA = summed pre-VAT amount, quantity = row count
    (one Item Price Credit row per unit; FR_Total Quantity = 1 per row)."""
    rev = df[df["fee_bucket"] == REVENUE_BUCKET]
    grouped = rev.groupby(["store", "order_id", "sku_id", "product_name"], as_index=False, dropna=False).agg(
        quantity=("order_line_id", "count"),
        credits=("amount_incl_vat", "sum"),
        vat_rate=("vat_rate", "max"),
    )
    # Promo pairing includes PRODUCT NAME: the same SKU can appear in one
    # order under two Details (e.g. a 35,000 CHIN-SU unit AND a 1,000,000
    # gift variant, Masan order 524944050276659) — pairing by (order, SKU)
    # alone double-applies the promo pool to both name-groups. Matching by
    # name reproduces the team's KA values exactly (gift groups zero out).
    promo_rows = df[df["fee_bucket"].isin(PROMO_BUCKETS)]
    promo = (promo_rows
             .groupby(["store", "order_id", "sku_id", "product_name"], as_index=False)["amount_incl_vat"].sum()
             .rename(columns={"amount_incl_vat": "promo"}))
    grouped = grouped.merge(promo, on=["store", "order_id", "sku_id", "product_name"], how="left")
    grouped["promo"] = grouped["promo"].fillna(0)
    matched_total = float(grouped["promo"].sum())
    promo_total = float(promo_rows["amount_incl_vat"].fillna(0).sum())
    if abs(promo_total - matched_total) > 0.5:
        log.warn(f"promo charges not matched to any revenue line: "
                 f"{promo_total - matched_total:,.0f} VND (left un-netted, as the team's pairing does)")

    # Team's "Price KA" = per-UNIT net price, rounded to whole VND (Excel
    # ROUND, half away from zero): (credits + promo) / units / VAT.
    # Evidence: gift line 1,000,000 credit - 1,000,000 Flexi-Combo -> 0;
    # Curel 2-unit line 500,000/2/1.08 -> 231,481; Unilever real product
    # (514,000 - 29,970)/1.08 -> 448,176 — all exact vs "PV used".
    x = ((grouped["credits"] + grouped["promo"])
         / grouped["quantity"].replace(0, pd.NA)).fillna(0) / grouped["vat_rate"]
    grouped["price_ka"] = np.where(x >= 0, np.floor(x + 0.5), np.ceil(x - 0.5))
    grouped["check_no_vat"] = grouped["price_ka"] * grouped["quantity"]
    grouped["check_with_vat"] = grouped["check_no_vat"] * grouped["vat_rate"]
    log.add(f"  revenue lines: {len(rev)} ledger rows -> {len(grouped)} (order x SKU) lines")
    return grouped

```


---

## `src/masters.py`

```python
"""Team-owned master data: fee-type mapping (Lib) and per-SKU VAT rates.

The team maintains "Lib & VAT rate.xlsb" (additive-only). At runtime the
pipeline reads that file when present (settings: masters_file, resolved
relative to config/); the ported CSVs (lazada_fee_types.csv,
lazada_vat_sku.csv) remain as fallback/snapshot, and any drift between the
live master and the snapshots is reported, never silently absorbed.

VAT model (all platforms): ONE default factor (vat_factors.default —
currently 1.08, a temporary tax concession; reverting to 1.10 is that one
line) plus per-SKU exceptions from the master's VAT sheet.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .runlog import RunLog


def _read_xlsb(path: Path) -> tuple[dict[str, dict], dict[str, float]]:
    from pyxlsb import open_workbook  # imported lazily: only needed with a master present

    fee_types: dict[str, dict] = {}
    vat_sku: dict[str, float] = {}
    with open_workbook(str(path)) as wb:
        with wb.get_sheet("Lib") as ws:
            rows = [[c.v for c in row][:6] for row in ws.rows()]
        status_by_bucket = {}
        for r in rows[1:]:
            r = list(r) + [None] * (6 - len(r))
            if r[0] and r[2]:
                status_by_bucket[str(r[0]).strip()] = str(r[2]).strip()
        for r in rows[1:]:
            r = list(r) + [None] * (6 - len(r))
            fee, bucket = r[4], r[5]
            if fee and bucket:
                fee_types[str(fee).strip()] = {
                    "bucket": str(bucket).strip(),
                    "status": status_by_bucket.get(str(bucket).strip(), ""),
                }
        with wb.get_sheet("VAT") as ws:
            for i, row in enumerate(ws.rows()):
                vals = [c.v for c in row][:3]
                if i == 0 or not vals or vals[0] is None:
                    continue
                if len(vals) > 2 and vals[2] is not None:
                    vat_sku[str(vals[0]).strip()] = float(vals[2])
    return fee_types, vat_sku


def _read_csv_snapshots(config_dir: Path) -> tuple[dict[str, dict], dict[str, float]]:
    fee_types: dict[str, dict] = {}
    p = config_dir / "lazada_fee_types.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                fee_types[row["fee_name"].strip()] = {"bucket": row["bucket"].strip(),
                                                      "status": row["status"].strip()}
    vat_sku: dict[str, float] = {}
    p = config_dir / "lazada_vat_sku.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                vat_sku[row["sku"].strip()] = float(row["rate"])
    return fee_types, vat_sku


def load_masters(config_dir: Path, settings: dict, log: RunLog) -> dict:
    """Returns {"fee_types", "vat_sku", "source"}. Live master preferred;
    CSV snapshots as fallback. Drift live-vs-snapshot is logged."""
    master_name = settings.get("masters_file", "Lib & VAT rate.xlsb")
    master = config_dir / master_name
    csv_fee, csv_vat = _read_csv_snapshots(config_dir)

    if master.exists():
        fee_types, vat_sku = _read_xlsb(master)
        log.add(f"  masters: live '{master.name}' ({len(fee_types)} fee names, "
                f"{len(vat_sku)} VAT SKUs, {sum(1 for v in vat_sku.values() if v != 1.08)} non-1.08)")
        drift = []
        for fee, m in fee_types.items():
            snap = csv_fee.get(fee)
            if snap is None:
                drift.append(f"fee '{fee}' new in master")
            elif snap["bucket"] != m["bucket"]:
                drift.append(f"fee '{fee}': bucket {snap['bucket']} -> {m['bucket']}")
        for fee in csv_fee:
            if fee not in fee_types:
                drift.append(f"fee '{fee}' missing from master (snapshot only)")
        for sku, rate in vat_sku.items():
            snap = csv_vat.get(sku)
            if snap is None:
                drift.append(f"VAT SKU '{sku}' new in master ({rate})")
            elif abs(snap - rate) > 1e-9:
                drift.append(f"VAT SKU '{sku}': {snap} -> {rate}")
        for sku in csv_vat:
            if sku not in vat_sku:
                drift.append(f"VAT SKU '{sku}' missing from master (snapshot only)")
        if drift:
            log.warn(f"master vs snapshot drift ({len(drift)} item(s)):")
            for d in drift[:20]:
                log.add(f"    drift: {d}")
        else:
            log.add("  masters: live master matches the CSV snapshots exactly")
        return {"fee_types": fee_types, "vat_sku": vat_sku, "source": "xlsb"}

    log.warn(f"masters file '{master_name}' not found — using CSV snapshots")
    return {"fee_types": csv_fee, "vat_sku": csv_vat, "source": "csv"}


def vat_factor_for(sku_series, settings: dict, vat_sku: dict[str, float]):
    """Default-plus-exceptions VAT: one default factor (the temporary 8%
    concession — reverting to 10% is the single vat_factors.default line)
    overridden per SKU by the master's VAT sheet."""
    default = float((settings.get("vat_factors") or {}).get("default", 1.08))
    return sku_series.map(vat_sku).fillna(default)

```


---

## `src/runlog.py`

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path


class RunLog:
    """Collects the audit trail for run_log.txt and echoes it to stdout."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []

    def _print(self, text: str) -> None:
        # Windows consoles often run cp1252, which chokes on Vietnamese text;
        # run_log.txt keeps the exact text (utf-8), stdout degrades gracefully.
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode())

    def add(self, text: str = "") -> None:
        self._print(text)
        self.lines.append(text)

    def warn(self, text: str) -> None:
        line = f"WARNING: {text}"
        self._print(line)
        self.lines.append(line)
        self.warnings.append(text)

    def section(self, title: str) -> None:
        self.add()
        self.add("=" * 64)
        self.add(title)
        self.add("=" * 64)

    def write(self, path: Path) -> None:
        header = [f"Run started: {datetime.now():%Y-%m-%d %H:%M:%S}", ""]
        footer = ["", f"Warnings: {len(self.warnings)}"]
        path.write_text("\n".join(header + self.lines + footer) + "\n", encoding="utf-8")

```


---

## `src/stitch.py`

```python
"""Stage 2 — Cross-period stitch.

Matches income-report lines back to their true order-creation date using the
order files (which include the prior-month re-pull in the same folder).
Income lines with no matching order line go to the exception report — never
silently dropped.
"""

from __future__ import annotations

import pandas as pd

from .runlog import RunLog


def stitch(income: pd.DataFrame, orders: pd.DataFrame, log: RunLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (income with order_created_at attached, unmatched income lines)."""
    order_dates = (
        orders.groupby("order_id", as_index=False)["order_created_at"].min()
    )
    merged = income.merge(order_dates, on="order_id", how="left")

    unmatched = merged[merged["order_created_at"].isna()].copy()
    matched = merged[merged["order_created_at"].notna()].copy()

    if len(matched):
        by_month = matched["order_created_at"].dt.to_period("M").value_counts().sort_index()
        for period, count in by_month.items():
            log.add(f"  income lines attributed to orders created in {period}: {count}")
    log.add(f"  matched: {len(matched)}; unmatched (-> exceptions): {len(unmatched)}")
    return matched, unmatched

```


---

## `src/tieout.py`

```python
"""Stage 5 — Tie-out checks.

Three automated checks. A breach is named in exceptions.xlsx with the
variance amount; the pipeline still completes — it flags, it doesn't hide.
"""

from __future__ import annotations

import pandas as pd

from .runlog import RunLog


def _check(name: str, expected: float, actual: float, tolerance: float) -> dict:
    variance = actual - expected
    return {
        "check": name,
        "expected": round(expected, 2),
        "actual": round(actual, 2),
        "variance": round(variance, 2),
        "tolerance": tolerance,
        "result": "PASS" if abs(variance) <= tolerance else "BREACH",
    }


def run_checks(
    income_ok: pd.DataFrame,
    sku_level: pd.DataFrame,
    finance_income_total: float,
    finance_return_total: float,
    settings: dict,
    log: RunLog,
) -> pd.DataFrame:
    tol = settings.get("tolerances") or {}
    exact = float(tol.get("exact_check_vnd", 1))
    split_tol = float(tol.get("split_rounding_vnd", 10000))

    pivot_total = float(income_ok["net_revenue"].fillna(0).sum())
    calc_total = float(sku_level["net_revenue_sku"].sum())

    # Check 2: each invoice split is rounded to whole VND (as the manual
    # brand-split files are) — the recombined sum may drift from the grand
    # total by rounding; the existing rule tolerates ≤ 10,000 VND.
    split_totals = sku_level.groupby("invoice_group")["net_revenue_sku"].sum().round(0)
    splits_recombined = float(split_totals.sum())

    finance_total = finance_income_total + finance_return_total
    return_total = float(finance_return_total)

    checks = [
        _check("1: Pivot-Income total == calculated total", pivot_total, calc_total, exact),
        _check("2: sum of brand splits == grand total", calc_total, splits_recombined, split_tol),
        _check("3: finance-file total == calculated total", calc_total + return_total, finance_total, exact),
    ]
    results = pd.DataFrame(checks)

    for _, row in results.iterrows():
        log.add(
            f"  Check {row['check']}: {row['result']} "
            f"(expected {row['expected']:,.2f}, actual {row['actual']:,.2f}, variance {row['variance']:,.2f})"
        )
    for group, total in split_totals.items():
        log.add(f"    split '{group}': {total:,.0f} VND")
    return results


def run_checks_tiktok(sku_level: pd.DataFrame, settings: dict, log: RunLog) -> pd.DataFrame:
    """The team's OWN three tolerance checks, ported from their TikTok
    invoicing workbook (formulas + tolerances in cell evidence below;
    tolerances configurable under tolerances.tiktok):

    - "PV sum"     ('PV sum'!E2/E3, tol 12,000): total pre-VAT amount summed
      per store equals the same amount summed per VAT bucket.
    - "Xuat HD bt" ('Xuat HD bt'!O5/P5, tol 2,000): line-level with-VAT total
      equals the VAT-bucket recombination (bucket pre-VAT total x factor).
    - "PV xuat HD" ('PV xuat HD'!H1/I1, tol 1,000): line-level pre-VAT total
      equals the SKU-pivot recombination.

    In their process each check compares a pivot against source rows after
    manual steps; a breach means a row was dropped/edited on one side.
    """
    tol = (settings.get("tolerances") or {}).get("tiktok") or {}

    by_store = float(sku_level.groupby("store")["amount_pre_vat"].sum().sum())
    by_vat_pre = sku_level.groupby("vat_factor")["amount_pre_vat"].sum()
    by_vat_total = float(by_vat_pre.sum())

    with_vat_lines = float(sku_level["amount_with_vat"].sum())
    with_vat_buckets = float((by_vat_pre * by_vat_pre.index).sum())

    pre_vat_lines = float(sku_level["amount_pre_vat"].sum())
    pre_vat_sku_pivot = float(
        sku_level.groupby(["store", "sku_id", "sku_name"], dropna=False)["amount_pre_vat"].sum().sum())

    checks = [
        _check("PV sum: pre-VAT per store == per VAT bucket",
               by_store, by_vat_total, float(tol.get("pv_sum_vnd", 12000))),
        _check("Xuat HD bt: with-VAT lines == VAT-bucket recombination",
               with_vat_lines, with_vat_buckets, float(tol.get("xuat_hd_vnd", 2000))),
        _check("PV xuat HD: pre-VAT lines == SKU pivot",
               pre_vat_lines, pre_vat_sku_pivot, float(tol.get("pv_xuat_hd_vnd", 1000))),
    ]
    results = pd.DataFrame(checks)
    for _, row in results.iterrows():
        log.add(
            f"  Check {row['check']}: {row['result']} "
            f"(expected {row['expected']:,.2f}, actual {row['actual']:,.2f}, variance {row['variance']:,.2f})"
        )
    return results

```


---

## `tools/build_finance_samples.py`

```python
"""Build template-shaped finance workbooks (src/finance_template.py) from a
window's staged inputs, for the team's review. Writes to
output/samples_for_nu/ with team-style file names. Does not replace the
finance_file.xlsx exports — wiring into the run happens after sign-off.

Usage:
    python tools/build_finance_samples.py --platform tiktok --period 2026-05_w1 \
        --label "01 to 17T5"
    python tools/build_finance_samples.py --platform lazada --period 2026-05_l2 \
        --label "26_04T5 to 10T5" --month "Laz 26T5"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, finance_template, ingest, lazada  # noqa: E402
from src.runlog import RunLog  # noqa: E402

OUT = ROOT / "output" / "samples_for_nu"


def sample_tiktok(period: str, settings: dict, meta: dict, log: RunLog) -> Path:
    d = ROOT / "input" / period / "tiktok"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "tiktok", "orders"),
                               "orders", settings, log, "tiktok")
    income = ingest.read_parts(d / "income", config.column_map(settings, "tiktok", "income"),
                               "income", settings, log, "tiktok")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    sku = calculate.explode_to_sku_tiktok(good, orders, log)
    sku = calculate.compute_sku_columns_tiktok(sku, settings, log)
    wb, checks = finance_template.build_tiktok(sku, settings, meta, log)
    path = OUT / f"Tiktok result {meta['label']} For KA.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def sample_shopee(period: str, settings: dict, meta: dict, log: RunLog) -> Path:
    d = ROOT / "input" / period / "shopee"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "shopee", "orders"),
                               "orders", settings, log, "shopee")
    income = ingest.read_parts(d / "income", config.column_map(settings, "shopee", "income"),
                               "income", settings, log, "shopee")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    cl = classify.classify_shopee_income(income, log)
    sku = calculate.explode_to_sku_shopee(cl, orders, log)
    sku = calculate.compute_sku_columns_shopee(sku, settings, log)
    wb, checks = finance_template.build_shopee(sku, settings, meta, log)
    path = OUT / f"shopee result For KA {meta['label']}.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def sample_lazada(period: str, settings: dict, meta: dict, log: RunLog) -> Path:
    fee_types = lazada.load_fee_type_map(ROOT / "config", log, settings)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log, settings)
    ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
    cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    if len(unmapped):
        log.warn(f"{len(unmapped)} unmapped fee rows are NOT in this sample")
    rev = lazada.revenue_lines(cl, log)
    wb, checks = finance_template.build_lazada(rev, settings, meta, log)
    path = OUT / f"Laz result KA used {meta['label']}.xlsx"
    finance_template.write_workbook(wb, path, checks, log)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--period", required=True)
    ap.add_argument("--label", required=True, help="window label for the file name / stamp")
    ap.add_argument("--month", default="", help="Lazada month stamp, e.g. 'Laz 26T5'")
    args = ap.parse_args()

    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    from src.masters import load_masters
    settings["_vat_sku"] = load_masters(ROOT / "config", settings, log)["vat_sku"]
    meta = {"label": args.label, "period_label": args.label, "month_label": args.month}

    log.section(f"FINANCE TEMPLATE SAMPLE {args.platform} {args.period}")
    fn = {"tiktok": sample_tiktok, "shopee": sample_shopee, "lazada": sample_lazada}[args.platform]
    path = fn(args.period, settings, meta, log)
    log.add(f"  sample ready: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/build_master_summary.py`

```python
"""Cross-window, cross-platform master summary in the team's PV sum /
Summary shape (team request, Aug 2026: "one master file to consolidate all
the weeks by brand ... for all platforms").

Reads the GENERATED per-window finance files and aggregates them, so every
figure in the master is provably the same number the team already has in the
weekly files. Column totals are asserted against each source file and the
check is printed; a master that cannot tie to its sources is worthless.

Usage:
    python tools/build_master_summary.py --month 2026-07 \
        --out "C:/Users/.../ADA marketplace master July 2026.xlsx"
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ACC0 = '_(* #,##0_);_(* \\(#,##0\\);_(* "-"??_);_(@_)'

# (period suffix, human label) per platform, in settlement order.
WINDOWS = {
    "TikTok": [("w1", "01-07"), ("w2", "08-14"), ("w3", "15-21"),
               ("w4", "22-28"), ("w5", "29-31")],
    "Shopee": [("s1", "01-10"), ("s2", "11-20"), ("s3", "21-28"), ("s4", "29-31")],
    "Lazada": [("l1", "01-05"), ("l2", "06-12"), ("l3", "13-19"),
               ("l4", "20-26"), ("l5", "27-31")],
}
PLATFORM_DIR = {"TikTok": "tiktok", "Shopee": "shopee", "Lazada": "lazada"}

# Template-shaped finance files (src/finance_template.py): line tabs are
# 'Xuat HD bt' (TikTok/Shopee) and '1.05'/'1.08'/'1.10' (Lazada), header on
# row 6. Lazada line tabs carry no with-VAT column (the team's template does
# not) — with-VAT is recombined as Amount x the tab's rate, which is exact.
SCHEMA = {
    "TikTok": (lambda s: s == "Xuat HD bt", "Source.Name",
               "Amount before VAT", "Check total"),
    "Shopee": (lambda s: s == "Xuat HD bt", "Source.Name",
               "Amount before VAT", "Check total"),
    "Lazada": (lambda s: re.fullmatch(r"1\.\d{1,2}", s.strip()) is not None, "Source.Name",
               "Amount", None),
}
HEADER_ROW = 5  # 0-based; template line tabs put headers on sheet row 6


def norm_store(name: str) -> str:
    """Same normalization as tools/full_run.py so labels agree."""
    s = unicodedata.normalize("NFC", str(name)).lower().strip()
    s = re.sub(r"^\s*\d+[._ ]*", "", s)
    s = re.sub(r"^(income|order)\b[. ]*", "", s)
    s = re.sub(r"\s+part\s*\d+", "", s)
    s = s.replace(".xlsx", "")
    s = re.sub(r"[._]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def read_window(platform: str, month: str, suffix: str) -> pd.DataFrame:
    sel, store_c, pre_c, wv_c = SCHEMA[platform]
    f = ROOT / "output" / f"{month}_{suffix}" / PLATFORM_DIR[platform] / "finance_file.xlsx"
    if not f.exists():
        raise FileNotFoundError(f)
    xf = pd.ExcelFile(f, engine="calamine")
    frames = []
    for sheet in [s for s in xf.sheet_names if sel(s)]:
        d = pd.read_excel(xf, sheet_name=sheet, engine="calamine", header=HEADER_ROW)
        d.columns = [str(c).strip() for c in d.columns]
        if store_c not in d.columns:
            continue
        pre = pd.to_numeric(d.get(pre_c), errors="coerce").fillna(0)
        if wv_c is None:  # Lazada: with-VAT recombined from the tab's rate
            wv = pre * float(sheet.strip())
        else:
            wv = pd.to_numeric(d.get(wv_c), errors="coerce").fillna(0)
        # Order-level columns are blanked on repeat SKU rows ("non repeat"
        # convention); forward-fill so every line carries its store.
        frames.append(pd.DataFrame({"store": d[store_c].ffill(), "pre": pre, "wv": wv}))
    if not frames:
        raise ValueError(f"no line tab found in {f}")
    df = pd.concat(frames, ignore_index=True)
    df["store"] = df["store"].map(norm_store)
    return df.groupby("store", as_index=False)[["pre", "wv"]].sum()


def write_grid(ws, title: str, row_label: str, rows: list[str], cols: list[str],
               values: dict, extra_cols: list[tuple[str, dict]] | None = None) -> None:
    ws["A1"] = title
    ws["A1"].font = Font(bold=True, size=12)
    ws["A3"] = row_label
    ws["A3"].font = Font(bold=True)
    for j, c in enumerate(cols, start=2):
        cell = ws.cell(row=3, column=j, value=c)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    n_extra = len(extra_cols or [])
    for k, (label, _) in enumerate(extra_cols or [], start=len(cols) + 2):
        cell = ws.cell(row=3, column=k, value=label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for i, r in enumerate(rows, start=4):
        ws.cell(row=i, column=1, value=r)
        for j, c in enumerate(cols, start=2):
            cell = ws.cell(row=i, column=j, value=float(values.get((r, c), 0.0)))
            cell.number_format = ACC0
        for k, (_, vals) in enumerate(extra_cols or [], start=len(cols) + 2):
            cell = ws.cell(row=i, column=k, value=float(vals.get(r, 0.0)))
            cell.number_format = ACC0

    total_row = len(rows) + 4
    ws.cell(row=total_row, column=1, value="Total").font = Font(bold=True)
    for j in range(2, len(cols) + 2 + n_extra):
        letter = get_column_letter(j)
        cell = ws.cell(row=total_row, column=j,
                       value=f"=SUM({letter}4:{letter}{total_row - 1})")
        cell.number_format = ACC0
        cell.font = Font(bold=True)

    ws.column_dimensions["A"].width = 34
    for j in range(2, len(cols) + 2 + n_extra):
        ws.column_dimensions[get_column_letter(j)].width = 18
    ws.freeze_panes = "B4"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", required=True, help="e.g. 2026-07")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data: dict[str, dict[str, pd.DataFrame]] = {}
    for platform, wins in WINDOWS.items():
        data[platform] = {}
        for suffix, label in wins:
            data[platform][label] = read_window(platform, args.month, suffix)
            tot = data[platform][label]["wv"].sum()
            print(f"  {platform:<7} {label}: {len(data[platform][label]):>3} stores, "
                  f"with-VAT {tot:>18,.0f}")

    wb = Workbook()
    wb.remove(wb.active)

    # --- Summary: vertical, one block per platform (the platforms do NOT
    # share window boundaries, so a common column axis would be sparse and
    # misleading; this mirrors the Lazada "Summary" tab idiom instead) ---
    plats = list(WINDOWS)
    ws = wb.create_sheet("Summary")
    ws["A1"] = f"Marketplace revenue summary, {args.month} (VND)"
    ws["A1"].font = Font(bold=True, size=12)
    for j, h in enumerate(["Platform", "Window", "Sum of Amount before VAT",
                           "Sum of Check total (with VAT)"], start=1):
        c = ws.cell(row=3, column=j, value=h)
        c.font = Font(bold=True)
        c.alignment = Alignment(horizontal="center" if j > 2 else "left")
    r = 4
    grand_pre = grand_wv = 0.0
    for p in plats:
        p_pre = p_wv = 0.0
        for _, lab in WINDOWS[p]:
            pre = float(data[p][lab]["pre"].sum())
            wv = float(data[p][lab]["wv"].sum())
            p_pre += pre
            p_wv += wv
            ws.cell(row=r, column=1, value=p)
            ws.cell(row=r, column=2, value=lab)
            ws.cell(row=r, column=3, value=pre).number_format = ACC0
            ws.cell(row=r, column=4, value=wv).number_format = ACC0
            r += 1
        ws.cell(row=r, column=1, value=f"{p} total").font = Font(bold=True)
        for col, v in ((3, p_pre), (4, p_wv)):
            c = ws.cell(row=r, column=col, value=v)
            c.number_format = ACC0
            c.font = Font(bold=True)
        grand_pre += p_pre
        grand_wv += p_wv
        r += 2
    ws.cell(row=r, column=1, value="ALL PLATFORMS").font = Font(bold=True, size=12)
    for col, v in ((3, grand_pre), (4, grand_wv)):
        c = ws.cell(row=r, column=col, value=v)
        c.number_format = ACC0
        c.font = Font(bold=True, size=12)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 26
    ws.column_dimensions["D"].width = 28

    # --- storefront -> client-brand mapping (config/brand_map.csv, team-
    # reviewable). Unmapped storefronts fall back to their own name and are
    # flagged; nothing is merged without an explicit mapping row. ---
    bmap: dict[tuple[str, str], tuple[str, str, str]] = {}
    map_path = ROOT / "config" / "brand_map.csv"
    if map_path.exists():
        m = pd.read_csv(map_path, dtype=str).fillna("")
        for _, r in m.iterrows():
            bmap[(r["platform"].strip().lower(), r["storefront"].strip())] = (
                r["client_brand"].strip(), r["confidence"].strip(), r["note"].strip())

    per_plat: dict[str, dict[str, float]] = {}
    for p in plats:
        acc: dict[str, float] = {}
        for lab in data[p]:
            for s, v in zip(data[p][lab]["store"], data[p][lab]["wv"]):
                acc[s] = acc.get(s, 0.0) + float(v)
        per_plat[p] = acc

    def brand_of(p: str, store: str) -> tuple[str, str, str]:
        return bmap.get((p.lower(), store),
                        (store, "UNMAPPED (kept as storefront)", "not in config/brand_map.csv"))

    # By brand (mapped)
    brand_vals: dict[tuple[str, str], float] = {}
    for p in plats:
        for s, v in per_plat[p].items():
            b = brand_of(p, s)[0]
            brand_vals[(b, p)] = brand_vals.get((b, p), 0.0) + v
    brands = sorted({b for b, _ in brand_vals})
    ws = wb.create_sheet("By brand")
    write_grid(ws, f"Revenue by client brand across platforms, {args.month} (with VAT, VND) — "
                   f"mapping on the 'Brand mapping' tab",
               "Client brand", brands, plats,
               {(b, p): brand_vals.get((b, p), 0.0) for b in brands for p in plats})

    # By storefront (unmapped view, for traceability)
    stores_all = sorted({s for p in plats for s in per_plat[p]})
    ws = wb.create_sheet("By storefront")
    write_grid(ws, f"Revenue by storefront (as named in the platform files), {args.month} (with VAT, VND)",
               "Storefront", stores_all, plats,
               {(s, p): per_plat[p].get(s, 0.0) for s in stores_all for p in plats})

    # Brand mapping — the reviewable table
    ws = wb.create_sheet("Brand mapping")
    ws["A1"] = ("Storefront -> client brand mapping. PLEASE REVIEW the rows marked "
                "needs-confirmation or UNMAPPED and correct in one pass; storefronts "
                "were NOT merged unless the mapping says so.")
    ws["A1"].font = Font(bold=True)
    headers = ["Platform", "Storefront (as in files)", "Proposed client brand",
               "Confidence", f"July with-VAT (VND)", "Note"]
    for j, h in enumerate(headers, start=1):
        c = ws.cell(row=3, column=j, value=h)
        c.font = Font(bold=True)
    r = 4
    flagged = 0
    for p in plats:
        for s in sorted(per_plat[p]):
            b, conf, note = brand_of(p, s)
            ws.cell(row=r, column=1, value=p)
            ws.cell(row=r, column=2, value=s)
            ws.cell(row=r, column=3, value=b)
            cell = ws.cell(row=r, column=4, value=conf)
            if conf != "high":
                cell.font = Font(bold=True, color="FFC00000")
                flagged += 1
            ws.cell(row=r, column=5, value=per_plat[p][s]).number_format = ACC0
            ws.cell(row=r, column=6, value=note)
            r += 1
    for col, w in (("A", 10), ("B", 44), ("C", 30), ("D", 30), ("E", 20), ("F", 62)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"
    print(f"  brand mapping: {r - 4} storefronts, {flagged} flagged for review")

    # --- One tab per platform: store x window, plus pre-VAT total ---
    for p in plats:
        labels = [lab for _, lab in WINDOWS[p]]
        stores = sorted({s for lab in labels for s in data[p][lab]["store"]})
        wv = {(s, lab): float(data[p][lab].set_index("store")["wv"].get(s, 0.0))
              for s in stores for lab in labels}
        pre = {s: sum(float(data[p][lab].set_index("store")["pre"].get(s, 0.0))
                      for lab in labels) for s in stores}
        ws = wb.create_sheet(p)
        write_grid(ws, f"{p} by store and window, {args.month} (with VAT, VND)",
                   "Source.Name", stores, labels, wv,
                   extra_cols=[("Sum of Amount before VAT", pre)])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"\nwrote {out}")

    print("\nTIE CHECK (master column totals vs the individual finance files):")
    ok = True
    for p in plats:
        for suffix, label in WINDOWS[p]:
            src = float(read_window(p, args.month, suffix)["wv"].sum())
            mine = float(data[p][label]["wv"].sum())
            good = abs(src - mine) < 1
            ok &= good
            print(f"  {p:<7} {label}: {mine:>18,.0f}  {'TIES' if good else 'MISMATCH'}")
    print("ALL COLUMNS TIE" if ok else "TIE FAILURE — do not send")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/build_review_package.py`

```python
"""Regenerate REVIEW_PACKAGE.md: every doc and every source file inline in
one reviewable document, built from the CURRENT tree so it can never drift
from the code again (the first hand-built edition went stale within weeks).

Usage: python tools/build_review_package.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = ["HANDOFF.md", "COMPLETION_REPORT.md", "README.md",
        "EVALUATION_DOSSIER/PROJECT_OVERVIEW.md",
        "EVALUATION_DOSSIER/WHAT_WAS_BUILT.md",
        "EVALUATION_DOSSIER/VERIFICATION_RECORD.md",
        "EVALUATION_DOSSIER/CHALLENGES_AND_FINDINGS.md",
        "EVALUATION_DOSSIER/HOW_TO_RUN.md",
        "EVALUATION_DOSSIER/OPEN_QUESTIONS_FOR_EVALUATOR.md"]

CODE_GLOBS = ["recon.py", "src/*.py", "tools/*.py", "config/settings.yaml",
              "config/brand_map.csv", "config/lazada_fee_types.csv",
              "config/lazada_vat_sku.csv", ".gitignore"]

LANG = {".py": "python", ".yaml": "yaml", ".csv": "", ".gitignore": ""}


def main() -> int:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    out = [f"# REVIEW PACKAGE — full documentation + source in one file\n",
           f"Generated {date.today()} from commit `{head}` by "
           f"`tools/build_review_package.py`. Regenerate rather than edit.\n"]

    out.append("\n\n# PART A — DOCUMENTATION\n")
    for rel in DOCS:
        p = ROOT / rel
        if not p.exists():
            out.append(f"\n\n---\n\n*(missing: {rel})*\n")
            continue
        out.append(f"\n\n---\n\n<!-- ===== {rel} ===== -->\n\n")
        out.append(p.read_text(encoding="utf-8"))

    out.append("\n\n# PART B — SOURCE\n")
    files: list[Path] = []
    for g in CODE_GLOBS:
        files.extend(sorted(ROOT.glob(g)))
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        lang = LANG.get(p.suffix, LANG.get(p.name, ""))
        out.append(f"\n\n---\n\n## `{rel}`\n\n```{lang}\n")
        out.append(p.read_text(encoding="utf-8"))
        out.append("\n```\n")

    target = ROOT / "REVIEW_PACKAGE.md"
    target.write_text("".join(out), encoding="utf-8")
    print(f"wrote {target} ({target.stat().st_size:,} bytes, "
          f"{len(DOCS)} docs + {len(files)} source files, commit {head})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/calc_verify.py`

```python
"""Row-level verification of the ported TikTok calculation chain (U food).

Runs stages 2-4 (stitch semantics, classification, SKU explode + yellow
columns) per settlement window and compares every ported formula column
against the team's own computed rows, extracted from:
  - intermediary "Tiktok result Sample T5 - * .xlsx" sheet "Xuat HĐ"
  - Total files' take-out pivot aggregates (passed as constants below)

Usage:
    python tools/calc_verify.py --team-dir <dir with extracted team CSVs> \
        [--store "U food"] [--team-csv-w1 ...] [--team-csv-w2 ...] \
        [--expected-json expected.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, ingest, tieout  # noqa: E402
from src.runlog import RunLog  # noqa: E402

# One reconciliation window = one period folder = one Total file, mirroring
# the team's process (input/<window>/tiktok mirrors "26_01 to 17T05" etc.).
WINDOW_PERIODS = {"W1": "2026-05_w1", "W2": "2026-05_w2"}

# Default aggregates = U food, read from the Total files' pivots:
#   V1/V2 "Pivot Income" (Final_Status = take out) U food rows,
#   V2 "Cross check with recon file" (Final_Status=OK, _Check_Status=Good).
# Override per store with --expected-json.
DEFAULT_EXPECTED = {
    "W1": {"takeout_settlement": -158_602},
    "W2": {"takeout_settlement": -471_486, "ok_good_settlement": 10_170_577,
           "ok_good_revenue": 12_540_000},
}

TEAM_COLS = {
    "quantity": 10,               # "Sum of Quantity" (K)
    "gross_rev": 11,              # "Gross Rev" (L)
    "net_after_seller_discount": 12,  # "net rev after discount" (M)
    "sku_seller_discount": 13,    # "Sum of SKU Seller Discount" (N)
    "order_gross_sale": 15,       # "order gross sale per order" (P)
    "vat_factor": 16,             # "VAT KA sử dụng" (Q)
    "unit_price_pre_vat": 17,     # "Đơn giá KA sử dụng trước VAT" (R)
    "amount_pre_vat": 19,         # "Cộng tiền hàng KA sử dụng trước VAT" (T)
    "amount_with_vat": 20,        # "Cộng tiền hàng KA sử dụng có VAT" (U)
    "order_revenue_check": 21,    # "Doanh thu by order" (V)
    "order_check_diff": 22,       # "check" (W)
}


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    out = pd.DataFrame({
        "order_id": df[cols[2]].astype(str).str.strip(),
        "sku_id": df[cols[7]].astype(str).str.strip(),
        "sku_name": df[cols[8]].astype(str).str.strip(),
        "unit_price_gross": pd.to_numeric(df[cols[9]], errors="coerce"),
    })
    for name, idx in TEAM_COLS.items():
        out[f"team_{name}"] = pd.to_numeric(df[cols[idx]], errors="coerce")
    return out


def compare(mine: pd.DataFrame, team: pd.DataFrame, window: str) -> dict[str, tuple[int, int]]:
    keys = ["order_id", "sku_id", "sku_name"]
    merged = team.merge(mine, on=keys, how="outer", indicator=True, suffixes=("", "_mine"))
    only_team = merged[merged["_merge"] == "left_only"]
    only_mine = merged[merged["_merge"] == "right_only"]
    both = merged[merged["_merge"] == "both"]
    print(f"  [{window}] row alignment: {len(both)} matched keys, "
          f"{len(only_team)} only in team file, {len(only_mine)} only in pipeline")
    for _, r in only_team.head(5).iterrows():
        print(f"    only-team: {r['order_id']} / {r['sku_id']}")
    for _, r in only_mine.head(5).iterrows():
        print(f"    only-mine: {r['order_id']} / {r['sku_id']}")

    results: dict[str, tuple[int, int]] = {}
    for name in TEAM_COLS:
        t, m = both[f"team_{name}"], both[name]
        if name == "order_revenue_check":
            # The team writes V only on the first row per order (repeat rows
            # get 0 from SUMIF over the blank "non repeat" id column); the
            # pipeline carries it on every row. Compare per order: team rows
            # sum to the single real value, pipeline rows are constant.
            t = both.groupby("order_id")[f"team_{name}"].transform("sum")
        ok = ((t - m).abs() < 0.01) | (t.isna() & m.isna())
        results[name] = (int(ok.sum()), len(both))
        bad = both[~ok]
        flag = "MATCH" if ok.all() and len(both) else "MISMATCH"
        print(f"  [{window}] {name}: {int(ok.sum())}/{len(both)} rows {flag}")
        for _, r in bad.head(3).iterrows():
            print(f"      {r['order_id']}/{r['sku_id']}: team={r[f'team_{name}']} mine={r[name]}")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", default="U food")
    ap.add_argument("--team-csv-w1", default="int_1_17_ufood.csv")
    ap.add_argument("--team-csv-w2", default="int_18_31_ufood.csv")
    ap.add_argument("--expected-json", default=None,
                    help="JSON {W1:{takeout_settlement,...}, W2:{...}} from the Total files' pivots")
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    team_csvs = {"W1": args.team_csv_w1, "W2": args.team_csv_w2}
    expected = DEFAULT_EXPECTED if args.expected_json is None else json.loads(
        Path(args.expected_json).read_text(encoding="utf-8"))
    log = RunLog()

    settings = config.load_settings(ROOT / "config")
    # Input folders may hold several stores' files; the store filter below
    # scopes the run, so skip the whole-platform store check here.
    settings.setdefault("expected_stores", {})["tiktok"] = []
    all_ok = True

    for window, period in WINDOW_PERIODS.items():
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        input_dir = ROOT / "input" / period / "tiktok"
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, "tiktok", "orders"),
                                   "orders", settings, log, "tiktok")
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, "tiktok", "income"),
                                   "income", settings, log, "tiktok")
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        orders = orders[orders["store"] == args.store]
        income = income[income["store"] == args.store]
        print(f"  scoped to store '{args.store}': {len(orders)} order rows, {len(income)} income rows")

        print("--- stage 3: classification (M-code port) ---")
        classified = classify.classify_tiktok_income(income, log)

        takeout = classified[classified["final_status"] == classify.FINAL_TAKE_OUT]
        ok_good = classified[(classified["final_status"] == classify.FINAL_OK)
                             & (classified["check_status"] == classify.CHECK_GOOD)]
        uncl = classified[classified["final_status"] == classify.FINAL_UNCLASSIFIED]
        if len(uncl):
            print(f"  unclassified (reimbursements/adjustments, outside both team pivots): "
                  f"{len(uncl)} lines, settlement {uncl['net_revenue'].fillna(0).sum():,.0f}")
        exp = expected[window]
        to_sum = float(takeout["net_revenue"].fillna(0).sum())
        print(f"  take-out settlement: mine {to_sum:,.0f} vs team {exp['takeout_settlement']:,.0f} "
              f"{'MATCH' if abs(to_sum - exp['takeout_settlement']) < 1 else 'MISMATCH'}")
        all_ok &= abs(to_sum - exp["takeout_settlement"]) < 1
        if "ok_good_settlement" in exp:
            g_set = float(ok_good["net_revenue"].fillna(0).sum())
            g_rev = float(ok_good["gross_revenue"].fillna(0).sum())
            print(f"  OK/Good settlement: mine {g_set:,.0f} vs team {exp['ok_good_settlement']:,.0f} "
                  f"{'MATCH' if abs(g_set - exp['ok_good_settlement']) < 1 else 'MISMATCH'}")
            print(f"  OK/Good revenue   : mine {g_rev:,.0f} vs team {exp['ok_good_revenue']:,.0f} "
                  f"{'MATCH' if abs(g_rev - exp['ok_good_revenue']) < 1 else 'MISMATCH'}")
            all_ok &= abs(g_set - exp["ok_good_settlement"]) < 1
            all_ok &= abs(g_rev - exp["ok_good_revenue"]) < 1

        print("--- stage 4: SKU explode + yellow columns ---")
        sku_level = calculate.explode_to_sku_tiktok(ok_good, orders, log)
        sku_level = calculate.compute_sku_columns_tiktok(sku_level, settings, log)

        team = load_team(team_dir / team_csvs[window])
        results = compare(sku_level, team, window)
        all_ok &= all(n == total and total > 0 for n, total in results.values())

        print("--- stage 5 preview: team's own tie-out checks ---")
        breaches = tieout.run_checks_tiktok(sku_level, settings, log)
        all_ok &= bool((breaches["result"] == "PASS").all())

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/calc_verify_lazada.py`

```python
"""Row-level verification of the Lazada ledger port against the team's
Total files: 'PV used' per-line pivot (Price KA / Quantity / VAT checks)
and 'SUM CP' fee-bucket totals.

Usage:
    python tools/calc_verify_lazada.py --team-dir <dir> --store "Curel" --slug curel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, lazada  # noqa: E402
from src.runlog import RunLog  # noqa: E402

WINDOW_PERIODS = {"L3": "2026-05_l3", "L5": "2026-05_l5"}

# 'SUM CP' rows for the verified stores, read from the Total files
# (Total Lazada 26_11T5 to 17T5 / 26_25T5 to 31T5, sheet "SUM CP"):
# buckets: 1.Doanh Thu, 2.Flexi-Combo, 3.Vouchers, 4.1 LazCoins, 7.CP no Inv, 6.CP co Inv
EXPECTED_SUM_CP = {
    ("L3", "curel"): {"1.Doanh Thu": 3_541_000, "3.Promotional Charges Vouchers": -23_250,
                      "7.CP no Invoice": -14_500, "6.CP co Invoice": -804_071},
    ("L3", "unilever2"): {"1.Doanh Thu": 397_616_030, "2.Promotional Charges Flexi-Combo": -304_000_000,
                          "3.Promotional Charges Vouchers": -3_580_870, "4.1 LazCoints Discount": -708_370,
                          "7.CP no Invoice": 1_033_800, "6.CP co Invoice": -23_995_064},
    ("L5", "curel"): {"1.Doanh Thu": 3_265_000, "3.Promotional Charges Vouchers": -19_590,
                      "6.CP co Invoice": -835_812},
    ("L5", "unilever2"): {"1.Doanh Thu": 468_549_490, "2.Promotional Charges Flexi-Combo": -344_000_000,
                          "3.Promotional Charges Vouchers": -6_373_146, "4.1 LazCoints Discount": -921_956,
                          "7.CP no Invoice": 2_212_284, "6.CP co Invoice": -42_888_498},
}


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    return pd.DataFrame({
        "order_id": df[cols[1]].astype(str).str.strip(),
        "sku_id": df[cols[2]].astype(str).str.strip(),
        "product_name": df[cols[3]].astype(str).str.strip(),
        "team_price_ka": pd.to_numeric(df[cols[4]], errors="coerce"),
        "team_quantity": pd.to_numeric(df[cols[5]], errors="coerce"),
        "team_check_with_vat": pd.to_numeric(df[cols[6]], errors="coerce"),
        "team_check_no_vat": pd.to_numeric(df[cols[7]], errors="coerce"),
    })


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", required=True, help="store token as derived from file names")
    ap.add_argument("--slug", required=True)
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    fee_types = lazada.load_fee_type_map(ROOT / "config", log)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log)
    all_ok = True

    for window, period in WINDOW_PERIODS.items():
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
        ledger = ledger[ledger["store"].str.contains(args.store, case=False, regex=False)]
        print(f"  scoped: {len(ledger)} ledger rows")
        classified, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)

        exp = EXPECTED_SUM_CP.get((window, args.slug), {})
        ok_buckets = True
        for bucket, expected in exp.items():
            mine = float(classified.loc[classified["fee_bucket"] == bucket, "amount_incl_vat"].fillna(0).sum())
            match = abs(mine - expected) < 1
            ok_buckets &= match
            print(f"  bucket {bucket}: mine {mine:,.0f} vs team {expected:,.0f} "
                  f"{'MATCH' if match else 'MISMATCH'}")
        all_ok &= ok_buckets

        mine = lazada.revenue_lines(classified, log)
        team = load_team(team_dir / f"laz_{window.lower()}_{args.slug}.csv")
        keys = ["order_id", "sku_id", "product_name"]
        merged = team.merge(mine, on=keys, how="outer", indicator=True)
        both = merged[merged["_merge"] == "both"]
        only_t, only_m = merged[merged["_merge"] == "left_only"], merged[merged["_merge"] == "right_only"]
        print(f"  row alignment: {len(both)} matched, {len(only_t)} only-team, {len(only_m)} only-mine")
        for _, r in pd.concat([only_t.head(4), only_m.head(4)]).iterrows():
            print(f"    unaligned ({r['_merge']}): {r['order_id']} / {r['sku_id']}")
        all_ok &= (len(only_t) == 0) and (len(only_m) == 0) and len(both) > 0

        for mine_col, team_col in [("price_ka", "team_price_ka"), ("quantity", "team_quantity"),
                                   ("check_with_vat", "team_check_with_vat"),
                                   ("check_no_vat", "team_check_no_vat")]:
            ok = ((both[team_col] - both[mine_col]).abs() < 0.01)
            print(f"  {mine_col}: {int(ok.sum())}/{len(both)} rows {'MATCH' if ok.all() else 'MISMATCH'}")
            for _, r in both[~ok].head(3).iterrows():
                print(f"      {r['order_id']}/{r['sku_id']}: team={r[team_col]} mine={r[mine_col]}")
            all_ok &= bool(ok.all())

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/calc_verify_shopee.py`

```python
"""Row-level verification of the ported Shopee chain against the team's
intermediary "Xuat HĐ" rows (extracted per store/window to CSV).

Usage:
    python tools/calc_verify_shopee.py --team-dir <dir> --store "Sanofi" --slug sanofi
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, ingest  # noqa: E402
from src.runlog import RunLog  # noqa: E402

WINDOW_PERIODS = {"S1": "2026-05_s1", "S2": "2026-05_s2", "S3": "2026-05_s3"}

# 0-based column indexes in the extracted 'Xuat HĐ' CSVs (header row 3):
TEAM_COLS = {
    "quantity": 17,            # R "Sum of Số lượng"
    "gross_rev": 18,           # S "Gross Rev"
    "net_after_discount": 19,  # T "net rev after discount"
    "seller_subsidy": 20,      # U "Sum of Người bán trợ giá"
    "total_discount": 22,      # W "Total discount" (first row per order)
    "order_gross_sale": 23,    # X "order gross sale per order"
    "discount_per_order": 24,  # Y "discount per order"
    "discount_allocated": 25,  # Z "Discount by product"
    "vat_factor": 26,          # AA "VAT KA sử dụng"
    "unit_price_pre_vat": 27,  # AB "Đơn giá KA sử dụng trước VAT"
    "amount_pre_vat": 29,      # AD "Cộng tiền hàng KA sử dụng trước VAT"
    "amount_with_vat": 30,     # AE (with VAT)
}
ORDER_LEVEL = {"total_discount"}  # blank on repeat rows in the team sheet


def load_team(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, header=0, encoding="utf-8-sig")
    cols = list(df.columns)
    out = pd.DataFrame({
        "team_tag": df[cols[0]].astype(str).str.strip(),
        "order_id": df[cols[2]].astype(str).str.strip(),
        "sku_id": df[cols[14]].astype(str).str.strip(),
        "sku_name": df[cols[15]].astype(str).str.strip(),
    })
    for name, idx in TEAM_COLS.items():
        out[f"team_{name}"] = pd.to_numeric(df[cols[idx]], errors="coerce")
    return out


def compare(mine: pd.DataFrame, team: pd.DataFrame, window: str) -> bool:
    keys = ["order_id", "sku_id", "sku_name"]
    merged = team.merge(mine, on=keys, how="outer", indicator=True, suffixes=("", "_mine"))
    both = merged[merged["_merge"] == "both"]
    only_t, only_m = merged[merged["_merge"] == "left_only"], merged[merged["_merge"] == "right_only"]
    print(f"  [{window}] row alignment: {len(both)} matched, {len(only_t)} only-team, {len(only_m)} only-mine")
    for _, r in pd.concat([only_t.head(4), only_m.head(4)]).iterrows():
        print(f"    unaligned ({r['_merge']}): {r['order_id']} / {r['sku_id']}")

    ok_all = (len(only_t) == 0) and (len(only_m) == 0) and len(both) > 0

    tag_ok = (both["team_tag"] == both["check_status"])
    print(f"  [{window}] status tag: {int(tag_ok.sum())}/{len(both)} rows "
          f"{'MATCH' if tag_ok.all() else 'MISMATCH'}")
    for _, r in both[~tag_ok].head(4).iterrows():
        print(f"      {r['order_id']}: team={r['team_tag']} mine={r['check_status']}")
    ok_all &= bool(tag_ok.all())

    for name in TEAM_COLS:
        t, m = both[f"team_{name}"], both[name]
        if name in ORDER_LEVEL:
            t = both.groupby("order_id")[f"team_{name}"].transform("sum")
        ok = ((t - m).abs() < 0.01) | (t.isna() & m.isna()) | (t.isna() & (m == 0))
        print(f"  [{window}] {name}: {int(ok.sum())}/{len(both)} rows {'MATCH' if ok.all() else 'MISMATCH'}")
        for _, r in both[~ok].head(3).iterrows():
            tv = r[f"team_{name}"] if name not in ORDER_LEVEL else "(order-sum)"
            print(f"      {r['order_id']}/{r['sku_id']}: team={tv} mine={r[name]}")
        ok_all &= bool(ok.all())
    return ok_all


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-dir", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--windows", default="S1,S2,S3")
    args = ap.parse_args()
    team_dir = Path(args.team_dir)
    log = RunLog()

    settings = config.load_settings(ROOT / "config")
    settings.setdefault("expected_stores", {})["shopee"] = []
    all_ok = True

    for window in [w.strip().upper() for w in args.windows.split(",") if w.strip()]:
        period = WINDOW_PERIODS[window]
        print()
        print("=" * 70)
        print(f"WINDOW {window} (period {period}, store {args.store})")
        print("=" * 70)
        input_dir = ROOT / "input" / period / "shopee"
        orders = ingest.read_parts(input_dir / "orders", config.column_map(settings, "shopee", "orders"),
                                   "orders", settings, log, "shopee")
        income = ingest.read_parts(input_dir / "income", config.column_map(settings, "shopee", "income"),
                                   "income", settings, log, "shopee")
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        orders = orders[orders["store"] == args.store]
        income = income[income["store"] == args.store]
        print(f"  scoped to '{args.store}': {len(orders)} order rows, {len(income)} income rows")

        print("--- classification (derived Return / 0 dong / ok rules) ---")
        classified = classify.classify_shopee_income(income, log)

        print("--- SKU explode + yellow columns ---")
        sku_level = calculate.explode_to_sku_shopee(classified, orders, log)
        sku_level = calculate.compute_sku_columns_shopee(sku_level, settings, log)

        team = load_team(team_dir / f"int_{window.lower()}_{args.slug}.csv")
        all_ok &= compare(sku_level, team, window)

    print()
    print("OVERALL:", "ALL VERIFIED" if all_ok else "DIFFERENCES REMAIN — see above")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/full_run.py`

```python
"""Full-platform run: all stores in a window through the verified chain,
finance-file export, and grand/per-store total ties against team references.

Usage:
    python tools/full_run.py --platform tiktok --period 2026-05_w1 \
        --refs <refs json>            # from tools/extract refs scripts

Refs JSON shape:
    {"per_store": {"<normalized store>": {"metric": value, ...}},
     "grand": {"pre_vat": x, "with_vat": y, ...},
     "grand_tolerance": 2000}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import calculate, classify, config, finance_template, ingest, lazada, tieout  # noqa: E402
from src.runlog import RunLog  # noqa: E402


def window_meta(dates: pd.Series) -> dict:
    """Team-style window labels derived from the data itself, e.g.
    settlements 2026-07-08..2026-07-14 -> label '08 to 14T07',
    period stamp '26_08 to 14T07', month stamp 'Laz 26T7'."""
    d = pd.to_datetime(dates, errors="coerce").dropna()
    if d.empty:
        return {"label": "", "period_label": "", "month_label": ""}
    lo, hi = d.min(), d.max()
    label = f"{lo:%d} to {hi:%d}T{hi:%m}"
    return {"label": label,
            "period_label": f"{hi:%y}_{label}",
            "month_label": f"Laz {hi:%y}T{hi.month}"}


def norm_store(name: str) -> str:
    """Shared normalization so team file labels ('income U food.xlsx',
    'Income.Masan part 1.xlsx') and pipeline store names compare equal."""
    s = unicodedata.normalize("NFC", str(name)).lower().strip()
    s = re.sub(r"^\s*\d+[._ ]*", "", s)
    s = re.sub(r"^(income|order)\b[. ]*", "", s)
    s = re.sub(r"\s+part\s*\d+", "", s)
    s = s.replace(".xlsx", "")
    s = re.sub(r"[._]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tie(per_store_mine: dict, refs: dict, log: RunLog) -> list[str]:
    variances = []
    # Team labels can split one store across several source files
    # ("Income.Masan part 1/2.xlsx") — SUM metrics when normalized keys collide.
    per_ref: dict[str, dict] = {}
    for k, v in (refs.get("per_store") or {}).items():
        acc = per_ref.setdefault(norm_store(k), {})
        for m, val in v.items():
            acc[m] = acc.get(m, 0.0) + float(val)
    for store, metrics in sorted(per_store_mine.items()):
        key = norm_store(store)
        ref = per_ref.get(key)
        if ref is None:
            # Team labels sometimes keep only the middle segment of long
            # underscore names ("Unilever Chăm Sóc Vẻ Đẹp" for
            # "..._Unilever 2.xlsx") — fall back to prefix matching.
            cands = [r for r in per_ref if len(r) >= 8 and (key.startswith(r) or r.startswith(key))]
            ref = per_ref[cands[0]] if len(cands) == 1 else None
        if ref is None:
            variances.append(f"{store}: no team reference found")
            continue
        for metric, mine in metrics.items():
            expected = ref.get(metric)
            if expected is None:
                continue
            diff = mine - float(expected)
            status = "TIES" if abs(diff) < 1 else f"VARIANCE {diff:+,.0f}"
            log.add(f"  {store} · {metric}: mine {mine:,.0f} vs team {float(expected):,.0f} -> {status}")
            if abs(diff) >= 1:
                variances.append(f"{store} {metric}: {diff:+,.0f}")
    return variances


def run_tiktok(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    d = ROOT / "input" / period / "tiktok"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "tiktok", "orders"),
                               "orders", settings, log, "tiktok")
    income = ingest.read_parts(d / "income", config.column_map(settings, "tiktok", "income"),
                               "income", settings, log, "tiktok")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    income = ingest.apply_settlement_bounds(income, period, settings, log)
    ingest.check_stores(income, "income", "tiktok", settings, log)
    cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    sku = calculate.explode_to_sku_tiktok(good, orders, log)
    sku = calculate.compute_sku_columns_tiktok(sku, settings, log)

    wb, checks = finance_template.build_tiktok(sku, settings, window_meta(sku["statement_date"]), log)
    finance_template.write_workbook(wb, ROOT / "output" / period / "tiktok" / "finance_file.xlsx",
                                    checks, log)
    tieout.run_checks_tiktok(sku, settings, log)

    per_store = {
        s: {"ok_good_settlement": float(g["net_revenue"].fillna(0).sum()),
            "ok_good_revenue": float(g["gross_revenue"].fillna(0).sum())}
        for s, g in good.groupby("store")
    }
    for s, g in cl[cl["final_status"] == classify.FINAL_TAKE_OUT].groupby("store"):
        per_store.setdefault(s, {})["takeout_settlement"] = float(g["net_revenue"].fillna(0).sum())
    # V1-style Total files pivot with Final_Status = All -> raw sums:
    for s, g in cl.groupby("store"):
        per_store.setdefault(s, {})["raw_settlement"] = float(g["net_revenue"].fillna(0).sum())
        per_store[s]["raw_revenue"] = float(g["gross_revenue"].fillna(0).sum())
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 1))
    for metric, mine in [("pre_vat", float(sku["amount_pre_vat"].sum())),
                         ("with_vat", float(sku["amount_with_vat"].sum()))]:
        if metric in grand:
            diff = mine - float(grand[metric])
            log.add(f"  GRAND {metric}: mine {mine:,.2f} vs team {float(grand[metric]):,.2f} "
                    f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
            if abs(diff) > tol:
                variances.append(f"GRAND {metric}: {diff:+,.0f}")
    return variances


def run_shopee(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    d = ROOT / "input" / period / "shopee"
    orders = ingest.read_parts(d / "orders", config.column_map(settings, "shopee", "orders"),
                               "orders", settings, log, "shopee")
    income = ingest.read_parts(d / "income", config.column_map(settings, "shopee", "income"),
                               "income", settings, log, "shopee")
    orders, income = ingest.derive_brand(orders, settings, log), ingest.derive_brand(income, settings, log)
    income = ingest.apply_settlement_bounds(income, period, settings, log)
    cl = classify.classify_shopee_income(income, log)
    sku = calculate.explode_to_sku_shopee(cl, orders, log)
    sku = calculate.compute_sku_columns_shopee(sku, settings, log)

    wb, checks = finance_template.build_shopee(sku, settings, window_meta(sku["statement_date"]), log)
    finance_template.write_workbook(wb, ROOT / "output" / period / "shopee" / "finance_file.xlsx",
                                    checks, log)

    ok = sku[sku["check_status"] == classify.SHOPEE_OK]
    per_store = {
        s: {"ok_pre_vat": float(g["amount_pre_vat"].sum()),
            "ok_with_vat": float(g["amount_with_vat"].sum())}
        for s, g in ok.groupby("store")
    }
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 2000))
    for metric, mine in [("pre_vat", float(ok["amount_pre_vat"].sum())),
                         ("with_vat", float(ok["amount_with_vat"].sum()))]:
        if metric in grand:
            diff = mine - float(grand[metric])
            log.add(f"  GRAND {metric}: mine {mine:,.2f} vs team {float(grand[metric]):,.2f} "
                    f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
            if abs(diff) > tol:
                variances.append(f"GRAND {metric}: {diff:+,.0f}")
    return variances


def run_lazada(period: str, settings: dict, refs: dict, log: RunLog) -> list[str]:
    fee_types = lazada.load_fee_type_map(ROOT / "config", log)
    vat_sku = lazada.load_vat_sku(ROOT / "config", log)
    ledger = lazada.read_ledger(ROOT / "input" / period / "lazada", settings, log)
    cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    rev = lazada.revenue_lines(cl, log)

    wb, checks = finance_template.build_lazada(rev, settings, window_meta(cl["transaction_date"]),
                                               log, classified=cl)
    finance_template.write_workbook(wb, ROOT / "output" / period / "lazada" / "finance_file.xlsx",
                                    checks, log)

    per_store = {}
    for (s, b), g in cl.groupby(["store", "fee_bucket"]):
        per_store.setdefault(s, {})[b] = float(g["amount_incl_vat"].fillna(0).sum())
    variances = tie(per_store, refs, log)
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", 1000))
    # The team's KA line sheets are per VAT rate — compare like for like.
    for rate_key, rate in [("pre_vat_105", 1.05), ("pre_vat", 1.08), ("pre_vat_110", 1.10)]:
        if rate_key not in grand or grand[rate_key] is None:
            continue
        mine = float(rev.loc[rev["vat_rate"] == rate, "check_no_vat"].sum())
        diff = mine - float(grand[rate_key])
        log.add(f"  GRAND pre_vat @{rate}: mine {mine:,.2f} vs team {float(grand[rate_key]):,.2f} "
                f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
        if abs(diff) > tol:
            variances.append(f"GRAND pre_vat @{rate}: {diff:+,.0f}")
    if len(unmapped):
        variances.append(f"{len(unmapped)} unmapped fee rows")
    return variances


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--period", required=True)
    ap.add_argument("--refs", default=None)
    args = ap.parse_args()
    log = RunLog()
    settings = config.load_settings(ROOT / "config")
    from src.masters import load_masters
    settings["_vat_sku"] = load_masters(ROOT / "config", settings, log)["vat_sku"]
    refs = json.loads(Path(args.refs).read_text(encoding="utf-8")) if args.refs else {}

    log.section(f"FULL RUN {args.platform} {args.period}")
    fn = {"tiktok": run_tiktok, "shopee": run_shopee, "lazada": run_lazada}[args.platform]
    variances = fn(args.period, settings, refs, log)

    log.section("RESULT")
    if variances:
        log.add(f"  {len(variances)} variance(s):")
        for v in variances:
            log.add(f"    - {v}")
    else:
        log.add("  ALL TIES")
    (ROOT / "output" / args.period / args.platform).mkdir(parents=True, exist_ok=True)
    log.write(ROOT / "output" / args.period / args.platform / "run_log.txt")
    return 0 if not variances else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/make_sample_data.py`

```python
"""Generate deterministic synthetic input files for a smoke run.

Writes input/2026-06_p1/{tiktok,shopee}/{orders,income}/ using the SOURCE
headers from config/settings.yaml column maps, so the run exercises header
mapping exactly as real files will. Bakes in the anomalies the pipeline must
surface: duplicate rows across overlapping file parts, income lines with no
matching order, an unknown SKU, refunds, zero-revenue lines, and prior-month
orders (the re-pull) for cross-period stitching.

Usage: python tools/make_sample_data.py
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PERIOD = "2026-06_p1"

STORES = {"Keoh Official Store": "KEOH", "Meko Official Store": "MEKO"}
SKUS = {
    "KEOH": [("KEOH-001", "Keoh Lip Balm 4g", 89000), ("KEOH-002", "Keoh Face Serum 30ml", 249000),
             ("KEOH-003", "Keoh Sunscreen SPF50 50ml", 189000)],
    "MEKO": [("MEKO-001", "Meko Shampoo 500ml", 129000), ("MEKO-002", "Meko Conditioner 500ml", 119000)],
}
UNKNOWN_SKU = ("MYST-999", "Mystery Gift Item", 15000)  # deliberately NOT in sku_master.csv


def build_platform(platform: str, rng: random.Random) -> tuple[list[dict], list[dict]]:
    orders, income = [], []
    for i in range(60):
        store = rng.choice(list(STORES))
        prefix = STORES[store]
        order_id = f"{platform.upper()}-{prefix}-{1000 + i}"
        # ~15% of orders are the prior-month re-pull (created in May, settled in June)
        created = (f"2026-05-{rng.randint(20, 31):02d} {rng.randint(8, 22):02d}:15:00"
                   if rng.random() < 0.15 else
                   f"2026-06-{rng.randint(1, 28):02d} {rng.randint(8, 22):02d}:15:00")

        n_lines = rng.choice([1, 1, 1, 2, 3])
        chosen = rng.sample(SKUS[prefix], k=min(n_lines, len(SKUS[prefix])))
        if i == 7:  # one order carries the unknown SKU
            chosen = chosen + [UNKNOWN_SKU]
        gross = 0
        for sku_id, sku_name, price in chosen:
            qty = rng.randint(1, 3)
            gross += qty * price
            orders.append({"order_id": order_id, "sku_id": sku_id, "sku_name": sku_name,
                           "quantity": qty, "unit_price_gross": price,
                           "order_created_at": created, "store": store})

        roll = rng.random()
        if i == 7:           # unknown-SKU order must settle normally so it reaches SKU explode
            roll = 0.99
        if roll < 0.08:      # returned
            refund, net = gross, 0
        elif roll < 0.13:    # zero revenue (fully voucher-covered / cancelled-settled)
            refund, net = 0, 0
        else:                # normal
            refund, net = 0, round(gross * rng.uniform(0.82, 0.95))
        income.append({"order_id": order_id, "store": store, "gross_revenue": gross,
                       "actual_refund": refund, "net_revenue": net,
                       "statement_date": f"2026-07-0{rng.randint(1, 5)}"})

    # two income lines that reference orders missing from the order files
    for j in (1, 2):
        income.append({"order_id": f"{platform.upper()}-GHOST-{j}", "store": "Keoh Official Store",
                       "gross_revenue": 99000, "actual_refund": 0, "net_revenue": 91000,
                       "statement_date": "2026-07-03"})
    return orders, income


def write_parts(rows: list[dict], folder: Path, colmap: dict[str, str], n_parts: int) -> None:
    """Split rows into parts; part 2 re-includes the tail of part 1 to
    simulate the overlapping exports that happen in real downloads."""
    folder.mkdir(parents=True, exist_ok=True)
    canonical_to_source = {v: k for k, v in colmap.items()}
    headers = list(colmap.keys())
    chunk = max(1, len(rows) // n_parts + 1)
    parts = [rows[i:i + chunk] for i in range(0, len(rows), chunk)]
    if len(parts) > 1:
        parts[1] = parts[0][-5:] + parts[1]  # overlap → duplicates to dedupe
    for idx, part in enumerate(parts, 1):
        with (folder / f"part_{idx}.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=headers)
            writer.writeheader()
            for row in part:
                writer.writerow({canonical_to_source[k]: v for k, v in row.items()})


def main() -> int:
    settings = yaml.safe_load(
        (ROOT / "tools" / "sample_config" / "settings.yaml").read_text(encoding="utf-8"))
    rng = random.Random(42)
    for platform in ("tiktok", "shopee"):
        orders, income = build_platform(platform, rng)
        base = ROOT / "input" / PERIOD / platform
        maps = settings["column_maps"][platform]
        write_parts(orders, base / "orders", maps["orders"], n_parts=3)
        write_parts(income, base / "income", maps["income"], n_parts=2)
        print(f"{platform}: {len(orders)} order lines, {len(income)} income lines -> {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/smoke_test.py`

```python
"""End-to-end smoke test on synthetic data.

Generates sample input, runs the pipeline for both platforms, and asserts
the outputs exist and contain what the baked-in anomalies predict.

Usage: python tools/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

import make_sample_data  # noqa: E402
import recon  # noqa: E402

PERIOD = "2026-06_p1"


def check(label: str, ok: bool) -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def main() -> int:
    make_sample_data.main()
    results: list[bool] = []

    for platform in ("tiktok", "shopee"):
        print(f"\n--- smoke: {platform} ---")
        rc = recon.main(["--period", PERIOD, "--platform", platform,
                         "--config-dir", str(ROOT / "tools" / "sample_config")])
        out = ROOT / "output" / PERIOD / platform
        results.append(check("pipeline exits 0", rc == 0))
        results.append(check("finance_file.xlsx written", (out / "finance_file.xlsx").exists()))
        results.append(check("run_log.txt written", (out / "run_log.txt").exists()))

        exc = pd.read_excel(out / "exceptions.xlsx", sheet_name=None)
        results.append(check("2 unmatched income lines flagged", len(exc["Unmatched Orders"]) == 2))
        results.append(check("unknown SKU flagged", (exc["Unknown SKUs"].get("sku_id") == "MYST-999").any()))
        results.append(check("zero-revenue lines flagged", len(exc["Zero Revenue"]) > 0))
        results.append(check("no tie-out breaches on clean data", len(exc["Tie-out Breaches"]) == 0))

        fin = pd.read_excel(out / "finance_file.xlsx", sheet_name=None)
        expected_tabs = ["Income"] + (["Return"] if platform == "shopee" else [])
        results.append(check(f"finance tabs are {expected_tabs}", list(fin) == expected_tabs))
        if platform == "shopee":
            results.append(check("Return tab has rows", len(fin["Return"]) > 0))
            neg = pd.to_numeric(fin["Return"]["Return Amount (negative adjustment)"], errors="coerce")
            results.append(check("return amounts are negative", bool((neg < 0).all())))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/stage1_probe.py`

```python
"""Stage-1-only probe: ingest + validate real files, print totals for manual
tie-out against the team's Total file. Runs NO other pipeline stage.

Usage:
    python tools/stage1_probe.py --period 2026-05_p1 --platform tiktok --expect-stores "U food"

--expect-stores overrides settings.expected_stores for subset runs (a partial
input set would otherwise hard-stop the full-platform store check).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config, ingest  # noqa: E402
from src.errors import ReconHardStop  # noqa: E402
from src.runlog import RunLog  # noqa: E402


def fmt(x: float) -> str:
    return f"{x:,.2f}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Stage 1 ingest probe (no downstream stages)")
    p.add_argument("--period", required=True)
    p.add_argument("--platform", required=True, choices=["tiktok", "shopee"])
    p.add_argument("--config-dir", default=str(ROOT / "config"))
    p.add_argument("--expect-stores", default=None,
                   help="Comma-separated store list for subset runs (overrides settings)")
    args = p.parse_args(argv)

    log = RunLog()
    settings = config.load_settings(Path(args.config_dir))
    if args.expect_stores is not None:
        stores = [s.strip() for s in args.expect_stores.split(",") if s.strip()]
        settings.setdefault("expected_stores", {})[args.platform] = stores
        log.add(f"(store check overridden for this probe: {stores})")

    input_dir = ROOT / "input" / args.period / args.platform
    try:
        log.section(f"STAGE 1 PROBE - {args.platform} - {args.period}")
        orders = ingest.read_parts(input_dir / "orders",
                                   config.column_map(settings, args.platform, "orders"),
                                   "orders", settings, log, args.platform)
        income = ingest.read_parts(input_dir / "income",
                                   config.column_map(settings, args.platform, "income"),
                                   "income", settings, log, args.platform)
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
        ingest.check_stores(orders, "orders", args.platform, settings, log)
        ingest.check_stores(income, "income", args.platform, settings, log)
    except ReconHardStop as stop:
        log.section("HARD STOP")
        log.add(str(stop))
        return 1

    log.section("ORDERS")
    log.add(f"  rows (SKU lines): {len(orders)}")
    log.add(f"  distinct orders : {orders['order_id'].nunique()}")
    created = orders["order_created_at"]
    log.add(f"  created range   : {created.min()} .. {created.max()}  (unparseable: {int(created.isna().sum())})")
    if "order_status" in orders.columns:
        for status, n in orders["order_status"].value_counts().items():
            log.add(f"    status {status}: {n}")

    log.section("INCOME")
    log.add(f"  rows: {len(income)}")
    if "income_type" in income.columns:
        for t, n in income["income_type"].value_counts().items():
            log.add(f"    type {t}: {n}")
    for col, label in [("gross_revenue", "Total Revenue (gross_revenue)"),
                       ("net_revenue", "Total settlement (net_revenue)"),
                       ("actual_refund", "Customer refund (actual_refund)")]:
        s = income[col]
        log.add(f"  {label}: sum {fmt(s.fillna(0).sum())} | non-zero rows {int((s.fillna(0) != 0).sum())} | unparseable {int(s.isna().sum())}")
    st = income["statement_date"]
    log.add(f"  settled range: {st.min()} .. {st.max()}  (unparseable: {int(st.isna().sum())})")
    if "income_order_created_at" in income.columns:
        oc = income["income_order_created_at"]
        log.add(f"  order-created range (from income file): {oc.min()} .. {oc.max()}  (unparseable: {int(oc.isna().sum())})")

    ids_in_orders = set(orders["order_id"])
    matched = income["order_id"].isin(ids_in_orders)
    log.add(f"  income lines with matching order file line: {int(matched.sum())}/{len(income)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

```


---

## `tools/verify_july_aliases.py`

```python
"""Evidence check for the July 29-31 window's renamed stores: the order
exports span the whole month, so if 'Reckitt' (w5) is the same store as
'Veet & Reckitt Personal Care' (w1-w4), their order-ID sets overlap heavily;
if it's a different store, overlap is ~zero. Read-only."""

from pathlib import Path

import pandas as pd

IN = Path(__file__).resolve().parents[1] / "input"

PAIRS = [
    ("Reckitt (w5)", "2026-07_w5", "23. Order Reckitt 07.xlsx",
     "Veet & Reckitt (w4)", "2026-07_w4", None, "Veet & Reckitt"),
    ("Nutifood Grow (w5)", "2026-07_w5", "22. Order Nutifood Grow 07.xlsx",
     "Nutifood Nutrition Store (w4)", "2026-07_w4", None, "Nutifood Nutrition"),
    ("Curel (w5)", "2026-07_w5", "16. Order Curel 07.xlsx",
     "any w4 store?", "2026-07_w4", None, None),
]


def order_ids(path: Path) -> set[str]:
    df = pd.read_excel(path, sheet_name="OrderSKUList", dtype=str, engine="calamine")
    col = next(c for c in df.columns if str(c).strip() == "Order ID")
    return set(df[col].dropna().astype(str).str.strip())


for name_a, win_a, file_a, name_b, win_b, _, needle in PAIRS:
    a = order_ids(IN / win_a / "tiktok" / "orders" / file_a)
    print(f"{name_a}: {len(a)} order IDs")
    if needle:
        files_b = [f for f in (IN / win_b / "tiktok" / "orders").glob("*.xlsx")
                   if needle.lower() in f.name.lower()]
        b = set()
        for f in files_b:
            b |= order_ids(f)
        ov = len(a & b)
        print(f"  vs {name_b} ({len(b)} IDs from {len(files_b)} file(s)): "
              f"overlap {ov} ({100 * ov / max(len(a), 1):.1f}% of w5 set)")
    else:
        best = ("", 0)
        for f in sorted((IN / win_b / "tiktok" / "orders").glob("*.xlsx")):
            ov = len(a & order_ids(f))
            if ov > best[1]:
                best = (f.name, ov)
        print(f"  best overlap in {win_b}: {best[0]} ({best[1]} IDs)"
              if best[1] else "  no overlap with any w4 store -> genuinely new store")
print("ALIAS CHECK DONE", flush=True)

```


---

## `config/settings.yaml`

```yaml
# settings.yaml — global pipeline configuration
#
# Column maps corrected against REAL May-2026 exports on 2026-07-16
# (TikTok order file "7. Order U food 17.05.xlsx", income "7. income U food.xlsx").
# The pre-sample placeholder maps are preserved in tools/sample_config/settings.yaml
# (used by the synthetic smoke test) and in the comment block at the end of this file.

vat_rate: 0.08            # used only by the legacy placeholder formulas (sample smoke path)

# VAT model (confirmed by the team): ONE default factor plus per-SKU
# exceptions from the team-owned master file below. The 8% rate is a
# TEMPORARY tax concession — when it reverts to 10%, change the single
# `default:` line to 1.10 and nothing else.
vat_factors:
  default: 1.08

# Team-owned master (additive-only, maintained by the team): fee-type
# mapping (Lib sheet) + per-SKU VAT exceptions (VAT sheet). Read live at
# runtime; the lazada_*.csv snapshots are the fallback, and live-vs-snapshot
# drift is reported on every run (src/masters.py).
masters_file: "Lib & VAT rate.xlsb"

# Force a specific Excel engine per platform/kind. June 2026 TikTok order
# exports ship a broken <dimension> tag that the default openpyxl reader
# truncates to one column; calamine ignores the dimension and reads the real
# cells (and reads well-formed May files identically). Everything not listed
# here keeps the default openpyxl fast path + single-column calamine fallback.
reader_engine:
  tiktok:
    orders: calamine
  # Shopee via openpyxl took 45+ min per window once OneDrive sync began
  # competing for the disk (Aug 2026); calamine reads the same files in
  # minutes and every July diagnostic already validated it on these exact
  # exports. Output totals are tie-checked after any engine change.
  shopee:
    orders: calamine
    income: calamine

tolerances:
  # Legacy values used by the sample/spec path only. The "~10,000 VND" rule
  # from the build spec is NOT what the team's files use — see tiktok below.
  split_rounding_vnd: 10000
  exact_check_vnd: 1
  # The team's OWN tolerance checks, read from their TikTok workbooks
  # ("Tiktok result 01 to 17T5 For KA.xlsx"):
  #   'PV sum'!E3     = IF(ABS(E2)<12000, "OK", "check lai sai roi")
  #   'Xuat HD bt'!P5 = IF(ABS(O5)<2000, "ok có thể xuất HD", "Cần check lại số có vấn đề")
  #   'PV xuat HD'!I1 = IF(ABS(H1)<1000, "ok có thể xuất HD", "Cần check lại số có vấn đề")
  tiktok:
    pv_sum_vnd: 12000       # pre-VAT: per-store totals vs per-VAT-bucket totals
    xuat_hd_vnd: 2000       # with-VAT: line totals vs VAT-bucket recombination
    pv_xuat_hd_vnd: 1000    # pre-VAT: line totals vs SKU-pivot recombination
  # Shopee's own checks (For KA "shopee result Sample For KA 01 to 10T05.xlsx"):
  #   'PV sum'!G3    = IF(ABS(E2)<2000,...)   — NOT TikTok's 12,000
  #   'Xuat HD bt'!R5 = IF(ABS(Q5)<2000,...)
  #   'return'!R7    = IF(ABS(Q7)<10, "Return full ko xuat HD",
  #                       "Return 1 phan phai xuat HD") — the finance-file
  #     Return-tab split: recomputed order total + refund within 10 VND of
  #     zero = full return (skip invoice), else partial (must invoice).
  shopee:
    pv_sum_vnd: 2000
    xuat_hd_vnd: 2000
    return_full_vnd: 10

periods:
  rolling_window_months: 2

# Settlement-window bounds — DEDUPLICATION OF A PULL ARTIFACT, not a rule.
# A window's finance file may only contain settlements dated inside its own
# labelled window. Add an entry ONLY when a raw export was pulled with the
# wrong start/end date AND its out-of-window rows are proven to be already
# present in the adjacent window. Evidence goes in the comment.
#
# 2026-07_w2 (verified 2026-08-07, tools scratch w2_boundary.py): the team's
# "Tiktok 2026_08 to 14 July" export also carried the whole 7-July settlement
# block (24,251 revenue rows — exactly the 24,251 rows w1 holds for 7 July)
# plus 256 stray rows dated 01-06 July. Every one of those orders is already
# in w1 ("01 to 07T07"), which legitimately owns settlements through 7 July.
# Rows settled 08-14 July whose orders also appear in w1 (1,180 rows) are
# genuine second payout events and are KEPT.
window_settlement_bounds:
  "2026-07_w2":
    from: "2026-07-08"

# TikTok amounts parse as standard (1234567.89). Re-verify per platform.
number_style: standard

# Drop raw columns that aren't in the column maps at read time: strips PII
# immediately (the team's Shopee M code does the same) and keeps full-
# platform runs within memory. Nothing downstream reads unmapped columns.
drop_unmapped_columns: true

# NO row dedupe on real data: byte-identical order lines are legitimate
# (duplicated gift SKUs — Sanofi Shopee May, team qty 2 vs deduped 1), and
# the team's Power Query never dedupes. Overlap protection comes from the
# one-period-folder-per-settlement-window layout instead.
dedupe_rows: false

# Date parsing: set true per platform if exports use dd/mm/yyyy text dates.
# TikTok confirmed dd/mm/yyyy (probe with false produced impossible Jan-Dec
# ranges for a May-only window).
dayfirst:
  tiktok: true
  shopee: false   # TODO verify when Shopee is mapped

# Rows to drop immediately after the header row. TikTok order exports have a
# per-column description row under the header ("Current order status." etc.)
# that must not be ingested as data.
skip_rows_after_header:
  tiktok:
    orders: 1

file_formats: [".xlsx", ".csv"]

# Which sheet to read per platform/kind (real exports are multi-sheet).
# null/absent = first sheet.
sheet_names:
  tiktok:
    orders: "OrderSKUList"
    income: "Order details"
  shopee:
    orders: "orders"

# Regex sheet matching for exports that split data across numbered sheets.
# Shopee income caps sheets at ~10k rows: "Doanh thu", "Doanh thu - 1", ...
# Mirrors the team's M code (Text.Contains(name, "Doanh thu")).
sheet_patterns:
  shopee:
    income: "Doanh thu"

# 1-based row holding the real (leaf) headers. Shopee income has two
# band/group rows above it (triple PromoteHeaders in the team's M code).
header_rows:
  shopee:
    income: 3

# Neither TikTok nor Shopee exports carry a store/shop column: store identity
# is implied by the per-store download file. Derive it from the file name.
# Group 1 of the regex = store name.
store_from_filename:
  # July 2026 added new order-file suffixes: "7.7"/"14.7"/"21.7"/"28.7"
  # (single-digit second part — May only had ".05"-style) and the 29-31
  # window's "29-31" income suffix. All optional trailing tokens, never
  # part of the store name.
  # "Life14.07" (no separator before the dotted token) appears in July w2 —
  # dotted date tokens may follow the name directly; bare numeric tokens
  # still require a separator so store names are never truncated.
  tiktok: '^\s*\d+\.?\s*(?:order|income)\s+(.+?)(?:[ _]*\d{1,2}\.\d{1,2}|[ _]+\d{1,2}|[ _]+\d{2}-\d{2})?\s*\.xlsx$'
  # Shopee names: "Income Kate.xlsx", "Income. Curel.xlsx", "Income.Astroman.xlsx",
  # "Order Kao part 10.xlsx", "Order.AHC part  6.xlsx", "Order.mars.wrigley part 2.xlsx".
  # June 2026 added a leading store-number prefix: "10_Income.Reckitt...xlsx",
  # "13_Order. ufood_store.xlsx", and inconsistently "1_ Income. Curel.xlsx"
  # (space after the underscore) — (?:\d+_\s*) handles all of them + May.
  shopee: '^\s*(?:\d+_\s*)?(?:order|income)[. ]\s*(.+?)(?:\s+part\s*\d+)?\s*\.xlsx$'

# Stores present in the May-2026 data (names as they appear in file names).
# TikTok: 17 stores in window 01-17T05; window 18-30T05 has 18 (adds
# "Nutifood Nutrition Store"). The old "26 TikTok stores" figure is NOT
# supported by this data; the requirements doc's 18 matches window 2.
# Numbering mystery RESOLVED (Hoang): #16 is the Veet & Reckitt combined
# file (Total files label it "Reckitt VN Chăm sóc cá nhân" — see aliases).
#
# KNOWN NAMING INCONSISTENCIES between Order and Income file names — these
# will break store matching on full-platform runs until an alias map is added:
#   - order "RVeet & Reckitt Personal Care" (typo) vs income "Veet & Reckitt Personal Care"
#   - order "Pediasure"                     vs income "Abbott Pediasure"
#   - order "Nutifood-Varna-Life"           vs income "Nutifood-Varna-life" (case)
# Stores that legitimately appear only in some windows: absent -> warning,
# not hard stop. "Nutifood Nutrition Store" onboarded mid-May (window 2 only).
stores_optional:
  tiktok:
    - "Nutifood Nutrition Store"
    # July 2026: not present in every weekly window
    - "Nutifood-Varna-life"
    # July 2026 onboarded stores (first seen in the July dump; presence
    # varies by window — Curel appears only in 29-31):
    - "Glucerna"
    - "Kate"
    - "Mondelez Kinh Do"
    - "Pepsico foods"
    - "Similac"
    - "Tolpa"
    - "Curel"
    # July w5 (29-31, 3-day window): these stores had zero settlements —
    # their income exports are header-only. Absence warns, never silent.
    - "Merries"
    - "Veet & Reckitt Personal Care"

expected_stores:
  tiktok:
    - "Abbott-Ensure"
    - "Abbott Pediasure"
    - "Abbott grow"
    - "AHC"
    - "Astroman"
    - "Unilever Homecare"
    - "U food"
    - "KAO"
    - "Lashe"
    - "Mars"
    - "Merries"
    - "Purite"
    - "Sanofi"
    - "Nutifood-Varna-Store"
    - "Nutifood-Varna-life"
    - "Xmen"
    - "Veet & Reckitt Personal Care"
    - "Nutifood Nutrition Store"
    # July 2026 onboarded stores:
    - "Glucerna"
    - "Kate"
    - "Mondelez Kinh Do"
    - "Pepsico foods"
    - "Similac"
    - "Tolpa"
    - "Curel"
  # Shopee: 17 stores per the May data (16 in windows 1-10/11-20;
  # "mondelezkinhdovn" appears from 21-end). Matches the requirements doc.
  shopee:
    - "Kate"
    - "Curel"
    - "Kao"
    - "Lashe"
    - "Merries"
    - "Sanofi"
    - "Thuan Phat"
    - "Unilever AHC"
    - "Xmenforboss"
    - "Astroman"
    - "Mars wrigley"
    - "Masan"
    - "nutifoodgpddvietnam"
    - "Reckitt Sức Khỏe Sắc Đẹp"
    - "Reckitt_chamsocnhacua"
    - "ufood_store"
    - "mondelezkinhdovn"

# Brand/invoice grouping per store. TikTok evidence (For KA "PV sum" bucket
# pivots, both windows): three invoice buckets — "KAO 8%", "Merries 8%",
# "Others 8%" — i.e. KAO and Merries get their own splits, everything else is
# combined (the spec's Keoh pattern). Shopee file names suggest Xmen and Kao
# split there. TODO-HUMAN (Hoang): confirm the full separate-invoice list.
# Empty map = brand falls back to the store name (loud warning at ingest).
store_to_brand: {}

# Store alias normalization, applied at ingest right after the store name is
# derived. Left side = name as it appears in data/file names; right side =
# canonical store name. "TODO-HUMAN" = unresolved, ingest warns if seen.
store_aliases:
  tiktok:
    "RVeet & Reckitt Personal Care": "Veet & Reckitt Personal Care"   # order-file typo (leading R)
    "Pediasure": "Abbott Pediasure"                                   # order files drop the "Abbott"
    "Nutifood-Varna-Life": "Nutifood-Varna-life"                      # casing differs order vs income
    # RESOLVED (Hoang): this Total-file label is source file #16
    # "income Veet & Reckitt Personal Care" — the missing number in the
    # 1-19 sequence. The file covers Veet + Reckitt combined.
    "Reckitt VN Chăm sóc cá nhân": "Veet & Reckitt Personal Care"
    # --- July 2026 drift: the 29-31 loose window renames most stores.
    # Canonical = the w1-w4 July file names (which match May where the
    # store existed). Typos ("Gluverna", "Reckit") are the team's.
    "Abbott Ensure": "Abbott-Ensure"
    "Abbott Grow": "Abbott grow"
    "Abbott Glucerna": "Glucerna"
    "Abbott Gluverna": "Glucerna"
    "Abbott Similac": "Similac"
    "Mondelez": "Mondelez Kinh Do"
    "Pepsifood": "Pepsico foods"
    "pepsifood": "Pepsico foods"
    "U Food": "U food"
    "Nutifood Varna Life": "Nutifood-Varna-life"
    "Nutifood Varna Store": "Nutifood-Varna-Store"
    # Verified by order-ID overlap (tools/verify_july_aliases.py):
    "Reckit": "Veet & Reckitt Personal Care"
    "Reckitt": "Veet & Reckitt Personal Care"
    "Nutifood Grow": "Nutifood Nutrition Store"
  shopee:
    # order vs income file-name drift (case / separator differences)
    "lashe": "Lashe"
    "mars.wrigley": "Mars wrigley"

column_maps:
  tiktok:
    # Real TikTok Seller Center order export: sheet "OrderSKUList",
    # 54 columns, header row 1, English. One row per SKU line.
    # Contains customer PII columns (Recipient, Phone #, Detail Address...)
    # which are NOT mapped and therefore never leave ingest.
    orders:
      "Order ID": order_id
      "Seller SKU": sku_id            # TODO confirm vs "SKU ID" (TikTok numeric id): which one does the calc file VLOOKUP on?
      "Product Name": sku_name
      "Quantity": quantity
      "SKU Unit Original Price": unit_price_gross
      "Created Time": order_created_at
      "Order Status": order_status
      "Order Refund Amount": order_refund_amount
      "SKU Subtotal After Discount": sku_subtotal_after_discount
      # Needed by the ported calculation chain (intermediary "Xuat HĐ" col N):
      "SKU Seller Discount": sku_seller_discount
      # The team's M code replaces null with "Good" (Thanh_recon Order query):
      "Cancelation/Return Type": cancel_return_type
    # Real TikTok income export: sheet "Order details" (1 of 4 sheets),
    # 65 columns, header row 1. One row per order or adjustment.
    income:
      "Order/adjustment ID": order_id   # May spelling
      "Order/Adjustment ID": order_id   # June 2026 rename (capital A)
      "Type": income_type               # May header
      "Transaction type": income_type   # June 2026 rename
      "Order created time": income_order_created_at
      "Order settled time": statement_date
      "Total Revenue": gross_revenue
      "Total settlement amount": net_revenue
      "Customer refund": actual_refund   # M code: refund amount reported for Partial Return / Payback lines
      # Columns the team's classification + calculation actually read
      # (evidence: Income_Final query in Thanh_recon V1/V2 M code):
      "Subtotal after seller discounts": subtotal_after_seller_discounts
      "Subtotal before discounts": subtotal_before_discounts
      "Refund subtotal after seller discounts": refund_subtotal_after_sd
      "Refund subtotal before seller discounts": refund_subtotal_before_sd
  shopee:
    # Real Shopee order export: sheet "orders", 62 Vietnamese columns, header
    # row 1; store comes from the file name (no shop column). PII columns
    # (Người Mua, Tên Người nhận, Số điện thoại, Địa chỉ nhận hàng, and the
    # invoice-request block Họ & Tên / Địa chỉ / Email) are NOT mapped — the
    # team's own M code removes them at ingest too.
    orders:
      "Mã đơn hàng": order_id
      "SKU phân loại hàng": sku_id
      "Tên sản phẩm": sku_name
      "Số lượng": quantity
      "Giá gốc": unit_price_gross
      "Ngày đặt hàng": order_created_at
      "Trạng Thái Đơn Hàng": order_status
      # Yellow-column inputs (intermediary "Xuat HĐ" cols U/V):
      "Người bán trợ giá": seller_subsidy
      "Được Shopee trợ giá": shopee_subsidy
    # Real Shopee income: sheets matching "Doanh thu*" (10k-row splits),
    # leaf header row 3, data from row 4. Lines are typed Order/Sku in
    # "Đơn hàng / Sản phẩm"; the team's M code keeps Order rows only.
    # Buyer username ("Người Mua") is PII — unmapped.
    income:
      "Mã đơn hàng": order_id
      "Đơn hàng / Sản phẩm": income_type          # "Order" / "Sku"
      "Ngày đặt hàng": income_order_created_at
      "Ngày hoàn thành thanh toán": statement_date
      "Tổng tiền đã thanh toán": net_revenue       # settlement (can be <= 0)
      "Giá sản phẩm": gross_revenue
      "Số tiền hoàn lại": actual_refund
      # Discount components read by the intermediary chain (cols H..M):
      "Mã ưu đãi Đồng Tài Trợ do Người Bán chịu": cofund_voucher
      "Sản phẩm được trợ giá từ Shopee": shopee_product_subsidy
      "Mã ưu đãi do Người Bán chịu": seller_voucher
      "Mã hoàn xu do Người Bán chịu": seller_coin_cashback
      "Phí vận chuyển - Người bán hỗ trợ": seller_ship_support

# ---------------------------------------------------------------------------
# Pre-sample placeholder maps (superseded 2026-07-16, kept for reference):
# tiktok.orders:  "Order ID"->order_id, "SKU ID"->sku_id, "Product Name"->sku_name,
#   "Quantity"->quantity, "SKU Unit Original Price"->unit_price_gross,
#   "Created Time"->order_created_at, "Shop Name"->store   (no Shop Name column exists)
# tiktok.income:  "Order/adjustment ID"->order_id, "Shop Name"->store,
#   "Total revenue"->gross_revenue (real header is "Total Revenue"),
#   "Actual refund"->actual_refund (no such column; refunds = "Customer refund"),
#   "Total settlement amount"->net_revenue, "Statement date"->statement_date
#   (real header is "Order settled time")
# shopee.orders:  "Tên Shop"->store (no such column; rest matched real headers)
# shopee.income:  flat map now marked TODO above (real file is two-row grouped)
# ---------------------------------------------------------------------------

```


---

## `config/brand_map.csv`

```
platform,storefront,client_brand,confidence,note
tiktok,abbott grow,Abbott,high,
tiktok,abbott pediasure,Abbott,high,
tiktok,abbott-ensure,Abbott,high,
tiktok,glucerna,Abbott,high,July 29-31 files name this store 'Abbott Glucerna'
tiktok,similac,Abbott,high,July 29-31 files name this store 'Abbott Similac'
tiktok,ahc,Unilever AHC,high,Shopee and Lazada name the same brand 'Unilever AHC'
tiktok,astroman,Astroman,high,
tiktok,curel,Curel,high,Kao brand; the team's own files invoice Curel separately
tiktok,kao,KAO,high,
tiktok,kate,Kate,high,separate Kate tab in the team's own invoicing files
tiktok,lashe,Lashe,high,
tiktok,mars,Mars,high,
tiktok,merries,Merries,high,Kao brand; separate invoice bucket in the team's files
tiktok,mondelez kinh do,Mondelez,high,
tiktok,nutifood nutrition store,Nutifood,high,
tiktok,nutifood-varna-life,Nutifood,high,Varna is a Nutifood brand
tiktok,nutifood-varna-store,Nutifood,high,Varna is a Nutifood brand
tiktok,pepsico foods,PepsiCo,high,
tiktok,purite,Purite,high,
tiktok,sanofi,Sanofi,high,
tiktok,tolpa,Tolpa,high,
tiktok,u food,U Food,high,
tiktok,unilever homecare,Unilever Home Care,high,Lazada 'Unilever Cham Soc Gia Dinh' translates to home care
tiktok,veet & reckitt personal care,Reckitt - Personal Care (Veet),needs-confirmation,NOT merged: Reckitt storefronts may be separate client entities
tiktok,xmen,X-Men,high,
shopee,astroman,Astroman,high,
shopee,curel,Curel,high,
shopee,kao,KAO,high,
shopee,kate,Kate,high,
shopee,lashe,Lashe,high,
shopee,mars wrigley,Mars,high,Mars Wrigley is Mars' confectionery arm; TikTok/Lazada store is 'Mars'
shopee,masan,Masan,high,
shopee,merries,Merries,high,
shopee,mondelezkinhdovn,Mondelez,high,
shopee,nutifoodgpddvietnam,Nutifood,high,
shopee,reckitt sức khỏe sắc đẹp,Reckitt - Suc Khoe Sac Dep,needs-confirmation,NOT merged: possibly a separate client entity
shopee,reckitt chamsocnhacua,Reckitt - Cham soc nha cua,needs-confirmation,NOT merged: possibly a separate client entity
shopee,sanofi,Sanofi,high,
shopee,thuan phat,Thuan Phat,high,
shopee,ufood store,U Food,high,
shopee,unilever ahc,Unilever AHC,high,
shopee,xmenforboss,X-Men,high,
lazada,curel,Curel,high,
lazada,kao,KAO,high,
lazada,lactacyd,Sanofi - Lactacyd,needs-confirmation,Lactacyd is a Sanofi brand; confirm whether it invoices under Sanofi
lazada,lashe,Lashe,high,
lazada,mars,Mars,high,
lazada,masan,Masan,high,
lazada,merries,Merries,high,
lazada,mondelez,Mondelez,high,
lazada,nutifood grow,Nutifood,high,
lazada,pepsico foods store,PepsiCo,high,
lazada,reckitt,Reckitt - Lazada storefront,needs-confirmation,NOT merged: scope of this storefront unknown
lazada,thuận phát,Thuan Phat,high,same store as Shopee 'Thuan Phat'; diacritics differ
lazada,thuan phat,Thuan Phat,high,
lazada,tolpa,Tolpa,high,
lazada,unilever ahc,Unilever AHC,high,
lazada,unilever chăm sóc gia đình nâng tầm cuộc sống unilever1,Unilever Home Care,high,'Cham Soc Gia Dinh' = home care; same division as TikTok 'Unilever Homecare'
lazada,unilever chăm sóc vẻ đẹp unilever 2,Unilever Beauty,high,single storefront; no cross-platform merge involved
lazada,xmen for boss,X-Men,high,

```


---

## `config/lazada_fee_types.csv`

```
﻿fee_name,bucket,status
Commission,6.CP co Invoice,exp
Commission fee - correction for undercharge,6.CP co Invoice,exp
Item Price Credit,1.Doanh Thu,bt
Lazada Bonus,4.Lazada bonus share by us,bt
Lazada Bonus - LZD co-fund,4.Lazada bonus share by us,bt
Lost Claim,1.Doanh Thu,bt
Payment Fee,6.CP co Invoice,exp
Payment fee - correction for undercharge,6.CP co Invoice,exp
Promotional Charges Flexi-Combo,2.Promotional Charges Flexi-Combo,bt
Promotional Charges Vouchers,3.Promotional Charges Vouchers,bt
Reversal Commission,6.CP co Invoice,exp
Reversal Item Price,1.Doanh Thu,bt
Shipping Fee Refund to Customer,7.CP no Invoice,exp
Shipping Fee Subsidy (By Seller),7.CP no Invoice,exp
Free Shipping Max Fee,6.CP co Invoice,exp
LCP Fee,6.CP co Invoice,exp
Reversal LCP Fee,6.CP co Invoice,exp
Sponsored Affiliates,6.CP co Invoice,exp
Reversal Promotional Charges Flexi-Combo,2.Promotional Charges Flexi-Combo,bt
Reversal Promotional Charges Vouchers,3.Promotional Charges Vouchers,bt
Wrong Weight Adjustment,7.CP no Invoice,exp
Lazcoin discount,5. Lazcoin discount,bt
Shipping Fee Voucher Refund to Laz,7.CP no Invoice,exp
Damaged Claim,1.Doanh Thu,bt
Reversal of Free Shipping Max Fee,6.CP co Invoice,exp
Sponsored Affiliates Refund,6.CP co Invoice,exp
LCP Fee - Adjustment,6.CP co Invoice,exp
Lazada Bonus - LZD co-fund - Reversal,4.Lazada bonus share by us,bt
Lazada Bonus - Reversal,4.Lazada bonus share by us,bt
FBL Handling Fee,6.CP co Invoice,exp
Storage Fee,6.CP co Invoice,exp
Sponsored Discovery - Top up,7.1. Sponsored Discovery check with CM,exp
Reversal Lazcoin discount,5. Lazcoin discount,bt
Shipping Fee Paid by Seller,6.1 CP shipping fee co invoice,exp
Sponsored Product Fee,6.CP co Invoice,exp
Marketing solution /social media advertising,6.CP co Invoice,exp
Sponsored Product Fee Claim,6.CP co Invoice,exp
Shipping fee - correction for undercharge,6.CP co Invoice,exp
FBL Bundling Fee,6.CP co Invoice,exp
Shipping Fee (Paid By Customer),6.1 CP shipping fee co invoice,exp
Shipping Fee Voucher (by Lazada),6.1 CP shipping fee co invoice,exp
Lost & Damaged FBL Inventories,1.Doanh Thu,bt
Seller Funded Marketing Voucher,3.1 Seller Funded Marketing Voucher,exp
Reversal shipping Fee (Paid by Customer),7.CP no Invoice,exp
Reversal Shipping Fee Voucher (by Lazada),7.CP no Invoice,exp
Shipping Fee Claims,6.1 CP shipping fee co invoice,exp
Shipping Fee Correction,6.CP co Invoice,exp
Sponsored Discovery - Lazada credit balance,7.CP no Invoice,exp
Pullout Charge,6.CP co Invoice,exp
Seller Incentive,1.1 Doanh Thu incentive,bt
Sponsored Discovery - Top up refund,7.CP no Invoice,exp
Universal Shopping Campaign,6.CP co Invoice,exp
Presale Deposit,1.Doanh Thu,bt
FBL Relabeling Fee,6.CP co Invoice,exp
LazSubsidy,1.Doanh Thu,bt
Seller balance adjustments - Credit,9.Hoan Thue VAT,Hoan Thue
Seller balance adjustments - Debit,6.CP co Invoice,exp
Sponsored Affiliates Claims,6.CP co Invoice,exp
Universal Shopping Campaign Refund,6.CP co Invoice,exp
Commission fee refund - correction for overcharge,6.CP co Invoice,exp
Pick Up Fee,7.CP no Invoice,exp
LazSubsidy - reversal,1.Doanh Thu,bt
Reversal - Damaged Claims,1.Doanh Thu,bt
Seller Incentive Offline Marketing,12. Income issue invoice,Other income
FBL Handling fee refund - correction for overcharge,11.Other income,exp
FBL Item Handling Fee,6.CP co Invoice,exp
FBL Item Handling Fee Discount,6.CP co Invoice,exp
FBL Order Handling Fee Discount,6.CP co Invoice,exp
FBL Outbound Fee,6.CP co Invoice,exp
LazFlash Extra/Everyday Below $9.99 Subsidy,7.CP no Invoice,exp
Everyday Below $9.99 Subsidy - Reversal/Everyday Below $9.99 Subsidy - Reversal,7.CP no Invoice,exp
Reversal Shipping Fee Paid by Seller,6.CP co Invoice,exp
Marketing Service Deduction - Marketing Fee,6.CP co Invoice,exp
Buyer Review Incentive,6.CP co Invoice,exp
Adjustments Item Charge,6.CP co Invoice,exp
Shipping fee refund - correction for overcharge,6.CP co Invoice,exp
Campaign Fee,6.CP co Invoice,exp
Reversal Campaign Fee,6.CP co Invoice,exp
Reversal Marketing Solution - Social Media Advertising,6.CP co Invoice,exp
LazCoins Discount Promotion Fee,6.CP co Invoice,exp
Freeship Max Fee Discount Rebate,6.CP co Invoice,exp
Reversal of LazCoins Discount Promotion Fee,6.CP co Invoice,exp
Seller Voucher Credit,7.CP no Invoice,exp
Reverse - Seller Voucher Credit,7.CP no Invoice,exp
Lazada Sponsored Solutions refund,7.CP no Invoice,exp
Wrong Shipping Fee Adjustment,7.CP no Invoice,exp
Payment Fee Credit,6.CP co Invoice,exp
LazCoins Discount,4.1 LazCoints Discount,bt
Reversal of LazCoins Discount,4.1 LazCoints Discount,bt
Adjustments Campaign Fee,6.CP co Invoice,exp
Strategic Seller Program Participation Fee,7.CP no Invoice,exp
Order Processing Fee,6.CP co Invoice,exp
Voucher Max,6.CP co Invoice,exp
Order Processing Fee - correction for undercharge,6.CP co Invoice,exp
Reversal Voucher Max,6.CP co Invoice,exp
Freeship Max Fee Discount Rebate - Reversal,6.CP co Invoice,exp
Infrastructure Fee - Auto,6.CP co Invoice,exp
Infrastructure Fee - Auto reversal,6.CP co Invoice,exp
Infrastructure Fee - Manual Reversal,6.CP co Invoice,exp
Reversal of Shipping Voucher (by Seller),6.CP co Invoice,exp
Seller Virtual Credit - Co-fund Voucher,7.CP no Invoice,exp
Reverse - Seller Virtual Credit - Co-fund Voucher,7.CP no Invoice,exp
Lazada Funded Commission from Seller Virtual Credit,6.CP co Invoice,exp
Reverse - Lazada Funded Commission from Seller Virtual Credit,6.CP co Invoice,exp
Customer Return Delivery Fee,6.CP co Invoice,exp
Order Processing Fee - Manual Reversal,6.CP co Invoice,exp
Reversal Lazada Subsidy on Item Price,1.Doanh Thu,bt
Product 360 Boost,6.CP co Invoice,exp
Product 360 Boost Rebate,6.CP co Invoice,exp
Seller Virtual Credit-LazCoins Discount Rebate,4.1 LazCoints Discount,bt
Product 360 Boost Refund,6.CP co Invoice,exp
Price Cut Discount,7.CP no Invoice,exp
Product 360 Boost Rebate Reversal,6.CP co Invoice,exp
Reversal of Seller Virtual Credit-LazCoins Discount Rebate,4.1 LazCoints Discount,bt
Seller Virtual Credit - Co-fund Price Cut,7.CP no Invoice,exp
Reversal Price Cut Discount,7.CP no Invoice,exp
Reverse - Seller Virtual Credit - Co-fund Price Cut,7.CP no Invoice,exp
LazCoins Discount-Affiliates Service Fee,4.1 LazCoints Discount,bt

```


---

## `config/lazada_vat_sku.csv`

```
﻿sku,rate
4901301245496_MEG2,1.05
4901301262509_MEG2,1.05
4901301262509,1.05
4901301245496,1.05
8851818070813,1.08
8992727007450,1.08
8851818070837,1.08
8851818129719,1.08
8851818707962,1.08
8851818129702,1.08
8992727007474,1.08
8851818943841,1.08
8851818188211,1.08
8851818205772,1.08
8851818390829,1.08
8851818340534,1.08
8851818188235,1.08
4901301354570,1.08
4901301354563,1.08
4901301043207,1.08
8851818969407,1.08
541513,1.08
8851818454224,1.08
4901301043221,1.08
8992727006033,1.08
8992727006026,1.08
4901301282743,1.08
4901301282750,1.08
8851818202528P01,1.08
KAO00012S,1.08
KAO00013S,1.08
KAO00014S,1.08
KAO00015S,1.08
KAO00016S,1.08
KAO00017S,1.08
8851818202528,1.08
8851818564985,1.08
8851818105072,1.08
8851818510555,1.08
KAO00035S,1.08
KAO00036S,1.08
KAO00037S,1.08
KAO00038S,1.08
KAO00039S,1.08
8851818070837G,1.08
KAO00050G,1.08
4901301230782,1.08
4901301230812,1.08
4901301230843,1.08
4901301230881,1.08
4901301253422,1.08
4901301230591,1.08
4901301230638,1.08
4901301230676,1.08
4901301281098,1.08
4901301230782_MRI2,1.08
4901301230812_MRI2,1.08
4901301230843_MRI2,1.08
4901301230881_MRI2,1.08
4901301253422_MRI2,1.08
4901301230591_MRI2,1.08
4901301230638_MRI2,1.08
4901301230676_MRI2,1.08
4901301281098_MRI2,1.08
4901301230782_MRI3,1.08
4901301230812_MRI3,1.08
4901301230843_MRI3,1.08
4901301230881_MRI3,1.08
4901301253422_MRI3,1.08
4901301230591_MRI3,1.08
4901301230638_MRI3,1.08
4901301230676_MRI3,1.08
4901301281098_MRI3,1.08
4901301230782_MRI4,1.08
4901301230812_MRI4,1.08
4901301230843_MRI4,1.08
4901301230881_MRI4,1.08
4901301253422_MRI4,1.08
4901301230591_MRI4,1.08
4901301230638_MRI4,1.08
4901301230676_MRI4,1.08
4901301281098_MRI4,1.08
4901301230782_MRI5,1.08
4901301230812_MRI5,1.08
4901301230843_MRI5,1.08
4901301230881_MRI5,1.08
4901301253422_MRI5,1.08
4901301230591_MRI5,1.08
4901301230638_MRI5,1.08
4901301230676_MRI5,1.08
4901301281098_MRI5,1.08
4901301259721,1.08
4901301259738,1.08
4901301281104,1.08
4901301230867,1.08
4901301230904,1.08
MER00003S,1.08
MER00004S,1.08
MER00005S,1.08
MER00001S,1.08
MER00002S,1.08
4901301388018,1.08
4901301388025,1.08
4901301388032,1.08
4901301396549,1.08
4901301396556,1.08
4901301396563,1.08
14901301403688,1.08
14901301403695,1.08
14901301403701,1.08
14901301402391,1.08
14901301402407,1.08
14901301402414,1.08
14901301402421,1.08
4901301424624,1.08
4901301424631,1.08
14901301424652,1.08
14901301424669,1.08
14901301424676,1.08
MERGIFT3,1.08
MRC00001S,1.08
8934712701028,1.08
8934712701004,1.08
8934712701189,1.08
8934712700205,1.08
8934712700212,1.08
8934712701462,1.08
8934712701479,1.08
8934712701004x4,1.08
8934712701004-V00,1.08
8934712701103V04,1.08
8934712701028-V00,1.08
8934712701103V03,1.08
8934712701004V01,1.08
8935136863392-V00,1.08
8934712701004-V02,1.08
8934712701004-V01,1.08
8934712711218_TP_001,1.08
8934712701004-V03,1.08
8934712700939V01,1.08
8934712700946V01,1.08
8934712700960V01,1.08
8934712700953V01,1.08
8934712007021V01,1.08
8934712700984V01,1.08
8934712211060V01,1.08
8934712701462V01,1.08
8934712701479V01,1.08
8934712701462V02,1.08
8934712701462V03,1.08
8934712701479V02,1.08
8934712701462V04,1.08
8934712701462V05,1.08
8934677000341,1.08
8934677000372,1.08
8934677000334,1.08
8934677000457,1.08
8934677020219,1.08
8934677000358,1.08
8934677013112,1.08
8934677013419,1.08
8934677020318,1.08
8934677013310,1.08
8934677013211,1.08
8934677020929,1.08
8934677020820,1.08
8934677014515,1.08
8934677034117,1.08
8934677042112,1.08
8934677056119,1.08
8934677026129,1.08
8852008300154,1.08
8852008300116,1.08
8852008300130,1.08
8852008304848,1.08
8852008304879,1.08
8852008510010,1.08
8852008510492,1.08
8852008511239,1.08
8852008510225,1.08
8990333822900,1.08
8934677001348,1.08
8934677001355,1.08
8934677013129,1.08
8934677013426,1.08
8934677020325,1.08
8852008510027,1.08
8852008510508,1.08
8852008511246,1.08
8934677042129,1.08
8934677001379,1.08
8934677001331,1.08
8934677001454,1.08
8934677020226,1.08
8934677034124,1.08
8852008300017,1.08
8852008300048,1.08
8852008304855,1.08
8852008304886,1.08
8934677013327,1.08
8934677013228,1.08
8934677050216,1.08
8934677050315,1.08
8934677050414,1.08
8934677029120,1.08
8934677025214,1.08
8934677035114,1.08
8934677035121,1.08
8852008510010P04,1.08
8852008300116P04,1.08
8934677042112P02,1.08
8852008511239P01,1.08
8852008510010P02,1.08
8852008510492P01,1.08
8934677042112P01,1.08
8935341300422,1.08
8935341300323,1.08
8935341300224,1.08
8935341300125,1.08
8934839124991,1.08
8934839118709,1.08
8934839119676,1.08
8934839123062,1.08
8934839123048,1.08
8934839123055,1.08
8934839120504,1.08
8934839123444,1.08
8934839123437,1.08
8934839122591,1.08
8934839123710,1.08
8934839123727,1.08
8934839123475,1.08
8934839123468,1.08
8934839122607,1.08
8934839123215,1.08
8934839123574,1.08
8934839125905,1.08
8934839125837,1.08
8934839126674,1.08
PS026658,1.08
8934839126667,1.08
8934839124991X3,1.08
8934839125837X3,1.08
8934839126209,1.08
8934839127442,1.08
8934839127411,1.08
8934839127435,1.08
8934839127398,1.08
8934839127961,1.08
8934839128463,1.08
8934839127954,1.08
8934839128470,1.08
8934839128449,1.08
8934839128418,1.08
8934839128432,1.08
8934839127978,1.08
8934839127909,1.08
8934839128456,1.08
8934839129576,1.08
8934839129217,1.08
8934839125905_PSU2_0405,1.08
8934839122607_PSU3_0600,1.08
8934839122607_PSU1_0605,1.08
8934839126117_PSU1_0630,1.08
8934839125837_PSU3_0631,1.08
8934839125905_PSU3_0904,1.08
8934839127442_PSU1_1011,1.08
8934839127435_PSU1_1012,1.08
8934839127411_PSU1_1013,1.08
8934839127411_PSU1_1091,1.08
8934839127442_PSU1_1217,1.08
8934839127435_PSU1_1218,1.08
8934839127411_PSU1_1219,1.08
8934707012078,1.08
8934707012085,1.08
8934707012092,1.08
8934707011972,1.08
8934707011514,1.08
8934707011040,1.08
8934707010456,1.08
8934707011729,1.08
8934707011736,1.08
8934707010487,1.08
8934707010500,1.08
8934707010944,1.08
8934707010968,1.08
8934707010982,1.08
8934707011644,1.08
8934707011002,1.08
8934707011026,1.08
8851932380393,1.08
8851932380409,1.08
4800888609540,1.08
4800888609526,1.08
8999999520991,1.08
8999999520984,1.08
8999999520960,1.08
8999999520977,1.08
8934868133735,1.08
8934868134138,1.08
8934868133742,1.08
8999999057442,1.08
8999999057480,1.08
8999999057459,1.08
8934707009269,1.08
8934868132035,1.08
8934868135647,1.08
8934868135654,1.08
8934868136477,1.08
8934707027638,1.08
8934707027621,1.08
8934707027706,1.08
8934707027614,1.08
8934707027690,1.08
8934707027867,1.08
8934707027478,1.08
8934707012122,1.08
8934707027874,1.08
8934707027898,1.08
8934707027881,1.08
8934707027737,1.08
8934707028550,1.08
8934707028574,1.08
8934707028598,1.08
8934707028611,1.08
8888086010302,1.08
8934707028727,1.08
8934707028765,1.08
8934707029168,1.08
8934707029182,1.08
8934707029175,1.08
8934707029441,1.08
8934707029434,1.08
8934707029137,1.08
8934707029342,1.08
8934707029366,1.08
8934707011002_KNO6_0103,1.08
8934707010944_KNO6_0104,1.08
8934707010982_KNO6_0105,1.08
8934707011972_KNO5_0106,1.08
8851932380393_KNO2_0136,1.08
8934707011644_KNO6_0183,1.08
8934707011040_KNO2_0199,1.08
8934707012085_KNO2_0258,1.08
8934707011040_KNO10_0266,1.08
8934707012078_KNO2_0315,1.08
8934707012085_KNO2_0316,1.08
8934707012122_KNO5_0470,1.08
8934707027478_KNO1_0506,1.08
8934707027478_KNO1_0545,1.08
8934707027874_LTN2_0571,1.08
8934707027898_LTN2_0572,1.08
8934707027881_LTN2_0573,1.08
8934707027737_KNO2_0574,1.08
8934707027478_KNO2_0575,1.08
8934868135647_LTN2_0576,1.08
8934868135654_LTN2_0577,1.08
8934868133735_LTN2_0642,1.08
8934868134138_LTN2_0643,1.08
8934868133742_LTN2_0644,1.08
8934707012122_KNO2_0804,1.08
8934707028611_LTN1_0851,1.08
8934707028611_LTN1_0855,1.08
8934707028598_LTN2_0868,1.08
8934707027874_LTN3_0933,1.08
8934707027898_LTN3_0934,1.08
8934868135647_LTN3_0935,1.08
8934707027874_LTN1_0936,1.08
8934868135654_LTN3_1020,1.08
8934707027881_LTN3_1021,1.08
8934868133735_LTN1_1260,1.08
8934868133735_LTN3_1261,1.08
8934707027898_LTN3_1262,1.08
8934868135647_LTN3_1263,1.08
MER00001G,1.08
710079,1.08
2000000000608,1.08
8934839129576_PSU1_1475,1.08
R22SW-Blk3,1.08
R22PU-Stealth,1.08
R22PU-Black,1.08
MER00009G,1.08
14901301419191,1.08
14901301425932,1.08
4901301425898,1.08
4901301425898V09,1.08
4901301425935,1.08
4901301425881,1.08
4901301425874V07,1.08
14901301425925,1.08
4901301425928,1.08
SAN00001SV01,1.08
SAN00001SV02,1.08
SAN00001S,1.08
SAN00002S,1.08
SAN00002SV01,1.08
SAN00002SV02,1.08
8934839133504,1.08
14901301419269,1.08
4901301418982V02,1.08
4901301418982,1.08
14901301419207,1.08
14901301419214,1.08
4901301425881V08,1.08
4901301425874,1.08
14901301419221,1.08
8992727007580_MRI2,1.08
4901301418999V03,1.08
4901301418999,1.08
4901301418975,1.08
4901301418975V01,1.08
8934839129576_PSU1_1547,1.08
8934839132835_PSU2_1533,1.08
2000000000778,1.08
KAO00030G,1.08
8992727006453_MRI3,1.08
8992727007580_MRI3,1.08
4901301418579V06,1.08
8934839133603,1.08
8934839134198,1.08
8934839133627,1.08
8934839133610,1.08
8934839132811_PSU2_1532,1.08
8934712701578,1.08
8934712701585,1.08
8934712701578V02,1.08
8934712701578V01,1.08
8934712701585V01,1.08
14901301425871,1.08
14901301425888,1.08
14901301418576,1.08
4901301420381P01,1.08
4901301418975P01,1.08
14901301425895,1.08
14901301425918,1.08
8992727006422_MRI2,1.08
8992727006453_MRI2,1.08
8992727007320_MRI2,1.08
8992727007320,1.08
14901301423266,1.08
4901301418982P01,1.08
4901301418999P01,1.08
4901301420312P01,1.08
4901301425911,1.08
14901301419238,1.08
4901301418579,1.08
14901301419009,1.08
4901301419002,1.08
8934712701004P01,1.08
4901301420381V05,1.08
4901301420381,1.08
4901301423269,1.08
14901301419252,1.08
MRC00118G,1.08
MRC00117G,1.08
8934712701103V05,1.08
4901301420312V04,1.08
8992727007320_MRI3,1.08
8934712701103V06,1.08
8934712701103,1.08
8992727006422_MRI3,1.08
8992727006453,1.08
8934712701004V02,1.08
8992727007580,1.08
4904746149480,1.08
4904746149497,1.08
4904746149503,1.08
4904746149510,1.08
4904746149527,1.08
4904746149534,1.08
4904746149541,1.08
4904746149558,1.08
4904746149565,1.08
4904746299482,1.08
4904746299789,1.08
4904746300089,1.08
4904746299185,1.08
4904746298881,1.08
4904746298584,1.08
4904746138538,1.08
4904746298188,1.08
4904746297983,1.08
4904746078902,1.08
4904746078919,1.08
4904746080332,1.08
4904746080356,1.08
4904746066749,1.08
4904746066763,1.08
4904746069665,1.08
4904746069689,1.08
4904746138422,1.08
4904746138439,1.08
4904746139856,1.08
4904746139863,1.08
4904746078049,1.08
4904746074829,1.08
4904746074836,1.08
4904746078094,1.08
4904746082237,1.08
4904746082282,1.08
4904746082336,1.08
4904746070364,1.08
4904746699480,1.08
4904746148629,1.08
4904746147257,1.08
4904746132185,1.08
4904746132215,1.08
4904746132246,1.08
4904746132277,1.08
4904746132307,1.08
4904746132314,1.08
4904746128348,1.08
4904746128379,1.08
4904746128409,1.08
4904746127266,1.08
4904746127273,1.08
4904746127310,1.08
4904746127327,1.08
4904746127365,1.08
4904746127372,1.08
4904746118448,1.08
4904746118479,1.08
4904746118493,1.08
4904746118516,1.08
4904746119001,1.08
4904746119018,1.08
4904746119025,1.08
4904746143303,1.08
4904746143310,1.08
4904746143327,1.08
4904746148285,1.08
4904746148292,1.08
4904746148308,1.08
4904746148315,1.08
SAN00015GV,1.08
6908594419151,1.08
6908594431177,1.08
8888336008073,1.08
8936123411343V01,1.08
8936123411343,1.08
8936123411343V19,1.08
8934712701004V03,1.08
8936123411343V02,1.08
4901301420312,1.08
8851818129696P01,1.08
6908594427538P01,1.08
8851818188242P01,1.08
8851818188228P01,1.08
8851818070837P02,1.08
8851818070806P01,1.08
8851818707962P02,1.08
8851818708266P01,1.08
4901301282743P01,1.08
6908594414484,1.08
8936123411343V10,1.08
6908594414491P01,1.08
8992727007320P02,1.08
8992727006453P02,1.08
8992727007580P02,1.08
14901301442953,1.08
8934839133689X3,1.08
8934839132866_PSU2_1534,1.08
8934839132835_PSU4_1823,1.08
8934839125905X3,1.08
8934839134617_PSU1_2048,1.08
8934839134617_PSU2_2041,1.08
8934839134631_PSU2_2040,1.08
4901301442956,1.08
8934839134594_PSU1_1931,1.08
8934839134594_PSU2_1863,1.08
8934839133689_CLU8_1835,1.08
8934839133689_CLU3_1833,1.08
8934839134594_PSU8934839128081X3_2094,1.08
8851818205789P01,1.08
4901301282750P01,1.08
8936123411343V14,1.08
8936123411343V15,1.08
14901301418989,1.08
8934839134617_PSU3_2043,1.08
8934839133689_CLU6_1834,1.08
8992727007580P01,1.08
KAO00002P,1.08
KAO00001P,1.08
8936123411343V11,1.08
14901301442960,1.08
4901301442963,1.08
14901301418996,1.08
4901301442918V10,1.08
9355465008537,1.08
OFS000014G,1.08
9355465008346,1.08
9355465008193,1.08
OFS00008G,1.08
9355465008155,1.08
9355465000494,1.08
9355465000487,1.08
9355465008551,1.08
OFS000012G,1.08
OFS00009G,1.08
9355465008711,1.08
9355465008568,1.08
OFS000015G,1.08
9355465008544,1.08
OFS000013G,1.08
9355465008582,1.08
9355465008759,1.08
9355465008193,1.08
9355465008759,1.08
9355465008155,1.08
OFS000015G,1.08
OFS000014G,1.08
9355465008346,1.08
9355465008605,1.08
9355465008520,1.08
9355465009121,1.08
OFS00008G,1.08
9355465008759,1.08
8992727007320P01,1.08
14901301442915,1.08
9355465009138,1.08
8851818708266,1.08
8851818070806,1.08
14901301442977,1.08
8934839134594_PSU4_1865,1.08
8934839134631_PSU3_2042,1.08
8934839134594_PSU3_1864,1.08
8934839134631_PSU4_2044,1.08
SAN00090V,1.08
8934839134617_PSU4_2045,1.08
4904746148285P01,1.08
MRC00267V,1.08
MRC00266V,1.08
MRC00273V,1.08
MRC00278V,1.08
SAN00008V,1.08
KAO00010P,1.08
OFS00003VG,1.08
MRC00055P,1.08
MRC00271V,1.08
9355465008773,1.08
OFS000010G,1.08
MRC00061P,1.08
MRC00058P,1.08
MRC00269V,1.08
MRC00272V,1.08
MRC00270V,1.08
MRC00056P,1.08
KAO00011P,1.08
4901301442970,1.08
4901301442918,1.08
KAO00009P,1.08
8934839135119,1.08
8934839127381,1.08
8934839127404,1.08
8934839133511,1.08
8934839127404_PSU2_1456,1.08
8934839127398_PSU2_1455,1.08
8934839127381_PSU2_1454,1.08
8934839133498,1.08
8934839134617_PSU8934839128081X3_2095,1.08
SAN00007V,1.08
4904746070159,1.08
4904746070142,1.08
MRC00059P,1.08
6914973203884,1.08
MAR00003P,1.08
MAR00005V,1.08
MAR00006V,1.08

```


---

## `.gitignore`

```
# Data never goes in git — input files contain client/order data
input/
output/

__pycache__/
*.pyc
.venv/
venv/

```
