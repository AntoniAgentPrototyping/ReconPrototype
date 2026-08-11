# Open Questions for the Evaluator

You are being asked for a critical, independent evaluation. The claims in
this dossier are evidence-backed but were produced by the same people who
built the tool — your job is to try to break them. Candid pointers on where
to push:

## 1. Is the evidence-first derivation actually sound?

- The calculation rules were ported from the team's M code and worksheet
  formulas, then verified against the team's own outputs. That proves
  **faithful reproduction of the team's process** — it does NOT prove the
  team's process is correct. Row-verification against a workbook whose own
  checks are broken (see CHALLENGES_AND_FINDINGS §2) inherits any systematic
  error the team makes. Question: is there any independent ground truth
  (platform statements? bank settlements? client acceptances?) the numbers
  could be tied to, beyond the team's own files?
- The Shopee status rules ("Return" / "0 dong" / "ok") were DERIVED from
  data patterns and verified against one manual XLOOKUP list. Would a rare
  fourth case (e.g. partial refund with positive settlement) classify
  correctly? Try to construct one from the raw data.
- The June Finance_Month convention was derived from six file tags. Is six
  data points enough? What happens in a month with an unusual settlement lag?

## 2. Which rules might not generalize beyond the three months tested?

- **Filename parsing**: the store-from-filename regexes have needed extension
  every single month. The store-name capture is only as safe as the suffix
  alternatives; a store whose name ends in a number-like token could be
  truncated. Audit the regex against hostile-but-plausible names.
- **Window labels/boundaries**: the settlement-bounds mechanism is
  config-per-window, added reactively when July's w2 was mis-pulled.
  Detection of NEW boundary overlaps is not yet automated — if August's
  exports overlap on a different day, nothing in the run path will catch it
  (identified gap, unfixed at dossier time).
- **VAT**: default-plus-exceptions rests on the team's VAT_SKU master being
  complete. Non-1.08 lines have barely been exercised live. What happens if
  a new SKU with 1.05 VAT trades before the master is updated? (Answer in
  code: silently defaults to 1.08 — is that acceptable?)
- **Brand buckets** (KAO/Merries/Others etc.) are substring matches on store
  names. A future store named e.g. "Kao Beauty Partner" would be swept into
  the KAO invoice bucket without warning.
- **Order-less revenue** (Lazada compensations) is handled; are there TikTok/
  Shopee analogues (platform compensations inside income exports) that would
  currently land in a classification bucket silently?

## 3. Where is the tool most fragile?

- **Monthly staging is manual** — a hand-adapted script per month. The most
  likely failure mode is human mis-staging (a file in the wrong window), and
  the defenses (store roster, coverage checks) are only partial.
- **Excel parsing edge cases**: three reader engines/configs exist because
  real exports were malformed in three different ways. The next malformed
  file may fail in a fourth way; the pipeline's posture is hard-stop rather
  than guess, which is safe but blocks the run.
- **In-process assumptions**: one machine, one operator, no tests around the
  newest code (settlement bounds, template exporter). A refactor could break
  the control-block arithmetic without anything failing loudly. (The
  template's checks would drift — would anyone notice a plausible-looking
  wrong verdict?)
- **The team's own template semantics**: the rebuilt invoicing workbook
  reproduces rounded-price pivot semantics (invoice view) vs exact line
  semantics deliberately. Confirm with finance that this distinction matches
  how they actually invoice clients — a wrong assumption here changes client
  bills by small amounts at scale.

## 4. Process questions worth asking the humans

- Who reviews the run logs each month? A pipeline that flags honestly is only
  as good as the person reading the flags.
- What is the sign-off protocol for flipping `PLACEHOLDER_FORMULAS` and for
  booking from these files instead of the manual chain?
- Is there a rollback story if a generated file is later found wrong after
  invoicing?
- The three Reckitt storefronts and the Lazada "lactacyd" mapping await
  business confirmation — who owns that decision?

## 5. Suggested falsification exercises

1. Pick 20 random orders from a raw July export; hand-compute their invoice
   lines from the documented rules; compare to the generated file.
2. Deliberately mis-stage one file (wrong window) and observe whether any
   guard catches it.
3. Feed the pipeline a copy of a window where you have edited one amount by
   1,000,000 VND; verify which check catches it and how it is reported.
4. Re-run the same window twice; confirm outputs are byte-identical
   (determinism claim).
5. Take the team's original May invoicing file and the pipeline's template
   re-issue of the same window; diff them cell-region by cell-region and
   satisfy yourself that every difference is one of the documented,
   deliberate ones.
