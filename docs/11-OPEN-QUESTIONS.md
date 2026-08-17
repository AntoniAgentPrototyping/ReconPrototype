# 11 — Open Questions

Things that need a **human decision**, not more engineering. Grouped by who can answer. Nothing here is blocked on code.

## For finance / business owners

| # | Question | Why it matters | Consequence of leaving it open |
|---|---|---|---|
| 1 | **Is there any external ground truth** the numbers can tie to — platform statements of account, bank settlement, client acceptances? | Everything currently ties to the team's own files, and those files were found to contain broken checks. Faithful reproduction inherits any systematic error they make. | Arguably the largest unaddressed assurance gap. See [07-VERIFICATION](07-VERIFICATION.md#what-verification-does-not-cover--honest-limits). |
| 2 | **What is the sign-off protocol** for booking from generated files instead of the manual chain? | It gates the whole project's value. (The `PLACEHOLDER_FORMULAS` flag was removed in M1 — [D10](06-DECISIONS.md#d10) — so there is no longer even a symbolic switch to flip; the decision is entirely procedural.) | The pipeline stays a parallel-run tool indefinitely. |
| 3 | **Is there a rollback story** if a generated file is later found wrong after invoicing? | Determines whether automated posting is safe to design at all. | Blocks the D365 workstream. |
| 4 | **Where do reimbursement / adjustment lines book?** These fall through every classification branch, appear in *neither* of the team's pivots, and are material (tens of millions VND per window at one store). | They are currently routed to exceptions and excluded from the invoice — matching the team's behaviour, but the destination is unknown. | Rows remain unexplained every month. |
| 5 | **Confirm the line-vs-pivot semantics.** The workbook deliberately reproduces exact line amounts *and* the rounded-price invoice view. | If the assumption is wrong, client bills are wrong by small amounts at scale. | A silent, systematic billing discrepancy. |
| 6 | **Who owns the brand mapping?** Several storefront→brand rows carry a "needs-confirmation" flag. | Wrong mapping puts revenue on the wrong client's invoice. | Those rows stay provisional. |
| 7 | **Who reviews the run log each month?** | A pipeline that flags honestly is only as good as the person reading the flags. Since M2 the flags can fire — including a `RECONCILING` line for ~21% of TikTok settlement that a reviewer should watch for *changes* in. | Flags accumulate unread. |

## For the platform / data owners

| # | Question | Why it matters |
|---|---|---|
| 8 | **Who owns `Lib & VAT rate.xlsb`** as a runtime dependency, and where would a server obtain it? | It is read live on every run. An unowned dependency in the critical path. |
| 9 | **What happens when a SKU with non-default VAT trades before the master covers it?** **Sharper than it was:** the master's 660 SKUs match **0** of the SKUs in every sampled window on all three platforms, so this is not a rare future event — the per-SKU override has never fired in production and every line invoices at the 1.08 default ([defect 1.4](08-KNOWN-DEFECTS.md#14-unmapped-sku-silently-receives-the-default-vat-factor--fixed-m25-2026-08-13)). The fall-through is now counted and logged every run; the *policy* is still unanswered. | Determines whether the pipeline should hard-stop, flag, or continue — and, before that, whether the master is even meant to cover these stores. |
| 10 | **Can seller-account owners register developer apps** on each platform? | The long pole for automated API extraction — an access and approvals task, not engineering. |
| 11 | **What is the retention and handling policy** for raw exports containing customer PII, especially if they are ever uploaded to cloud storage? **Sharper since M6:** uploads now go to a bucket with a lifecycle expiry rule, so there is a *mechanism* for the first time — but `UPLOAD_RETENTION_DAYS=30` is a number engineering picked, not a policy anyone approved. | The pipeline strips PII at read and at the upload boundary, and the stripped copy is what is stored — but somebody still has to own the number. |
| 16 | **Is the Shopee settlement-date format day-first or not?** `dayfirst.shopee` is `false` in `settings.yaml` carrying a *"TODO verify when Shopee is mapped"* comment that has never been resolved. | Found while building the demo generator (M6), which had to abstain and emit ISO dates rather than assert an unverified answer. If the real export is `dd/mm/yyyy`, every Shopee date in the first twelve days of a month currently parses as a **month**, which would move `apply_settlement_bounds` and any date-keyed grouping. The committed Shopee goldens tie, so either the real dates are unambiguous or no committed window depends on the difference — neither of which is the same as knowing. |

## For engineering / architecture sign-off

| # | Question | Why it matters |
|---|---|---|
| 12 | **What are the month-end quarantine semantics?** Hard-stop-on-unknown is correct at three platforms and becomes a close blocker at five. | Neither the current design nor the proposed one specifies partial-run behaviour. |
| 13 | ~~**Who owns configuration** once a UI exists — finance or engineering — and who approves a rate change?~~ | **ANSWERED 2026-08-17 (M6).** Anyone but a viewer proposes; only an admin approves, rejects or applies; self-approval is permitted and **recorded** in a generated column. `RECON_CONFIG_APPROVAL` is deleted — with an answer, a configurable policy is only a way to weaken it. Closes [defect 2.7](08-KNOWN-DEFECTS.md). **The caveat is part of the answer:** this is recorded evidence, not separation of duties, and a single-admin deployment still has one person on both ends. See [D47](06-DECISIONS.md#d47). |
| 14 | **What is the GL mapping for D365** — which accounts and dimensions, and who specifies it? | The pipeline emits an invoicing worksheet, not a journal. This mapping does not exist anywhere yet. |
| 15 | **Should money move to integer VND or `Decimal`?** VND has no minor unit, so float64 is carrying values that are conceptually integers. | **Unblocked 2026-08-12.** This was deferred because it would have broken cross-engine parity by construction; with the port descheduled ([D25](06-DECISIONS.md#d25)) that objection is gone. It is now an ordinary Class B change: state the expected delta, then re-baseline the goldens. Worth doing *after* M2 so it doesn't tangle with the control rebuild. |

## Known-unknowns worth probing

Not questions for a person — places where the system's confidence is thinner than it looks, and where an auditor should push:

- **Would a rare fourth Shopee case classify correctly?** The three status rules were derived from data patterns and verified against one hand-curated list. A partial refund *with* positive settlement is the obvious construction to attempt from raw data.
- **Is six data points enough** to have derived the team's period convention? What happens in a month with an unusual settlement lag?
- **Audit the store-name regexes against hostile-but-plausible names.** They have needed extension every month, and the store-name capture is only as safe as its suffix alternatives — a store whose name ends in a number-like token could be truncated.
- **Are there TikTok/Shopee analogues of Lazada's order-less revenue** (platform compensations inside income exports) that would currently land in a classification bucket silently?
- **Detection of *new* cross-window overlaps is not automated.** The settlement-bounds mechanism exists, but the material catch was found by ad-hoc analysis. If a future export overlaps on a different day, nothing in the run path will notice.
- **Regression coverage is one store per platform.** The golden gate now covers the settlement-bounds logic and the template exporter (both terminate in workbook cells), which closes the older "nothing tests the newest code" gap — but only for the three windows that have goldens. A code path exercised solely by a store outside those three is still unguarded.
