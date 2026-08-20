# 06 — Decisions

Why things are the way they are. Each entry states the decision, the reasoning, and what it costs — so a future maintainer can tell a deliberate constraint from an accident.

Anchors are stable (`#d1`…); do not renumber.

**Revision convention.** When a decision changes, the entry is amended in place and tagged *Revised* / *Narrowed* / *Tightened* / *Superseded* with the date, and the original reasoning is kept. A superseded entry is not deleted: it explains the shape of code that still exists, and the reasoning is often the thing you need when the question comes back. Entries currently carrying a revision: [D12](#d12), [D13](#d13), [D14](#d14), [D17](#d17), [D19](#d19), [D20](#d20), [D21](#d21), [D24](#d24), [D27](#d27).

---

## Domain and correctness

### D1 — Evidence-first: never invent a rule {#d1}
Every calculation rule is extracted from the team's own artifacts (Power Query M code, worksheet formulas, pivot structure) and verified row-by-row against their outputs. Where evidence was missing, the question went to a human rather than being guessed.

*Why:* the system's value is not that it computes revenue — it's that it provably computes *the same* revenue the team already computes. An invented rule that happens to look right is worthless for a parallel run.

*Cost:* slow derivation, including reverse-engineering DataMashup-embedded M code out of workbooks.

### D2 — Config is the contract, and its comments are the audit trail {#d2}
Everything month-specific or drift-prone lives in `config/settings.yaml`: column maps (all historical header spellings kept in parallel), store rosters, aliases, tolerances, VAT, reader engines, settlement bounds.

*Why:* monthly change should not need a code change. And the *reasons* matter as much as the values — an alias entry cites its order-ID-overlap proof, a reader-engine choice cites the specific malformed tag.

*Consequence:* any tooling that round-trips this file must preserve comments (`ruamel.yaml`, never `PyYAML`), and a form-based UI that flattens it would destroy the audit trail. This is why a config-editing UI must keep git-backed YAML canonical rather than moving the source of truth into a database.

### D3 — Hard-stop over guess {#d3}
Structural problems stop the run with named specifics rather than producing a plausible number: missing required columns, an unrecognised store, an undetectable schema.

*Why:* a wrong invoice is far more expensive than a late one.

*Cost:* a monthly operational tax — new stores block the run until a human confirms them. Accepted deliberately. At larger platform counts this becomes a month-end close risk and will need quarantine semantics.

### D4 — A variance is a finding, not an error to force away {#d4}
The pipeline flags and reports; it never bends numbers to tie. Unknown values go to exceptions — never dropped, never guessed.

### D5 — No row dedupe on real data {#d5}
Byte-identical order lines are legitimate (duplicated gift SKUs), and the team's Power Query never dedupes. Overlap protection comes from the one-folder-per-window discipline instead. The synthetic sample path still dedupes, because its generator bakes overlapping parts.

### D6 — Store identity comes from the filename {#d6}
TikTok/Shopee exports carry no store column, so the per-store download filename *is* the identity, via a per-platform regex with a hard stop on failure.

*Cost:* the regex has needed extension every month, and this is the single most invasive thing to change if inputs ever become API responses.

### D7 — Aliases require evidence, not similarity {#d7}
A store alias is added only after order-ID-overlap proof. One storefront looked like an alias by name similarity and was proven genuinely new by having **zero** overlap.

*Why:* this is the concrete reason a fuzzy or embedding-based matcher would be actively dangerous here — it would have merged that store and corrupted a client's revenue.

### D8 — The team keeps owning the master data {#d8}
`Lib & VAT rate.xlsb` is read live at runtime, with committed CSV snapshots as fallback and drift reported every run.

*Why:* the alternative is a second, silently-divergent copy of the team's rules.

*Cost:* an unowned runtime dependency on a shared file, and no caching (re-read up to 3× per Lazada run).

### D9 — Settlement bounds are declared dedup of a pull artifact, not a rule {#d9}
A window may only contain settlements dated inside its own labelled window. An entry is added **only** when a raw export was proven mis-pulled *and* its out-of-window rows are proven already present in the adjacent window, with the evidence in the comment.

*Why:* one mis-pulled export carried an extra week of settlements — 5.97B VND of double-invoicing risk. Narrow, evidence-gated, and per-window by design so it can never become a silent filter.

### D10 — `PLACEHOLDER_FORMULAS` was a governance flag, not a code-quality flag {#d10}
**Removed in M1 (2026-08-13)** along with the placeholder math it described. Kept here because the distinction it encoded still applies and the question recurs.

The flag stated that the numbers were not yet blessed for production booking — a *governance* fact, flipped by stakeholder sign-off plus one clean parallel cycle, never by code becoming verified. It was routinely misread as "the code is unfinished".

*The nuance that made deleting it safe:* the placeholder math was reachable only from the legacy `recon.py`, so no production number was ever computed by it. Deleting both left all three golden manifests unchanged, which is the proof ([D19](#d19)).

*What still stands:* the production-booking authorisation remains **ungiven** and undefined ([11-OPEN-QUESTIONS](11-OPEN-QUESTIONS.md)). Removing the flag removed a misleading signal, not the gate.

---

## Engineering

### D11 — Reader engine is configurable per platform/kind {#d11}
Real exports have been malformed in several distinct ways; one shipped a broken `<dimension>` tag that the default reader truncated to a single column. `calamine` ignores it. It is also dramatically faster: one platform took 45+ minutes per window under disk contention and reads in minutes with calamine.

### D12 — The verified tree is the baseline: provenance, not correctness {#d12}
**Revised 2026-08-12** — the original wording froze the pandas engine as an *oracle* for a polars migration. That migration was descheduled ([D25](#d25)), but the principle underneath it survives and still orders the roadmap.

The committed tree is the one row-verified against the team's files. **A refactor is never mixed with a semantic fix.** A build with corrected tie-outs and composite join keys would be *better code* and a *worse baseline*, because nothing verified it.

*Why:* if structure and semantics change in the same commit, every numeric difference has two possible causes and the golden gate can no longer attribute it.

*Consequence:* the M1 seam extraction must be **output-identical**, proven at zero tolerance. Controls are rebuilt **after** it, split into "adds signal, same cells" (Class A) and "changes cells" (Class B), with the expected delta stated in advance.

### D13 — Bug-for-bug behaviour through a refactor {#d13}
**Narrowed 2026-08-12** — originally scoped to a cross-engine port; now scoped to the M1 refactor.

Known defects are deliberately preserved while structure moves, and fixed afterwards in separate commits. Each fix is a commit-pair: the fix first (the strict-xfail XPASS in CI is the evidence), the marker removal second.

*What was dropped with the port:* the `PARITY_DIVERGENCES` registry and the per-defect tests asserting the *buggy* output. Same engine means the golden gate already pins current behaviour cell by cell, so a second mechanism restating it was redundant.

### D14 — Compare stored artifacts, not live processes {#d14}
**Superseded 2026-08-12.** This existed because the oracle (Python 3.12 + pandas 2.x) and the target (Python 3.14 + polars) could not share an interpreter, so a live side-by-side comparison was impossible. With one runtime the constraint is gone.

*What survives:* goldens are still stored artifacts rather than a live re-derivation, because a frozen artifact cannot drift and comparing against one needs neither the team-owned `.xlsb` nor real client data in CI. Recorded rather than deleted — it explains the shape of `tests/goldens/`.

### D15 — Goldens live outside the repo; only digests are committed {#d15}
Goldens derive from client data. Committed manifests carry one-way digests, shapes and integer counts — no values, no store names. A second developer regenerates from their own copy of the exports and their digests must match, proving reproducibility without either party moving client data.

### D16 — Never hash `.xlsx` bytes {#d16}
openpyxl stamps timestamps into `docProps/core.xml` and zip entry order is not guaranteed, so identical data yields different files. Comparison is on canonical *content*.

### D17 — The golden gate runs at zero tolerance {#d17}
**Tightened 2026-08-12.** The gate was previously 0.5 VND on workbook cells. That epsilon existed only because a *cross-engine* comparison cannot be bit-exact: pandas uses pairwise summation, polars chunked SIMD, so reduction order genuinely differs. Worst-case float accumulation over ~288K rows of ~1e9 VND is ≈0.06 VND, which is how 0.5 was derived — ~8× above the theoretical worst case and 20× below the tightest business tolerance (10 VND).

With one engine, same reduction order, **bit-exact is achievable**. So `TolerancePolicy` collapses to exact comparison and the `knife_edge` allowlist is deleted along with it — that machinery existed for a genuine discontinuity in the invoice rounding model, where a last-bit difference can flip a `round()` and move an amount by `qty`. Same engine, no last-bit difference, no discontinuity to allowlist.

*A stricter gate with less code.* And a real consequence: if a re-run ever diffs at all, something is genuinely non-deterministic (dict ordering, `set` iteration, an unsorted `unique`). That is a finding to chase, never a reason to widen the tolerance.

*Arithmetic retained above* because it is the record of why 0.5 was once correct — and it is the number to return to if the engine port is ever triggered ([D25](#d25)).

### D18 — Row order is part of the contract, never normalized in the diff {#d18}
Order-level columns are blanked on repeat rows for the team's visual convention, and the monthly master reads that output back with a forward-fill. A reordered frame therefore **misattributes revenue to the wrong storefront while every total still ties**. Normalizing order in the differ would pass a workbook that is wrong for its consumer. An order-insensitive mode exists for triage only and never gates.

### D19 — Delete the unverified placeholder path rather than keep it {#d19}
**Executed 2026-08-13 (M1).** `calculate.explode_to_sku` / `compute_sku_columns`, `PLACEHOLDER_FORMULAS`, `tieout.run_checks`, `recon.py` and `src/export_platforms.py` are gone; `export.write_exceptions_file` was kept. **All three manifests were unchanged**, which is the evidence the path was unreachable from production. `src/` fell from 2,291 to ~1,950 lines of live code.

Maintaining unverified formulas alongside the verified chain is risk for zero gain: it is the one part of `src/` that no evidence covers, and its existence invites a future caller. The gate is that all golden digests stay unchanged after deletion — which is itself the proof the path was unreachable from production.

*Simplified 2026-08-12:* with `oracle_rev` gone ([D26](#d26)), the gate is a plain "manifests unchanged". The earlier plan needed a **cross-revision** comparison, because deleting code changed the revision hash and therefore wrote a new manifest file, so "unchanged" had to mean "unchanged per window across two revisions". That subtlety disappears with the hash.

### D20 — Dependency pins are controls, not laziness {#d20}
`pandas>=2.2,<3` exists because pandas 3.0 makes Copy-on-Write the default, changing whether an in-place mutation of a passed-in frame is visible to its caller — and the pipeline does exactly that at `finance_template.py:159`. Lifting it requires re-running the golden comparison, not just a green suite. The comments in `pyproject.toml` are part of the control.

*Amended 2026-08-12 — the two bounds have different lifespans.* The `<3` pandas bound is a **standing control** over real behaviour. `requires-python <3.14` is a **temporary artifact**: it existed so the oracle venv could resolve pandas 2.x, which has no cp314 wheels. It is the bound to lift once pandas ships 3.14 support, and lifting it is a routine dependency bump gated on the goldens — not a migration. Worth stating explicitly because "we're stuck on old Python" was a stated reason to rewrite the engine, and it is not a durable one.

### D21 — One environment, outside the synced folder {#d21}
**Revised 2026-08-12** — there were two venvs (a 3.12 + pandas oracle and a 3.14 + polars target). With the engine port descheduled ([D25](#d25)) the polars venv is deleted and there is one runtime.

It stays outside the project directory because the folder is cloud-synced and sync contention is a documented cause of 45-minute runs; adding ~200 MB of site-packages would make it worse. That reason is unrelated to the engine question and unchanged.

*Consequence:* "run the suite in both venvs" is retired, along with the `importorskip`/skip-in-reverse dance that let modules collect cleanly under a pandas-free runtime.

### D22 — `xfail(strict=True)` is a drift detector, not a to-do list {#d22}
Known gaps are pinned as strict xfails with `raises=AssertionError`. During the port they must all stay xfail: an unexpected XPASS means behaviour changed unintentionally, and erroring the suite is the correct response. `raises=` matters because without it a `KeyError` mid-refactor would report as "gap still open" and hide a broken test.

*Procedure:* never flip marker and behaviour in one commit. Fix first (the XPASS error in CI is the evidence), remove the marker second.

### D23 — Roster relaxation is a property of the run, not a config fork {#d23}
Generating a golden from a single-store window would hard-stop on the store roster. Rather than editing config (which would change the revision hash and make it misreport what produced the golden), the relaxation is a CLI flag that makes expected stores optional **for that run**, keeps the *unexpected*-store check armed, and records `partial_roster: true` plus a store count in the committed manifest entry — so a subset golden can never be mistaken for full coverage.

### D24 — The CLI stays first-class {#d24}
Any future service wrapper must be deletable without changing a line the pipeline executes: run functions take explicit directories, database writes are a post-run side effect and never a precondition, and files on disk remain the canonical artifact. Month-end cannot depend on a web app being up, and this is also the mitigation for having a single maintainer.

*Strengthened 2026-08-12:* the M1 seam is how this becomes enforceable rather than aspirational. `pipeline.run()` writes nothing; `write_artifacts()` is the only writer. The CLI and the worker are then two callers of the same function, and neither can quietly become the privileged one.

*Enforced 2026-08-13 (M4), now that the wrapper exists.* `tests/service/test_service_is_deletable.py` checks three import directions — `src/ ⇏ service/`, `tools/ ⇏ service/`, `service/ ⇏ tools/` — and then does the thing a lint cannot: denies `import service` at the interpreter level and runs `pipeline.run()`, which catches the lazy in-function import that is how the dependency would actually arrive. See [D28](#d28).

---

## Scope and sequencing

### D25 — The engine port is trigger-gated, not scheduled {#d25}
A polars rewrite (with the pandas engine retained as a diff oracle) was planned and then **descheduled on 2026-08-12**, before any polars was written. The stated motivation had been "to account for large datasets".

*Why it was dropped:*

- **Volume doesn't support it.** The largest month is 427,917 Shopee order rows; row-level verification covered ~288,000. pandas is comfortable an order of magnitude above that.
- **The measured bottleneck is I/O.** `settings.yaml:31-34` records 45+ minute runs caused by cloud-sync contention on Excel reads; the fix that worked was the `calamine` reader engine ([D11](#d11)). Polars touches neither the sync contention nor openpyxl.
- **The harness would have decayed, not compounded.** Goldens were keyed to `oracle_rev`, which hashes `src/` + `config/`. Config changed in *every month tested* — 13 entries in the drift log. Building the harness now and porting later means building it twice.
- **Porting later costs the same.** The cost scales with the compute layer, and M4–M7 don't grow it: FastAPI, the job queue, Next.js and the exception UI never touch a DataFrame.
- **The Python bound is temporary.** See [D20](#d20).

*What replaces it:* the *option*, not the migration — the M1 seam, an I/O-boundary lint, and instrumentation that separates compute from I/O so the decision is a measurement ([D27](#d27)). Thresholds are in [10-ROADMAP](10-ROADMAP.md#the-engine-port-is-trigger-gated).

*Confirmed by measurement (M1, 2026-08-13).* The decision was originally argued; it is now instrumented. Across the three May windows, DataFrame compute is **1.3%–3.8% of wall time** (2.3–2.5 seconds of a 120–171 second run). The costs are Excel reading (~66%) and openpyxl workbook materialization (~32%), neither of which a DataFrame engine touches. A 5× faster engine would save about two seconds. Peak RSS is 832 MB, 41% of a 4 GB container. See [10-ROADMAP](10-ROADMAP.md#what-the-instrumentation-actually-found).

*Cost of being wrong:* if volume grows 5× or a container turns out memory-bound, the port happens later on a codebase with a live web app depending on it — higher stakes and harder to freeze. Accepted, because the seam keeps the port mechanical and dtype tuning (`string[pyarrow]`, `Categorical`) is a cheaper first move.

*This decision expires if the boundary lint is deleted or exempted.* The lint is what keeps the bet reversible.

### D26 — The parity harness is repurposed as a golden-file regression gate {#d26}
The workbook differ was built for cross-engine parity. On descheduling the port, deleting it outright was the first instinct and was wrong: most of it is not engine-specific, and [12-CHANGE-HISTORY](12-CHANGE-HISTORY.md#durability-gaps-that-were-predicted) records "no regression tests over settlement bounds and the template exporter" as an open gap. The differ closes it.

*Kept* (→ `tests/goldens/`): `cellset.py`, `diff.py`, the differ self-test, the manifest-integrity/PII guards, and the committed digests.
*Deleted:* `fingerprint.py` and the stage-instrumentation half of `make_golden.py`. Per-stage fingerprints existed to localize a divergence *between engines*; with one engine a cell diff is already localized.
*Moved:* `stage_exports.py` → `tools/`. It is production-adjacent tooling — it already fixed the Lazada folder-name defect — and is the seed of the M2.5 normalizer.

*`oracle_rev` is dropped.* It bound a golden to an exact source tree so a parity diff could be attributed to engine or config. A regression gate wants deliberate re-baselining instead: one `tests/goldens/manifest.json`, moved only by `make_golden.py --rebaseline --reason "..."`, with `git diff` on that file as the audit trail — which is what `oracle_rev` was approximating.

*Cost:* ~350 lines deleted, ~865 retained. The retained code has no engine dependency (stdlib + openpyxl), so its ongoing maintenance is close to zero.

### D27 — Instrumentation separates compute from I/O — and from serialization {#d27}
`src/metrics.py` tags every stage `io`, `compute` or `serialize` and records wall time, row counts and peak RSS.

*Why the split is load-bearing rather than nice-to-have:* the runs are dominated by Excel read/write, so total wall time cannot answer "would a faster compute engine help?" — it is the one question the number is asked to answer. Without the tag, [D25](#d25)'s trigger is unmeasurable and the port decision stays a matter of opinion.

*Why three categories and not two.* The first implementation had only `io` and `compute`, and it gave the wrong answer. `build_workbook` spends 30–39 seconds constructing openpyxl cell objects; tagged `compute`, it pushed the measured share to **31% and fired the 25% trigger**. But openpyxl materialization is engine-independent — no DataFrame library changes it. Tagged as its own `serialize` kind, actual compute is ~2%.

    io          reading and writing files
    compute     DataFrame math — the ONLY thing a different engine would change
    serialize   building the openpyxl workbook — engine-independent

`compute_share` therefore puts serialize time in the **denominator but never the numerator**. One mis-tagged stage inverted the verdict on the project's largest open architectural question, which is the argument for keeping the taxonomy narrow, explicit and tested (`tests/test_metrics.py::test_serialize_time_does_not_count_as_engine_addressable`).

*A second silent-zero caught the same way:* peak RSS reported 0 MB on every run because `GetCurrentProcess()` returns a pseudo-handle that ctypes truncated to 32 bits, and the failure path returned the fallback. A metric that is always 0 never fires a trigger and never looks broken — so the test asserts it is **greater than zero**, not merely that it runs.

*Why RSS and not `tracemalloc`:* `tracemalloc` counts Python allocations and misses numpy/Arrow buffers, which is exactly the exposure being measured. Peak working set comes from the OS — `GetProcessMemoryInfo` on Windows, `getrusage` on POSIX. The POSIX branch is not hypothetical: the M4 worker runs in a Linux container, and the container limit is what the memory trigger is really about.

*Kept out of `RunLog` deliberately:* `QueueRunLog` substitutes for `RunLog` under the web app ([D24](#d24)) and should not have to implement a metrics protocol to do it. Metrics ride on `RunResult`. *Confirmed in M4:* `QueueRunLog` exists and implements no metrics protocol; the worker copies `RunResult.metrics.to_dict()` onto the run row as columns.

---

## The M4 service (2026-08-13)

### D28 — The service is a wrapper, and its deletability is a test {#d28}
`service/` imports `src/`; nothing in `src/` or `tools/` may import `service/`. Delete the directory and `tools/devrun.py` still produces the month's invoicing workbook.

*Why a test and not a rule:* this is the same situation as the I/O boundary before M1 — a property that was already true, that nothing enforced, and that would therefore have eroded. One `from service import current_run_id` in `src/calculate.py` for "just the run id" is a two-line change that makes month end depend on Postgres being up.

*What made it real rather than declarative:* `build_context` lived in `tools/full_run.py`, and the worker needs it. The three options were duplicate it in the worker, import `tools/` from the service, or move it into `src/pipeline.py`. The first duplicates the `_vat_sku` back-channel, which silently changes numbers if it diverges. The second breaks the deployable unit — the container ships `src/` + `service/` and no `tools/`. So it moved, along with `EXIT_CODES` and the `RESULT` log section, each for the same reason: **two callers, and the copy in the worker would have been the one that drifted.** All three moves were gated on the goldens and came out byte-identical across all eight windows.

*Cost:* `pipeline.py` grew ~90 lines of assembly that is not computation, and `full_run.py` shrank to reading one JSON file. A reader looking for "where does a run get its settings" now finds it in `src/` rather than in the driver.

### D29 — Hand-written SQL in one file; no ORM, no migration framework {#d29}
Four tables in numbered `.sql` files, applied in order and recorded by filename and content hash. All SQL lives in `service/repository.py`.

*Why:* the queue's correctness is one `FOR UPDATE SKIP LOCKED` statement, and an ORM renders exactly that as `.with_for_update(skip_locked=True)` — a keyword argument that reads like a config flag rather than the lock it is. Keeping the statement visible is the point. Alembic would add a dependency, a code generator and an autogenerate diff nobody reviews, to manage a handful of files that already read as the schema.

*What the migration table is actually for:* refusing an **edited** migration. Two databases recording the same history with different schemas is the one failure mode it exists to prevent, so a changed hash is an error rather than a re-run.

*Cost:* no automatic downgrade path, and column changes are hand-written. At four tables that is cheaper than the machinery.

### D30 — Job state and run status are two axes, and retries are for infrastructure only {#d30}
`jobs.state` answers "did the worker manage to execute this?"; `runs.status` answers "what did the run conclude?" and is `src.pipeline.RunStatus`, not a second copy of those four values.

*Why the separation is load-bearing:* a run that hard-stops on bad input is a job that executed perfectly. Collapsing the two would make a data problem indistinguishable from a dead worker — the same conflation that made exit code 1 useless before M2, where "not checked" and "disagrees" shared one number.

*The consequence that matters:* `max_attempts` defaults to **1**, so nothing is retried automatically. An automatic retry of a settlement run is a second write of the same money, and the only failure it can fix is infrastructural — an OOM kill, a container eviction. Bad input retried produces the same answer and hides the problem ([D3](#d3)). A hard stop therefore ends as `state=done, status=hard_stop`, and only a lease expiry can consume an attempt.

*Also:* `runs.status` is **NULL** while a run is in flight, rather than gaining a `running` value. There is no `RunStatus` for "still going", and inventing one would put a value in the column that `src/` cannot produce. `finished_at is null ⇔ status is null` is a database constraint, and the reclaim sweep closes any run a dead worker left open so that "in flight" keeps meaning it.

### D31 — The worker writes artifacts through `write_artifacts`, then uploads {#d31}
The worker calls the same `pipeline.write_artifacts()` the CLI calls, pointed at a per-job scratch directory, and the artifact store then takes the finished files.

*Why not a store-aware writer:* the obvious reading of "the worker streams to object storage while the CLI writes to disk" gives the worker its own writer. That writer would be a second implementation of the code path that produces the deliverable the team invoices from — and since the goldens are generated through the CLI, the service's copy would be the unverified one ([D12](#d12), [D19](#d19)). Write-then-upload keeps `run()` and `write_artifacts()` as the only two functions either caller uses, and makes the bytes identical by construction rather than by inspection.

*Evidence it holds:* the service's `2026-05_l1` workbook matches the committed golden digest cell for cell ([07-VERIFICATION](07-VERIFICATION.md#the-m4-service-gate)).

*Cost:* one local copy per artifact. For a cloud store that copy *is* the upload.

### D32 — The log's sequence is producer-assigned and gapless; polling before streaming {#d32}
`QueueRunLog` numbers each line from 0 and the api serves `?after_seq=N`.

*Why not a database sequence:* a `bigserial` has gaps under concurrency, and a gapless per-run counter is what lets a client prove it lost nothing rather than guess. It also makes a re-flush idempotent — the numbers travel with the batch, so `on conflict (run_id, seq) do nothing` handles a network wobble without duplicating or reordering a line.

*Why polling first:* streaming dies silently through corporate proxies and is miserable to debug at month end. Server-sent events would push the same rows in the same order with the same `seq`, so the schema does not change when they arrive. The part that is genuinely *streamed* today is the producer: lines reach Postgres mid-run, so a 171-second run can be watched rather than read afterwards.

*A second job the flush does:* it beats the job's lease. Liveness is then "is this run still saying anything", instead of a timer thread that would keep a hung run looking healthy. The lease (900s) must therefore outlast the longest **silent** stretch of a run, not the run — the quietest measured stage is openpyxl materialization at 30–39s.

*And a flush never raises.* The pipeline is midway through producing the month's workbook; a database hiccup must cost log lines, never a finance file. Failed lines stay buffered and the authoritative copy is still `run_log.txt`.

### D33 — One active job per settlement window, enforced by the database {#d33}
A partial unique index on `(platform, period) where state in ('queued','leased')`.

*Why:* two concurrent runs of one window is the double-invoicing shape this pipeline already defends against through one-folder-per-window discipline ([D5](#d5), [D9](#d9)) — and a queue with a button would reintroduce it in one impatient double-click. Scoping the index to live states keeps a deliberate re-run after a config fix legal, which is normal and necessary.

*It is not the same guard as an idempotency key,* and both exist: the key makes a **retried request** return the same job, the index refuses a **second opinion** about the same window. A retried POST answers 200 with the original job; a genuine second request answers 409 carrying the existing job, so a UI can link to the run already in flight instead of telling the operator to go and find it.

### D34 — `QueueRunLog` subclasses `RunLog` rather than reimplementing it {#d34}
The duck-typed logger contract is four methods, and `QueueRunLog` could have implemented them from scratch. It inherits instead, overriding only `add`, `warn` and `section` to also buffer.

*Why:* `run_log.txt` is an artifact operators read and the team keeps. A second implementation would format its own section rules and its own `WARNING: ` prefix, and the two copies would drift — silently, one whitespace at a time, because the golden gate covers the workbook and not the log. Inheriting means there is exactly one implementation of the text and the subclass only decides where else it goes. `tests/service/test_runlog_mirror.py::test_the_log_file_is_identical_to_a_cli_run` drives both loggers through the same calls and compares the files; `test_worker.py::test_the_stored_log_and_the_database_log_are_the_same_log` checks the two copies of a real run against each other.

*It does not weaken the duck typing.* Nothing runs an `isinstance` check, `RecordingLog` in the test suite is still an unrelated class, and `test_the_pipeline_accepts_it_without_an_isinstance_check` still pins the substitution.

---

## The M5 web app (2026-08-14)

### D35 — Bearer tokens now, with the Entra seam already in place {#d35}
Authentication is hashed bearer tokens with three roles. Entra ID SSO is the destination and is **blocked on a tenant app registration**, which needs directory permissions a developer does not have ([13-ENTRA-SETUP](13-ENTRA-SETUP.md)).

*Why not wait for Entra:* M5's whole justification is that it builds the thing the roadmap's gate warns about — a status a finance user reads in a browser. Shipping that on top of an unauthenticated api, and a config editor that **writes**, was not an option. Waiting for an access request that is not in this project's control would have blocked every other surface behind it.

*Why the substitution is cheap:* the seam is `Principal(subject, role)`, and nothing downstream asks how it was established. The role strings are Entra's own — `recon.viewer`, `recon.user`, `recon.admin` (`operator` was renamed `user` in M6, which was nearly free because `api_tokens` was being dropped anyway and the check constraint was written fresh) — so an OIDC `roles` claim maps onto them directly. Swapping the identity provider changes *who vouches* for a role, not what a role means.

*Why SHA-256 and not bcrypt/argon2:* slow KDFs exist to make **low-entropy** secrets expensive to guess. A token here is 32 bytes from `os.urandom`; brute force is not on the table and a slow hash would only add latency to every request. This depends on tokens being GENERATED, never user-chosen, which `service/auth.py` is the only thing able to do.

*Cost, stated plainly:* every token is issued and revoked by hand, and there is no central "this person has left" event. That is acceptable for an internal prototype and is not a substitute for directory-managed identity.

### D36 — Authentication fails closed, and refuses rather than warns {#d36}
`auth_enabled` is true unless `RECON_AUTH_DISABLED` is **present** — presence, not truthiness, so `RECON_AUTH_DISABLED=false` cannot become a trap. Binding a non-loopback host with auth off raises at startup.

*Why refusing rather than warning:* a warning is for something you might legitimately want. An unauthenticated api on a routable address can queue settlement runs, read client revenue and rewrite the config the money math uses; there is no deployment where that is the intent. M4 had exactly this as a printed warning, and [defect 2.1](08-KNOWN-DEFECTS.md) recorded that a default is not a control.

*The corollary:* `requested_by` now comes from the authenticated subject and a request body that claims otherwise is ignored. For a system that produces invoices, "who asked for this run" has to be a fact, and through M4 it was a caller-supplied string.

### D37 — A window is pinned to the config its first successful run used {#d37}
`config_versions` stores the full text of every config a run has used; `period_config` pins a window to one; `runs.config_version_id` records what each run actually ran under.

*Why the whole file and not a parsed structure:* the comments **are** the audit trail ([D2](#d2)). Storing parsed YAML would discard precisely the part that carries the evidence for each value.

*Why pinned on first run rather than at configuration time:* it makes an ordinary first run behave exactly as it did before M5, and protects only the case that matters — a **re-run** producing different numbers than the run its invoice came from, because an unrelated edit landed in between. A hard stop pins nothing, since the fix for it may well be a config change.

*Cost:* a pin is automatic and per-window; there is no "this month runs under version 7" and unpinning is an admin action with a warning rather than a workflow.

### D38 — The config editor takes one path and one value, never a document {#d38}
`POST /config/proposals` accepts a dotted path, a scalar, and a stated reason. It refuses to create keys.

*Why not a YAML text box:* accepting a whole document makes the api a way to replace the domain contract wholesale, and no diff review reliably catches a subtle change in a 300-line file. One path and one value produces a **one-line diff**, which a human can actually check.

*Why creating keys is refused:* every key in that file exists because something reads it. A new one produces config the pipeline ignores, which then presents as a bug in the pipeline.

*Why apply refuses a moved base:* a three-way merge of a file whose comments are evidence would produce something nobody wrote and everybody would later have to defend. Withdraw and re-propose against the current file.

*Why the write still lands when `git` is unavailable:* the database row — who proposed, who approved, when — is the audit record that matters; git is the reviewable *form* of it. Refusing a config change because a container has no `.git` would be the wrong trade.

### D39 — Who may approve a config change is a deployment setting, not a decision this repo makes {#d39}
~~`RECON_CONFIG_APPROVAL` is `separate` (default), `same`, or `disabled`.~~ **Superseded by [D47](#d47) in M6:** open question 13 is answered, so the policy object and the variable are deleted and the rule lives on the route. Self-approval is permitted and recorded rather than forbidden.

*Why it is not decided here:* [open question 13](11-OPEN-QUESTIONS.md) asks a human who owns configuration and who signs off a rate change, and nobody has answered. Baking in an approval model would be inventing a control and then relying on it.

*Why the default is the strict one:* a one-admin deployment has to **choose** `same`, and thereby notice the question, rather than inherit an answer nobody made. What the schema does fix regardless is that proposing and approving are separately recorded, so whichever policy is chosen can be audited against.

*This is honest rather than resolved.* A deployment running `same` has one person on both ends of a config write path, and no schema makes that a control ([defect 2.7](08-KNOWN-DEFECTS.md)).

### D40 — PII is stripped at the upload boundary using the pipeline's own allowlist {#d40}
`POST /uploads` keeps exactly the columns in that platform's column map and discards the rest, then deletes the unstripped original before the request returns.

*Why reuse the column map instead of a PII denylist:* `ingest.read_parts` already does exactly this at read time, from the same config. A separate list of PII column names would be a second thing to maintain, and the failure mode of a stale denylist is silent.

*Why strip at all, when ingest already does:* ingest strips what it reads into *memory*; the file itself would sit on disk, in a backup, and in whatever the host does with volumes. The roadmap's phrasing is exact — *PII stripped at the upload boundary; raw uploads on short retention*.

*The risk this creates, and how it is answered:* rewriting an export inserts a transformation into the path that produced every verified number. That is gated, not assumed — a real window is sanitized and its workbook matched against the committed golden cell for cell ([07-VERIFICATION](07-VERIFICATION.md#the-m5-gate)).

### D41 — The web app is a BFF in a container, not a Vercel front end {#d41}
`web/` is Next.js in its own image. Only it is published; the api, worker and database stay on the private network.

*Why a container rather than Vercel:* the workload is wrong for serverless — a run is 171 seconds of CPU-bound pandas at 832 MB peak RSS, holding a 900-second lease, writing artifacts to a filesystem. Vercel would only ever have hosted the front end, which means two vendors for a prototype.

*What the BFF actually buys:* the bearer token lives in an httpOnly cookie and is attached **server-side**, so the browser never holds a credential that can queue settlement runs. A front end talking directly to a public api would put that token where any XSS can lift it. It also means the api needs no public address at all.

*Why the private network is not the control:* it is defence in depth. The BFF is a deliberate hole through it, any service in the project can reach the api, and publishing a port is one dashboard click. Authentication ([D35](#d35)) is what closes the defect; the topology reduces the blast radius.

*Cost:* a second language and toolchain for a single maintainer — the standing bus-factor risk. Kept as small as possible: 27 npm packages, no UI framework, no state library, no generated client.

### D42 — The config form extracts evidence rather than stripping it {#d42}
`config/settings.yaml` renders as editable sections, and each field carries the comment block from the file above the control that changes it. `config_store.evidence_for()` reads it out of the same bytes the form is editing.

*The objection this answers, in the old page's own words:* "It does not render settings.yaml as a form. The file's in-line comments are the audit trail... A form would show values stripped of the evidence for them." That was correct, and it had to be answered rather than ignored — the alternative was a text box requiring the operator to already know a dotted YAML path, which is unusable by the people who own the rates.

*Why this is strictly more evidence, not less:* the four-line VAT block now sits directly above the box you type `1.10` into. In the old UI the same comment was 400 lines down a `<pre>` nobody scrolled. The verbatim file is still on the page.

*Read from the text, not from ruamel's comment attribute — measured.* ruamel attaches a comment block to the key **preceding** the one it visually documents: `vat_rate`'s comment slot holds the VAT-model block belonging to `vat_factors`, `tolerances`' slot holds the block above its first child, and `expected_stores` has no slot at all. Rendering `.ca` would caption almost every field with the previous field's justification — worse than showing none, because it would look authoritative and be wrong. So ruamel locates the line and the raw text supplies the block, which is what a reader does and cannot mis-attribute. A key with no block of its own inherits its parent's.

*Two fields stay uneditable, and are shown as such rather than hidden:* `drop_unmapped_columns` is the PII control in two places and its diff reads as an ordinary boolean flip, so it is `LOCKED` with its reason; `vat_rate` and `periods.rolling_window_months` are read by nothing and are rendered `DEAD`. A field missing from a form is indistinguishable from a field nobody thought about.

### D43 — Artifacts and uploads live in object storage, not on a shared volume {#d43}
`service/objects.py` provides an `ObjectStore` with two real implementations — `S3Objects` (MinIO/S3/R2 via boto3) and `LocalDirObjects` — and `ArtifactStore` grows one method, `stream()`.

*Why this was not optional.* `api` and `worker` were already separate containers, and the worker writes the workbook bytes while the api serves them. Railway's documentation, verified: **"Each service can only have a single volume"**, with no cross-service mounting. So a shared filesystem is not expressible there, and `GET /runs/{id}/artifacts/{name}` would have returned 501 forever in production while passing every test locally — the worst shape a defect can have.

*Why not presigned URLs.* A presigned URL is a bearer credential in a query string that `service/auth.py` never sees. For its whole lifetime anyone holding the link downloads a workbook containing every store's revenue, with no role check and no audit line. The api streams instead: one proxy hop, and every download stays inside the authorization model.

*Why boto3 and not the minio client.* S3's vocabulary is what MinIO, Railway, R2 and S3 itself all speak, so one implementation reaches a local container and a managed bucket.

*Why two buckets.* `recon-uploads` and `recon-artifacts` have opposite retention — raw client exports must expire, the deliverable the team invoiced from must not. A lifecycle rule is per-bucket, so one bucket would mean either keeping PII forever or deleting the evidence. `minio-init` applies the expiry rule, which is also the first actual *mechanism* behind the short-retention promise [04-DATA-FLOW](04-DATA-FLOW.md) has always made.

*`LocalDirObjects` is not a test double.* With no `RECON_S3_ENDPOINT` it is what the api and worker use, which is what keeps the whole upload to download path exercisable without a container and every M4 worker test passing verbatim.

### D44 — Uploaded exports are renamed to a uniform scheme, and the rename is a machine-checked fixed point {#d44}
Files materialise as `NNN.order <store>.xlsx` (TikTok), `NNN_order. <store>.xlsx` (Shopee), `NNN_<store>.xlsx` (Lazada).

*Why this is not cosmetic:* store identity is derived from the filename ([D6](#d6)) because the exports carry no store column. A rename that got it wrong would silently reassign a storefront's revenue.

*Four properties, each against a measured failure.* Nothing is appended after the store name, because TikTok's own pattern eats a trailing bare 1-2 digit token and Shopee's eats ` part N` — so the ordinal goes in the prefix, which TikTok already required. The ordinal is zero-padded to fixed width, because `read_parts` reads `sorted(iterdir())` and concatenates in that order, and workbook row order follows: `9` before `10` lexicographically would move cells in the invoicing file. Lazada's `(N)` browser-duplicate marker disappears, since five weekly exports of one store are five different settlement weeks. And the target extension is always `.xlsx`, closing a latent bug where a `.csv` upload was written as openpyxl bytes under a `.csv` name and then handed to `pd.read_csv`.

*The ordinal is assigned at materialisation, never at upload.* It is a property of the whole window; assigning it on arrival would let two concurrent uploads race to decide workbook row order. Object keys are content-addressed instead.

*`validate_roundtrip` is the invariant.* It re-runs *the pipeline's own* `src.ingest.store_from_filename` — deliberately made public rather than copied — on every generated name and refuses if the store does not survive. Measured over the real tree: `derive(uniform(derive(x))) == derive(x)` for **73/73** real exports across all eight golden windows, and `sorted(new)` preserved `sorted(old)` in 12/12 folders. The gate that makes it trustworthy is `test_a_sanitized_renamed_window_produces_the_committed_golden`, extended in M6 from Lazada alone to all three platforms.

### D45 — A config change that can move a cell triggers a measured verification run {#d45}
Applying a proposal that touches a field marked `invalidates_goldens` re-runs a canary window under the new config and compares its workbook to a committed golden at zero tolerance. The outcome is recorded on `config_versions`. **Nothing is blocked.**

*Why not the obvious thing, which was already tried.* M1's `oracle_rev` keyed every golden manifest on a hash of `src/` + `config/`, so any change to either orphaned every golden, the manifest lookup missed, and the zero-tolerance gate silently degraded into a **skip** ([D26](#d26)). A gate that turns itself off when the code changes is worse than no gate, because it reports green.

*This inverts the assumption.* Instead of declaring every golden invalid, measure whether the change actually moved anything. Most changes — a tolerance, an alias, a roster addition — move nothing and say so, which is the outcome `oracle_rev` could never report because it could not tell "unchanged" from "unknown".

*Five states, deliberately distinct:* `verified`, `cells_moved`, `unavailable`, `failed`, `not_applicable`. Collapsing them into a boolean is exactly how "we never checked" comes to read as "we checked and it was fine". An **unknown** path counts as invalidating, for the same reason.

*Which window answered is part of the answer.* A real committed golden exercises the real column maps, sheet names and header spellings; the synthetic demo window only exercises the paths its own generator emits, so a `column_maps` edit for a header the generator never writes would move nothing there and everything in production. The canary resolves real, then demo, then none, and `verified_window_is_real` carries the weakness into the database so the UI cannot render the two as one statement.

*A verification failure never undoes the applied change.* The config is on disk and in git by then; claiming success is the one unacceptable outcome, so a broken canary is recorded as `failed` and returned.

### D46 — The partial-roster override is a per-window declaration, not a per-run checkbox {#d46}
`ingest.check_stores` is **unchanged** and still hard-stops when a window's stores do not match the roster. What M6 removed is `EnqueueRequest.partial_roster` and the queue-form checkbox; the override is now a row in `windows` with a mandatory reason and a named author.

*Why the checkbox was the problem, not the hard stop.* The control that caught a real Shopee window arriving with 16 of 17 stores absent is worth keeping. But a checkbox on the queue form had all three properties a control should not have: ticked by whoever was in a hurry, no reason recorded, and invisible to whoever reviewed the numbers afterwards.

*Absence fails closed.* No declaration means the hard stop applies, which is today's behaviour.

*Deliberately not done:* stamping `ROSTER: n expected store(s) absent` into the finance workbook's control block, so the artefact the team invoices from carries its own caveat. That is the stronger control and it **moves workbook cells** — it needs its own commit and a deliberate rebaseline, so it is deferred rather than smuggled into a change that must be output-identical.

### D47 — The approval model is a decision, and self-approval is recorded rather than forbidden {#d47}
`RECON_CONFIG_APPROVAL`, `ApprovalPolicy` and `ApprovalDenied` are deleted. `recon.user` and `recon.admin` propose; `recon.viewer` cannot; only `recon.admin` approves, rejects or applies.

*Why they existed and why they go.* The three-mode policy object was honest scaffolding for an unanswered question — [open question 13](11-OPEN-QUESTIONS.md), who owns configuration and who signs off a rate change. It is answered, which closes [defect 2.7](08-KNOWN-DEFECTS.md). With an answer, a configurable policy is just a way for a deployment to weaken it, so the rule moved to where every other authorization rule in this service lives: the role on the route, walked by `test_auth.py`.

*Why self-approval is permitted.* M5's default refused it with a 403. In a single-admin deployment that did not produce a second reviewer — it produced a hand-edit of `settings.yaml` with no proposal, no diff and no audit row. `config_proposals.self_approved` is a **generated** column, computed from the two names, so it cannot be set to a convenient value and a reviewer counting them is reading a fact.

*The honest form of closing 2.7:* this is **recorded evidence, not separation of duties**. No schema can invent a second person.

### D48 — A proposal records the edits, so a stale one is replayed rather than retyped {#d48}
`config_proposals.edits` stores the operations that were requested; `POST /config/proposals/{id}/rebase` replays them against the current file into a new pending proposal.

*Why the resulting file was not enough.* With only `content` and `diff`, a proposal whose base had moved could only be refused — and the change then had to be retyped from memory against a file that had changed, which is how a two-line intent becomes a three-line diff.

*Rebase is not a merge.* [D38](#d38) refuses a three-way merge of a file whose comments are evidence, and it is right: a merge produces something nobody wrote and everybody would later have to defend. A replay re-runs the stated intent and produces a fresh diff for a fresh review.

*A pre-M6 proposal cannot be replayed, and says so.* Guessing at what it meant would be inventing an audit trail.

### D49 — Removing a commented config entry asks what happens to the comment {#d49}
`remove_list_item` and `remove_map_entry` refuse until the caller states `comment_disposition`: `remove` (the block described only this entry) or `keep` (it describes the group).

*Measured, and it corrects a claim the M6 plan made.* The plan asserted that removing a commented item takes its comment with it — "200 to 199, which is the desired semantics". Against the real file that holds **only for an EOL comment**. For a comment **block** above the item, ruamel leaves the block exactly where it was: removing `"Merries"` from `stores_optional.tiktok` left its two-line July-w5 justification captioning `"Veet & Reckitt Personal Care"`, a store it does not describe.

*Why it is a question and not a refusal.* Evidence silently re-attached to the wrong entry is the worst outcome available to a module whose entire purpose is preserving evidence — but a blanket refusal would be a dead end for an ordinary action. Whether a block describes one entry or a group is a judgement only a human can make, so it is asked at the point it matters.

*A related fix, found by the same test.* Appending to a list whose trailing block introduces the **next** key left the new item under that block — a new TikTok store rendered beneath a comment announcing Shopee's roster. The item is reflowed above it.

## M8 — production readiness (2026-08-18)

### D50 — Configuration is normalized into Postgres, and the file is rendered from it {#d50}
Eleven `config_*` tables hold the contract; `service/config_render.py` turns them back into `settings.yaml` text; that text is what `config_versions` stores and what a run is pinned to. `config/settings.yaml` in git is the **seed** and the CLI's input.

*This reverses [D2](#d2), and the reversal is argued rather than waved through.* D2 kept config as git-backed YAML because a database "would destroy the evidence comments and make month-end depend on the app being up". Both objections are answered by the shape, not by disagreement:

- **Evidence became a column, which is strictly stronger than a comment.** It is queryable, it carries an author and a date, and it cannot be orphaned by an edit to a neighbouring key — a real failure mode the previous editor had to model ([D49](#d49)).
- **The rendered file still exists**, comments and all, and is still what a run is pinned to. The tables are the editable working set; `config_versions` remains the archive.
- **Month-end does not newly depend on Postgres.** The worker already cannot claim a job without it, and `python -m service.admin config export` writes the rows back to the file, so `tools/devrun.py` and the golden gate never require the service ([D24](#d24)).

*The reason to do it at all was not tidiness.* A config change applied in the browser did not reach the worker: `config/` is baked into each image and no volume joins them, so the api wrote `settings.yaml` into its own container's writable layer and the change was lost on restart (register A1). Rows in a shared database are how one edit reaches both.

*The seam made it cheap.* `build_context(settings_text=…)` takes a YAML **string**, so `src/` never learns about Postgres, the I/O-boundary allowlist is unchanged, and `service/` stays deletable.

*Gated, not asserted:* all eight golden windows re-run under rendered config — **matched 8, moved 0**.

### D51 — Config is edited as rows, and the dotted-path editor is deleted {#d51}
An edit names a table and a row: `upsert` or `delete`, and nothing else. `service/config_edits.py` and `service/config_schema.py` are gone, replaced by `service/config_rows.py`.

*Three things stopped being handled and started being impossible.* [D49](#d49) is retired outright — a row deletes its own evidence, so a comment cannot end up captioning its neighbour, and `comment_disposition` has nothing to ask about. Per-entry evidence became servable: a file can only caption a top-level key, so the M6 editor showed the roster's justification against all 42 storefronts in it. And "can this change move a workbook cell" stopped being inferred from a dotted path — it is a column on every table (migration `008`), so [D45](#d45)'s verification run is *given* the answer rather than resolving a path back to a declared field that may no longer exist.

*What did not change, deliberately.* Propose → approve → apply, the `self_approved` generated column, `base_sha256` optimistic concurrency, and the refusal to merge ([D38](#d38)). What `base_sha256` compares is now the **rendered** contract, so the check is against what the worker would run rather than against one container's disk copy.

*The honesty rule survived the rewrite in a stronger form.* `open_container` became `may_insert` / `may_delete` on a table that must state `closed_reason` — and the refusal quotes it. `config_scalars` is closed because `src/` reads its keys by name; `config_tolerances` is closed because a tolerance nothing reads is precisely what Phase 1.1 deleted three of.

*An unseeded deployment refuses rather than falling back.* Editing this process's copy of `config/` would recreate A1 exactly, so the editor returns 503 naming `python -m service.config_import`, and `build_app` seeds the tables once on first boot.

### D52 — Integrity is checked against the digest of what was STORED, not what was uploaded {#d52}
`uploads.sha256` and the bytes in the object store are two different files, and conflating them is a bug that hides for as long as nobody checks.

`sha256` digests what the user handed over. It is the provenance record, and it is what the unique constraint uses to refuse a byte-identical re-upload — the M2.5 double-pull control moved to the door ([D9](#d9)). What actually goes into the store is the **sanitized rewrite**: PII columns stripped, one sheet, written by openpyxl. Different bytes, on purpose, and that difference is the entire value of the upload boundary.

So [defect 2.10](../docs/08-KNOWN-DEFECTS.md#210) could not be closed the way it was written up ("`uploads.sha256` sits unused ten lines from the download"). Comparing a materialised file against it failed every healthy window on the first run. Migration `010` adds `object_sha256`, recorded from the sanitized bytes, and that is what `materialize.verify_digest` compares.

*No backfill, and the refusal to backfill is the decision.* Recomputing the expected digest for existing rows means reading whatever is in the store today and writing it down as correct — which certifies the store against itself and passes even if the bytes had already been replaced. That is [D26](#d26)'s failure with a different name. `NULL` means "uploaded before this check existed" and is **refused** at materialisation; the upload has to be re-made. A trust-on-first-use fallback would have been the easy option and would have made the check ornamental.

### D53 — An unreadable date is counted, but does not stop the run {#d53}
The mirror of [D3](#d3), deliberately set one notch lower, and the asymmetry is the point.

A settlement export never legitimately contains an unparseable **amount**, so one hard-stops. A **date** can legitimately be blank — `apply_settlement_bounds` has always kept and reported undated income rows — so hard-stopping on one would refuse windows that are fine. The default is `warn`, with `date_coercion: hard_stop` for an operator who has looked and decided.

What was wrong until M8 was never the leniency, it was the silence: money went through `report_unparseable` while dates went through a bare `errors="coerce"` with no counter at all. And the date failure is the quieter of the two — an unreadable amount produces a *wrong* number, an unreadable date produces a **missing** one, because `finance_template` groups on `.dt.month` and pandas drops a `NaN` group key by default. The row's money leaves the invoice with nothing said.

*The related warning is captured rather than silenced.* pandas infers a date format from the first element and warns when that format contradicts the configured `dayfirst`. That warning is the exact signal that the contract and the file disagree, and it went to stderr and died there. It fires on real TikTok income today (`%Y/%m/%d` against `dayfirst.tiktok: true`), where the dates parse correctly *only* because the first value of that column is unambiguous.

*Declaring the format is the durable fix and is deliberately not in this commit.* Explicit parsing is stricter than inference and can turn a row dateutil forgave into a `NaT`. The counter has to run first and come back clean, or a format change and a golden move land together with nothing to attribute either to — [D18](#d18) restated.

### D54 — The settlement date's format is declared per platform/kind, not inferred {#d54}
The fix [D53](#d53) deliberately deferred, landed on 2026-08-19 once the counter had run clean — and July is the month that proved it was not theoretical.

`dayfirst` is per-PLATFORM. The formats are per-KIND, and no boolean can express that: TikTok **orders** really are `%d/%m/%Y %H:%M:%S` while TikTok **income** is `%Y/%m/%d`. pandas infers a format from the first non-null element and `dayfirst` only decides the ambiguous case, so which one wins is *data-dependent*. May's first `Order settled time` value happened to be unambiguous, inference quietly overrode the flag, and everything was correct by luck. July's first value is `2026/07/07` — ambiguous — so `dayfirst=True` won and the whole column parsed as `%Y/%d/%m`. A window covering 1–7 July came out spanning `2026-01-07..2026-09-07`, and `tools/stage_exports.py` could not derive a single window from a 3.7 GB dump.

`date_formats.<platform>.<kind>` takes precedence over `dayfirst`, and `ingest.date_format` is the one accessor `src/`, `service/` and `tools/stage_exports.py` all read — the stager had been reading `dayfirst` directly, which is how it became a second source of truth for a spelling.

*Every value was measured, not assumed.* Each format parses 100% of the non-blank cells in its date columns across **both** the May set (the golden windows) and the July set — 10 files per platform/kind, 80 for Lazada. The measurement found something no one had noticed: Lazada's two variants **disagree**. Weekly writes `03-Jul-2026` and Daily writes `29 Jul 2026`, so the ledger is parsed per variant. A single format over the concatenated frame would have `NaT`-ed every row of whichever variant lost, and the 25th-to-month-end Daily week is a permanent monthly fixture, not a corner case.

*Explicit parsing is stricter, and the golden gate is what licensed it.* All eight golden windows were regenerated with the change in place and **not one cell moved**. That is the evidence, and it is the only thing that made this safe to land — [D18](#d18) again.

### D55 — The month-end master is a chained job, never part of the settlement run {#d55}
A window run that finishes with a workbook enqueues `kind='month_master'` for its month. It does not build the master inline, and both halves of that matter.

A settlement run **must not fail because a cross-month aggregation failed**. By the time the master could run, the workbook a person invoices from is already written and stored; letting a summary turn that into a failed run would be the tail wagging the dog. So the enqueue is best-effort and a failure to queue is logged, not raised.

It also cannot share the window's artifact set. That set is golden-gated — appending a second, differently-shaped workbook to it would move every window's manifest for a file that has nothing to do with that window. The master gets its own run record, its own log and its own artifacts, and shows up on the month board without anyone asking for it.

*`ActiveJobExists` is the normal case, not an error.* Several windows of a month commonly finish within minutes of each other. The master already queued will read whichever windows have finished when it actually runs, so a second one would build the same file twice. The existing `(platform, period)` active-job index gives this for free, with `platform='all'` and the MONTH in `period` — which is also why `platform` is `'all'` rather than `NULL`: NULLs do not compare equal, so two concurrent masters for one month would both slip through the index.

*It writes through `pipeline.write_artifacts` like everything else.* `RunResult.workbook_name` exists solely so the master can be `month_master.xlsx` instead of `finance_file.xlsx` while still going through the one declared writer. The alternative — a writer in `service/` — is a second unverified implementation of a deliverable, which is [D31](#d31).

### D56 — A master that does not cover its month says so on its face {#d56}
The master is rebuilt every time a window finishes, so for most of the month it is **partial by construction**. `Coverage` carries the included and the missing windows, the workbook's first tab is `Coverage`, and the `Summary` tab repeats the banner because that is the tab people actually read.

A partial master is reported as `UNVERIFIED`, not `VARIANCE`. Nothing disagrees — it is a master that does not yet cover the month, and `UNVERIFIED` already means "completed, but nothing corroborated this". Calling it a variance would put a number-disagreement verdict on a coverage fact.

The missing list is only possible because the window list comes from the database ([task 3.3](../docs/14-PRODUCTION-READINESS.md)): `month_windows` unions runs, roster declarations and uploads, so a window whose files arrived but which nobody ran is *known* and therefore reportable. The hardcoded `WINDOWS` dict it replaced could not have reported a missing window, because a window it had never heard of — `s2x`, `s3k` — was indistinguishable from one that does not exist.

### D57 — The upload door reads settlement dates, and treats the three cases differently {#d57}
`POST /uploads` validated `period` for character safety and nothing else, so a file could be uploaded to any window and nothing looked at what it settled. `tools/stage_exports.py` has derived the window from settlement dates since M2.5; the api had none of it ([defect 2.3](08-KNOWN-DEFECTS.md)'s residual). July's two mis-pulls are the bill — an order export labelled for the later window, byte-identical to the earlier one, inside a month that came up 4,527,401,608 VND short.

The door now reads `settles_from..settles_to` out of the pass the sanitizer already makes, and does **three different things** with the answer:

*A window-defining file that does not INTERSECT its window's month is refused* (422), before the object store is written and before the row is inserted — a file this window should not contain must leave no trace that it might. **Intersect, not contain**: Lazada's 25th-to-month-end Daily week is a permanent monthly fixture and its weeklies lap the boundary, so a containment test would refuse the healthy case every month, which is how a control gets switched off.

*The mis-pull shape WARNS and never refuses.* A file starting earlier than every sibling is `find_outliers`' signal, but the first upload into a window has no siblings, [D9](#d9)'s `window_settlement_bounds` owns the hard control at run time, and a door that refuses on suspicion at month end teaches operators to fight it. It is also stricter than `find_outliers`: earlier than *every* sibling rather than earlier than the modal start, because the mode needs an arbitrary tie-break on an even split and nobody is reviewing a plan at the door.

*Order exports are not date-checked at all.* An order created in June legitimately settles in July, and TikTok re-ships each store's prior-month pull in every weekly folder because the cross-period stitch needs it. Checking them would flag every healthy window in the tree — the same over-broad check that had to be narrowed in staging.

A file whose date column is absent or unparseable is reported as *not checked* rather than guessed at, the posture `ingest.date_format` already takes. Dates are parsed through that accessor and never a second spelling ([D54](#d54)).

### D58 — The database may know where every number came from; it may never compute one {#d58}
Defect 2.12 needed a question answered that nothing could ask: *does some OTHER window's order export hold this order's SKU lines?* `uploads_for_window` is hard-keyed to one `(platform, period)` and the stored objects are opaque blobs. Migration `015_order_index.sql` adds `upload_order_index(upload_id, store, order_id)`, and that is the **only** new question the database learns to answer.

It was proposed as the seed of moving the reconciliation into SQL, and that half is deliberately **rejected**. The money math in `src/` was ported formula-by-formula from the team's own workbooks and verified row-by-row against their output; that provenance is the project's entire value ([D12](#d12)). A SQL reimplementation would be a second, unverified implementation of the path that produces the invoice — [D31](#d31)'s failure, the same reason the worker adds no compute and does not own a writer. So the rule is stated as a boundary rather than left to judgement: **every column in this table is an identifier or a count. No amount, no rate, no total.** `tests/service/test_order_index.py` asserts the table's columns structurally, so a future migration adding an amount fails a test rather than a review.

*Why file-level dedupe did not need building.* The question "have these exact bytes arrived before" was already answered: `uploads.sha256` (the original), `object_sha256` (the sanitized copy, [D52](#d52)) and per-file digests in `staging.json` on the CLI path. The expensive-sounding half of the proposal was already paid for.

*Why the backfill is not the [D26](#d26) trap.* Indexing reads bytes out of the store, so it must first establish they are the bytes the door accepted. The expected digest was recorded independently, at the door; the backfill **checks** it and never derives it, before reading a single order id. A NULL digest is skipped and named, a mismatch is refused and exits non-zero. Nothing the index holds is ever an integrity reference for anything else — it is derived, rebuildable, reporting-only data.

*Exposure.* `store` and `order_id` are already persisted per row in `run_exceptions` fingerprints and named in `run_log_lines`, so this is the exposure accepted in [defect 2.6](08-KNOWN-DEFECTS.md), not a new one. No customer field, no cell value.
