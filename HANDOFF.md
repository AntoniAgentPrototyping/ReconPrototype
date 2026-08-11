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
