# Project Overview — E-commerce Marketplace Reconciliation Pipeline

**Audience:** independent evaluator. This dossier is deliberately honest about
what is not done and what is uncertain — see `CHALLENGES_AND_FINDINGS.md` and
`OPEN_QUESTIONS_FOR_EVALUATOR.md`. It is an evaluation pack, not a pitch.

## What this reconciliation is, and why it exists

ADA operates official brand storefronts on three Vietnamese marketplaces —
TikTok Shop, Shopee, and Lazada — on behalf of client brands (Unilever, Abbott,
Kao, Masan, Reckitt, and others). Every month, each platform pays out settled
revenue net of its own fees, subsidies, vouchers, returns, and adjustments.

Finance must rebuild, from the platforms' raw exports, the **true invoiceable
revenue per client brand**: which orders count, at what net price, net of which
discounts, at which VAT rate — so that:

1. clients are billed correctly (the "KA" — key account — invoice files),
2. revenue is booked in D365 in the right period, and
3. VAT is calculated on the right base at the right rate per SKU.

None of the three platforms hands this over directly. TikTok's settlement file
does not carry per-SKU invoice lines; Shopee's income export mixes orders,
returns, and zero-settlement rows; Lazada provides only a transaction ledger of
fee lines with no order rows at all. The reconciliation is the process of
turning those exports into invoice-grade numbers, per settlement window (there
are 12–14 windows per month across the platforms).

## Source documents (in the shared cloud folder, next to the data)

Two ADA documents predate the build and are the requirements baseline this
project should be evaluated against — read them FIRST:

- **"Ecommerce Invoicing flow 30_06_2026.docx"** — the as-is process
  walkthrough (the manual TikTok/Shopee/Lazada flow, step by step, incl. the
  Total-file Power Query mechanics, the yellow-column calculation file, the
  brand splits, and the three tie-out checks). Phase 1 of this build is a
  faithful automation of THAT process; the pipeline's stages map 1:1 onto its
  steps.
- **"Ecommerce_Invoicing_Architecture_and_Roadmap.docx"** — the as-is/to-be
  architecture note (root causes, target architecture, AI use-case backlog,
  platform evaluation). Useful context for judging whether Phase 1's scope
  cut was the right one and what Phases 2-3 were envisioned to be.

Evaluator note: the flow doc says "26 TikTok stores". The May data contained
17-18; July contained 25 (7 onboarded mid-period). The pipeline's store
roster is config, updated monthly with evidence — the "26" is the team's
round number, not what the data showed in any single window.

## The old manual process (what this replaces)

- A ~36 GB Excel/Power Query workbook chain; a single refresh took ~15 minutes
  and had to be repeated per window, per platform.
- Manual copy-paste across 3–4 linked files per window to produce the final
  invoicing file (the "For KA" / "KA used" workbooks).
- Manual entry of results into D365.
- The rules — which columns, which formulas, which tolerances, which rows are
  excluded — lived partly in Power Query M code, partly in worksheet formulas,
  and partly in individual team members' heads (tribal knowledge). Several of
  the workbook's own consistency checks are broken (dead `#REF!` references,
  verdict cells reading blank cells) and are silently ignored in practice; the
  evidence is in `CHALLENGES_AND_FINDINGS.md`.

## Phased approach — what is and is not built

| Phase | Scope | Status |
|---|---|---|
| **Phase 1** | Post-download automation: given the raw exports the team already downloads from each seller center, produce the finance/invoicing files automatically, with the team's own checks computed and every rule explicit in code/config. | **Built and verified** (this repo). Three real months processed: May (row-level verification), June (external tie against the team's own outputs), July (independent run on unseen data, incl. producing findings the team confirmed). |
| **Phase 2** | Automated data extraction via the platforms' official seller APIs (no manual downloads). | **Not started.** Feasibility assessed only: all three platforms expose suitable finance/order APIs; requires seller-account owners to register developer apps. |
| **Phase 3** | Automated D365 posting of the reconciled results. | **Not started.** Not designed beyond the phase label. |

Also built beyond the original Phase-1 spec, in response to team feedback:
- Finance files in the team's own invoicing-template shape (PV sum / Summary /
  brand tabs / control-block verdicts), replacing an earlier plain format.
- A monthly cross-platform master summary workbook (by window, by brand, by
  storefront) with a storefront→client-brand mapping table for team review.

Planned next (agreed in principle, not yet built at dossier time): a processed
Parquet data layer so each month's raw exports are parsed once, and a
self-service reporting portal for finance on top of it.

## Ground rules the project was built under

- **Evidence-first**: every calculation rule was extracted from the team's own
  artifacts (Power Query M code, worksheet formulas, pivot structures) and then
  verified row-level against the team's own outputs — never assumed or
  invented. Where evidence was missing, the question went to the team
  (documented as TODO-HUMAN items) instead of being guessed.
- **A variance is a finding, not an error to force away.** The pipeline flags
  and reports; it does not bend numbers to tie.
- **No client data in the repo.** Code and config only. Raw exports, staged
  inputs, and outputs live outside version control (see `HOW_TO_RUN.md`).
- **`PLACEHOLDER_FORMULAS`** is a deliberate governance flag stating the
  numbers are not yet blessed for production booking. It is still `True`:
  verification criteria were met in July, but the formal flip (and the
  accompanying doc updates) awaits the owner's go-ahead.

## People context

- **Nu** — extraction + D365 booking.
- **Hoang** — owns the reconciliation files; answered the rule-confirmation
  questions (store aliases, invoice splits, dedup decisions).
- **Huong / Dashaini** — finance stakeholders.
- The pipeline was built by Abdul Ashraff with an AI coding assistant; every
  ported rule cites its evidence (file, sheet, cell) in code comments.
