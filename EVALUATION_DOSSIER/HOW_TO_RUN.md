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
