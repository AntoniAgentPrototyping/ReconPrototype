# E-commerce Settlement Reconciliation Pipeline

Turns raw marketplace exports (TikTok Shop, Shopee, Lazada) into invoice-grade revenue per client brand: classify what counts, recompute net price and VAT per SKU, tie out against the finance team's own figures, and emit the invoicing workbook finance bills from.

Replaces a manual Excel/Power Query chain. Every rule was reverse-engineered from the team's own formulas and verified row-by-row against their outputs — **never invented**. ~288,000 rows verified row-exact across three real months.

**Status:** verified, running in parallel with the manual process. The numbers are not yet authorised for production booking — that is a human sign-off, not a code state, and it has not been given ([11-OPEN-QUESTIONS](docs/11-OPEN-QUESTIONS.md)).

## Quick start

```bash
# One-time: a venv, deliberately outside this (cloud-synced) folder
py -3.12 -m venv "$LOCALAPPDATA/recon-venv"
PY="$LOCALAPPDATA/recon-venv/Scripts/python.exe"
"$PY" -m pip install -e ".[dev]"

# Does my machine work? (synthetic data, no real data needed)
"$PY" tools/smoke_test.py       # synthetic end-to-end, no client data
"$PY" -m pytest

# Run one settlement window end to end. DEVELOPER path since M6 — a user does
# this in the browser (see below) and the worker makes the same calls.
"$PY" tools/devrun.py --platform tiktok --period 2026-05_w1

# No client data? Generate a believable three-platform window and run that.
"$PY" -m service.sampledata --out .scratch/demo
"$PY" tools/devrun.py --platform lazada --period 2026-05_demo
```

Outputs land in `output/<window>/<platform>/`: `finance_file.xlsx` (the deliverable), `run_log.txt` (the audit trail) and `run_metrics.json` (timing and memory). Inputs are read from `input/<window>/<platform>/...` and both directories are gitignored — **client data never enters version control**.

Full command reference and troubleshooting: **[docs/09-OPERATIONS](docs/09-OPERATIONS.md)**.

### The application (M4 · M5 · M6)

`service/` wraps the same pipeline in an api, a worker and a Postgres job queue; `web/` is a Next.js BFF over it — month board, run view with a live log, exception queue, upload screen, sectioned config editor. **Since M6 this is how users work:** they sign in with a password, upload the exports, run the window and download the workbook, without touching a terminal.

It is still a **wrapper**: delete `service/` and `web/` and the pipeline above still produces the month's invoicing workbook, which three tests enforce ([D24](docs/06-DECISIONS.md#d24), [D28](docs/06-DECISIONS.md#d28)). That is why the golden gate and the row-level verification harnesses are unchanged by all of this — they are the reason anyone believes the numbers, and they were never the part a user touched.

```bash
"$PY" -m pip install -e ".[dev,service]"
export RECON_DATABASE_URL="postgresql://recon:<pw>@127.0.0.1:5432/recon"
"$PY" -m service.api        # 127.0.0.1:8080 — migrates on start
"$PY" -m service.worker     # claims jobs; --once or --drain

# The FIRST identity cannot come from the api — creating one needs an admin
# credential, so issuing an identity needs more access than using one. The
# password is generated, shown once, and must be changed at first sign-in.
"$PY" -m service.admin user create --username you@ada --role admin

cd web && npm install && npm run dev        # localhost:3000

# ...or the whole thing in containers
cd deploy && cp .env.example .env && docker compose up --build
```

Every endpoint is authenticated and names the role it needs (three roles: `viewer` reads, `user` runs work and uploads, `admin` changes the rules and manages accounts; the api **refuses to start** on a routable address with auth off). Uploads and artifacts live in an object store, so the api and worker share nothing but the network ([D43](docs/06-DECISIONS.md#d43)).

Entra ID SSO is the destination and is still blocked on a tenant app registration — **[13-ENTRA-SETUP](docs/13-ENTRA-SETUP.md)** is the permissions escalation for it. And the honest limit: there is **no browser automation**, so claims about how a screen renders are claims about code that compiles and an API that answers ([defect 2.8](docs/08-KNOWN-DEFECTS.md)).

## Documentation

Written to be read by humans and by coding agents. Start at Orientation.

| Doc | Contents |
|---|---|
| **[01 — Orientation](docs/01-ORIENTATION.md)** | The business problem, vocabulary, roles, and the three ideas that explain the design. **Start here.** |
| **[02 — Architecture](docs/02-ARCHITECTURE.md)** | Module map, entry points, the I/O boundary, the hidden second compute layer |
| **[03 — Pipeline](docs/03-PIPELINE.md)** | The six stages, how the three platforms differ, failure posture |
| **[04 — Data Flow](docs/04-DATA-FLOW.md)** | Sources, window naming, staging, masters, outputs, PII handling |
| **[05 — Domain Rules](docs/05-DOMAIN-RULES.md)** | The money math: classification, calculation, VAT, tie-outs, rounding |
| **[06 — Decisions](docs/06-DECISIONS.md)** | Why things are the way they are, with costs. Stable anchors (`#d1`…) |
| **[07 — Verification](docs/07-VERIFICATION.md)** | What is proven, how, and the honest limits |
| **[08 — Known Defects](docs/08-KNOWN-DEFECTS.md)** | Verified defects — ours and the team's. **Read before trusting a green run.** |
| **[09 — Operations](docs/09-OPERATIONS.md)** | Environments, commands, monthly cadence, troubleshooting |
| **[10 — Roadmap](docs/10-ROADMAP.md)** | Milestones, immediate to-do, target architecture |
| **[11 — Open Questions](docs/11-OPEN-QUESTIONS.md)** | Decisions that need a human, grouped by owner |
| **[12 — Change History](docs/12-CHANGE-HISTORY.md)** | Format drift absorbed each month, and milestone history |
| **[13 — Entra ID & Azure access](docs/13-ENTRA-SETUP.md)** | The one-time portal setup for M5 sign-in, and the permissions to escalate for |

Also in the repo:

- **[ARCHITECTURE_POSITION.md](ARCHITECTURE_POSITION.md)** — a stakeholder-facing position document on the proposed target architecture (platform APIs, transaction store, AI layer, D365 posting). Written for leadership, not maintainers.
- **[.claude/CLAUDE.md](.claude/CLAUDE.md)** — operating guide for coding agents: commands, invariants, and the things not to re-discover.
- **`deploy/`** — Dockerfile and compose file for the api, worker and database. Built and brought up in M5.
- **`web/`** — the Next.js BFF. Its own image; the only service that should get a public address.
- **`config/settings.yaml`** — the rules contract. Worth reading top to bottom; the comments carry the evidence for each value.

## Two things to know before changing anything

**The tie-out checks were rebuilt in M2 and now work — but read what they cover.** They were algebraic identities that could not fail; they now compare the order-file rebuild against an income-file reference and every revenue-loss mutation breaches. All three platforms have a measured money crossing as of 2026-08-13 — Shopee's is a derived pair from the team's own June `Net revenue` formula, because no single income column conserves. The honest limit that remains: ~21% of TikTok settlement is legitimately excluded from invoicing and reported as a reconciling item. See [08-KNOWN-DEFECTS](docs/08-KNOWN-DEFECTS.md#11-the-tie-out-checks-cannot-fail--fixed-m2-2026-08-13).

**The committed tree is the verified baseline, so never mix a refactor with a fix.** Its authority is *provenance*, not correctness — it is the tree that was checked row-by-row against the team's own files. If structure and semantics change in one commit, a numeric difference has two possible causes and the golden gate can no longer tell you which. Structural change first, proven output-identical; behaviour change second, with the delta stated in advance. See [D12](docs/06-DECISIONS.md#d12).

*(A polars migration was planned here and descheduled on 2026-08-12 — the data volume didn't justify it and the real bottleneck is Excel I/O. pandas stays; a port is trigger-gated on measurements. See [D25](docs/06-DECISIONS.md#d25).)*

## Layout

```
src/            the pipeline; pipeline.py is the seam, and its only writer
config/         settings.yaml (the contract) + team-owned masters + snapshots
tools/          devrun.py (developer) · calc_verify*.py · make_golden.py ·
                stage_exports.py · smoke_test.py · metrics_report.py
                stage_exports.py · metrics_report.py · smoke_test.py
tests/          control gaps pinned as strict xfails · goldens/ · the I/O lint
docs/           the documentation set above
input/ output/  gitignored — client data and generated artifacts
```
