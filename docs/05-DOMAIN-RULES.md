# 05 — Domain Rules

The money logic. Every rule here was ported from the team's own artifacts and row-verified against their outputs; see [07-VERIFICATION](07-VERIFICATION.md) for the evidence and [06-DECISIONS](06-DECISIONS.md#d1) for why nothing was invented.

Money is **VND** — no minor unit, but the math is not integer (a VAT back-out is irreducibly fractional).

## VAT model (all platforms)

**One default factor plus per-SKU exceptions.**

- `vat_factors.default: 1.08` — a temporary tax concession. Reverting to 10% is **that single config line and nothing else**.
- Per-SKU overrides come from the team-owned master's `VAT` sheet (660 SKUs, 4 at 1.05).
- TikTok/Shopee template cells for 1.05/1.10 are **vestigial and verified dead**: one multiplies an empty cell, the other double-VATs inside a `#REF!`-broken block whose failing verdict the team ignores.

A SKU absent from the master receives the default. That is the team's rule and it is row-verified, but it *used* to happen silently; since 2026-08-13 the fall-through is counted and logged every run ([defect 1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13)).

**Measured, and it matters when reading the rule above:** master coverage is **store-dependent**. One Lazada store (KAO) matches 41–50% of its ledger rows, so the lookup demonstrably works — but the largest stores in this data (TikTok's two, Shopee's two, Lazada's Unilever 2) match **zero** of their 295 / 650 / 92 distinct SKUs, and every one of their lines invoices at the 1.08 default by fall-through. Every matched SKU seen so far is itself 1.08, so no override has yet changed a number: **the 1.05/1.10 path is live-unexercised**. A SKU that genuinely carries a different rate at an uncovered store would be invoiced at 1.08. The fall-through is counted and logged every run ([defect 1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13)).

## TikTok

**Classification** (ported from the M code in the team's recon workbooks). Income settlement lines are first collapsed per (store, order, type, order-created time), then:

| Status | Rule |
|---|---|
| `Good` | subtotal + refund-before ≠ 0, settlement ≥ 0, Type = Order |
| `Total Return` | subtotal + refund-before = 0 |
| `Partial Return` | both ≠ 0 **and** refund ≠ 0 |
| `Payback_Order` | not Good **and** revenue < 0 |

`Final_Status`: **OK = Good only**; **take out = the three non-Good statuses**. Reimbursement/adjustment lines (logistics/platform reimbursement etc.) fall through every branch — the team's pivots contain them in *neither* bucket, so the pipeline carries them as `unclassified` and routes them to exceptions. They are material (tens of millions VND per window at one store) and the team books them outside the revenue invoice.

**Calculation** (ported cell-by-cell from the intermediary "Xuất HĐ", header row 3). SKU explode joins income → orders on Order ID with **Seller SKU** identity, then:

```
gross            = unit original price × qty
net              = gross − SKU Seller Discount
unit_price_preVAT= (net / qty) / VAT
amount_preVAT    = unit_price_preVAT × qty
amount_withVAT   = amount_preVAT × VAT
order check      = Σ amount_withVAT per order − income subtotal
```

The income file is the **check**, not the revenue source — revenue is rebuilt from the order side.

**Invoice splits:** KAO / Merries / Others.

## Shopee

**Classification.** The team's "return + 0đồng" sheet was a hand-curated XLOOKUP list. The membership rules were derived and then verified exactly:

| Status | Rule | Verification |
|---|---|---|
| `Return` | order refund sum ≠ 0 | 11/11 and 178/178 exact |
| `0 đồng` | settlement ≤ 0 **and** refund = 0 | 40/40 exact |
| `ok` | otherwise | — |

`0 đồng` orders are fully promo-covered; their residual settlement is only fees.

**Calculation** (from their "Xuất HĐ"):

```
gross          = Giá gốc × Số lượng
net            = gross − seller subsidy
total discount = seller voucher + shipping support + coin cashback + co-funded voucher
allocation     Z = (T / X) · Y            proportional across SKU lines
unit_preVAT    = ((T + Z) / qty) / VAT
```

**Return tab rule.** Recompute the returned order's with-VAT total and add the (negative) refund; `|sum| < 10 VND` → full return, skip the invoice; otherwise "Return 1 phần" → **must invoice**. Ten VND is the real tolerance — the tightest in the system.

**Invoice splits:** Curel / KAO / Merries / Kate / Others, plus Xmen and Kao batch files.

## Lazada

Structurally different: a **transaction ledger**, one row per (order item × fee event), no order files.

**Classification is fee-typing.** Fee name → bucket/status via the team's `Lib` master — 118 mappings, e.g. `Item Price Credit` → `1.Doanh Thu`, `Commission` → `6.CP co Invoice`. Refunds carry no credit notes; reversal lines net into final sales through this mapping.

**Revenue is gross-credited** (gifts credit full price) and promo charges are separate ledger lines. The invoiced unit price:

```
Price KA = round( (credits + per-(order, SKU, product) promo charges) / units / VAT )
```

Whole-VND rounding, **half away from zero** (Excel `ROUND`, not banker's). Promo pairing **must include product name**: the same SKU can appear as both a normal unit and a gift variant within one order.

Weekly (`Transaction Overview`) and Daily (`Income Overview`) schemas are both permanent fixtures and are normalized then unioned.

**Order-less revenue** — platform compensations (e.g. lost/damaged inventory) map via the team's own Lib to a revenue bucket but have no order or SKU. These are surfaced as **named reconciling rows** in the control blocks and kept in the sale-report figure; an earlier plain exporter had been silently zeroing them.

## Invoice buckets

Per-brand tabs are chosen by **case-insensitive substring match on the store name** against a per-platform bucket list, defaulting to "Others". This is simple and currently correct, but a future storefront whose name merely *contains* an existing brand token would be swept into that brand's invoice without warning.

**There is no config file for this.** `config/brand_rules.yaml` carried a separate, much smaller notion — `invoice_grouping: separate | combined` per brand — and was **deleted on 2026-08-18** along with its only reader, `classify.classify()`, which had no callers of its own. The invoice bucket lists were always hardcoded in `finance_template.py:90-92`, so nothing about the invoice split changed; what went was a config file that looked like it governed the split and did not. See [D11](14-PRODUCTION-READINESS.md) and [D19](06-DECISIONS.md#d19) for the deletion standard (goldens unchanged is the proof).

`config/settings.yaml`'s `store_to_brand` still advertises itself as the home for this and is empty, so every store currently falls back to its own name with a loud ingest warning — [D12](14-PRODUCTION-READINESS.md).

## Tie-out checks and tolerances

Tolerances are the **team's own**, read from their formulas — not the build spec's assumption (the spec's "~10,000 VND" matches none of TikTok's actual checks).

| Platform | Check | Tolerance (VND) |
|---|---|---|
| TikTok | `PV sum` — pre-VAT per store vs per VAT bucket | 12,000 |
| TikTok | `Xuat HD bt` — with-VAT lines vs VAT-bucket recombination | 2,000 |
| TikTok | `PV xuat HD` — pre-VAT lines vs SKU pivot | 1,000 |
| Shopee | `PV sum`, `Xuat HD bt` | 2,000 each |
| Shopee | `return` full/partial split | **10** |
| Lazada | sale-report cross-refs (1.05/1.08 · 1.10) | 1,000 · 2,000 |

These are the *team's* tolerances, reproduced in the workbook's control blocks. The pipeline's own tie-outs are separate and were rebuilt in M2 so they cross a file boundary and can fail — read [08-KNOWN-DEFECTS 1.1](08-KNOWN-DEFECTS.md#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13) for what that means before citing a green result, and note that **all three platforms now have a measured money crossing** — Shopee's, closed 2026-08-13, is the team's own June `Net revenue` formula rearranged into an order-file-vs-income-file statement:

```
SUM(amount_with_vat − discount_allocated)  ==  gross_revenue + shopee_product_subsidy
```

Refund orders are held out and named: the order export keeps the full ordered quantity while income is reduced for returned units, so they cannot tie by construction.

## Rounding conventions

Two different conventions coexist deliberately, and mixing them changes verdicts:

- **Excel `ROUND` — half away from zero.** Used for Lazada's whole-VND `Price KA`.
- **Half to even (banker's).** What Python's `round()` and pandas `.round()` do. Used for reported/compared numbers and the invoice pivot rounding model.

The dangerous property is that the two disagree only on exact ties — so a wrong choice moves money in a small fraction of rows and ties everywhere else, which is exactly the failure that survives a spot check. `tests/test_rounding_modes.py` pins both conventions against an exact-tie table so a library default changing under a version bump fails loudly instead of silently moving money.

*Measured on polars 1.43.2 while the engine port was still planned, kept because it is a non-obvious fact worth not re-deriving:* `Expr.round(decimals=0, mode='half_to_even')` is polars' default, so it already agrees with pandas — the assumed migration risk here did not exist — and `mode="half_away_from_zero"` reproduces Excel's `ROUND` bit-identically across 123,976 division-shaped values.

## Two semantics, on purpose

The finance workbook carries **line tabs** (exact amounts) and **pivot tabs** (the rounded-price invoice view) side by side. The drift between them is not an error — it is precisely what the team's tolerance checks are measuring. Any rewrite must preserve both, and the distinction should be reconfirmed with finance, since it determines client bills by small amounts at scale.
