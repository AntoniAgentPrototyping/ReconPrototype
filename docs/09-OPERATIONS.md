# 09 — Operations

How to set up, run, and interpret. For what the stages do, see [03-PIPELINE](03-PIPELINE.md).

## Environment

One venv, **outside the project folder** (it is cloud-synced and sync contention is a documented cause of 45-minute runs — see [D21](06-DECISIONS.md#d21)):

| Venv | Contents | Purpose |
|---|---|---|
| `%LOCALAPPDATA%\recon-venv` | Python 3.12.10, pandas 2.3.3 | Everything: runs the pipeline, the suite, and golden generation |

Python 3.12 was installed via the Python install manager (`py install 3.12`), which is user-local and reversible (`py uninstall 3.12`). It is required because pandas 2.x has no 3.14 wheels — a temporary bound, not a permanent one ([D20](06-DECISIONS.md#d20)).

```bash
py -3.12 -m venv "$LOCALAPPDATA/recon-venv"
"$LOCALAPPDATA/recon-venv/Scripts/python.exe" -m pip install -e ".[dev]"
```

Dependency pins in `pyproject.toml` are controls — read [D20](06-DECISIONS.md#d20) before changing them.

> **Retired 2026-08-12.** There used to be a second venv (`recon-polars-venv`, Python 3.14 + polars) for a compute-engine migration. That migration was descheduled before any polars was written ([D25](06-DECISIONS.md#d25)); the venv is deleted and the "run the suite in both venvs" rule no longer applies.

## Commands

```bash
PY="$LOCALAPPDATA/recon-venv/Scripts/python.exe"

# --- Verify the environment, no real data needed -------------------------
"$PY" tools/smoke_test.py            # synthetic end-to-end through pipeline.run()
"$PY" -m pytest                      # full suite

# --- Tests --------------------------------------------------------------
"$PY" -m pytest tests/goldens -q                                 # workbook regression gate
"$PY" -m pytest tests/test_io_boundary.py -q                     # I/O-boundary lint
"$PY" -m pytest tests/test_tieout_blindness.py::test_clean_data_passes_all_checks
"$PY" -m pytest -k rounding                                      # by keyword

# --- Production: one window end to end ----------------------------------
"$PY" tools/devrun.py --platform tiktok --period 2026-05_w1 [--refs refs.json]

# --- Monthly master (after all windows of a month exist) ----------------
"$PY" tools/build_master_summary.py --month 2026-07 --out "<path>.xlsx"

# --- Row-level verification against the team's own files ----------------
"$PY" tools/calc_verify.py            # TikTok   (each has a usage docstring)
"$PY" tools/calc_verify_shopee.py
"$PY" tools/calc_verify_lazada.py

# --- Diagnostics --------------------------------------------------------
"$PY" tools/stage1_probe.py --platform shopee --period 2026-05_s1   # ingest only
"$PY" tools/verify_july_aliases.py                                  # alias overlap evidence

# --- Staging: plans by default, copies only with --apply -----------------
# Windows are DERIVED from each export's own settlement dates, so there is no
# folder-name -> window table to maintain. Read the plan, then apply it.
"$PY" tools/stage_exports.py --platform shopee                 # plan, copies nothing
"$PY" tools/stage_exports.py --platform shopee --apply         # stage it
"$PY" tools/stage_exports.py --platform tiktok --pattern "*Mars*" --period 2026-05_w1 --apply
#   --period  stage everything matched into ONE named window (a deliberate
#             subset, e.g. one store for a golden) instead of deriving
#   --pattern filename glob, for when a dump holds more than you want

# --- Goldens ------------------------------------------------------------
"$PY" tools/make_golden.py   --platform lazada --period 2026-05_l1
"$PY" tools/make_golden.py   --platform tiktok --period 2026-05_w1 --partial-roster

# Moving a baseline is deliberate and reviewable — never a side effect.
# Without --rebaseline, a run whose digests differ REFUSES and points at the differ.
"$PY" tools/make_golden.py --platform lazada --period 2026-05_l1 --rebaseline --reason "VAT revert 8% -> 10%"

# --- Is pandas running out of road? (the engine-port trigger) -----------
"$PY" tools/metrics_report.py --month 2026-05 --container-mb 4096

# --- Production, one window end to end, with a SUBSET of the roster ------
# Until M4 this needed a config edit, which relaxed the check for every later
# run too. Now it is a property of the run ([D23](06-DECISIONS.md#d23)); the
# unexpected-store check stays armed and the log says loudly that the totals
# are not the month's.
"$PY" tools/devrun.py --platform tiktok --period 2026-05_w1 --partial-roster
```

Expected suite baseline: **`167 passed, 3 skipped, 0 xfailed`** without a database, **`435 passed, 3 skipped`** with `RECON_TEST_DATABASE_URL` set. The xfail count is the number worth watching — every pinned control gap is now closed, so an empty list is the correct state rather than a missing one ([D22](06-DECISIONS.md#d22)). One test is marked `slow` (~195s, reads every raw export); `-m "not slow"` is the fast inner loop.

## The M4 service

`service/` is a wrapper around the same pipeline: an api that queues work, a worker that executes it, Postgres for the queue and the run record. **It is deletable** — everything above still works with the directory gone, which is the point ([D24](06-DECISIONS.md#d24)).

```bash
"$PY" -m pip install -e ".[dev,service]"          # fastapi · uvicorn · psycopg

export RECON_DATABASE_URL="postgresql://recon:<pw>@127.0.0.1:5432/recon"
"$PY" -m service.api                              # 127.0.0.1:8080, migrates on start
"$PY" -m service.worker                           # loop; --once or --drain

# Queue a window that is ALREADY STAGED on the worker's input root
curl -s localhost:8080/jobs -H 'content-type: application/json' \
     -d '{"platform":"lazada","period":"2026-05_l1","requested_by":"antoni"}'

curl -s localhost:8080/healthz
curl -s "localhost:8080/runs/1/log?after_seq=-1"  # poll with the next_seq it returns
curl -sO localhost:8080/runs/1/artifacts/finance_file.xlsx
```

### Signing in

Every endpoint is authenticated since M5. The first token cannot come from the api — `POST /tokens` needs an admin token — so it comes from the CLI, which needs the database URL. Issuing an identity should need *more* access than using one:

```bash
"$PY" -m service.admin user create --username antoni@ada --role admin
"$PY" -m service.admin token list                 # `last used` is how you spot the one to revoke
"$PY" -m service.admin token revoke 3             # effective on the next request

curl -s localhost:8080/me -H "Authorization: Bearer recon_…"
```

| Role | May |
|---|---|
| `recon.viewer` | read the board, runs, logs, exceptions, config |
| `recon.user` | …plus queue and cancel runs, upload exports, declare a partial roster, propose config changes |
| `recon.admin` | …plus issue tokens, approve and apply config changes, pin and unpin windows |

The api **refuses to start** on a non-loopback host with `RECON_AUTH_DISABLED` set. That combination can queue settlement runs, read client revenue and rewrite the config the money math uses, so it is an error rather than a warning ([D36](06-DECISIONS.md#d36)).

Still open: SSO. Bearer tokens are issued and revoked by hand, with no central "this person has left" event — see [13-ENTRA-SETUP](13-ENTRA-SETUP.md) for the permissions to request.

### The web app

```bash
cd web && npm install && npm run dev        # http://localhost:3000
```

Set `RECON_API_URL` if the api is not on `127.0.0.1:8080`. **You sign in with a username and password since M6** — there is no token to paste. The api mints an opaque session, the Next.js server keeps it in an httpOnly cookie and attaches it to API calls **server-side**, so the browser never holds a credential that can queue a settlement run ([D41](06-DECISIONS.md#d41)). The api reads only the `Authorization` header and never a cookie, which is why CSRF against it is structurally absent rather than mitigated.

A new account's password is **generated**, shown once, and must be changed at first sign-in: an admin who picks your password can be you, and every audit column here — `requested_by`, `proposed_by`, `decided_by` — is only evidence if impersonating a colleague is hard.

**Still no browser automation** ([defect 2.8](08-KNOWN-DEFECTS.md)). The old screens were exercised by hand on 2026-08-17 — that session is the origin of M6's scope — but the screens M6 adds have the same limit: they type-check, their container serves, and the API beneath them is covered by ~480 tests.

### The whole stack in containers

Brought up and exercised end to end on Docker **29.7.2**, 2026-08-17 ([07-VERIFICATION](07-VERIFICATION.md)).

```bash
# The real .env lives OUTSIDE the tree (C13): this folder is OneDrive-synced,
# and a sync client ships credentials to a cloud tenant regardless of gitignore.
cp deploy/.env.example "$LOCALAPPDATA/recon-deploy/.env" && $EDITOR "$LOCALAPPDATA/recon-deploy/.env"
cd deploy && docker compose --env-file "$LOCALAPPDATA/recon-deploy/.env" up --build

# The first identity. Note this is the CLI, not the api: creating an account
# needs an admin credential, so the first one cannot come from the api.
docker compose exec api python -m service.admin user create \
    --username you@ada --role admin
```

Only `web` is published, on `127.0.0.1:3000`, plus the MinIO **console** on `:9001` for an operator to look at a bucket. The api, worker, database and MinIO's S3 API stay on the private network — defence in depth on top of authentication, not a substitute for it. In particular the S3 port is deliberately unpublished: uploaded exports are raw client data and the api is the only thing that should read them.

**There is no input mount.** `RECON_INPUT_DIR` is gone: exports arrive through the browser, are stripped at the upload boundary, and land in `recon-uploads`; the worker materialises each window into its own scratch directory at run time. That also removed the vector for the `.dockerignore` trap, where a copy under `deploy/` sat inside the build context compose sends to the daemon.

| Variable | Default | Notes |
|---|---|---|
| `RECON_DATABASE_URL` | *required* | No default on purpose — one would point production at a developer's database. |
| `RECON_INPUT_ROOT` | `./input` | The **local-directory fallback** for a window with no uploads recorded. Since M6 the normal path is upload to a bucket and materialise per run (`service/materialize.py`); this is what a developer running a hand-copied window uses, and it is a supported mode, not a legacy one. |
| `RECON_CONFIG_DIR` | `./config` | Must be the same contract the CLI reads, including the `.xlsb` master. |
| `RECON_ARTIFACT_ROOT` | `./artifacts` | `<period>/<platform>/run-<id>/`. Gitignored. Ignored when `RECON_S3_ENDPOINT` is set. |
| `RECON_SCRATCH_ROOT` | `./.scratch` | Per-job working dir; removed on success, **kept on a hard stop** as evidence. |
| `RECON_LEASE_SECONDS` | `900` | Must outlast the longest *silent* stretch of a run, not the run. |
| `RECON_WORKER_ID` | `<host>:<pid>` | Identifies the lease holder. |
| `RECON_UPLOAD_ROOT` | `./.uploads` | Where uploaded exports are kept in local-directory mode. Sanitized copies only — since M6 **neither** copy outlives the request: the stripped bytes go to the store and the original is deleted. Gitignored. |
| `RECON_S3_ENDPOINT` | *unset* | Set it and uploads/artifacts live in buckets; unset and they live in directories. Both are supported. Setting it **without** credentials is a `ConfigError`, not a silent fallback — that would write client exports somewhere the deployment does not look ([D43](06-DECISIONS.md#d43)). |
| `RECON_S3_ACCESS_KEY` / `RECON_S3_SECRET_KEY` | *unset* | The **scoped** service account, never the MinIO root pair. An app holding root credentials can delete the bucket holding every workbook the team has invoiced from. |
| `RECON_S3_UPLOADS_BUCKET` | `recon-uploads` | Short retention, via a bucket **lifecycle rule** applied by `minio-init`. The first actual mechanism behind the short-retention promise. |
| `RECON_S3_ARTIFACTS_BUCKET` | `recon-artifacts` | Versioned, never expired: this is the deliverable the team invoiced from. |
| `RECON_S3_REGION` | `us-east-1` | Ignored by MinIO; required by botocore's signer. |
| `RECON_AUTH_DISABLED` | *unset* | **Presence disables auth**, so `=false` is not a trap. Refused on a non-loopback host. |
| ~~`RECON_CONFIG_APPROVAL`~~ | *deleted* | **Removed in M6, and now refused rather than ignored.** Open question 13 is answered: anyone but a viewer proposes, only an admin decides, self-approval is recorded. A deployment that set this was expressing an intent about who may approve a rate change, so dropping it silently on upgrade would be the worst kind of quiet — `ServiceSettings.from_env` raises and names the replacement ([D47](06-DECISIONS.md#d47)). |
| `RECON_EXCEPTION_ROW_CAP` | `500` | Rows per exception sheet stored in the database. Never silent — the true total is recorded alongside. |
| `RECON_API_URL` | `http://127.0.0.1:8080` | Read by the **web** app at request time, never baked into its image. |

Running the service tests needs a Postgres, and they create and drop their own database inside it:

```bash
export RECON_TEST_DATABASE_URL="postgresql://recon:<pw>@127.0.0.1:5432/postgres"
"$PY" -m pytest tests/service -q
```

### M6 additions

```bash
# Bootstrap the FIRST identity. It cannot come from the api — POST /tokens needs
# an admin token — so issuing an identity needs more access than using one.
"$PY" -m service.admin user create --username you@ada --role admin

# The synthetic demo window: three platforms, deterministic, its own pinned
# config. Seeded from the browser by an admin, or on disk for a developer:
"$PY" -m service.sampledata --out .scratch/demo

# How much non-NFC text is in the values we hash as exception identities.
# Read-only, and reports COUNTS ONLY — the identity columns include store names.
"$PY" -m service.nfc_audit
"$PY" -m service.nfc_audit --database-url "$RECON_DATABASE_URL"

# Index which uploaded file holds which (store, order_id) — defect 2.12's
# detection half. Every upload since 2026-08-19 is indexed at the door; this
# clears the backlog of everything older. Identifiers and counts only.
# It VERIFIES each object against the digest recorded at the door before reading
# it, and refuses rather than indexes on a mismatch — so a non-zero exit means an
# object store is serving bytes that did not pass the door, not that a sweep was
# incomplete. An upload with no recorded digest (pre-M8/2.5) is skipped and named;
# re-upload it to index it.
"$PY" -m service.order_index --backfill
"$PY" -m service.order_index --backfill --dry-run     # report, write nothing

# The whole stack, including MinIO and the web app. The real .env lives at
# %LOCALAPPDATA%\recon-deploy\.env since Phase 6.9 — see "The whole stack in
# containers" above.
cd deploy && docker compose --env-file "$LOCALAPPDATA/recon-deploy/.env" up --build
```

**There is no input mount any more.** `RECON_INPUT_DIR` is gone from `deploy/.env.example`: exports arrive through the browser, are stripped at the upload boundary, and land in `recon-uploads`. That also removed the vector for the `.dockerignore` trap, since a copy under `deploy/` sat inside the build context compose sends to the daemon.


`RECON_DATABASE_URL` is deliberately not a fallback for that variable — a suite that quietly runs against the production queue is worse than one that skips.

### Phase 6 additions (2026-08-20)

```bash
# Who downloaded which workbook (C12). Written by the api on every successful
# download; a refused (tampered) download records nothing.
"$PY" -m service.admin audit downloads
"$PY" -m service.admin audit downloads --run 41

# One retention pass, by hand (C10). The worker sweeps hourly on its own;
# --dry-run says what would go and removes nothing. Ages: scratch 14d, run-log
# DB mirror 90d (run_log.txt in the artifact store is the durable copy), dead
# sessions 30d. Overridable via RECON_RETENTION_*; RECON_RETENTION_INTERVAL_S=0
# turns the automatic sweep off.
"$PY" -m service.admin retention sweep --dry-run
"$PY" -m service.admin retention sweep

# Service telemetry (C5): queue depth by state, 24h run outcomes, worker
# liveness, oldest-queued age. Counts and ages only. Any viewer token works.
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/metrics

# The front door (C4/C9): TLS + per-IP rate limits, under a profile so the
# default local stack stays loopback-only. Certs live OUTSIDE the tree, same
# rule as .env; self-signed until a hostname exists.
sh deploy/ingress/make-self-signed.sh localhost
RECON_INGRESS_CERTS="$LOCALAPPDATA/recon-deploy/certs" \
  docker compose --env-file "$LOCALAPPDATA/recon-deploy/.env" --profile ingress up --build

# Nightly database backup (C3) — the database is the audit trail. 14 dumps kept
# in the db-backups volume; getting a copy OFF the machine is the hosting side's
# half and is not claimed here.
docker compose --env-file "$LOCALAPPDATA/recon-deploy/.env" --profile backup up -d
```

**Worker liveness is on `/healthz`** (C6): `workers_alive` counts heartbeats
within 60 seconds — beaten every idle loop turn *and* every lease extension, so a
worker mid-run stays visible. `queued: 3, workers_alive: 0` means nothing will
pick those jobs up: start a worker, then look at `service.admin job list` for
expired leases.

**The restore drill** (C3) — run it quarterly and after any Postgres upgrade,
and log it here:

```bash
sh tools/db_restore_drill.sh "postgresql://recon:<pw>@127.0.0.1:55432/recon_dev"
```

| Date | Database | Result |
|---|---|---|
| 2026-08-20 | `recon_dev` (local cluster, PG 17.10) | **PASSED** — `pg_dump -Fc` 102,254 bytes; restored into a scratch database; 30 tables, 1,037 rows, every per-table row count identical; scratch database dropped |

### Interpreting a service run

| Signal | Meaning |
|---|---|
| `job.state = done` | The worker executed the job. Says **nothing** about whether the numbers tied — read `run.status` ([D30](06-DECISIONS.md#d30)). |
| `job.state = error` | The *worker* broke (crash, OOM, expired lease). The run row carries the traceback. |
| `run.status = null` | Still running. There is no `RunStatus` for "in flight". |
| `run.exit_code` | The same 0/1/2/3 the CLI returns, from the same table. |
| `409` on `POST /jobs` | A queued or leased job already covers that window — the double-run guard ([D33](06-DECISIONS.md#d33)). The existing job is in the response. |
| `200` on `POST /jobs` | Your `idempotency_key` was already used; this is the original job, not a second run. |
| `complete: false` with an empty `lines` | Keep polling — the run has not finished. |
| `501` on an artifact download | The store has no local view of that URI (an object-store deployment must serve a signed URL). |
| `401` | Not authenticated — no token, or one that is unknown, revoked or expired. Deliberately one message for all three. |
| `403` | Authenticated, but the role is too low. The message names the role required. |
| `409` on `POST /uploads` | These exact bytes were uploaded before — the double-pull guard. |
| `409` on applying a config proposal | The contract moved since the proposal was made. Rebase it — that replays the recorded intent against the current rows into a fresh proposal — or withdraw and propose again. This will not merge a change nobody reviewed. |
| `503` from the config editor | The `config_*` tables have never been seeded in this deployment. Run `python -m service.config_import`. The api seeds them on first boot, so this means the tables were emptied afterwards. |
| `rules: pinned` on the board | The run used the config an earlier run of that window used, not today's. |

## Monthly cadence

1. **Extract** — download exports per window per store.
2. **Stage** — place them in `input/<window>/<platform>/...`. See [04-DATA-FLOW](04-DATA-FLOW.md#staging). Never mix windows.
3. **Run** — queue each platform/window in the browser (12–14 times a month). `tools/devrun.py` is the developer equivalent.
4. **Read the log** — `output/<window>/<platform>/run_log.txt`.
5. **Parallel-compare** — the reconciler runs the legacy Excel chain; variances beyond the team's tolerances are investigated **before booking**. Finance books from the manual output until a full clean cycle passes.
6. **Month end** — `build_master_summary.py`.

## Interpreting a run

**Exit codes** (M2 — previously `0` or `1`, with "not checked" indistinguishable from "disagrees"):

| Code | Status | Meaning |
|---|---|---|
| `0` | `OK` | every figure tied against the team's references |
| `1` | `VARIANCE` | a real numeric disagreement, or a tie-out breach |
| `2` | `UNVERIFIED` | ran clean but had nothing to check against (no `--refs`) |
| `3` | `HARD_STOP` | nothing was produced |

A run without `--refs` now exits **2**, not 1. Variances and unchecked stores are printed under separate headings instead of one list — the old behaviour emitted one alarming line per store for a run that was simply never compared, which is how operators learn to ignore a list.

**What to look at, in order:**

| Signal | Meaning |
|---|---|
| `TIE-OUT` section | The rebuilt checks. `BREACH` sets the exit code; `RECONCILING` lines are named, quantified and expected — not errors. |
| `RECONCILING Settlement with no matching order lines` | ~21% of TikTok settlement, by design. Investigate a large *change*, not its presence. |
| `METRICS` section | Wall time split into io / compute / serialize, peak RSS, three slowest stages. Also written as `run_metrics.json`. |
| `HARD STOP` section | Nothing was produced. The message names the specific stores/columns. |
| `headers not found: [...]` | A configured column did not match. See defect [1.2](08-KNOWN-DEFECTS.md#12-vietnamese-headers-arrive-in-unicode-nfd--fixed-m2-2026-08-13) before assuming version drift. |
| `store check ...: OK (n/m expected)` | Roster check passed. TikTok and (since M2) Shopee income; Lazada has no roster configured. |
| `masters: live master matches the CSV snapshots exactly` | No drift. Otherwise the drifted items are listed. |
| `check [tab] ... -> verdict` | Template control blocks, computed from the engine. |
| `Check ... : PASS` | Real since M2 — the checks cross a file boundary. Shopee's money crossing is still coverage-only. |
| `WARNING:` lines | Warning count is footered but does not affect the exit code. |

## Troubleshooting

| Symptom | Cause and action |
|---|---|
| `PermissionError` on `finance_file.xlsx` | The file is open in Excel. Close it and re-run. Since M1 the failure is *reported* and the log is still written; since 2026-08-19 the write also goes through a sibling `.tmp` + `os.replace`, so **the previous artifact survives intact** rather than being left truncated — but an Excel-held destination still fails, because `os.replace` raises `PermissionError` there too. Defect [1.7](08-KNOWN-DEFECTS.md#17-no-error-handling-in-the-production-driver--fixed). |
| `Store-count check FAILED … Missing stores: [...]` | Either mis-staging, or genuinely new stores. Confirm with the business, then add to `expected_stores` / `stores_optional` / `store_aliases`. Never guess an alias — require order-ID-overlap evidence ([D7](06-DECISIONS.md#d7)). |
| `missing required columns after header mapping` | The export renamed a header. Add the new spelling as a **parallel** entry in `column_maps` — keep the old one, several coexist by design. |
| `Could not derive the store name from file name` | Extend the `store_from_filename` regex. Store names must never be truncated by a suffix alternative. |
| `no sheet matching /…/` | A new sheet-naming variant. Check `sheet_names` / `sheet_patterns`. |
| Reader sees only one column | A broken `<dimension>` tag. Force `calamine` for that platform/kind in `reader_engine`. |
| Run is extremely slow | Cloud-sync contention on the data folder, and/or the default Excel reader. `calamine` is configured for the known-slow paths. |
| `No fee-type mapping available` | The `.xlsb` master is missing *and* the CSV snapshots are absent. |
| A window shows as **running** and never finishes | Its worker died mid-run, so the lease expired with nobody left to sweep it. `python -m service.admin job list` shows whether the lease is live or `EXPIRED`. If expired, `job reclaim` closes it out — or an admin presses *A run appears stuck* on the board. It does **not** re-run the window: `max_attempts` is 1 because an automatic retry of a settlement run is a second write of the same money ([D30](06-DECISIONS.md#d30)). Check nothing is progressing first — on a slow-but-live worker this ends a run that would have finished. |
| A failed run says only *"the service itself hit an error"* | That is the deliberate answer for an infrastructure failure (M8/4.1). The type, message and traceback are in the **run log** on the same page. `service/failures.py` holds the mapping; an unrecognised exception gets a fixed sentence rather than its own text, because that text routinely contains a path or a connection string. |
| A run reports **UNVERIFIED** | It ran clean and had nothing to check against. Supply the team's totals on the window page (*The team's figures*); the next run compares against them. Not a failure — exit code 2 means exactly this. |
| `predates the M8/2.5 integrity check` on a run | The upload was recorded before `object_sha256` existed, so nothing can establish that the materialised file is the file that was uploaded. Re-upload the export. The digest is deliberately not recomputed from the store — that would certify the store against itself ([D52](06-DECISIONS.md#d52)). |
| `RECONCILING Settlement whose order lines exist in an earlier window (APPLIED)` | **Normal since 2026-08-20**, and not an error. Orders this window settles had their SKU lines exported with an *earlier* window, and the run used them (defect 2.12, [D59](06-DECISIONS.md#d59)). The figure is settlement now **inside** the invoice that would previously have left it silently. Those lines pass the same conservation checks as the window's own, so a drifted re-export breaches a check rather than mis-invoicing. Nothing to do — the number is there so a *change* in it is visible. |
| `RECONCILING Settlement whose order lines exist in an earlier window (NOT applied)` | The same finding under `cross_window_order_backfill: report` (or on a window **pinned** to a config from before 2026-08-20 — pinning is why a re-run can still say this). The figure is settlement **outside** the invoice. Ask the platform to re-export this window's order file, which is the real fix because the lines belong here; or repin the window to a config with `apply`. Do **not** respond by copying the earlier window's order files into this window's folder — that pools them, measured at a 4.5× over-count. |
| A store still shows a large `Order-file coverage` share after backfill | The lines are in **no** window, so there is nothing to borrow, and the cause is upstream. Two shapes seen in July, with different asks: an order export that is the *wrong file* (`purite` w2 was byte-identical to w1's — ask for a re-export of that window), and one that looks *truncated* (`masan` s4 held exactly 210,000 orders and stopped mid-afternoon on the final day — ask for a re-pull in narrower date ranges). Neither is recoverable in code; see [08-KNOWN-DEFECTS 2.12](08-KNOWN-DEFECTS.md) and [16-DATA-REQUEST](16-DATA-REQUEST-MONTH-MASTER.md). |
| `cross_window_order_backfill is '...' which is not one of ['off', 'report', 'apply']` | A typo in that setting, including an empty value. It hard-stops rather than defaulting, because defaulting quietly to `off` would disable the control that makes a 4.5B VND gap visible. |
| `<key> is not configured, and there is deliberately no default for it` | One of `dedupe_rows`, `drop_unmapped_columns` or `cross_window_order_backfill` is absent from the config this run uses. The message says what the value decides. Set it in the rules editor, or in `config/settings.yaml` for a CLI run; on a **pinned** window the remedy is the audited unpin/repin. Since 2026-08-21 these three have no code default at all: each one's default was, or became, the opposite of the configured value — the backfill mode's was safe on the morning it was written and wrong by that evening (A9). *This row replaces the older advice that an unset backfill mode "does mean `off`".* |
| `<key> is 'false' (str), not true or false` | A boolean arrived quoted. In Python `"false"` is truthy, so believing it would invert the flag — PII retained, or legitimate duplicate lines dropped. Fix the value's type; the editor guards this at the row, so a quoted flag means the config came from a hand-edited file. |
| `vat_factors.default is not configured` | The VAT fall-through factor is missing. It prices every SKU the master does not cover, which so far is all of them, so the run stops rather than assuming 1.08 — the 8%→10% revert is meant to be that one config line ([A14](14-PRODUCTION-READINESS.md)). |
| An upload is refused with `does not overlap <month>` | The file's settlement dates fall entirely outside the month of the window it was addressed to (M8, defect 2.3's residual). Either the window label is wrong, or it is a mis-pull carrying another period's block — July had two, and they cost 4,527,401,608 VND of understatement. Check the export, then re-upload to the right window. The check is INTERSECT, not contain, so a Lazada weekly lapping into the next month is accepted. Order exports are never date-checked: an order created in June legitimately settles in July. |
| An upload is accepted with `settles from … while all its sibling(s) start at …` | The mis-pull *shape*, warned rather than refused: this file starts earlier than every other window-defining file already uploaded here. Verify the export. If it genuinely carries an earlier settlement block, declare the window's real range under `window_settlement_bounds` ([D9](06-DECISIONS.md#d9)) so the run drops the rows belonging to the earlier window — do **not** delete the file if it is the only source of its own window's rows (July's `w5` Curel income is exactly that case). |
| `date cell(s) could not be read` | A date format changed. The dates are kept as blank, which means those rows drop out of the month grouping in the workbook — quiet, so this is a warning worth acting on. Check the export against `dayfirst`; `date_coercion: hard_stop` makes it stop instead ([D53](06-DECISIONS.md#d53)). |
| `Parsing dates in %Y/%m/%d format when dayfirst=True` | The file's own format contradicts the contract. **Do not flip `dayfirst` on this alone** — it fires on real TikTok income today and those dates are correct. It means the setting is not what is deciding the parse. |
| Golden digests changed unexpectedly | An edit to `src/` or `config/` moved a cell. That is the gate working. Find the cell with the differ before deciding whether the change was intended; if it was, re-baseline deliberately ([D26](06-DECISIONS.md#d26)). Never widen a tolerance to make it pass ([D17](06-DECISIONS.md#d17)). |
| The month summary needs rebuilding but no window needs re-running | A late reference total, a repin, a corrected declaration. Queue a master directly: the board's *Build the month summary* form, `POST /months/{month}/master`, or `python -m service.admin job enqueue-master --month 2026-07` when the api is down (A4). A 409 / "not queued" means one is already waiting — it will read every window finished by the time it runs. **Never re-run a settlement window just to trigger the chain** — that is a second run of the same money. |
| A run's page says `could not queue the month-end master…` | The settlement run itself succeeded and its finance file is unaffected — the failure was in queueing the month's summary afterwards (`runs.chained`, A4). Queue one by hand as above once the cause (usually the database) is resolved; the service log carries a `month_master_chain_failed` WARNING for alerting. |
| An exception row keeps coming back every run | That is the queue working — it re-presents until somebody decides. Mark it *reviewed* or *expected* on the run page with a reason ([D61](06-DECISIONS.md#d61)); the decision follows the fingerprint across runs as a badge. It never hides the row: an "expected" variance that grows must still be seen. Re-opening requires its own reason and the history survives. |
| A run hard-stops with `names store(s) the … roster does not know` | The window's roster declaration names a store that is not on the roster of the config this run uses — a repin, a rename, or a typo predating the picklist ([D62](06-DECISIONS.md#d62)). Fix the declaration on the window page (or the alias in the rules). Deliberately not skipped: skipping would resurface later as a misleading "missing store" stop. |
| `ROSTER DECLARATION STALE` in a run log, or an amber notice on the window page | A store declared absent now has files (or a blanket declaration sits on a complete window). The figures ARE included — only the record no longer matches the window. Withdraw or re-declare on the window page so the workbook's roster stamp stays honest. |

## Safety rules

- **Never print cell values** from real exports. Schemas, column names and counts only — the files contain customer PII ([04-DATA-FLOW](04-DATA-FLOW.md#pii--what-must-never-leave)).
- **Never mix a refactor with a semantic fix** in one commit — a structural bug and an intended change become indistinguishable ([D12](06-DECISIONS.md#d12)).
- **Never re-baseline a golden to make a suite green.** Diff first, understand the moved cell, then re-baseline with a stated reason ([D26](06-DECISIONS.md#d26)).
- **Never commit `input/`, `output/`, `artifacts/`, `.scratch/`, or a golden.** Only digests.
- **Never point the service tests at `RECON_DATABASE_URL`.** They create and drop a database; aimed at a real queue they would delete an operator's run history.
- Write to a temp output root when experimenting: `pipeline.build_context` takes an explicit `output_root`, so real outputs stay untouched. (The old `recon.py --output-root` route was deleted in M1.)
