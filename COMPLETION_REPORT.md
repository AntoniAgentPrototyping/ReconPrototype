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
