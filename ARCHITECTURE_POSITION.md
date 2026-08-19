# Architecture Position — Phase 2/3 Target State

**Subject:** engineering response to the proposed four-layer target architecture
(platform APIs → integration & data layer → AI agent orchestration → experience layer).

**Status:** for discussion. Written against the roadmap note
(*Ecommerce_Invoicing_Architecture_and_Roadmap.docx*) and the current verified
Phase 1 pipeline in this repository.

---

## 1. Executive summary

**The direction is right, and two of the four layers should be funded as proposed.**
Pulling from the platforms' official APIs and building a unified transaction store
are the highest-value items on the roadmap. They remove the single largest source of
error in the current process — the manual download — and they make everything
downstream cheaper.

**One prerequisite has not been costed, and it is the reason to sequence carefully.**
The pipeline's automated tie-out checks do not currently detect lost or altered
revenue. This was found during preparation of this document and is demonstrated
reproducibly in Appendix A: a run in which **100% of revenue is deleted still reports
all three checks as PASS**. The checks are faithful ports of the team's Excel
formulas, but those formulas earned their value by policing *manual* copy-paste steps.
Ported into code, where those steps no longer exist, most of them reduce to
comparing a number with itself.

This matters far more under the proposed architecture than it does today. **The real
control in the current process is a person comparing output against a spreadsheet.**
Every layer in the proposal removes a human touchpoint, and the final layer posts to
the general ledger automatically. If the checks that are supposed to replace that
person cannot fail, automation does not carry today's risk forward — it multiplies it.

**On the AI orchestration layer, the recommendation is about placement, not merit.**
Three of the four proposed agents sit directly on the money path, where the work is
already provably deterministic — a four-predicate rule ladder, roughly twenty-five
branchless arithmetic expressions, and tolerance comparisons as tight as 10 VND.
Non-determinism there is not a tradeoff; it is disqualifying, because a revenue
figure that cannot be reproduced cannot be defended to an auditor or a tax authority.

But there is real, funded AI work in this system — it simply sits at the edges rather
than in the calculation. The pipeline's own drift log records **13 distinct
export-format changes across two months**: renamed headers, localized column names,
new filename conventions, novel fee types. Every one required a human to notice,
diagnose, and hand-edit config. That is open-vocabulary work, it is the genuine
recurring cost of running this system, and it is where an assistive model pays for
itself. The proposed control boundary is:

> **AI recommends, deterministic code executes, human approves.**

Recommended sequence: **fix the controls → APIs → transaction store → exception queue
→ AI assists → D365 posting.** Detail in §8.

---

## 2. What is already established

Phase 1 is built and verified, and that is the basis for the confidence expressed
here as well as the caution.

- **~288,000 rows reproduced exactly**, row-by-row, against the team's own computed
  outputs across all 12 May settlement windows and all three platforms.
- **Three real months processed**: May (row-level verification), June (external tie
  against the team's outputs), July (independent run on previously unseen data).
- **Every rule was reverse-engineered from the team's own artifacts** — Power Query M
  code, worksheet formulas, pivot evidence — and never invented. Where evidence was
  missing, the question went to the team rather than being guessed.
- The run surfaced **defects in the source process**, including a missed income
  download and a July double-pull carrying a **5.97 billion VND** double-count risk.

Two caveats stated plainly: `PLACEHOLDER_FORMULAS` is still `True`, meaning the
numbers are not yet blessed for production booking; and the row-level verification
covers one month, with several formula branches (non-1.08 VAT in particular) not yet
exercised on real trades.

---

## 3. Endorsed without reservation

### Platform APIs (Phase 2)

This is the strongest item in the proposal. The manual download is not merely slow —
it is the origin of an entire class of error that the pipeline currently has to detect
after the fact. The July double-pull, where a weekly export silently contained an
extra week's data, exists *because* a human assembles the inputs. Direct API pulls
with explicit settlement-window parameters eliminate the category.

One dependency to flag early: this requires seller-account owners to register
developer applications on each platform. That is an access and approvals task, not an
engineering one, and it should start now because it is likely the long pole.

### The unified transaction store

Equally endorsed, and it quietly solves four problems at once: cross-period stitching
without re-downloading the prior month; a real source for Power BI instead of
workbook scraping; somewhere to keep raw payloads so a figure can be traced back to
what the platform actually sent; and the natural home for the run provenance
described in §5.

One design requirement worth fixing now: the store should be **append-only, with
every row carrying the identifier of the run that produced it**. Reconciliation data
is restated, not updated. Overwriting destroys the audit trail that is the main
reason to build it.

---

## 4. The prerequisite: the current controls do not work

### What was found

`run_checks_tiktok` in `src/tieout.py:84-93` implements the team's three named
tolerance checks. Reduced to closed form:

| Check | Compares | Reduces to |
|---|---|---|
| PV sum | pre-VAT summed per store vs. summed per VAT bucket | `sum(x)` vs. `sum(x)` |
| PV xuat HD | pre-VAT line total vs. SKU-pivot total | `sum(x)` vs. `sum(x)` |
| Xuat HD bt | with-VAT lines vs. VAT-bucket recombination | `sum(p·f)` vs. `sum(p·f)` |

Checks 1 and 3 are the same quantity grouped two different ways — they are equal for
any input whatsoever. Check 3 (`Xuat HD bt`) is slightly stronger: it verifies that
`amount_with_vat` equals `amount_pre_vat × vat_factor`, and it *will* fail if that
relation is broken. But `src/calculate.py:142` computes `amount_with_vat` as exactly
that product, so within a single run it cannot fail either.

The generic path has the same shape for a different reason. `recon.py:70` exports the
finance file *before* running the tie-out, with the comment
`"finance file first, so Check 3 ties against it"` — the check compares the output
against totals derived from that same output.

**Appendix A** is a reproducible test. Dropping 30%, 50%, and 90% of SKU rows,
deleting an entire store, halving every amount, and zeroing all revenue outright each
produce **ALL PASS**.

### Why this happened, and why it is not a coding error

In the manual process these checks are genuinely load-bearing. Between each pivot and
each worksheet there is a human copy-paste, and the check exists to detect a row
dropped or edited during that step — the docstring in `tieout.py` says exactly this.
The formulas were ported faithfully. What could not be ported is the manual step they
were watching. **The control's value lived in the process, not in the arithmetic.**

This is worth stating precisely because it generalizes: several of the team's own
workbook checks are also broken — a control block summing a `#REF!`, a verdict cell
reading a blank so it always reports "OK". Neither process has had a working
end-to-end check for some time. Everything held because a person was comparing
numbers.

### What automation changes

| Behaviour today | Under the proposed architecture |
|---|---|
| Tie-out reports PASS regardless of input (`tieout.py:84-93`) | The only signal a scheduler receives |
| `main()` returns exit code 0 even on BREACH (`recon.py:94`) | A pipeline runner reads 0 as "safe to post" |
| Order joins key on `order_id` alone (`stitch.py:21`, `calculate.py:74,121,202`) | Per-store folders hide collisions today; multi-tenant API pulls remove that accident and duplicate revenue |
| Unparseable amounts coerce to 0 (`ingest.py:81` → `.fillna(0)`) | Excel exports are stable; JSON payloads introduce new formats silently as zero revenue |
| Unmapped SKU silently defaults to VAT 1.08 (`masters.py:117`) | A silent wrong-*tax* path, with no warning raised |
| Run log is free text, no input hashes or row lineage (`runlog.py`) | "Why did this row get this number?" becomes unanswerable after posting |

None of these are exotic. Each is a place where the current design assumes a human
will notice. The proposal's value depends on that assumption no longer holding.

**Recommendation.** Treat control rebuild as a gate on Phase 2, not a parallel
workstream. Concretely: tie-outs that cross a source boundary (conservation of rows
and money against the platform's own settlement figure, per-order closure, and a
round-trip re-read of the written workbook); BREACH blocks export; distinct exit
codes; composite join keys with a post-merge row-count assertion; a strict parse
policy for money columns; and a run manifest. This is on the order of two to three
weeks and it makes every later phase verifiable. The codebase already contains the
right pattern in one place — `lazada.py:208` correctly merges on a composite key.

---

## 5. AI orchestration: where it pays, and where it is disqualifying

Taking the four proposed agents individually, because they are not alike.

**Invoice calculation agent — recommend against.** The calculation is roughly
twenty-five branchless arithmetic expressions (`amount_pre_vat = unit_price × qty`
and similar), checked against tolerances as tight as **10 VND** on the Shopee return
rule. There is no judgment to exercise and no ambiguity to resolve. A model here adds
latency, cost, and irreproducibility to arithmetic that is already exact.

**Classification agent — recommend against.** This one looked like the strongest
candidate, because the team's "return + 0 dong" list is hand-curated and reads like
judgment. It was tested: the derived rules reproduced the hand-tagged sets exactly —
**178/178, 40/40, 11/11, zero missing and zero extra** (`classify.py:143-147`). The
one thing that appeared to require human judgment turned out to be decidable.

**Reconciliation agent — recommend against the model, but the layer is correctly
identified as weak.** The tie-out logic itself is `abs(actual − expected) ≤ tolerance`
— an SQL `GROUP BY … HAVING`, and it should stay that way. However, as §4 shows, the
claim "we already have deterministic tie-outs" does not currently hold, and there is
a genuine gap: cross-window overlap detection does not exist in the run path. The
5.97B VND double-pull was caught by ad-hoc human analysis, not by the pipeline. That
capability should be built — statistically and in SQL, not with a language model.

**D365 posting agent — recommend the automation, with the framing corrected.** This
is a systems integration, not an AI use case, and it should be scoped as one. It is
also the highest-risk item in the proposal, because double-posting revenue invoices is
a material finance incident. Prerequisites before any automated posting:

1. An **idempotency key** per invoice (platform · store · period · invoice group),
   enforced unique on the D365 side.
2. A **posting-state record written before the call**, transitioning
   `PENDING → POSTED / FAILED`. A timeout mid-post is the actual double-post vector,
   and it is only detectable if intent was recorded first.
3. A **dry-run mode** emitting the exact payload for diff.
4. A **reversal path** keyed off the same external reference.
5. A **release gate**: no posting while tie-outs breach, while
   `PLACEHOLDER_FORMULAS` is `True`, or while blocking exceptions are open.

Worth flagging for planning: the pipeline currently produces an *invoicing worksheet*,
not a GL journal. The account and dimension mapping does not exist anywhere yet. That
is a finance data-modelling workstream in its own right and should be scoped
separately from the integration.

### Where AI genuinely earns its place

The recurring cost of this system is not calculation — it is **absorbing change**.
The drift log records 13 format changes in two months, each needing a human to
notice, diagnose, and edit config. Three assistive use cases follow directly:

1. **Schema-drift mapper.** On unmapped headers, propose a column-map diff from the
   header text and sample values, with confidence, for human merge. This attacks the
   single largest documented maintenance cost.
2. **Fee-name bucketer.** Propose a bucket for novel fee names against the 118-entry
   master. Additive-only, recommend-only.
3. **Exception triage.** Cluster the exception report, rank by VND, and draft
   "what changed and where to look" in prose for the finance reviewer.

The guardrail is already proven by this project. When seven stores were onboarded
mid-period with renamed and typo'd labels, name similarity suggested one store was an
alias of an existing one; **order-ID overlap proved it was genuinely new**. A
similarity or embedding model would likely have merged it and corrupted a client's
revenue. Models may propose; evidence decides; a human approves. Applying that as a
written control boundary is what an auditor will ask for regardless.

Explicitly out of scope for AI, at any confidence level: computing or adjusting any
amount, selecting a VAT factor, deciding whether an order is invoiceable, resolving a
store alias without overlap evidence, or posting to D365.

---

## 6. API credentials in production

Business logic requires **no changes** for this. `stitch`, `classify`, `calculate`,
`tieout`, and `finance_template` already take and return DataFrames with no file or
network awareness; all I/O is confined to four modules. Three changes are needed:

- **Secret references, not secrets.** `settings.yaml` holds a reference
  (`client_secret: "kv://tiktok-app-secret"`), resolved after parsing inside
  `src/config.py` through a provider interface — environment variables for local
  development, **Azure Key Vault with Managed Identity** in production. Call sites do
  not change, and in production no credential exists on disk or in the repository at
  all. This directly answers the requirement that production not depend on a `.env`
  file.
- **A token store, which is the non-obvious part.** All three platforms use OAuth
  with **rotating refresh tokens**, so this needs read-*write* state with optimistic
  concurrency, not read-only secret retrieval. The real hazard is two concurrent runs
  refreshing the same shop and mutually invalidating each other's tokens — a class of
  outage that is confusing and expensive to diagnose at month end.
- **Store identity.** Today the store is parsed from the filename
  (`ingest.py:163-165`). API responses have no filename, and `store` is a join key
  throughout, so identity must instead be attached at fetch time from the credential
  that authenticated. This is the one genuinely invasive refactor in the migration.

Separately, and independent of Phase 2: `config/Lib & VAT rate.xlsb` — which contains
client VAT and SKU data — is committed to the repository, and `.gitignore` carries no
secret or credential patterns. Both should be corrected now.

---

## 7. Experience layer

**Finance exception queue — build first.** This carries the most value of the three
and is where the AI triage assist lands. It also replaces, in an auditable form, the
review that currently happens by eye.

**Power BI cockpit — build on the transaction store, not on exports.** Straightforward
once the store exists; premature before it.

**Brand rules console — defer, and re-scope.** The honest position is that there is
very little to configure yet. `config/brand_rules.yaml` was **13 lines with exactly one
rule type** (`invoice_grouping: separate | combined`) — and on 2026-08-18 it was deleted,
because it turned out nothing read it: its only consumer was a `classify()` function that
had no callers. The rules that actually drive invoice splits are hardcoded in
`finance_template.py:90-92` as substring matches on store names. A console today would
surface a single dropdown while the consequential logic stayed in code — and the substring
matching carries its own risk, since a future storefront named "Kao Beauty Partner" would
be swept into the KAO invoice silently.

The valuable work is consolidating those rules into a versioned configuration store
first. Two requirements for it:

- **Period-versioned rules** (`effective_from` / `effective_to`). Changing a VAT rate
  in August must not change what a re-run of May produces. Without this, reproducibility
  is lost the first time a rule changes — which is also the first time anyone needs it.
- **An append-only change log.** For financial configuration, who changed what, when,
  and why *is* the deliverable.

When it is built: **Next.js with server actions** is a good fit and does not require a
separate backend service — Next.js is itself the server — with Entra ID for
authentication. **Power Apps with Dataverse** is a serious alternative given the
existing Microsoft footprint: native authentication and audit, owned by finance, no
custom code. That option should be rejected deliberately rather than by default.

---

## 8. Recommended sequence

| Phase | Scope | Gate |
|---|---|---|
| 0 | Repository hygiene: remove client data from version control | — |
| 1 | **Rebuild controls**: real tie-outs, exit codes, composite join keys, strict money parsing, run manifest | **Blocks Phase 2** |
| 2 | Ingest seam + store registry (source becomes file *or* API) | — |
| 3 | Secrets, token store, first platform API, parallel run against manual | Clean parallel cycle |
| 4 | Transaction store, Power BI, exception queue | — |
| 5 | AI assists — schema-drift mapper, fee bucketer, exception triage (all recommend-only) | — |
| 6 | D365: dry-run → staged with human release → automated | All of §5's five prerequisites |

The ordering principle: **each phase must be verifiable by the phase before it.**
Phase 1 exists because nothing after it can be trusted otherwise.

One resourcing note that should inform scope. This system is currently built and
maintained by one engineer. A four-agent orchestration layer, a transaction store, a
console, and a posting integration is a multi-person surface area, and concentrating
it on a single person is itself an operational risk — the pipeline would become
harder to hand over than the spreadsheet chain it replaced. The recommendation is the
smallest system that satisfies the control requirements, with regression tests over
the settlement-bounds logic and the template exporter, both of which are currently
absent.

---

## 9. Open questions for decision

1. **There is no external ground truth.** Every figure ties to the team's own files —
   files now known to contain broken checks. Real assurance means tying to the bank
   settlement or the platform's own statement of account. This is arguably the highest
   -value item on the entire roadmap and it is in nobody's plan. Should it be funded?
2. **Who owns configuration** once a console exists — finance or engineering — and who
   approves a rate change?
3. **What is the GL mapping** for D365 posting: which accounts and dimensions, and who
   owns that specification?
4. **Month-end quarantine semantics.** The pipeline currently hard-stops on unknown
   stores, which is correct at three platforms and becomes a close blocker at five.
   What should partial-run and quarantine behaviour be when close is time-boxed?
5. **Confirmation of the `unclassified` bucket** — reimbursement and adjustment lines
   appear in neither team pivot and are currently routed to exceptions. Where do they
   book?

---

## Appendix A — Tie-out falsification test

Method: construct a SKU-level frame the way `calculate.py:140-142` constructs one,
apply corruptions, and run `src/tieout.py::run_checks_tiktok` unmodified at the team's
own tolerances (12,000 / 2,000 / 1,000 VND).

```
Baseline: 2,000 SKU lines, 2,148,376,478 VND pre-VAT

 SCENARIO                                        VERDICT               REVENUE REMOVED
 untouched (control)                             ALL PASS                            0
 dropped 30% of all SKU rows                     ALL PASS              628,442,458 VND
 dropped 50% of all SKU rows                     ALL PASS            1,072,807,689 VND
 dropped 90% of all SKU rows                     ALL PASS            1,922,233,878 VND
 dropped an ENTIRE store                         ALL PASS              568,994,870 VND
 halved every revenue amount                     ALL PASS            1,074,188,239 VND
 zeroed ALL revenue                              ALL PASS            2,148,376,478 VND
 amount_with_vat set to wrong VAT rate      PASS / BREACH / PASS                     0

Per-check verdict with 100% of revenue deleted:
   PASS   PV sum: pre-VAT per store == per VAT bucket
   PASS   Xuat HD bt: with-VAT lines == VAT-bucket recombination
   PASS   PV xuat HD: pre-VAT lines == SKU pivot
```

Reading: six of seven corruptions pass undetected. The only detected case is a
deliberately broken relationship between two derived columns — which the production
code cannot produce, since `calculate.py:142` defines one as the product of the other.

The checks do not verify revenue. They verify that addition is commutative.
