# 01 — Orientation

Start here. This explains the business problem, the vocabulary, and the mental model. Nothing here is code-specific.

## The business problem

An agency operates official brand storefronts on three Vietnamese marketplaces — **TikTok Shop, Shopee, Lazada** — on behalf of client brands (Unilever, Abbott, Kao, Masan, Reckitt, Sanofi and others). Each month, every platform pays out settled revenue *net of its own fees, subsidies, vouchers, returns and adjustments*.

Finance must rebuild, from the platforms' raw exports, the **true invoiceable revenue per client brand**: which orders count, at what net price, net of which discounts, at which VAT rate. Three things depend on that number:

1. Clients are billed correctly (the "KA" — key account — invoice files).
2. Revenue is booked in Dynamics 365 in the right period.
3. VAT is calculated on the right base at the right rate per SKU.

**No platform hands this over directly.** TikTok's settlement file carries no per-SKU invoice lines. Shopee's income export mixes orders, returns and zero-settlement rows. Lazada provides only a transaction ledger of fee lines with no order rows at all. Reconciliation is the work of turning those exports into invoice-grade numbers, once per settlement window.

## What this system replaces

A manual Excel/Power Query chain:

- A ~36 GB workbook chain; one refresh took ~15 minutes and had to be repeated **per window, per platform**.
- Manual copy-paste across 3–4 linked files per window to produce the final invoicing file.
- Manual entry of results into D365.
- The rules — which columns, which formulas, which tolerances, which rows are excluded — lived partly in Power Query M code, partly in worksheet formulas, and partly in individual team members' heads.

There are **12–14 settlement windows per month** across the three platforms.

## Success criterion

For one full cycle, pipeline output must match the team's manually produced finance file **to the cent**, within the team's own tolerance rules. No process change until a parallel run passes. Finance continues booking from the manual output until then.

## Vocabulary

| Term | Meaning |
|---|---|
| **Window** / period | One settlement payout period for one platform, e.g. `2026-05_w1`. The atomic unit of work. Never mix two windows' exports. |
| **Store** / storefront | One seller account on one platform, e.g. "Unilever Homecare". Identified by the **download filename**, not by a column in the data. |
| **Brand** | The client the storefront sells for. Mapped from store via `store_to_brand` in the contract, per platform, matched through `ingest.norm_store`. Several storefronts can map to one client brand. It was `config/brand_map.csv` — a second mapping the pipeline never read — until 2026-08-21 ([D12](14-PRODUCTION-READINESS.md), [D65](06-DECISIONS.md#d65)). |
| **KA** | "Key account" — the client. "For KA" files are the invoicing workbooks handed to finance. |
| **Xuất HĐ** | Vietnamese for "issue invoice". The team's intermediary calculation sheets, and the source of the ported formula chain. |
| **PV sum** | A control tab in the team's workbook; also the name of one of their tolerance checks. |
| **Take out** | TikTok rows excluded from the invoice (returns, paybacks, partial returns). |
| **0 đồng** | Shopee orders fully covered by promotions, whose residual settlement is only fees — invoiced at zero. |
| **Price KA** | Lazada's invoiced unit price: `round((credits + matched promo) / units / VAT)`, whole VND. |
| **Yellow columns** | The team's name for the calculated columns in their intermediary sheet — the core money math. |
| **Fee bucket** | Lazada fee-name → accounting category (e.g. `1.Doanh Thu` = revenue, `6.CP co Invoice` = cost with invoice). 118 mappings. |
| **Settlement bounds** | A declared per-window date boundary, used only to drop rows proven to be a mis-pull artifact. |
| **Baseline** | The committed, row-verified tree. A refactor must reproduce its output exactly; a deliberate change re-baselines with the delta stated first. (Earlier docs called this the *oracle*, from a since-cancelled engine rewrite — see [D25](06-DECISIONS.md#d25).) |
| **Golden** | A stored, canonical record of one window's verified output, used as a comparison target. |

## Roles (not individuals)

| Role | Does |
|---|---|
| **Extractor** | Downloads seller-centre exports per window, per store. |
| **Stager** | Places exports into the window folder layout. Manual today; the biggest fragility. |
| **Pipeline operator** | Runs the pipeline per platform/window, reads the run log. |
| **Reconciler** | Owns the legacy Excel chain; runs it in parallel and answers rule questions. |
| **Finance** | Reviews, invoices clients, books to D365. |
| **Master owner** | Maintains `Lib & VAT rate.xlsb` (fee buckets, per-SKU VAT). Additive-only. |

## The mental model

Three ideas explain most of the design:

**1. Evidence over invention.** Every rule was extracted from the team's own artifacts — Power Query M code lifted out of their workbooks' embedded DataMashup, worksheet formulas read cell by cell — then verified row-by-row against their outputs. Where evidence was missing, the question went to a human rather than being guessed. The system's value is not that it computes revenue; it's that it computes *the same revenue the team already computes*, provably.

**2. Config is the contract.** Everything that drifts month to month — column spellings, store rosters, aliases, tolerances, VAT rates — lives in `config/settings.yaml` with its evidence in the comments. Code changes are rare; config changes are monthly.

**3. Flag, never fudge.** A variance is a finding, not an error to force away. Unknown values go to an exception report; they are never dropped and never guessed. Structural problems (a missing required column, an unrecognised store) **hard-stop** the run rather than producing a plausible wrong number.

## Reading order

| If you want to… | Read |
|---|---|
| Understand the code layout | [02-ARCHITECTURE](02-ARCHITECTURE.md) |
| Follow what happens to a row | [03-PIPELINE](03-PIPELINE.md), [04-DATA-FLOW](04-DATA-FLOW.md) |
| Understand the money math | [05-DOMAIN-RULES](05-DOMAIN-RULES.md) |
| Know why something is the way it is | [06-DECISIONS](06-DECISIONS.md) |
| Judge whether to trust the numbers | [07-VERIFICATION](07-VERIFICATION.md), [08-KNOWN-DEFECTS](08-KNOWN-DEFECTS.md) |
| Actually run it | [09-OPERATIONS](09-OPERATIONS.md) |
| Know what's next | [10-ROADMAP](10-ROADMAP.md), [11-OPEN-QUESTIONS](11-OPEN-QUESTIONS.md) |
| Know what changed and when | [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md) |
