# M6 — Browser-only reconciliation: passwords, bucket input, revamped config

## Context

The system today is a verified pipeline (`src/`) with a service wrapper (`service/`), a
Next.js BFF (`web/`), and a CLI that is still the primary human entry point. Credentials
are pasted API tokens minted from a terminal, reconciliation input is files a human copies
into `input/<period>/<platform>/`, and the config editor is three text boxes that require
you to already know a dotted YAML path.

You want it to become an application: log in with a username and password, upload the
exports in the browser, run the window, and edit the rules through a real form with an
approval workflow. No terminal for users.

Five things change: **auth**, **input**, **the roster control**, **config**, and **sample
data**. The verification apparatus — the zero-tolerance golden gate, the four row-level
`calc_verify*` evidence scripts, `metrics_report.py` — stays, because it is the reason
anyone believes the numbers. It just stops being something a *user* touches.

The governing constraint does not change: **the committed tree's authority is provenance,
not correctness.** Structural changes must be output-identical; behaviour changes get their
own commit with the expected delta stated in advance.

> **Revision 2** (2026-08-17). Nine flagged concerns from revision 1 were reviewed and
> answered. Two design risks were **measured rather than assumed** and both came back
> favourable; two requirements were corrected. The net effect is that **no phase of M6 moves
> a golden digest** — see *What changed in revision 2*.

---

## Decisions locked

| Question | Decision |
|---|---|
| CLI | Browser-only for users. `tools/full_run.py` goes; golden/verification tooling stays as developer tooling |
| Roles | **Three**: `recon.viewer` (read-only), `recon.user`, `recon.admin`. `operator` is renamed `user`; the tier structure is unchanged from today |
| Credentials | Username + password (Argon2id), opaque server-side sessions. `api_tokens` deleted |
| Roster control | **Hard stop retained.** The per-run toggle is removed and replaced by a per-window, recorded declaration made at upload time |
| Input | Browser upload → MinIO bucket → materialised into the worker's scratch at run time |
| Config approval | `user` and `admin` request; `viewer` cannot. Only `admin` approves/rejects. Self-approval allowed and recorded |
| Config UI | Purpose-built widgets per section. **No JSON, no YAML, no dotted paths shown to a user** |
| Sample data | Seeded demo window, all three platforms, synthetic stores, its own pinned config version |
| Goldens | **Nothing in M6 moves a committed digest.** Any movement is a bug, not an expected delta |

`docs/11-OPEN-QUESTIONS.md` **OQ13** (who owns config, who approves) is now answered, which
closes **defect 2.7**. The object store closes **defect 2.4**.

---

## What changed in revision 2

| # | Revision 1 said | Now |
|---|---|---|
| 1 | Collapse to two roles; read-only is lost | **Three roles.** `recon.viewer` stays read-only. Since this is today's tier structure, the route-role table barely changes and the two tests that were going to be deleted survive |
| 2 | Runs proceed no matter what; roster becomes a report | **Hard stop retained.** Only the *per-run checkbox* goes. This removes the one phase that would have moved goldens |
| 3 | Typed widgets, described abstractly | Concrete widget per section, below. A user never sees a bracket |
| 4 | ruamel list-editing is unproven; fallback named | **Measured — all five operations are safe.** Fallback deleted |
| 5 | D26 tension "mitigated, not solved" | **Solved**: a goldens-affecting config change triggers an automatic re-verification run against a committed golden |
| 6 | Withdraw authorship hole | **Still open** — planned, not done. See the note below |
| 7 | NFC fix, own commit | Planned with a **measurement step first**, and a fingerprint migration if the count is non-zero |
| 8 | Migration 003 freezes on first apply | `recon_dev` will be dropped, so 003 can be iterated freely |
| 9 | Railway volumes force object storage | **Verified** (quoted below). api and worker are already separate containers; MinIO *is* the separate container |

**On #6 — a correction.** Revision 1 said the withdraw hole was "fixed in phase 2", meaning
*will be fixed*. It is not fixed. What was fixed earlier in this session was a different
thing: `create_app` now raises a `TypeError` when handed a repository that cannot
authenticate, instead of silently reporting every credential as invalid. The withdraw
authorship check is still outstanding and is planned below.

---

## Phase 0 — One live problem, before anything else

> **Corrected 2026-08-17.** Revision 1 listed a leaked password here, inherited from an
> earlier session's notes. **It was verified and the claim is false.** `deploy/` is entirely
> untracked (`git ls-files deploy/` returns nothing); `deploy/.env.example` contains
> `change-me`; the real password lives only in `deploy/.env`, which `.gitignore:35` covers;
> and the password appears in **no tracked file**. Nothing to fix. The only residual is that
> it appears in a chat transcript, and it guards a loopback-only test cluster running with
> `fsync = off` — rotate it whenever convenient, not urgently.

**`deploy/input/` holds 40 real client Shopee exports — 382 MB of customer PII — inside the
Docker build context.** Verified empirically, not inferred: a mimic build with
`.dockerignore` containing `input/` excluded `input/root.txt` and **included**
`deploy/input/nested.txt`. Docker anchors ignore patterns at the context root and compose
builds with `context: ..`, so `input/` excludes `<root>/input/` and not `deploy/input/`.
Git is unaffected — its patterns match at any depth — which is exactly what makes the
asymmetry easy to miss.

The Dockerfile has no `COPY . .`, only four explicit COPYs
([deploy/Dockerfile:35-38](deploy/Dockerfile#L35-L38)), so the data is **not** in an image
layer. It is transferred to and cached by the Docker daemon on every build.

Fix, in order:

1. `.dockerignore` → `**/input/`. **The same bug affects every other data pattern in that
   file** — `output/`, `artifacts/`, `.scratch/`, `.uploads/` are all root-anchored. Fix all
   five.
2. Delete `deploy/input/`, and point `RECON_INPUT_DIR` at a path outside the build context
   rather than at the compose file's own directory.
3. `docker builder prune` — the 382 MB is already in the local build cache from previous
   builds, and fixing the pattern does not evict it.
4. Workstream B dissolves this permanently by removing the input mount altogether.

Also in phase 0: **drop `recon_dev`** (per #8). Nothing in it is worth keeping, and it lets
migration `003` be iterated instead of frozen by
[service/db.py:68-73](service/db.py#L68-L73) on first apply.

---

## Migration numbering

[service/db.py:40-41](service/db.py#L40-L41) sorts by filename, so ordering is by number.
`001` and `002` are immutable.

| File | Contents |
|---|---|
| `003_password_auth.sql` | `users`, `user_sessions`; `drop table api_tokens` |
| `004_uploads_objects.sql` | `uploads` gains `store`, `store_canonical`, `object_key`, `consumed_by_run_id`; `kind` check; `windows` roster-declaration table |
| `005_config_multi_edit.sql` | `config_proposals` gains `edits jsonb`, `rebased_from`, generated `self_approved`; `config_versions` gains verification columns |
| `006_exception_nfc.sql` | Conditional fingerprint migration — see workstream F |

Each header must name what it supersedes in `002_m5.sql`, because 002 cannot be edited and
its comments would otherwise become uncorrectable lies. `003` in particular must open by
distinguishing the two hashes: `002_m5.sql:14-18` argues sha256-not-bcrypt is correct
*because tokens are never user-chosen*, and a password violates exactly that premise.

---

## Workstream A — Password auth (three roles)

**New:** `service/passwords.py`, `service/ratelimit.py`, `service/repository_identity.py`,
`003_password_auth.sql`. **Rewritten:** [service/auth.py](service/auth.py),
[service/admin.py](service/admin.py). **Amended:** [service/api.py](service/api.py),
[service/config.py](service/config.py).

### Roles (revised per #1)

```python
class Role(enum.Enum):
    VIEWER = "recon.viewer"   # rank 0 — read everything, change nothing
    USER   = "recon.user"     # rank 1 — upload, run, request config changes
    ADMIN  = "recon.admin"    # rank 2 — approve/reject, manage accounts
```

`operator` → `user` is a rename, not a restructure. It is nearly free *now* because
`api_tokens` is being dropped anyway, so there is no data to migrate and the check
constraint is written fresh in `003`. It touches
[docs/13-ENTRA-SETUP.md](docs/13-ENTRA-SETUP.md) (a plan, not a deployment),
`service/admin.py`'s aliases, `web/lib/api.ts`'s `Role` type, and ~26 test call sites.

**Consequences of keeping three tiers, all good:**

- The route-role table is **unchanged** except for the deleted `/tokens` routes and the new
  `/users` and `/sessions` routes.
- `test_a_viewer_cannot_upload` ([tests/service/test_uploads.py:278](tests/service/test_uploads.py#L278))
  and `test_a_viewer_cannot_propose` ([tests/service/test_config_editor.py:291](tests/service/test_config_editor.py#L291))
  **survive** — revision 1 was going to delete both.
- `test_every_mutating_route_requires_at_least_operator`
  ([tests/service/test_auth.py:288](tests/service/test_auth.py#L288)) stays **meaningful**
  rather than going vacuous. Rename the constant to `Role.USER` and keep the walk.
- A finance reviewer or auditor can be given an account that reads the invoicing numbers and
  cannot queue a run — the capability revision 1 was going to lose.

Still add the explicit `EXPECTED: dict[(method, path), Role]` table asserted in both
directions. It is no longer *load-bearing*, but it makes a role regression fail a test
instead of passing silently.

### The rest of workstream A (unchanged from revision 1)

- **Hashing:** `argon2-cffi` in `[project.optional-dependencies].service` (never core —
  [tests/service/test_service_is_deletable.py:137](tests/service/test_service_is_deletable.py#L137)
  enforces that, and its package tuple must be extended). Argon2id at **m=19456 KiB, t=2,
  p=1**, hardcoded. Not env-configurable: lowering the cost makes `check_needs_rehash`
  *downgrade* every strong existing hash on its owner's next login. m=19 MiB rather than the
  64 MiB default because FastAPI runs sync handlers in a 40-thread pool and the worker's
  4 GB limit is the denominator of a documented memory trigger — 40×64 MiB is 2.5 GB of
  hashing. A `threading.Semaphore(4)` bounds it further.
- **Sessions:** API mints an opaque `recon_s_…` token on `POST /sessions`; the BFF keeps it
  in the httpOnly cookie and keeps sending `Authorization: Bearer`. **The API never reads a
  cookie** — CSRF against the API is structurally absent rather than mitigated, and
  `make_client`/`curl` keep exercising the real path.
- **Role resolved by join, never copied onto the session row.** `principal_for_session` puts
  every liveness condition — signed out, idle, absolute expiry, owner disabled — in one
  `UPDATE … WHERE`, the discipline
  [service/repository_m5.py:51-73](service/repository_m5.py#L51-L73) already uses. A demoted
  admin loses admin on their next request, not at sign-out.
- **Passwords are generated, never admin-chosen**, paired with `must_change_password`
  enforced in `require()` (not the UI). Every audit column this service exists to make
  trustworthy — `requested_by`, `proposed_by`, `decided_by` — is only evidence if
  impersonating a colleague is hard.
- **Bootstrap:** `python -m service.admin user create`, needing `RECON_DATABASE_URL`. Not a
  network surface. `user reset-password` is **break-glass** and is why the CLI cannot be
  deleted once the admin UI exists — say so in the docstring, because that cleanup will look
  obviously correct later.
- **Throttle keys on username, not IP** — every request reaches the API from the BFF's
  address, so an IP limit throttles the whole company as one attacker. Per-app, so tests are
  not order-dependent. Lockout short and self-clearing.
- **Uniform 401** across unknown user / wrong password / disabled, *and uniform wall-clock*
  via `verify_dummy()` on the unknown-username branch.
- **Fix the withdraw hole (#6):** `POST /config/proposals/{id}/withdraw`
  ([service/api.py:598](service/api.py#L598)) is role-gated only, so any signed-in caller can
  withdraw anyone's proposal. Add `proposal["proposed_by"] == principal.subject or
  principal.can(Role.ADMIN)`.

**Web:** `SESSION_COOKIE` → `recon_session`; export `apiBase()` (killing the duplicate at
[web/app/runs/[id]/download/[name]/route.ts:22](web/app/runs/[id]/download/[name]/route.ts#L22));
username+password form with correct `autoComplete`; new `/account/password` and
`/admin/users`; `middleware.ts` as a **cookie-presence pre-filter only**.

**`signOut` must start revoking server-side.** Today it only deletes the cookie
([web/app/actions.ts:54-58](web/app/actions.ts#L54-L58)) — under sessions that leaves a valid
credential alive for up to 12 hours. The code looks correct and the manual test looks
correct; only an API-level test catches it.

Role conditionals in `web/` stay (three roles), but each `"recon.operator"` becomes
`"recon.user"`.

---

## Workstream B — Bucket input and uniform naming

**New:** `service/objects.py`, `service/naming.py`, `service/materialize.py`,
`004_uploads_objects.sql`. **Amended:** [service/uploads.py](service/uploads.py),
[service/artifacts.py](service/artifacts.py), [service/worker.py](service/worker.py),
[service/api.py](service/api.py), `deploy/`.

- **`boto3`** (not `minio`) in the service extra — S3's vocabulary is what Railway, R2 and S3
  all speak, and ~50 MB is noise next to pandas. Two buckets: `recon-uploads` (short
  retention + lifecycle expiry — the promise `docs/04-DATA-FLOW.md` already makes and has
  never had a mechanism) and `recon-artifacts`. A `minio-init` one-shot creates them and
  mints a **scoped** service account so the app never holds root credentials.
- **`ArtifactStore` grows exactly one method: `stream()`.**
  [service/api.py:337-341](service/api.py#L337-L341) currently 501s for any non-`file:` URI;
  it becomes `open()` → `FileResponse`, else `stream()` → `StreamingResponse`.
  **Deliberately not presigned URLs:** a presigned URL is a bearer credential in a query
  string that `service/auth.py` never sees, and for its lifetime anyone holding the link
  downloads a workbook containing every store's revenue.
- **Stop letting the upload path borrow the artifact store.**
  [service/api.py:447](service/api.py#L447) reuses `ArtifactStore.open()` to read an upload;
  that conflation is the root of the api-has-no-input-mount defect.

### The uniform naming scheme

```
tiktok  orders   NNN.order <store>.xlsx     shopee  orders   NNN_order. <store>.xlsx
tiktok  income   NNN.income <store>.xlsx    shopee  income   NNN_income. <store>.xlsx
lazada  weekly   NNN_<store>.xlsx           lazada  daily    NNN_<store>.xlsx
```

`NNN` = zero-padded 3-digit ordinal, unique within `(period, platform, kind)`.

**Measured, not reasoned about.** Store identity is derived from the filename in three places
by two `.xlsx$`-anchored regexes ([src/ingest.py:152-160](src/ingest.py#L152-L160),
[src/lazada.py:105](src/lazada.py#L105),
[tools/stage_exports.py:219-229](tools/stage_exports.py#L219-L229)), all hard-stopping.
Simulated over the real `input/` tree: `derive(uniform(derive(x))) == derive(x)` for **73/73
files** across all 8 committed golden windows, `sorted(new_names)` preserves the original
`sorted(iterdir())` order in every folder, and all 166 filenames are already NFC.

1. **Nothing is appended after the store name.** TikTok's regex eats a trailing bare 1–2
   digit token; Shopee's eats ` part N`. The ordinal goes in the *prefix*, which TikTok
   already requires.
2. **Fixed-width padding keeps `sorted()` numeric**, so `read_parts` and `read_ledger` see
   today's file order. Order feeds `pd.concat`, which feeds workbook row order — the
   non-obvious property that makes the rename output-identical.
3. **Lazada's `(N)` marker disappears.** Five weekly exports of one store become `001_…` to
   `005_…`, and `norm_store` stops yielding `"<s> (0)"`.
4. **Always `.xlsx`**, fixing the latent bug where a `.csv` upload lands as openpyxl bytes
   under a `.csv` name.

`service/naming.py::validate_roundtrip` re-runs the platform's own regex on every generated
name and refuses if the store does not survive — turning "the rename is a fixed point of the
pipeline's parser" into a machine-checked invariant that catches any future edit to
`store_from_filename`.

**The ordinal is assigned at materialisation, not at upload.** The name the pipeline sees
*is* uniform and generated; but the ordinal is a property of the whole window, so assigning
it at upload either races between concurrent uploads or lets arrival order decide read
order, destroying property (2) and with it byte-reproducibility. Object keys are
content-addressed (`<period>/<platform>/<kind>/<sha256>.xlsx`); the uniform name is computed
per run from the window's sorted original names; the upload UI shows it with the ordinal as
a greyed `NNN`.

**Store identity at upload:** derived with the pipeline's own regex (exposed as a public
`src.ingest.store_from_filename`, so there is never a second copy), checked against
`expected_stores ∪ store_aliases`, and **confirmed or corrected by the operator** before
submission. A genuinely new store opens a config proposal rather than bypassing the roster.

**Materialisation** (`service/materialize.py`, in `service/` so `src/` stays driver-free and
[tests/test_io_boundary.py](tests/test_io_boundary.py) needs no new grant): the worker
downloads the window into `scratch/job-N/input/` before `build_context`, writes a
`materialized.json` provenance record, and marks each upload `consumed`. `run()` still writes
nothing. **The local-disk mode stays** (`settings.s3 is None`), which is what lets every
existing worker test — including the two importing `tools.smoke_test.build_window` — keep
passing verbatim.

`POST /uploads/{id}/stage` is **deleted**: the bucket is the store, so there is nothing to
move, and its two defects (no collision guard, borrowing `ArtifactStore.open`) go with it.

### Why object storage rather than another container (#9)

`api` and `worker` are **already separate containers**. The problem was never process
separation — it is that the worker writes artifact bytes and the api serves them, so they
need shared bytes. Railway's docs, verified:

> **"Each service can only have a single volume"**

with no cross-service mounting. So a shared filesystem is not expressible for two services.
The alternatives:

| Option | Verdict |
|---|---|
| **Object storage (MinIO/S3)** | **Chosen.** MinIO *is* the separate container — both services reach it over the network. Also the only option that makes the browser download path work in the target deployment |
| api + worker in one container | Would share a local dir, but costs `--scale worker=N`, couples restarts, and fights the deliberate one-job-per-process design (`_vat_sku` is a mutable back-channel) |
| Worker exposes HTTP, api proxies | Reinventing object storage, worse |
| Artifact bytes in Postgres | Viable at 5–30 MB, but puts binary blobs in the run database and adds a second storage story |

So `S3ArtifactStore` is in scope, not a nice-to-have: without it
`GET /runs/{id}/artifacts/{name}` 501s in the exact deployment being targeted.

---

## Workstream C — Roster control (revised per #2)

**Hard stop retained.** [src/ingest.py:290-312](src/ingest.py#L290-L312) `check_stores` keeps
raising `ReconHardStop` for both missing and unexpected stores. `apply_partial_roster`,
`build_context(partial_roster=...)` and `tools/make_golden.py --partial-roster` all **stay**
— they are developer tooling, and a subset golden must still be generatable and still be
labelled as a subset in the manifest.

What goes is the **per-run user-facing toggle**:

- `EnqueueRequest.partial_roster`, `jobs.partial_roster`, `web/app/queue-form.tsx`'s
  checkbox and the board's `partial` badge are deleted.
- It is replaced by a **per-window declaration**, recorded once: the upload screen shows
  roster completeness ("12 of 25 expected TikTok stores have files"). Queuing a run for an
  incomplete window requires an explicit acknowledgement **with a reason**, stored in a new
  `windows` table (`platform, period, roster_declared_partial, reason, declared_by,
  declared_at`) and shown on the board.
- The worker reads the declaration and passes `partial_roster` through as it does today. The
  plumbing is unchanged; only the *source* of the fact moves, from a checkbox someone ticks
  every run to a statement someone makes once, with a reason, that a reviewer can see.
- Without a declaration, an incomplete window **hard-stops** — which is today's behaviour and
  the control that caught a real Shopee window arriving with 16 of 17 stores missing.
- Unexpected stores stay a hard stop *and* are refused at the upload door, so the hard stop
  becomes a backstop rather than the first line.

**This is what removes the only golden-moving phase.** `check_stores` behaviour is
untouched, so no finding, no `variance_count` and no digest moves. `runs.roster_missing`
is still added, purely as a rendered count. `tests/goldens/manifest.json` is not regenerated
at all, and `test_rebaseline_guard.py` keeps its `partial_roster` parametrize case.

`tests/service/test_worker.py:271-282` (which asserts the `PARTIAL ROSTER:` line exists and
that its kind is `warning`) **survives** — the warning still fires, it is just sourced from
the window declaration.

---

## Workstream D — Config editor

**New:** `service/config_schema.py`, `service/config_edits.py`,
`005_config_multi_edit.sql`, components under `web/app/config/`.
**Amended:** [service/config_store.py](service/config_store.py),
[service/api.py](service/api.py). **Deleted:** `web/app/config/propose-form.tsx`.

### The objection has to be answered, not ignored

[web/app/config/page.tsx:12-23](web/app/config/page.tsx#L12-L23) and `propose-form.tsx:7-15`
both argue *against* form-rendering this file: "A form would show values stripped of the
evidence for them." Half of `config/settings.yaml` is comments and they are the audit trail.

The answer: **evidence is extracted, never copied.** A new
`config_store.evidence_for(content, path)` reads ruamel's `.ca` comment attribute for each
key out of *the same bytes the form is editing*, and each field renders its comment block
verbatim above its input. The four-line VAT block sits directly above the box you type
`1.10` into — strictly more evidence at the point of decision than the current UI, where
that comment is 400 lines down a `<pre>` nobody scrolls. The verbatim file stays on the
page. Every field names the module that reads it. This becomes **D42**, and the now-false
comments in `page.tsx`, `propose-form.tsx`, `config_store.py` and `api.py` are rewritten in
the same commit.

### ruamel mechanics — verified, not assumed (#4)

Probed against the real 411-line file:

| Operation | Result |
|---|---|
| Load → dump, no edit | **byte-identical**, 200 comment lines preserved |
| Append to `expected_stores.tiktok` | interleaved comments stay at offsets 20/28/29; everything outside the block byte-identical |
| Append **with** an attached note | written as an EOL comment on the new item; outside the block byte-identical |
| Add a new `store_aliases.tiktok` key + note | preserved; outside the block byte-identical |
| Remove a commented item from `stores_optional.tiktok` | 200 → 199 — **the comment goes with the item**, which is the desired semantics |
| Set `vat_factors.default` | exactly one changed line (15) |

The whole-list-retype fallback from revision 1 is **deleted**. The list and mapping editors
can be built directly. Keep these six as regression tests in
`tests/service/test_config_editor.py` — they are the reason the design is safe.

### The widgets — no user ever sees a bracket (#3)

The requirement said "text input". A bare text input is the wrong affordance for two thirds
of this file, and for a non-technical user it is worse than what exists. Every section gets
a purpose-built control instead:

| Section | What the user sees | Never |
|---|---|---|
| **Store roster** (`expected_stores`, `stores_optional`) | A table of stores, one per row, with an ✕ to remove and an "Add store" row. An "optional" checkbox per store instead of a second list | A list literal |
| **Store aliases** (`store_aliases`) | Two labelled columns — *"Name as it appears in the file"* → *"Real store"* — with the right side a **dropdown of the roster**. Adding a row requires a *why* note, which is written into the file as a comment | `{key: value}` |
| **Column maps** (`column_maps`) | Two labelled columns — *"Header in the export"* → *"What the pipeline calls it"* — with the right side a **dropdown of the closed set of canonical fields** (`order_id`, `sku_id`, `gross_revenue`, …). The user picks; they never type a canonical name | Nested mappings |
| **Settlement windows** (`window_settlement_bounds`) | A window picker plus two date pickers | ISO strings in a box |
| **Money tolerances** | Number inputs labelled *VND*, with thousands separators | Raw ints |
| **Enums** (`numeric_coercion`, `number_style`, `reader_engine`) | Radio buttons with plain-English labels — *"Stop the run"* vs *"Warn and keep going"* | The literal enum value |
| **Booleans** (`dedupe_rows`, `dayfirst`) | A toggle with a sentence stating what on and off each mean | `true` / `false` |
| **Filename patterns** (`store_from_filename`) | The pattern, plus a **live tester**: paste a filename, see which store it finds. Pre-seeded with the real filenames from the field's own comment block | A regex with no way to check it |

The dotted path is never shown; it exists only in the wire format. The canonical-field
dropdown for column maps is the single biggest usability win here — it turns "know the
pipeline's internal vocabulary" into "pick from a list".

Two fields stay **locked**, and I still recommend this against the literal request:

- **`drop_unmapped_columns`** is the PII control in two places
  ([src/ingest.py:224](src/ingest.py#L224) and the upload sanitizer). Its diff reads as an
  ordinary boolean flip, not as "customer names and addresses now enter the pipeline". A
  privacy incident should not be two clicks. Renders disabled with its reason.
- **`vat_rate`** and **`periods.rolling_window_months`** are read by **nothing**. A control
  on a dead key invites an edit that appears to work and changes no behaviour. Render
  read-only as "nothing reads this"; delete them from the file in a separate commit.

### Multi-edit proposals

`edits: [{op, path, value?, key?, comment?}]` with five ops (`set`, `set_map_entry`,
`remove_map_entry`, `append_list_item`, `remove_list_item`). `apply_edits` parses once,
mutates one `CommentedMap`, dumps once — which is what preserves byte-identity under N
edits. `apply_edit` stays as a shim so the twelve existing canary tests keep testing the
same thing.

The honesty rule gets **stricter**, not looser. `apply_edit`'s "the key must already exist"
is a proxy; the schema states the real property: new keys are permitted only where a
container is declared `open_mapping` or `list_of_str`, **and that declaration names the
reader that loops over it**. `vat_factors` is `mapping_of_scalar` because
`src/masters.py:144` reads exactly `.get("default")`, so adding a key there is refused where
today it would be allowed.

`POST /config/proposals/{id}/rebase` replays stored edits against current bytes into a new
pending proposal. **Not a merge** — D38 refuses a three-way merge of a file whose comments
are evidence, and it is right.

### Approval, under three roles

`RECON_CONFIG_APPROVAL`, `ApprovalPolicy` and `ApprovalDenied` are **deleted** — they existed
only because OQ13 was unanswered. `recon.user` and `recon.admin` propose; **`recon.viewer`
cannot** (so `test_a_viewer_cannot_propose` survives). Only `recon.admin` approves, rejects
or applies. **Self-approval is allowed and recorded** via a generated
`config_proposals.self_approved`: forbidding it deadlocks a single-admin prototype and
pushes the edit back to hand-editing `settings.yaml`, which has no audit trail at all.
Approve and Apply stay two buttons.

Closing defect 2.7 is only honest with the caveat attached: the control is **recorded
evidence**, not separation of duties.

### The D26 fix (#5) — measure, don't assume

The tension: a cheap editor means more config commits, and a `column_maps` edit can move
workbook cells. `oracle_rev` was killed in M1 (D26) because keying manifests on a hash of
`src/` + `config/` orphaned every golden on every edit — it **assumed** any config change
invalidated every golden, so the gate silently degraded to a skip.

The fix inverts that: **measure whether the change actually moved anything.**

1. Each schema field carries `invalidates_goldens: bool` (true for `column_maps`,
   `store_from_filename`, `sheet_names`, `header_rows`, `skip_rows_after_header`,
   `reader_engine`, `vat_factors`, `dedupe_rows`).
2. On **apply** of a proposal touching any such field, the service enqueues a
   **verification run**: a designated canary window per platform, whose uploads live in the
   bucket and whose golden digest is committed, is re-run under the *new* config and its
   workbook compared cell-for-cell against the committed digest.
3. The outcome is written to `config_versions` (`verification_state`, `verified_window`,
   `cells_moved`) and shown on the config page: **"verified — no cells moved"**, **"N cells
   moved in 2026-05_l1 — the goldens need a deliberate re-baseline"**, or **"not verified —
   no canary window available"**.
4. Nothing is blocked. The change lands; the system tells you what it did.

This is the property `oracle_rev` wanted and could not deliver: a config change is linked to
its actual effect on the numbers, not to a hash. Most changes — a tolerance, a store alias,
a roster addition — will move nothing and say so. The residual (no canary window available,
so no claim can be made) is recorded honestly rather than silently.

---

## Workstream E — Sample data

**New:** `service/sampledata.py` (in `service/` so the browser can seed it in a deployed
container), `tools/make_sample_data.py` as a thin CLI, `POST/DELETE /demo/seed` (admin).

Deterministic (`random.Random(SEED)`, one ordered pass), verified by comparing **cellset
digests** across two generations — never file hashes, since openpyxl stamps timestamps.
Synthetic store names including one Vietnamese (`Demo Đông Á`, exercising `SAFE_FILENAME`'s
`À-ỹ` class), with a test asserting no generated name appears in `config/settings.yaml`.

**The synthetic roster is made legitimate through the existing pin mechanism:** build a demo
settings text via ruamel round-trip (every comment survives), record it as a
`config_version`, and `pin_period_config` it to the demo window. `config/settings.yaml` on
disk is never touched, and the real rosters never apply to the demo — so the demo runs
cleanly under the hard stop that workstream C keeps.

Per-platform traps that otherwise silently produce wrong data:

- **TikTok** needs one junk row immediately under the orders header, because
  `skip_rows_after_header.tiktok.orders: 1` drops it. Omit it and every row shifts.
- **Shopee** needs two band rows above the leaf header (`header_rows: 3`), two sheets
  matching the `Doanh thu` regex, and **the 1-VND crossing derived, never randomised** —
  generate the five components as integers, then set the total to their exact sum. Emit one
  order file with NFD headers, because real exports do.
- **Lazada** fee names must come from `config/lazada_fee_types.csv` (the discipline
  `smoke_test.py:_fee_names()` already uses) or revenue tabs come out empty.

Keep the deleted generator's anomalies: ~15% prior-month settlement, ~8% fully returned,
~5% zero-revenue, 2 ghost income lines, one SKU absent from the VAT master, a duplicate
across Shopee parts, one deliberate tolerance breach and one deliberate unmapped fee. The
demo should land as VARIANCE with a populated exception queue — **an empty exception queue
teaches nothing.** `tools/smoke_test.py` is left alone; its two test importers keep working.

**E is self-verifying and independent of D.** Its own gate is the determinism test — two
generations compared by cellset digest — which needs nothing from the config-verification
feature. Build it whenever; it does not wait on anything and nothing waits on it.

It also serves as the **fallback canary** for the D26 verification run, for a deployed
container that has no client data. Fallback, not first choice: see remaining concern 3 for
why a synthetic canary is the weaker claim and must be labelled as such.

---

## Workstream F — NFC normalisation (#7)

[service/exceptions.py](service/exceptions.py)`::_norm` never NFC-normalises, so an identity
value changing Unicode form silently orphans every stored fingerprint and detaches its
history. Three steps, own commit:

1. **Measure first.** A read-only script reports, as counts only and never values:
   - `run_exceptions` rows whose stored identity values are non-NFC;
   - non-NFC values among `expected_stores` / `store_aliases` in `settings.yaml`;
   - non-NFC `fee_name` values in `config/lazada_fee_types.csv` and the live `.xlsb`.

   Revision 1's "0 impact" claim was measured over **filenames** (166/166 NFC). It says
   nothing about `fee_name`, which comes from Vietnamese Lazada exports and is a real
   candidate for NFD. Do not assume it carries over.
2. **Change `_norm`** to `unicodedata.normalize("NFC", …)`, matching what
   `ingest.py:211` and `uploads.py:132` already do to headers.
3. **Migrate if the count is non-zero.** `006_exception_nfc.sql` recomputes and updates
   affected `run_exceptions.fingerprint` values in the same commit, so history is carried
   rather than orphaned. If the count is zero, the migration is a no-op and says so.

The consequence is stated in the commit message either way, with the measured number.

---

## Sequencing

| Phase | Content | Goldens |
|---|---|---|
| **0** | Fix `.dockerignore` (all five patterns); delete `deploy/input/`; prune the build cache; drop `recon_dev` | none |
| **1** | `tools/devrun.py` replaces `full_run.py`'s `build_context` for `make_golden.py:46` and `smoke_test.py:120`; **then** delete `full_run.py`. Demote `stage_exports.py` to dev tooling | none |
| **2** | Workstream A — passwords → ratelimit → migration `003` → auth.py → repository → api → route table → renames → admin CLI → web | none |
| **3** | Workstream B — objects/stream/S3 store, `naming.py` + tests, upload boundary, materialisation, compose | none — this is the proof |
| **4** | Workstream C — window roster declaration, remove the per-run toggle | none |
| **5** | Workstream D (config) and E (sample data), independent of each other | none |
| **6** | Workstream F (NFC), measured then applied | none |
| **7** | Docs, one commit with the code | none |

Phase 1 must precede everything: `tools/make_golden.py:46` does
`from full_run import build_context`, so deleting that file first breaks the golden gate.

**No phase moves a committed golden digest.** If one moves, it is a bug — not an expected
delta to be re-baselined away.

---

## Verification

```bash
PY="$LOCALAPPDATA/recon-venv/Scripts/python.exe"
export RECON_TEST_DATABASE_URL="postgresql://recon:$(cat "$LOCALAPPDATA/recon-pg/pgpass.txt")@127.0.0.1:55432/recon_test"

"$PY" -m pytest -m "not slow"          # fast inner loop
"$PY" -m pytest                        # full; baseline today is 435 passed, 3 skipped
"$PY" -m pytest tests/goldens -q       # zero-tolerance workbook gate
"$PY" -m pytest tests/test_io_boundary.py -q
cd web && npm run typecheck && npm test
```

**Gates that decide whether this worked:**

1. **`tests/test_io_boundary.py` must need no change.** If it does, the design is wrong —
   materialisation belongs in `service/`, not `src/`.
2. **`tests/goldens` must be bit-identical throughout.** There is no phase where movement is
   expected, which makes this the sharpest available gate on the whole change.
3. **The upload golden gate, extended to all three platforms.**
   [tests/service/test_uploads.py:140-182](tests/service/test_uploads.py#L140-L182) is the
   strongest existing gate — sanitize a real window, run the pipeline, match the committed
   digest cell for cell at zero tolerance. It currently covers **Lazada only** and does not
   exercise the rename at all. Extending it to sanitize **and rename** across
   `2026-05_l1`/lazada, `2026-05_w1`/tiktok and `2026-05_s1`/shopee is what makes the naming
   scheme safe. It skips silently without local client data, so add a coverage test gated on
   `RECON_REQUIRE_CLIENT_DATA` that **fails** on the machine holding `input/`.
4. **The six ruamel canaries** from the probe above, as permanent regression tests.
5. **End-to-end by hand, in a browser.** Bootstrap an admin, sign in, get bounced to change
   the password, create a `recon.viewer` and a `recon.user`, confirm the viewer can read and
   cannot upload, seed the demo window, upload a file, run it, watch the log, download the
   workbook, request a config change, approve and apply it, and confirm the verification run
   reports "no cells moved" **and names the window it used**.

   **Defect 2.8 is already closed** — the web UI was opened and exercised by hand on
   2026-08-17, and what that session found is the origin of this revamp's scope, not a task
   still outstanding. Update `docs/08-KNOWN-DEFECTS.md` accordingly. The pass above is not
   re-closing 2.8; it is the equivalent inspection for the surfaces M6 *adds* — login,
   accounts, upload, the sectioned config editor — none of which exist yet and none of which
   an automated test can sign off.

---

## Remaining flagged concerns

Revision 1 raised nine; seven are resolved above. What is left:

1. **`drop_unmapped_columns` stays locked** against the literal request. It is the PII
   control and its diff does not look like one.
2. **The window roster declaration is still a human judgement.** It is better than a per-run
   checkbox — recorded once, with a reason, visible to a reviewer — but somebody can still
   declare a partial window carelessly. The stronger version is stamping
   `ROSTER: n expected store(s) absent` into the finance workbook's control block so the
   artefact the team invoices from carries its own caveat. That **moves workbook cells** and
   needs its own commit and rebaseline. Deferred deliberately, not smuggled in.
3. **A canary window's strength varies, and the result must name which one answered.**
   There is **no build-order constraint** between workstreams D and E — the dependency is at
   runtime, not build time, and the "not verified" state covers the gap in either order. The
   sample data can be created long before anything verifies it.

   What matters is *which* window answered. A real committed golden (`2026-05_l1`) exercises
   the real column maps, sheet names and header spellings. The synthetic demo window only
   exercises the paths its own generator emits — so a `column_maps` edit for a header the
   generator never writes would move nothing in the demo and everything in production. A
   green result from the demo is a genuinely weaker claim.

   So the verification run resolves its canary in order: a **real** window whose uploads are
   in the bucket, else the **demo** window, else **none**; and the stored result records
   which. "Verified against 2026-05_l1", "verified against the demo window" and "not
   verified" are three different statements and the UI must not render them as one. In
   practice the dev machine holds the real windows and gets the strong gate; a deployed
   container gets the weak one and says so.

## Out of scope

Entra ID SSO (still blocked on the tenant app registration in `docs/13-ENTRA-SETUP.md`);
the polars port (descheduled, D25 — trigger-gated); the workbook control-block stamp; D37's
pin-on-first-*run* asymmetry.
