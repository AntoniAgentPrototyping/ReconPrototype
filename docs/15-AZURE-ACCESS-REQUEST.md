# 15 — Azure access request (prototype, synthetic data only)

**Status:** draft, not yet sent. **Read Part 0 and fill every `[BRACKET]` before sending.**

This is the ticket to raise with IT for somewhere to host the system **as a working
prototype**. It is deliberately small.

Scope decision, 2026-08-18: **prototype only, and it holds no client data.** This is
not a production deployment, is not a request to run a month-end close in the cloud,
and does not ask for anything that only a production system needs. Real runs continue
on the workstation. [13-ENTRA-SETUP](13-ENTRA-SETUP.md) is the companion doc for
sign-in, and is **not** part of this ask — see [Part 0](#the-entra-half-is-deferred).

---

# Part 0 — For you, not for IT

Delete everything above "The ticket" before sending.

## Azure is a third party, and pretending otherwise will not survive review

Microsoft would be a **data processor** and the company the **controller**. Putting
client data in Azure is a disclosure to an external provider, in the same legal
category as Railway or any other vendor. "It's our own tenant" is a governance
statement, not a legal one. Do not lead with it.

There is a real distinction — an existing Microsoft agreement means the processor
relationship is already contracted, where a new vendor needs onboarding, a DPA and a
security review — but **that argument is about the company's agreement with Microsoft,
and the constraint here is a contractual data compliance policy governing the client
data itself.** Those are different documents and the second one wins.

## So the decisive move is to host nothing that the policy governs

The system already generates a **deterministic synthetic demo window across all three
platforms** — `service/sampledata.py`, exposed as `POST /demo/seed`. It exercises
upload, the queue, a run, the streamed log, the workbook, exceptions and the config
editor, using data that is invented.

A prototype seeded from that holds **no client data at all**, which means:

- the compliance policy has nothing to bind, because there is nothing it governs;
- no DPIA, no retention period to get approved, no residency question;
- no need for a managed identity, Key Vault, or private endpoints;
- and the ask shrinks to something IT can approve in one pass.

**Real settlement data never goes near it.** Production runs stay on the workstation,
where they already work today.

## Read the actual policy before sending — four questions

The answers change the ticket, and guessing is what produces an embarrassing
retraction:

1. Does it govern **client data specifically**, or all company material? If the
   latter, even synthetic data derived from a real schema may need a mention.
2. Does it name an **approved processor list** or an approved cloud? Azure may be
   explicitly in it, which makes this whole conversation short.
3. Is there a **residency clause**? Relevant only if real data is ever hosted, but
   worth knowing now.
4. Who **owns** the policy — IT, legal, or the client relationship? That is who
   actually answers, and it may not be the IT specialist you spoke to.

## The zero-ask option, which is genuinely competitive

If the prototype's purpose is to show people it works, the workstation plus a screen
share costs nothing, needs no ticket, and can use **real** data because it never
leaves the machine. Host it only if stakeholders need to click it themselves, on their
own time, without you present. That is a real reason — it is just worth being honest
that it is the only one.

A company-managed VM running `docker compose` is the middle option: no external
processor, and the compose file already works. Slower to get, and someone has to patch
it.

## The Entra half is deferred {#the-entra-half-is-deferred}

[13-ENTRA-SETUP](13-ENTRA-SETUP.md) argues for requesting directory permissions in the
same ticket, on lead-time grounds. **That advice assumed a real deployment.** Single
sign-on for a synthetic-data prototype used by a handful of people is not worth a
directory-role escalation, and a smaller ask is approved faster. The existing
username/password accounts are sufficient here.

Raise Entra when there is a real deployment to raise it for. It is mentioned in §5 of
the ticket only so nobody is surprised later.

---

# The ticket

*(Everything from here down is what you send.)*

## 1 — What this is

I have built an internal tool that reconciles marketplace settlement data (TikTok
Shop, Shopee, Lazada) into the workbook the finance team invoices from. It runs on my
workstation today and I would like somewhere to stand it up as a **working prototype**,
so that `[STAKEHOLDERS]` can use it themselves rather than watching me demo it.

| | |
|---|---|
| Purpose | Prototype / proof of concept. **Not a production system** |
| Users | `[N]` people, occasional use |
| Data | **Synthetic only** — see §2. No client data, no personal data |
| Shape | Five small containers: web UI, API, background worker, PostgreSQL, object storage |
| Cost centre | `[COST CENTRE]` |
| Estimated cost | ~USD `[25–45]` / month, and it can be shut down between demos |
| Lifetime | `[N]` months, then reviewed or deleted |
| Requested by | `[YOUR NAME]`, `[YOUR TITLE]` |

## 2 — It holds no client data, by design

This is the part I would like on the record, because it is what makes the request
small.

The application ships a **generated synthetic dataset** — invented storefronts,
invented orders, invented settlement figures across all three marketplaces. It is
deterministic, so the prototype produces the same demonstration every time.

**The prototype will be seeded from that and nothing else.**

- No real marketplace exports will be uploaded to it.
- Therefore no customer personal data, and no client commercial data, is hosted.
- Real reconciliation runs continue to happen on my workstation, unchanged, as they
  do today.

I am flagging this because our data compliance obligations govern client data, and I
want it clear that this request does not touch them. **If hosting real data is ever
proposed, that is a separate request with a separate review, and I will raise it as
one.**

## 3 — What I am asking for

Everything scoped to **one new resource group**. Nothing at subscription scope.

| Role | Scope | Why |
|---|---|---|
| **Contributor** | new resource group `rg-recon-proto` | Create and manage the resources in §4 |
| **Storage Blob Data Contributor** | that resource group | **Needed in addition to Contributor.** Contributor lets me manage a storage account but not read the data in it — without this the application can create its file store and cannot use it |

That is the whole ask.

### What I am not asking for

- **Not Global Administrator**, and no directory or Entra ID roles at all.
- **Not Owner**, and nothing at subscription scope.
- **Not `User Access Administrator`.** A production deployment would use a managed
  identity so there are no passwords in configuration, and granting that identity its
  roles needs this. A synthetic-data prototype does not justify it — connection
  strings held as container secrets are proportionate here.
- **No Key Vault.** Same reasoning: the only secrets are the prototype's own database
  and storage credentials, which container secrets hold adequately when nothing
  confidential is stored.
- **No private endpoints or network changes**, unless you would prefer them.

## 4 — Resources I would create

All inside `rg-recon-proto`. Figures are indicative estimates for `[REGION]`, to be
confirmed on the Azure pricing calculator.

| Resource | SKU | Purpose | Est. USD/month |
|---|---|---|---|
| Azure Database for PostgreSQL — Flexible Server | Burstable **B1ms**, smallest storage | Job queue and run history | ~`[15]` |
| Storage account (blob, LRS) | Standard, a few GB | Generated workbooks | ~`[1]` |
| Container Registry | **Basic** | Holds the application image | ~`[5]` |
| Container Apps environment + 3 apps | Consumption, minimum sizing | Web UI, API, worker | ~`[10–25]` |
| | | **Total** | **~`[25–45]`** |

Two notes. **Container Apps** rather than App Service because it provides an HTTPS
endpoint with a managed certificate, so I do not need to stand up a reverse proxy for
a prototype. And the whole resource group can be **stopped or deleted between
demonstrations** — if cost is a concern I am happy to treat it as ephemeral and
recreate it, since it holds nothing that matters.

If there is an existing **container registry** I should push to rather than creating
one, tell me and I will use it — I would then need `AcrPush` on it.

## 5 — What this is not

Being explicit so this is not mistaken for a production request.

- **It is a prototype.** No backups, no monitoring, no alerting, no disaster recovery,
  and no security review. If it breaks, it breaks, and nothing is lost.
- **It holds no client or personal data**, as set out in §2.
- **It uses its own username/password accounts**, not single sign-on. A real
  deployment should use Microsoft Entra sign-in, which needs directory permissions —
  I am deliberately **not** requesting those now, because it is not warranted for
  this.
- **Nothing built is Azure-specific.** The application uses standard PostgreSQL and a
  single file-storage interface, and already runs as Docker containers. If Azure is
  the wrong home, or the answer is no, no work is lost.

If the prototype earns a real deployment, that is a separate and much larger
conversation — hosting real settlement data, a compliance review, single sign-on,
backups and a restore drill — and I will raise it as its own request rather than
growing this one.

## 6 — Questions

1. Which **subscription** should this go in?
2. Does the cost in §4 need approval before I create anything, and against which cost
   centre?
3. Is there an existing **container registry** I should use?
4. Is there a **naming convention or tagging policy** for resource groups I should
   follow?
5. Is there a **review or expiry** you would like attached to a prototype environment?
   I am happy to agree a date to either justify it or delete it.

---

# Annex — how I will confirm the access works

So we both know whether the grant landed, without a vague "it doesn't work"
follow-up. I have enough when I can, in order: see `rg-recon-proto` in the portal;
create a PostgreSQL Flexible Server in it; and create a storage account **and list
blobs inside it**. That last step is the one that returns 403 if only Contributor was
granted, which is why the second role is named explicitly in §3.
