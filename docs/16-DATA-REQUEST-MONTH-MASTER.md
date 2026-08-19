# Data request — the month-end master file

> **Status: BOTH ASKS SATISFIED, 2026-08-19.** This document is kept as the record
> of what was asked for and why, not as an outstanding request.
>
> * **Ask 1 — the master file.** Received as a **per-tab CSV export** rather than a
>   delabelled workbook. That is enough to compare against and is what
>   `tools/compare_master.py` reads. The underlying `.xlsx` is still label-encrypted
>   and still unopenable here, so the *durable* half of Ask 1 — option 2, granting
>   the label rights to the identity this service runs as — **remains outstanding**
>   and is register item **C15** / [13-ENTRA-SETUP](13-ENTRA-SETUP.md). It is a
>   hosting blocker, not a Phase 3 blocker.
> * **Ask 2 — the July raw exports.** Received: 1,051 files, 9.8 GB, all three
>   platforms. Staged into 14 windows (`2026-07_l1..l5`, `_s1..s4`, `_w1..w5`).
>
> **One caveat on the CSVs, and it is not the team's fault.** Excel's
> "CSV (Comma delimited)" writes the system ANSI codepage, so every Vietnamese
> storefront name was destroyed on export — `Unilever Chăm Sóc Vẻ Đẹp` arrived as
> `Unilever Ch?m S�c V? ??p`. The numbers are intact and the comparison works
> (`compare_master._skeleton` matches the mangled label to ours), but **if these are
> ever re-exported, "CSV UTF-8 (Comma delimited)" is the format to pick.**

Two things were needed from the finance team before the month-end master summary
could be built *and* checked. Neither was an engineering task, and no amount of
engineering substitutes for either.

**Ask 1 changed substantially on 2026-08-19** — it was a request to re-save a
mislabelled file, and it is now a request about an information-protection label. The
original diagnosis was wrong; the correction is kept in place rather than tidied
away, because the *shape* of that mistake (a signature check that identified the
container and not what was in it) is worth remembering.

Written 2026-08-18, after Phase 2 of [14-PRODUCTION-READINESS](14-PRODUCTION-READINESS.md).
Phase 3 is blocked on both items and nothing else.

---

## What we are building, in one paragraph

The team asked (Aug 2026) for "one master file to consolidate all the weeks by brand
… for all platforms". A prototype exists — `tools/build_master_summary.py` — which
reads the per-window finance files this system already produces and aggregates them,
so every figure in the master is provably the same number the weekly files carry.
Moving it into the product means it gets generated automatically after each window
run, ties to its sources by test, and can be compared against the team's own master
rather than only against itself.

That last part is what needs you.

---

## Ask 1 — a copy of the master file without the sensitivity label

**The file:** `ADA marketplace MASTER July 2026.xlsx`

**It is a real `.xlsx`, and the extension is right.** What stops anything opening it
is that it carries a **Microsoft Purview sensitivity label with encryption**. Excel
opens it for you because your account has rights to that label; nothing else does.

The evidence, so nobody has to take this on trust — the file is an OLE2 container
holding these streams:

```
EncryptedPackage                                   28,680 bytes  <- the real .xlsx
DataSpaces/TransformInfo/DRMEncryptedTransform/…
DataSpaces/DataSpaceInfo/DRMEncryptedDataSpace
DataSpaces/TransformInfo/LabelInfo                 <- the label that encrypted it
```

and `LabelInfo` says:

```xml
<clbl:label id="{ec93f274-b095-4c63-9235-6d36a96e107a}"
            enabled="1" method="Privileged" siteId="{914b406b-…}" removed="0" />
```

`method="Privileged"` means a person applied it deliberately rather than a policy
auto-labelling the file.

**What we need — either one:**

1. a copy saved with the label removed (Excel: **Sensitivity → remove label**, then
   Save As), for use in this prototype only; **or**
2. the label extended to grant rights to the identity this service will run as,
   which is the durable answer and is an IT/Purview change rather than a favour.

**This is not a one-off, and that is the important part.** The same label — same id,
same tenant — is on one of the Lazada weekly exports already in our tree. Any file
the team labels is unreadable to this system, so **option 2 is what has to happen
before this is hosted anywhere**. See [13-ENTRA-SETUP](13-ENTRA-SETUP.md).

*This section previously asked for the file to be re-saved as "a genuine `.xlsx`",
on the reading that its OLE2 signature meant a legacy `.xls` with the wrong
extension. That was wrong: the OLE2 wrapper is the encryption, not a legacy
workbook. Corrected 2026-08-19 after reading the container's streams. Re-saving it
would not have helped, and asking for it would have wasted somebody's afternoon.*

## Ask 2 — the July raw exports

**What we need:** the raw platform exports for **July 2026** — TikTok, Shopee and
Lazada, all windows — the same files you would normally hand over for a settlement
run.

**Why:** the master summary is an aggregation of window results. To compare our
master against yours, we have to have produced July's windows first. This machine
holds the **May** set (75 files) and no July data at all, so today the comparison
cannot be made in either direction.

**What happens without it:** we can still build the master and prove it ties to the
per-window files it aggregates — that is an internal consistency check and it is
worth having. What we cannot do is the check that actually matters: comparing our
figures against a file the team produced independently. That claim stays open in
[07-VERIFICATION.md](07-VERIFICATION.md) until July is staged.

---

## What we will do with them

1. Record the reference file's tabs, header rows and grouping in
   [05-DOMAIN-RULES.md](05-DOMAIN-RULES.md) — **blocked until Ask 1 lands**; nothing
   here can read the file's contents today. All tabs are in scope **except** brand
   mapping, which this system already holds as configuration.
2. Build July's windows and generate our master from them.
3. Compare tab by tab, and treat every difference as one of two things: documented
   and deliberate, or a finding.

**On that last point, plainly:** the team's file is not automatically the right
answer, and neither is ours. Five defects have already been found in the team's
workbooks and are recorded in [08-KNOWN-DEFECTS.md](08-KNOWN-DEFECTS.md) Part 2 —
found by this kind of comparison, which is the argument for doing it. A difference
starts a conversation; it does not settle one.

---

## Not blocking, but worth asking while we are here

`config/Lib & VAT rate.xlsb` — the fee-type and per-SKU VAT master — is read live
from a shared folder on every run. Two things about it:

* Its 660 SKUs currently match **none** of the SKUs traded in any window we have
  sampled, on any of the three platforms. So the per-SKU VAT exception has never
  fired and everything invoices at the 1.08 default. That may well be correct — but
  we cannot tell from the code whether the master is *meant* to cover these
  storefronts. It is [open question 9](11-OPEN-QUESTIONS.md).
* If the team is willing, uploading it into the system instead of having it read
  from a folder would let us report exactly what changed each time it is edited.
  That changes how the team works, so it is a request rather than a plan.
