# 03 — Pipeline

Stage by stage. For the *rules* each stage applies, see [05-DOMAIN-RULES](05-DOMAIN-RULES.md); for file conventions, [04-DATA-FLOW](04-DATA-FLOW.md).

```
raw exports (one settlement window, one platform)
   │   input/<YYYY-MM_wN|sN|lN>/<platform>/...        never mixed across windows
   ▼
1. INGEST         src/ingest.py       multi-part xlsx/csv → typed frames. Column maps from
   │                                  config; store from filename; unmapped (PII) columns
   │                                  dropped at read; per-file row counts logged; settlement
   │                                  bounds applied where an export was proven mis-pulled;
   │                                  store roster checked (TikTok income only)
   ▼
2. STITCH         src/stitch.py       income lines matched to true order-creation dates via the
   │                                  order files (which include the prior-month re-pull).
   │                                  Unmatched → exceptions, never dropped
   ▼
3. CLASSIFY       src/classify.py     TikTok: Good / Partial Return / Total Return / Payback
   │                                  (+ unclassified → exceptions).  Shopee: ok / Return / 0 đồng
   ▼
4. CALCULATE      src/calculate.py    the "yellow columns": SKU explode, net revenue, pre-VAT
   │              src/lazada.py       back-out, per-SKU VAT.  Lazada instead: fee ledger →
   │                                  buckets → Price KA lines
   ▼
5. TIE-OUT        src/tieout.py       the team's own checks at their own tolerances.
   │                                  ⚠ currently non-functional — see 08-KNOWN-DEFECTS
   ▼
6. EXPORT         src/finance_template.py    invoicing workbook in the team's template shape
                                      (PV sum / Summary / brand tabs / control blocks)
```

Driver: `tools/devrun.py --platform <p> --period <window> [--refs refs.json]` (developer only since M6 — a user queues the window in the browser and `service/worker.py` makes the same calls).

## Stage notes

**1. Ingest.** Reads every file part in the folder and concatenates. Header row is configurable per platform/kind (Shopee income headers sit on row 3 — the team's triple `PromoteHeaders`). A regex sheet pattern handles exports split across numbered sheets (Shopee income caps at ~10k rows/sheet: `Doanh thu`, `Doanh thu - 1`, …). Reader engine is overridable per platform/kind because real exports have been malformed in several distinct ways. Row dedupe is **off** for real platforms — byte-identical order lines are legitimate.

**2. Stitch.** Exists because an income line's settlement date is not its order date, and revenue must land in the period the order belongs to. The order folder deliberately contains the prior-month re-pull so cross-period orders can be attributed.

**3. Classify.** Decides who gets invoiced. TikTok collapses settlement lines per (store, order, type, order-created time) before classifying. Reimbursement/adjustment lines fall through every branch and are carried as `unclassified` → exceptions, matching the team, who book them outside the revenue invoice.

**3b. Cross-window orders** (TikTok and Shopee, between classify and the explode). An order settled in this window may have been *created* earlier, so its SKU lines are in a previous window's order export and the explode's join finds nothing — the revenue then leaves through the "~21% unmatched" door, which is expected to have traffic and so says nothing. `src/backfill.py` looks for those lines in the **nearest same-month predecessor window**, one window per order, never pooled ([D59](06-DECISIONS.md#d59)). `cross_window_order_backfill` decides what happens next: `off` skips the step entirely, `report` measures it and changes no number, and `apply` — **the current default, since 2026-08-20** — puts the lines in the frame, where they pass the same conservation checks as the window's own. It moved no golden cell (no golden window has a predecessor holding anything) and closed July's gap from 4,527,401,608 VND to 1,579,645,766. Lazada has no order files, so this cannot happen there.

**4. Calculate.** The money math. TikTok rebuilds revenue from the **order side** (income is the *check*, not the source); Shopee allocates discounts proportionally across SKU lines; Lazada nets promos into a whole-VND unit price. Per-column provenance lives in `TIKTOK_FORMULA_STATUS` / `SHOPEE_FORMULA_STATUS` / `LAZADA_FORMULA_STATUS`.

**5. Tie-out.** Intended to be the control. Read [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md) before trusting a green result: the three TikTok checks are algebraic identities, their return value is discarded by the driver, and Shopee/Lazada never call the module at all.

**6. Export.** Reproduces the team's workbook shape including control blocks and verdict cells, computed from the engine rather than copied. Deliberately reproduces two different semantics side by side: **line tabs** carry exact amounts, **pivot tabs** carry the rounded-price invoice view. The drift between them is what the team's checks measure.

## The three platforms are genuinely different

| | TikTok | Shopee | Lazada |
|---|---|---|---|
| Raw inputs | order export (SKU rows) + income export (order rows) | order export + income export (3-row grouped header, 10k-row split sheets) | transaction **ledger** only — fee lines, no order rows |
| Windows/month | 2 (May) → 5 weekly (from July) | 3–4 + sub-batches | 5 weekly (last uses the Daily schema) |
| Revenue rebuild | **order-side rebuild**: SKU lines joined to OK income orders; income is the check | income-side amounts with **proportional discount allocation** `Z=(T/X)·Y` | **Price KA** = `round((credits + matched promo)/units/VAT)` |
| Status logic | M-code port: OK = Good; take-out = {Total Return, Payback, Partial}; unclassified → exceptions | derived + verified: Return (refund≠0), 0 đồng (settlement≤0 ∧ refund=0), ok | fee-name → bucket via the team's Lib master; refunds net inside the ledger |
| VAT | all 1.08 (the 1.05/1.10 template cells are dead — verified) | per-SKU exceptions exist; none traded in May | per-SKU via the VAT_SKU master; first live non-1.08 SKUs |
| Invoice splits | KAO / Merries / Others | Curel / KAO / Merries / Kate / Others + Xmen & Kao batch files | Curel / KAO / Merries / Others, per VAT rate |
| Tolerances (VND) | 12,000 / 2,000 / 1,000 | 2,000 / 2,000 / 10 | 1,000 / 2,000 |
| Quirks | dd/mm dates; a description row under the header; no store column; multi-settlement orders | byte-identical duplicate rows are **legitimate** (gift SKUs — dedupe forbidden); Return tab 10-VND full/partial split | Weekly **and** Daily schemas both permanent; order-less revenue rows (compensations) surfaced as named reconciling items |

## Failure posture

**Hard-stop (exit 1, no finance file):** input folder missing · no files of the expected suffix · no sheet matching the pattern · required columns unresolved after header mapping · store not derivable from the filename · store-roster mismatch · Lazada missing canonical columns or both schema folders · no fee-type/VAT mapping available.

**Warn and continue:** unknown SKUs · unmapped fee names · master-vs-snapshot drift · unresolved store aliases · stores with no brand mapping · optional stores absent.

Exit code 1 also fires on any tie variance, which conflates "I checked and found a discrepancy" with "I could not check at all" — see [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md).

## How the rules were derived

- **Power Query M code** was extracted from the team's Total files' embedded DataMashup (UTF-16 customXml → base64 → length-framed inner zip → `Formulas/Section1.m`) and ported statement by statement. Source of the TikTok classification, the column removals including PII, and the null→"Good" rule.
- **Worksheet formulas** in the intermediary "Xuất HĐ" sheets were read cell by cell (header row 3, formulas from row 4) and ported as the calculation chain. Each column in `src/calculate.py` cites its source cell.
- **Per-formula status dicts** record when each column was row-verified and by which harness. Nothing was marked verified without a row-level match against the team's own file.
- Where the team's artifacts conflicted or were silent, the question was escalated rather than guessed; answers were folded back into config with the answer documented.
