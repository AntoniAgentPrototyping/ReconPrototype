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
