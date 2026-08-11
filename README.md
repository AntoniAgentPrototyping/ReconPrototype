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
