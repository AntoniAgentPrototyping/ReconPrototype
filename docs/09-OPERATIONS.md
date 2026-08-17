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
cd deploy && cp .env.example .env && $EDITOR .env
docker compose up --build          # db + minio + api + worker + web

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

# The whole stack, including MinIO and the web app.
cd deploy && cp .env.example .env && $EDITOR .env && docker compose up --build
```

**There is no input mount any more.** `RECON_INPUT_DIR` is gone from `deploy/.env.example`: exports arrive through the browser, are stripped at the upload boundary, and land in `recon-uploads`. That also removed the vector for the `.dockerignore` trap, since a copy under `deploy/` sat inside the build context compose sends to the daemon.


`RECON_DATABASE_URL` is deliberately not a fallback for that variable — a suite that quietly runs against the production queue is worse than one that skips.

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
| `409` on applying a config proposal | `settings.yaml` moved since the proposal was made. Withdraw and re-propose; this will not merge a file whose comments are evidence. |
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
| `PermissionError` on `finance_file.xlsx` | The file is open in Excel. Close it. Since M1 the failure is *reported* and the log is still written, but the write is not atomic — defect [1.7](08-KNOWN-DEFECTS.md#17-no-error-handling-in-the-production-driver--partly-fixed-m1-scheduled). |
| `Store-count check FAILED … Missing stores: [...]` | Either mis-staging, or genuinely new stores. Confirm with the business, then add to `expected_stores` / `stores_optional` / `store_aliases`. Never guess an alias — require order-ID-overlap evidence ([D7](06-DECISIONS.md#d7)). |
| `missing required columns after header mapping` | The export renamed a header. Add the new spelling as a **parallel** entry in `column_maps` — keep the old one, several coexist by design. |
| `Could not derive the store name from file name` | Extend the `store_from_filename` regex. Store names must never be truncated by a suffix alternative. |
| `no sheet matching /…/` | A new sheet-naming variant. Check `sheet_names` / `sheet_patterns`. |
| Reader sees only one column | A broken `<dimension>` tag. Force `calamine` for that platform/kind in `reader_engine`. |
| Run is extremely slow | Cloud-sync contention on the data folder, and/or the default Excel reader. `calamine` is configured for the known-slow paths. |
| `No fee-type mapping available` | The `.xlsb` master is missing *and* the CSV snapshots are absent. |
| Golden digests changed unexpectedly | An edit to `src/` or `config/` moved a cell. That is the gate working. Find the cell with the differ before deciding whether the change was intended; if it was, re-baseline deliberately ([D26](06-DECISIONS.md#d26)). Never widen a tolerance to make it pass ([D17](06-DECISIONS.md#d17)). |

## Safety rules

- **Never print cell values** from real exports. Schemas, column names and counts only — the files contain customer PII ([04-DATA-FLOW](04-DATA-FLOW.md#pii--what-must-never-leave)).
- **Never mix a refactor with a semantic fix** in one commit — a structural bug and an intended change become indistinguishable ([D12](06-DECISIONS.md#d12)).
- **Never re-baseline a golden to make a suite green.** Diff first, understand the moved cell, then re-baseline with a stated reason ([D26](06-DECISIONS.md#d26)).
- **Never commit `input/`, `output/`, `artifacts/`, `.scratch/`, or a golden.** Only digests.
- **Never point the service tests at `RECON_DATABASE_URL`.** They create and drop a database; aimed at a real queue they would delete an operator's run history.
- Write to a temp output root when experimenting: `pipeline.build_context` takes an explicit `output_root`, so real outputs stay untouched. (The old `recon.py --output-root` route was deleted in M1.)
