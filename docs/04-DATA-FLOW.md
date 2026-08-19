# 04 — Data Flow

Where data comes from, what shape it arrives in, where it goes, and what must never leave.

## Sources

Raw seller-centre exports, downloaded by hand per window per store. The repo is **data-free by design**: `input/` and `output/` are gitignored, and the real May/June/July data lives in a shared SharePoint folder (ask the project owner for access).

| Platform | Files per window | Sheet(s) read | Header row |
|---|---|---|---|
| TikTok orders | one per store, sometimes suffixed with a date token | `OrderSKUList` | 1 (+1 description row skipped) |
| TikTok income | one per store | `Order details` | 1 |
| Shopee orders | one per store, often `part 1..N` (28 parts observed) | `orders` | 1 |
| Shopee income | one per store, `part 1..N` | every sheet matching `/Doanh thu/` (caps ~10k rows each) | 3 |
| Lazada ledger | one per store | `Transaction Overview` (Weekly) **or** `Income Overview` (Daily) | 1 |

Observed real volumes for a single store-window: TikTok 93,428 order rows + 58,925 income rows; Shopee 174,425 order rows across 28 parts + 83,134 income rows; Lazada 1,826 ledger rows. A full verified month is ~288,000 rows.

## Window naming

`YYYY-MM_` + a platform letter + an index:

- TikTok `w1..w5` — May used 2 windows (`01–17`, `18–31`); weekly cadence began in July (`01-07/08-14/15-21/22-28/29-31`)
- Shopee `s1..s4`, plus sub-batches like `s2x` (Xmen) and `s3k` (Kao) — `01-10/11-20/21-28/29-31`
- Lazada `l1..l5` — `01-05/06-12/13-19/20-26/27-31`

## Input layout

```
input/<window>/tiktok/{orders,income}/*.xlsx
input/<window>/shopee/{orders,income}/*.xlsx
input/<window>/lazada/{Weekly,Daily}/*.xlsx        either or both
```

**Never mix two windows' order exports** — re-exports drift, and cross-window overlap is a real, material failure mode (see [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md)).

## Staging

Getting raw downloads into that layout used to be **the single biggest fragility** in the system: a hand-written PowerShell script (`tools/stage_july.ps1`, deleted in M2.5) carrying one developer's absolute paths and a hand-maintained folder-name → window table, rewritten every month because zip layouts change, routing on the substrings `rder`/`ncome`, validating nothing.

`tools/stage_exports.py` replaces it. **The window is derived from the data, never from a folder name:**

- Every income/ledger export states its own settlement date range. Files are grouped (top-level dump folder for TikTok/Shopee, so `part 1..N` and the `Doanh Thu/` + `Order New/` split stay together; per-file for Lazada, which ships one ledger per store per window), each group's span is read, **groups whose spans overlap are one window**, and the windows are then sorted chronologically and indexed — `2026-05_s1`, `_s2`, …
- Order exports never define a window: they deliberately span earlier months for the cross-period stitch. They inherit their group's.
- Lazada Weekly-vs-Daily comes from the **sheet name**, not the folder — this fixed a defect where a mislabelled July window needed a manual restage.

That derivation reproduces the labels a human assigned by hand for all eight currently staged windows, which is what `tests/test_staging.py` pins.

**It plans by default and copies only with `--apply`**, because staging is where a whole store — or an entire extra settlement block — silently enters a month.

The guarantee is **never stage a partial window**, enforced per window rather than per run: a problem holds back the affected window and the others still stage, with a non-zero exit code so "staged" can never be read as "staged everything".

| Condition | Effect | Why |
|---|---|---|
| an order/income export that will not open, has no derivable store name, or is missing its settlement-date column | **blocks that window** | the window would be silently incomplete, and the run afterwards would look fine |
| identical content (SHA-256) bound for two windows, or already staged under another | **blocks** both | the **double-pull class** — 5.97B VND of double-invoicing risk when it last happened |
| an export whose settlement range starts before its siblings' | **warns** | the same mis-pull, caught at staging rather than by ad-hoc analysis a month later. Confirm, then declare it under `window_settlement_bounds` ([D9](06-DECISIONS.md#d9)) |
| a file that is not identifiable as an export at all (name says neither order nor income; not a Lazada schema) | **skipped** | a team analysis workbook in the dump folder is not a missing export. An encrypted file is *named*, not a traceback ([D6](06-DECISIONS.md#d6)) |
| an export with no data sheet | **skipped** | nine Shopee "part 2" income files in July each declared total 0 in their own Summary; the team's conclusion was to leave them out, and `check_stores` still fires if a store truly vanishes |

Copies, never moves; idempotent by digest, so re-staging cannot perturb a golden already built from a tree. Each staged window gets a `staging.json` recording every file's origin, digest, row count, store and settlement span — staging previously left no record, so "is this window complete?" was unanswerable afterwards. The pipeline ignores it (`file_formats` covers `.xlsx`/`.csv` only).

**Not automated:** order-ID-level overlap between windows. The date-range check above catches the observed failure shape cheaply; genuine ID-level comparison still needs the ad-hoc analysis that found the July double-pull.

## Store identity

**TikTok and Shopee exports carry no store column.** Store identity comes from the download filename via a per-platform regex, and is a hard stop if the regex misses. Aliases in `config/settings.yaml` fold renamed/typo'd storefronts onto a canonical name — and each non-obvious alias carries its evidence, because names alone are not sufficient proof. One store that *looked* like an alias by name similarity was proven genuinely new by having zero order-ID overlap with the store it resembled.

This is the most invasive thing to change if inputs ever become API responses, because `store` is a join and group key throughout.

## Masters (team-owned reference data)

`config/Lib & VAT rate.xlsb` is maintained by the team, additive-only, and read **live at runtime**:

- `Lib` sheet → 118 fee-name → bucket/status mappings (Lazada)
- `VAT` sheet → 660 per-SKU VAT exceptions (4 at 1.05)

`config/lazada_fee_types.csv` and `config/lazada_vat_sku.csv` are committed point-in-time snapshots used as a fallback, and **live-vs-snapshot drift is reported on every run**. The intent is that the team keeps owning the rules while the pipeline stops being a second, silently-divergent copy of them.

Note: the master is re-read up to three times per Lazada run — no caching.

## Outputs

```
output/<window>/<platform>/finance_file.xlsx     the deliverable
output/<window>/<platform>/run_log.txt           the audit trail
output/<window>/<platform>/run_metrics.json      timing / rows / peak RSS  (M1)
output/<window>/<platform>/exceptions.xlsx       when any exception sheet has rows  (M2)
```

Through the M4 service the same four files are produced by the same function and then handed to the artifact store, which addresses them **per run** so a re-run does not overwrite the evidence:

```
.scratch/job-<id>/<window>/<platform>/...              the worker's working copy, removed on success
artifacts/<window>/<platform>/run-<id>/<name>          the kept copy, indexed in the `artifacts` table
```

`finance_file.xlsx` — the team's invoicing-template shape, used as-is. TikTok 6 tabs, Shopee 12, Lazada 12 (per-brand **and** per-VAT-rate). Values are static numbers, no formulas. Two fields are deliberately left for human fill: Lazada's `Summary!F16` "prior invoiced", and the `Xuất HD khách` / `Note` columns.

`run_log.txt` — per-file row counts, dedupe counts, store check, master drift, classification counts, every template check with its diff/tolerance/verdict, per-store and grand ties when `--refs` is supplied, and a warning-count footer. This is the audit trail; it carries a wall-clock timestamp, so it can never be compared byte-for-byte between runs.

`run_metrics.json` — per-stage wall time, row counts and peak RSS, tagged `io` / `compute` / `serialize`. The engine-port trigger reads it via `tools/metrics_report.py` ([D27](06-DECISIONS.md#d27)). Through the service the same numbers land as columns on the run row.

`exceptions.xlsx` (Unmatched Orders · Unknown SKUs · Tie-out Breaches · Zero Revenue) is defined in `src/export.py` and **has been written since M2**, whenever any exception sheet has rows. Before that it was computed and dropped every run.

Monthly: `tools/build_master_summary.py --month YYYY-MM --out <path>` aggregates all windows into a cross-platform master (Summary / By brand / By storefront / Brand mapping / per-platform tabs) and asserts its column totals against each source finance file, refusing to ship on a mismatch.

## PII — what must never leave

Raw exports contain **customer personal data**. `config/settings.yaml` records the specific columns: `Recipient`, `Phone #`, `Detail Address`, `Buyer Username` (TikTok); `Tên Người nhận`, `Số điện thoại`, `Địa chỉ nhận hàng`, `Người Mua` (Shopee).

Controls:

- `drop_unmapped_columns: true` strips every column not in the mapping **at read time**, so PII never enters a DataFrame that flows downstream. The team's own Power Query does the same.
- `input/`, `output/`, and (since M4) `artifacts/` and `.scratch/` are gitignored; no client data is committed.
- Parity goldens live **outside the repo**; only one-way digests are committed.
- Fingerprints hash store names; committed manifests contain integer counts and digests only, enforced by `tests/goldens/test_manifest_integrity.py`.
- When probing real files, report schemas, column names and counts — **never cell values**.

Note the asymmetry worth planning around: PII is stripped at read, but the **raw files themselves** sit wherever they were staged. Any future upload path must strip at the boundary and keep raw exports on a short retention. M4 deliberately did **not** build that upload path — an upload boundary is the PII-stripping boundary and deserves its own milestone rather than being bolted onto a job queue ([defect 2.3](08-KNOWN-DEFECTS.md#23-there-is-no-upload-or-staging-endpoint--open-m5)).

### Since M6: the upload boundary, and the first real retention mechanism

That asymmetry is now closed for anything arriving through the browser, which since M6 is everything a *user* supplies.

**Neither copy of a raw export outlives its request.** `POST /uploads` reads the bytes, strips to the columns the contract names — using the pipeline's own column map, so there is no second PII list to go stale — writes the **stripped** copy to the `recon-uploads` bucket, and deletes both temporary files in a `finally`. There is no quarantine directory holding unstripped originals any more, and no staging step: the bucket **is** the window, and the worker materialises each window into its own scratch directory at run time (`service/materialize.py`).

**The retention promise finally has a mechanism.** `minio-init` applies a bucket **lifecycle expiry rule** to `recon-uploads` (`UPLOAD_RETENTION_DAYS`, default 30). That is deliberately a bucket rule and not an application deletion loop: a scheduled job can silently stop running, and this document has promised short retention since M4 with nothing behind it. `recon-artifacts` is versioned and never expired, because it holds the deliverable the team invoiced from — which is why they are two buckets and not two prefixes ([D43](06-DECISIONS.md#d43)).

**What is still exposed, and named:** the stripped upload retains store names and order IDs (it must — the pipeline reads them), the run log in Postgres names stores and counts rows ([defect 2.6](08-KNOWN-DEFECTS.md)), and `UPLOAD_RETENTION_DAYS=30` is a number engineering picked rather than a policy anyone approved ([open question 11](11-OPEN-QUESTIONS.md)).

**Filenames are renamed on the way in**, to a uniform scheme, because store identity is derived from them and a consistent name is one fewer thing to parse defensively. The rename is proved a fixed point of the pipeline's own parser and gated by the golden comparison on all three platforms ([D44](06-DECISIONS.md#d44)).

**What M4 added to this picture.** The run log now also lives in Postgres (`run_log_lines`), carrying store names and row counts and **no cell values** — the pipeline never logs them, so this is the exposure `run_log.txt` already had on disk, now in a database with no authentication in front of it ([2.1](08-KNOWN-DEFECTS.md#21-the-api-is-unauthenticated--open-m5), [2.6](08-KNOWN-DEFECTS.md#26-the-run-log-in-postgres-contains-store-names--accepted)). `jobs.refs` holds the team's reference totals — store names and revenue figures, no customer data. The artifact table stores paths and a transfer hash, never contents.

## Determinism

Same inputs produce the same outputs: files are read in sorted order, group-bys are sorted, there is no RNG, and inputs are never mutated in place (defensive `.copy()` throughout). Golden regeneration has been verified bit-stable.

Two caveats: `run_log.txt` contains a timestamp, and `.xlsx` **bytes** differ between identical runs because openpyxl stamps `docProps/core.xml` — compare content, never file hashes.
