# 13 — Entra ID and Azure access (one-time, done by a human in the portal)

M5 closes [defect 2.1](08-KNOWN-DEFECTS.md#21-the-api-is-unauthenticated--open-m5) — the api has no authentication. The code for that can be written here; the **app registration cannot**, because it lives in your Microsoft tenant and needs someone with directory permissions. This page is the checklist for doing it, written for a first time in the Microsoft ecosystem.

Everything here is a portal task. None of it touches this repo, and none of it is reversible-by-accident — an app registration does nothing until an application uses its credentials.

---

# Step 0 — The permissions to request

**Read this before anything else if you cannot currently open App registrations.** Every step below needs directory permissions that ordinary accounts often do not have, and the roles have confusingly similar names.

## First, what is *not* needed

Saying this explicitly saves an escalation round-trip, because "Azure" makes people reach for the wrong things:

- **Not Global Administrator.** Nothing here requires it. Asking for it will slow the request down and should be refused.
- **Not any Azure *subscription* role — for the sign-in setup.** An app registration lives in **Entra ID (the directory)**, not in an Azure subscription; the two are separate permission systems and directory roles grant nothing in a subscription. **But the rest of the system will need subscription access** — see [Step 0b](#step-0b--azure-resource-permissions-a-second-and-separate-ask). Ask for both in one ticket.
- **Not any Microsoft Graph API permission.** The app reads who you are and what role you have out of the sign-in token. It never calls Graph.
- **Not admin consent.** The scopes used (`openid`, `profile`, `email`) are consentable by an ordinary user and need no tenant-wide grant.

## Three ways to get unblocked, cheapest first

### Option A — Ask IT to do it and send you the values (zero permissions)

Entirely viable, and for a one-off setup often the fastest path. Send them **steps 1–4** of this document and ask for the four values in step 6. The costs are real, though: every later change (a new redirect URI when the app gets a real hostname, a **secret rotation every 12–24 months**, adding a colleague) is another ticket. Given the secret must be rotated on a deadline or everyone is locked out, standing access is worth arguing for.

### Option B — Least privilege (what a security-conscious IT team will prefer)

| Ask for | Scope | Buys you |
|---|---|---|
| **Application Developer** | Directory role, tenant-wide | Create the app registration even when the tenant's "Users can register applications" setting is **No**. You are automatically made **owner** of what you create. |
| **Owner** of the resulting enterprise application | That one app only | Assign users and groups to it, and set "Assignment required" — i.e. step 4. |

As the **owner** of the app registration you can then manage its secrets, redirect URIs and app roles without any tenant-wide role. This is the smallest ask that leaves you self-sufficient.

The wrinkle worth naming in the request: creating a registration also creates a service principal (the enterprise application). Ownership of the two objects is tracked separately, and whether you are automatically made owner of the *service principal* varies. **Ask IT to confirm you are an owner of both**, or step 4 will fail with a permissions error that looks nothing like a permissions error.

### Option C — One role that covers everything end to end

| Ask for | Buys you |
|---|---|
| **Cloud Application Administrator** | Create and manage all app registrations and enterprise applications: registration, secrets, app roles, user assignment, "Assignment required". Everything in steps 1–5. |

It is a genuinely privileged role — it covers *all* applications in the tenant, not just yours — so expect pushback, and expect it to be granted **time-bound through Privileged Identity Management (PIM)** rather than permanently. That is the normal and correct answer; ask for an eligible assignment you can activate for a few hours when you need it.

> **Application Administrator** is the same thing plus on-premises Application Proxy. You do not need Application Proxy, so ask for **Cloud** Application Administrator — asking for less is what gets requests approved.

## Optional extras, only if you hit these

| Ask for | When you need it |
|---|---|
| **Reports Reader** | To read Entra **sign-in logs**. The single most useful debugging tool when a sign-in fails — every `AADSTS…` error in this document's troubleshooting table appears there with the real reason. Low-privilege and easy to get. Worth requesting alongside the main ask. |
| **Groups Administrator** | Only if you want to assign access by security group (`Finance-Recon-Users`) rather than naming individuals. Recommended once more than two or three people use this; not needed to get started. |
| A **Conditional Access** exclusion or review | Only if sign-in fails with a policy message. Some tenants block new applications, or require compliant devices, in ways that stop a localhost dev redirect. This one is **Conditional Access Administrator** territory and is IT's to change, never yours. |
| **Key Vault Secrets Officer** on a specific vault | Later, if the client secret is stored in Azure Key Vault rather than an env var. This *is* an Azure subscription RBAC role, and it is the only one on this page. |

---

# Step 0b — Azure resource permissions (a second, and separate, ask) {#step-0b--azure-resource-permissions-a-second-and-separate-ask}

**Entra directory roles give you nothing in an Azure subscription.** They are two independent permission systems that share a portal, and this catches almost everyone:

| | Entra ID (the directory) | Azure RBAC (subscriptions) |
|---|---|---|
| Governs | Users, groups, app registrations, sign-in | Resource groups, databases, storage, container hosting |
| Roles look like | Application Developer, Cloud Application Administrator | Owner, Contributor, Reader, Storage Blob Data Contributor |
| Granted at | Tenant / a single application | Management group / subscription / resource group / one resource |

So **Application Developer will not let you create a PostgreSQL server**, and Contributor on a subscription will not let you register an application. If the plan is to host this system in Azure, both asks belong in the same ticket — access requests are slow, and a second round-trip costs more than a slightly larger first one.

## First: is Azure actually the target?

Worth deciding before requesting anything, because it changes the ask. The hosting decision is **not made** — `deploy/docker-compose.yml` describes Postgres + api + worker as containers and says nothing about where they run, and it has [never been built](08-KNOWN-DEFECTS.md#22-deploydockerfile-and-deploydocker-composeyml-have-never-been-built--open).

Azure is the obvious default *if* the organisation is already a Microsoft shop (Entra for identity, D365 as the eventual posting target), because identity, secrets and the database can all use one credential model. It is not the only option, and nothing built so far is Azure-specific — the service talks plain PostgreSQL and writes artifacts through a one-method storage interface.

**Nothing is blocked on this today.** M4 and M5 run against the local Postgres at `%LOCALAPPDATA%\recon-pg`. The Azure ask is about where this lands in production, so it can be requested in parallel and used later.

## What the system will eventually need

| Resource | Role | Purpose |
|---|---|---|
| A dedicated **resource group** (e.g. `rg-recon-dev`) | **Contributor**, scoped to that resource group | Create and manage everything below. Scoping to one resource group — rather than the subscription — is what makes this a reasonable ask. |
| **Azure Database for PostgreSQL — Flexible Server** | covered by Contributor above | The job queue, run records and run log ([D29](06-DECISIONS.md#d29)). Burstable B1ms is adequate at ~14 jobs a month. |
| **Storage account** (blob) | Contributor **plus** `Storage Blob Data Contributor` | The artifact store — the second `ArtifactStore` implementation ([defect 2.4](08-KNOWN-DEFECTS.md#24-artifacts-are-local-filesystem-only--open)) — and staged raw exports. |
| **Key Vault** | Contributor **plus** `Key Vault Secrets Officer` | The Entra client secret and database credentials. |
| **Container Registry** | `AcrPush` | Pushing the api/worker image. |
| **Container Apps** or **App Service** | covered by Contributor above | Running that image. |
| Role assignments for the app's **managed identity** | `User Access Administrator` on the resource group, **or** IT does it | See the warning below. |

### Two things that surprise people

**Control plane and data plane are separate.** `Contributor` on a storage account lets you *configure* it and still returns **403 when you list blobs**, because blob access is granted by a different family of roles (`Storage Blob Data Contributor`). Key Vault behaves the same way (`Key Vault Secrets Officer`). If you ask only for Contributor you will be able to create the storage account and unable to use it.

**Assigning roles is itself a privilege.** The clean design here is a **managed identity** — the app authenticates to Postgres, Blob and Key Vault as itself, with no passwords in environment variables at all. Azure Database for PostgreSQL Flexible Server supports Entra authentication directly, which would remove `RECON_DATABASE_URL`'s password entirely. But *granting* that identity its roles needs `User Access Administrator` (or `Owner`), which IT frequently keeps. Either ask for it scoped to your one resource group, or plan for IT to make those three role assignments for you.

### Also worth settling in the same conversation

- **Who pays.** Resources cost money and someone owns the budget line. A "Contributor on a resource group" request is usually approved much faster when it names a cost centre and an approximate monthly figure.
- **Which subscription**, and whether a separate dev/prod pair is expected.
- **Networking posture.** Many tenants require private endpoints and no public database access. That changes how the worker reaches Postgres and is far cheaper to learn now than after the first deployment.
- **Where the PII sits.** Staged raw exports contain customer personal data ([04-DATA-FLOW](04-DATA-FLOW.md#pii--what-must-never-leave)), so the storage account's region, encryption and retention are a compliance question, not a preference. This is [open question 11](11-OPEN-QUESTIONS.md), still unanswered.

## What to send the escalation

Copy this, fill in the two brackets:

> I'm building an internal finance reconciliation tool and need two separate sets of access. They are in two different permission systems, so I've listed them separately — but I'd rather request both now than come back twice.
>
> **1 — Entra ID, for sign-in.** The app is single-tenant, uses OpenID Connect sign-in only, and requests **no Microsoft Graph permissions**, so no admin consent is required.
>
> - *Preferred (least privilege):* the **Application Developer** role, plus **ownership of both the app registration and its enterprise application**, so I can manage the client secret and assign users myself. The secret expires within 24 months and must be rotated or everyone is locked out, so standing access avoids a recurring ticket.
> - *Alternative:* **Cloud Application Administrator**, ideally **PIM-eligible** rather than permanent.
> - Plus **Reports Reader**, to read sign-in logs when diagnosing failures.
>
> I do **not** need Global Administrator or any Graph API permission for this part.
>
> **2 — Azure subscription, for the application's infrastructure.** The system needs a PostgreSQL database, blob storage for output files, a key vault and container hosting.
>
> - **Contributor scoped to a single new resource group** (e.g. `rg-recon-dev`) — not subscription-wide.
> - **Storage Blob Data Contributor** and **Key Vault Secrets Officer** on that resource group. These are needed in addition to Contributor: Contributor grants management but not data access, so without them I can create a storage account and not read from it.
> - **AcrPush** on a container registry, if one exists.
> - **User Access Administrator** on that one resource group — *or*, if that is not granted, I will need IT to assign the application's managed identity its roles on the database, storage account and key vault. The managed-identity approach is what removes stored passwords from the deployment, so I'd like to use it.
>
> Happy to discuss: which subscription, whether dev and production should be separate, the cost centre this bills to, and whether private endpoints are required for the database.
>
> **If policy is that only IT can do part 1**, I can send a four-step setup document instead, and would need four values back: tenant ID, client ID, the registered redirect URI, and the client secret (via [your secret-handling channel]). Someone would then need to rotate that secret before [expiry date] each cycle.

## Sanity check once access arrives

You have enough permission when you can, in order: open **Entra ID → App registrations** and see **+ New registration**; open **Certificates & secrets** on your app and add one; open **App roles** and create one; and open **Enterprise applications → your app → Users and groups** and add yourself. If the last one is greyed out or errors, you have the app-registration half and not the service-principal half — go back to IT with that specific sentence.

---

## The vocabulary, first — it is the confusing part

| Term | What it actually is |
|---|---|
| **Microsoft Entra ID** | The new name for Azure Active Directory. Same product, renamed in 2023. Documentation and blog posts use both. |
| **Tenant** | Your organisation's directory. It has a GUID — the "Directory (tenant) ID". |
| **App registration** | The *definition* of an application: its ID, its secrets, its redirect URIs, its roles. This is what you create. |
| **Enterprise application** | The *instance* of that app inside your tenant — where you assign which users may sign in. The portal creates it automatically alongside the registration. **Two views of one thing**, which is the single most confusing part of this for a newcomer. |
| **Client ID** | Public identifier of the app. Not a secret. |
| **Client secret** | A password the server uses to prove it is the app. **Is a secret.** |
| **Redirect URI** | The exact URL Entra sends the user back to after they sign in. Must match character for character. |
| **Claim** | A field inside the token Entra issues — the user's name, email, roles. |

The flow being set up is **OpenID Connect authorization code flow with PKCE**, for a *confidential client* (a server that can keep a secret). The user's browser never sees the secret, and this app never sees the user's password.

## What you will create

1. One app registration.
2. One client secret.
3. Three app roles, so the api can tell an operator from a viewer.
4. User/group assignments, so only the finance team can sign in.

## Step 1 — Create the app registration

**Portal** → search **Microsoft Entra ID** → **App registrations** → **+ New registration**.

| Field | Value | Why |
|---|---|---|
| **Name** | `Recon Reconciliation` | Internal label only; users see it on the consent screen. |
| **Supported account types** | **Accounts in this organizational directory only (Single tenant)** | This is an internal finance tool. Any other option lets identities outside your tenant attempt sign-in. |
| **Redirect URI** | Platform **Web**, URI `http://localhost:8080/auth/callback` | The dev one. Add the production URL later — see step 5. |

Press **Register**.

On the **Overview** page that appears, copy two values. Neither is secret:

- **Application (client) ID**
- **Directory (tenant) ID**

> **Platform must be "Web", not "Single-page application".** SPA registrations refuse to use a client secret, and this app is a server holding one. Picking the wrong platform here produces an `AADSTS9002326` error much later that reads as if the code is wrong.

## Step 2 — Create a client secret

**Certificates & secrets** → **Client secrets** → **+ New client secret**.

- **Description:** `recon api`
- **Expires:** 12 or 24 months. **24 months is the maximum Entra allows** — there is no non-expiring option.

Press **Add**, then copy the **Value** column immediately.

> **The Value is shown exactly once.** Navigate away and it is unrecoverable — you delete the secret and make a new one. Do not copy the "Secret ID" by mistake; that is not the credential.

**Put a calendar reminder on the expiry date now.** A silently expired client secret means everyone is locked out of the app on a random morning, and at month end that is a bad morning. This is the single most common way an internal app like this breaks.

## Step 3 — Define three app roles

**App roles** → **+ Create app role**. Create three, identical except for the names:

| Display name | Value | Allowed member types | What it will mean |
|---|---|---|---|
| Viewer | `recon.viewer` | Users/Groups | See runs, logs, exceptions. Changes nothing. |
| User | `recon.user` | Users/Groups | Everything a viewer can, plus queue and cancel runs, upload exports, declare a partial roster, and propose config changes. (Named `recon.operator` before M6.) |
| Admin | `recon.admin` | Users/Groups | Everything an operator can, plus propose and approve configuration changes. |

The **Value** strings are what arrive inside the token and what the code will match on, so they must be exactly as written above.

*Why roles here rather than security groups:* a role arrives as a `roles` claim inside the sign-in token itself. A group arrives as a group **GUID** that the app must then look up through Microsoft Graph — a second API call, a second permission to be granted, and a mapping table of opaque GUIDs to maintain. Roles keep the whole authorization decision inside the token.

## Step 4 — Restrict and assign who can sign in

This is in the **Enterprise application** view — the other half of the same object.

**Portal** → **Microsoft Entra ID** → **Enterprise applications** → find `Recon Reconciliation`.

1. **Properties** → set **Assignment required?** to **Yes**, then **Save**.
   Without this, *every* user in your tenant can sign in — they arrive with no role, but they arrive.
2. **Users and groups** → **+ Add user/group** → pick the person or group, then pick the role.

Assign yourself `recon.admin` so you can test it.

> If your account cannot complete this step, you need someone with **Cloud Application Administrator** or **Global Administrator**. Creating the registration is often allowed for ordinary users; assigning users and granting consent frequently is not. Worth finding out early rather than at the end.

## Step 5 — When there is a real deployment URL

Add the production redirect URI alongside the dev one: **App registration** → **Authentication** → **Add URI** →
`https://<the real host>/auth/callback`

Rules that catch people out:

- **Exact match.** Trailing slash, `http` vs `https`, and port are all part of it.
- **HTTPS is mandatory** for anything other than `localhost`.
- Both URIs can coexist, so dev and production share one registration. A separate registration per environment is tidier and is the usual advice — worth doing if there will be a staging environment.

## Step 6 — Send four things back

Three are safe to send normally. **One is not.**

| Value | Where to find it | Sensitive? |
|---|---|---|
| Directory (tenant) ID | App registration → Overview | No |
| Application (client) ID | App registration → Overview | No |
| The redirect URI(s) you registered | Authentication | No |
| **Client secret Value** | Only from step 2, at the moment you created it | **Yes** |

**Do not paste the client secret into a chat window, a ticket, or a commit.** It is the credential that lets anything impersonate this application. Put it in a password manager, or a Key Vault, or hand it over in person. It will be read from the `RECON_OIDC_CLIENT_SECRET` environment variable and will never be written into this repository — `.gitignore` already covers `.env`.

Also confirm, in words:

- the three app roles exist with those exact `Value` strings;
- **Assignment required** is set to **Yes**;
- at least one account is assigned `recon.admin`.

## What happens with those values

```
RECON_OIDC_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
RECON_OIDC_CLIENT_ID=<application-client-id>
RECON_OIDC_CLIENT_SECRET=<from a secret store, never a file in this repo>
RECON_OIDC_REDIRECT_URI=http://localhost:8080/auth/callback
RECON_SESSION_SECRET=<random 32+ bytes, rotate to log everyone out>
```

The api reads the discovery document at `<issuer>/.well-known/openid-configuration`, which is how it learns the authorize, token and JWKS endpoints without any of them being hardcoded. Sign-in becomes a redirect; the app validates the returned token's signature against Entra's published keys, reads the `roles` claim, and sets a signed session cookie.

## Things that will go wrong, and what they mean

| Symptom | Cause |
|---|---|
| `AADSTS50011: redirect URI mismatch` | The URI in the request differs from a registered one. Almost always a trailing slash, or `http` where `https` was registered. |
| `AADSTS650057: Invalid resource` | Scopes requested that the registration does not have. `openid profile email` needs no admin consent. |
| `AADSTS7000215: Invalid client secret` | The Secret **ID** was copied instead of the **Value**, or the secret has expired. |
| Sign-in succeeds, everything is 403 | The user has no role assigned. Enterprise application → Users and groups. |
| `AADSTS9002326` | The registration's platform is "Single-page application" instead of "Web". |
| It worked for months, then everyone is locked out overnight | The client secret expired (step 2). |

## What is deliberately not being asked for

- **No Microsoft Graph permissions.** The app needs to know who you are and what role you have; both are in the sign-in token. Graph access would let it read your directory, which it has no reason to do.
- **No `offline_access` / refresh tokens.** Sessions expire and sign-in is a redirect. Storing refresh tokens means storing long-lived credentials for every user, which is a real liability for a tool used a dozen times a month.
- **No certificate credential.** A client secret is simpler and appropriate here. Certificates are the better practice at larger scale, and the switch later is a configuration change rather than a code one.
