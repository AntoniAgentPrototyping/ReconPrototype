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
