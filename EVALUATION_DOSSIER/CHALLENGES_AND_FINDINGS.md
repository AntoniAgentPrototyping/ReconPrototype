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
