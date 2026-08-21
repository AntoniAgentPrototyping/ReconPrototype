"""The run seam: one way in, one way out, and nothing written until you ask.

Before this module, `tools/full_run.py` interleaved three concerns inside each
of three platform functions — compute, disk I/O, and tie-out. That is fine for a
CLI and wrong for anything else: a worker that streams artifacts to object
storage had no way to obtain the workbook without a file already existing on
disk, and a test had no way to inspect a run's frames without writing one.

The split:

    run(ctx) -> RunResult        reads inputs, computes, WRITES NOTHING
    write_artifacts(result)      the only thing in the codebase that writes

so the CLI and the future worker are two callers of one function and neither can
quietly become the privileged one (docs/06-DECISIONS.md#d24).

**This module is a refactor and must stay output-identical.** Behaviour changes
— consuming the tie-out result, calling tieout for Shopee/Lazada, real exit
codes, the NFC header fix — are M2, each with its expected delta stated in
advance (docs/06-DECISIONS.md#d12). Two things below therefore look like bugs
and are deliberate; both are marked `PARITY:`.
"""

from __future__ import annotations

import enum
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import (backfill, calculate, classify, config, finance_template, ingest, lazada,
               masters, tieout)
from .errors import ReconHardStop
from .metrics import RunMetrics
from .runlog import RunLog

# The income-side settlement column each platform reconciles against. TikTok's
# order-file rebuild matches it exactly (max deviation 0.0000 VND across 44,129
# real orders), which is why the conservation tolerance is 1 VND and not a
# nominal figure.
TIKTOK_MONEY = "subtotal_after_seller_discounts"
# Shopee has no single income column to conserve against — its settlement is net
# of platform fees — so `money_col` stays None and its crossing is a *pair* of
# derived columns instead, built by tieout.revenue_crossing_shopee from the
# team's own June "Net revenue" formula. Closed 2026-08-13; the gap it replaces
# is docs/08-KNOWN-DEFECTS.md#11.
SHOPEE_MONEY = None


def norm_store(name: str) -> str:
    """Shared normalization so team file labels ('income U food.xlsx',
    'Income.Masan part 1.xlsx') and pipeline store names compare equal.

    Moved here from tools/full_run.py: it is pipeline logic, and `src/` must not
    import from `tools/` (tests/test_io_boundary.py pins that). Unchanged
    otherwise — note it already normalizes to NFC, which is the treatment
    ingest.py:158 never got (docs/08-KNOWN-DEFECTS.md#12).
    """
    s = unicodedata.normalize("NFC", str(name)).lower().strip()
    s = re.sub(r"^\s*\d+[._ ]*", "", s)
    s = re.sub(r"^(income|order)\b[. ]*", "", s)
    s = re.sub(r"\s+part\s*\d+", "", s)
    s = s.replace(".xlsx", "")
    s = re.sub(r"[._]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class RunStatus(enum.Enum):
    """Why the run ended, as distinct from whether a file appeared.

    `UNVERIFIED` is the one that did not exist before. Today a run with no
    `--refs` reports one "no team reference found" line per store through the
    same channel as a genuine numeric disagreement, so an operator sees a long
    list of scary text for a run that was simply never checked — and learns to
    ignore the list (docs/08-KNOWN-DEFECTS.md#11). Separating the two here is
    what lets M2 give them different exit codes without also having to untangle
    the plumbing.
    """

    OK = "ok"                   # ties against the team's references
    UNVERIFIED = "unverified"   # ran clean, but nothing to check against
    VARIANCE = "variance"       # a real numeric disagreement
    HARD_STOP = "hard_stop"     # nothing was produced


# Process exit codes, kept next to the status they map rather than in the CLI.
#
# Moved here in M4: the CLI is no longer the only caller. The worker records the
# same number on the run row, so an operator reading a run in the web app and an
# operator reading `echo $?` in a terminal are reading one definition — and
# `2 = ran clean but had nothing to check against` cannot come to mean two
# different things in two places.
EXIT_CODES: dict["RunStatus", int] = {
    RunStatus.OK: 0,
    RunStatus.VARIANCE: 1,
    RunStatus.UNVERIFIED: 2,
    RunStatus.HARD_STOP: 3,
}


@dataclass(frozen=True)
class RunContext:
    """Everything a run needs, passed explicitly.

    Explicit roots rather than module-level constants are what make the run
    relocatable — a worker points them at a scratch volume, a test at tmp_path.
    """

    platform: str                  # "tiktok" | "shopee" | "lazada"
    period: str
    input_root: Path
    output_root: Path
    config_dir: Path
    settings: dict
    log: "Any"                     # duck-typed: RunLog, QueueRunLog, RecordingLog
    refs: dict = field(default_factory=dict)
    # Facts discovered while building the context, carried as FIELDS rather than
    # stuffed into `settings` (defect 1.9). They are not configuration: `vat_sku` is
    # reference data the money math reads, and the two `masters_*` values are an
    # OUTCOME a finance user has to see, because a run on stale snapshots looks
    # exactly like a run on the live file. Lazada already threaded its VAT map
    # explicitly, so this is the shape that was already right for one of three
    # platforms.
    vat_sku: dict = field(default_factory=dict)
    masters_source: str | None = None
    masters_searched: tuple = ()
    # Which expected stores this run relaxed (D3): the declared list, or the
    # whole roster in blanket/CLI mode; empty when the run is not partial. The
    # workbook stamps its own caveat from this — the file the team invoices from
    # must say it is a subset (closes D46's deliberate deferral).
    roster_relaxed: tuple = ()

    @property
    def input_dir(self) -> Path:
        return self.input_root / self.period / self.platform

    @property
    def output_dir(self) -> Path:
        return self.output_root / self.period / self.platform


def apply_partial_roster(settings: dict, platform: str,
                         optional_stores: list[str] | None = None) -> int:
    """Make expected stores optional for THIS run. Returns how many.

    Roster relaxation is a property of the run, never a config fork
    (docs/06-DECISIONS.md#d23): editing `config/settings.yaml` to generate a
    single-store golden would misreport what produced it, and would relax the
    check for every later run too. So the relaxed names are folded into
    `stores_optional` in the in-memory settings dict and the file on disk is
    untouched.

    `optional_stores` names WHICH stores the declaration covers (D3). **None is
    the original behaviour verbatim — every expected store optional** — and must
    stay so: it is what the developer CLI's `--partial-roster` bool produces, and
    the tiktok/shopee goldens are regenerated through exactly that path. A named
    list relaxes only those stores, so a store the declaration does not name goes
    back to hard-stopping in `check_stores` — which is the whole point: the
    blanket waved a genuinely forgotten store through with the legitimately
    absent ones.

    A name the roster does not know is a hard stop, not a skip. The declaration
    was written against SOME roster; if it no longer matches the one this run
    uses (a repin, a rename), silently ignoring the name would resurrect the
    misleading "missing store" stop one step later — better to say which name is
    wrong here. Checked against the run's OWN roster deliberately: the API door
    cannot do this, because the window may be pinned to a different config.

    The UNEXPECTED-store check stays armed. That is the asymmetry that makes this
    safe: a window may legitimately hold a subset of the roster, but a store
    nobody has confirmed must still stop the run (docs/06-DECISIONS.md#d3).
    """
    expected = (settings.get("expected_stores") or {}).get(platform) or []
    if optional_stores is None:
        relax = set(expected)
    else:
        unknown = sorted(set(optional_stores) - set(expected))
        if unknown:
            raise ReconHardStop(
                f"the roster declaration for this window names store(s) the "
                f"{platform} roster does not know: {unknown}. The roster this run "
                f"uses has {len(expected)} store(s); fix the declaration (or the "
                f"alias) rather than running with a claim that matches nothing.")
        relax = set(optional_stores)
    optional = settings.setdefault("stores_optional", {})
    optional[platform] = sorted(set(optional.get(platform) or []) | relax)
    return len(relax)


def build_context(platform: str, period: str, *, root: Path | None = None,
                  config_dir: Path | None = None, input_root: Path | None = None,
                  output_root: Path | None = None, refs: dict | None = None,
                  log: "Any" = None, partial_roster: bool = False,
                  roster_stores: list[str] | None = None,
                  settings_text: str | None = None) -> RunContext:
    """Assemble a RunContext the way production does.

    Moved out of `tools/full_run.py` in M4. It had three callers — the CLI, the
    golden generator, and now the service worker — and the worker cannot reach
    it there: `src/` is the deployable unit and the container image ships it
    without `tools/` (tests/test_io_boundary.py::test_src_never_imports_tools_or_tests).
    Duplicating it was the alternative, and the thing that would have been
    duplicated is the masters load below — which decides per-SKU VAT rates, so a
    diverged copy silently changes numbers. (Through M8 it was worse than a
    duplicate: the map travelled as `settings["_vat_sku"]`, invisible in every
    signature that read it. See `RunContext.vat_sku`.)

    Roots are separately overridable because the worker needs exactly that: the
    same `config_dir` and `input_root` as the CLI, and a per-job scratch
    `output_root` whose contents it then hands to the artifact store.

    `refs` is a dict rather than a path on purpose. The CLI reads its JSON file
    and the service reads the `jobs.refs` column; either way the file I/O happens
    in the caller, which is what keeps this module inside its I/O grant.

    `settings_text` is the same idea one step further: pass the exact config a
    previous run of this window used, and a re-run cannot be changed by an edit
    made since (docs/08-KNOWN-DEFECTS.md 2.5). Omitted — which is what the CLI
    does — it reads `settings.yaml` off disk exactly as before. `config_dir` is
    still required either way, because the team-owned `.xlsb` master lives there
    and is read live regardless (docs/06-DECISIONS.md#d8).
    """
    log = log if log is not None else RunLog()
    if root is not None:
        root = Path(root)
    config_dir = Path(config_dir) if config_dir is not None else _under(root, "config")
    input_root = Path(input_root) if input_root is not None else _under(root, "input")
    output_root = Path(output_root) if output_root is not None else _under(root, "output")

    settings = (config.parse_settings(settings_text) if settings_text is not None
                else config.load_settings(config_dir))
    # Masters are loaded here and carried on the CONTEXT as fields.
    #
    # Until 2026-08-19 these three values were written into the settings dict as
    # `_vat_sku`, `_masters_source` and `_masters_searched` — the configuration
    # contract used as a data channel (defect 1.9). Nothing was broken by it: the
    # containment was that `build_context` runs per job, so two runs never shared a
    # dict, pinned by test_worker.py::test_each_job_gets_its_own_settings_dict. But
    # the containment was the only thing standing between the pattern and
    # cross-contaminated VAT rates, and a leading underscore in a dict is not a
    # signature — it cannot be type-checked, cannot be seen by a reader of
    # `compute_sku_columns_*`, and had already been copied once for the masters
    # provenance. It is now three fields and an explicit parameter.
    #
    # One-job-per-process still holds. It was never only about this dict: memory is
    # the binding constraint (a window peaks well into the GBs) and concurrency comes
    # from more worker processes, which is what FOR UPDATE SKIP LOCKED is for.
    loaded = masters.load_masters(config_dir, settings, log)

    # `roster_stores` names WHICH stores the window's declaration covers (D3);
    # None — which is all the developer CLI's `--partial-roster` bool can say,
    # and the mode every partial-roster golden is regenerated in — relaxes the
    # whole expected list exactly as before.
    relaxed: tuple = ()
    if partial_roster:
        count = apply_partial_roster(settings, platform, roster_stores)
        relaxed = tuple(sorted(roster_stores if roster_stores is not None
                               else (settings.get("expected_stores") or {})
                               .get(platform) or []))
        if roster_stores is None:
            log.warn(f"PARTIAL ROSTER: {count} expected {platform} store(s) made optional "
                     f"for this run; the unexpected-store check stays armed. This run "
                     f"covers a SUBSET of the roster and its totals are not the month's.")
        else:
            log.warn(f"PARTIAL ROSTER: {count} declared-absent {platform} store(s) made "
                     f"optional for this run: {sorted(roster_stores)}. Any other "
                     f"expected store still hard-stops if absent, and the "
                     f"unexpected-store check stays armed. This run covers a SUBSET "
                     f"of the roster and its totals are not the month's.")

    return RunContext(
        platform=platform, period=period,
        input_root=input_root, output_root=output_root, config_dir=config_dir,
        settings=settings, log=log, refs=refs or {},
        vat_sku=loaded["vat_sku"],
        masters_source=loaded.get("source"),
        masters_searched=tuple(loaded.get("searched") or ()),
        roster_relaxed=relaxed,
    )


def _under(root: Path | None, name: str) -> Path:
    if root is None:
        raise ValueError(f"build_context needs either root= or an explicit {name} path")
    return root / name


@dataclass
class RunResult:
    """The whole outcome of a run, in memory.

    `frames` carries the stage outputs. It exists so a tie-out can reference
    something other than the frame it is checking — the structural reason the
    current checks cannot fail is that every input to them comes from one frame
    (docs/08-KNOWN-DEFECTS.md#11). M2's replacements read from here.
    """

    context: RunContext
    workbook: Any | None = None                 # openpyxl Workbook, unwritten
    # What `write_artifacts` calls the workbook. A settlement window's deliverable
    # is `finance_file.xlsx` and always has been; the month-end master is the same
    # shape of thing under a different name (M8 Phase 3). Overriding the NAME is
    # what lets the master go through the one declared writer instead of the
    # worker growing a second one, which is the whole of D31.
    workbook_name: str = "finance_file.xlsx"
    checks: list = field(default_factory=list)
    tieout: Any | None = None                   # the tie-out results frame
    exceptions: dict[str, Any] = field(default_factory=dict)
    frames: dict[str, Any] = field(default_factory=dict)
    metrics: RunMetrics = field(default_factory=RunMetrics)
    error: BaseException | None = None

    # One ORDERED list of (kind, message), kind in {"variance", "unverified"}.
    #
    # Storing the split as two lists and concatenating them would reorder the
    # findings: the original loop interleaved "no team reference found" with
    # genuine variances, store by store. That order is committed — it is inside
    # variances.json, whose digest is in tests/goldens/manifest.json — so
    # reordering would move a golden during what must be an output-identical
    # refactor. One ordered list, two views.
    findings: list[tuple[str, str]] = field(default_factory=list)

    def add_variance(self, message: str) -> None:
        self.findings.append(("variance", message))

    def add_unverified(self, message: str) -> None:
        self.findings.append(("unverified", message))

    def consume_tieout(self) -> None:
        """Turn tie-out breaches into variances.

        The single most important line of M2. Before this the result was
        computed and thrown away (`full_run.py:108`), so even a working check
        could not have affected the outcome — the checks were blind AND
        disconnected, and fixing only one of those would have changed nothing.
        """
        if self.tieout is None or not len(self.tieout):
            return
        for _, row in self.tieout.iterrows():
            if row["result"] == "BREACH":
                self.add_variance(
                    f"TIE-OUT {row['check']}: variance {row['variance']:+,.2f} "
                    f"(tol {row['tolerance']:,.2f})")

    @property
    def all_findings(self) -> list[str]:
        """Every finding in original order — what the log and variances.json
        record, byte-identical to the pre-seam behaviour."""
        return [m for _, m in self.findings]

    @property
    def variances(self) -> list[str]:
        """Genuine numeric disagreements only."""
        return [m for k, m in self.findings if k == "variance"]

    @property
    def unverified(self) -> list[str]:
        """Stores with no team reference to check against. Not a failure —
        a gap in checking. M2 gives it its own exit code."""
        return [m for k, m in self.findings if k == "unverified"]

    @property
    def status(self) -> RunStatus:
        if self.error is not None or self.workbook is None:
            return RunStatus.HARD_STOP
        if self.variances:
            return RunStatus.VARIANCE
        if self.unverified:
            return RunStatus.UNVERIFIED
        return RunStatus.OK

    @property
    def workbook_path(self) -> Path:
        return self.context.output_dir / self.workbook_name


# ---------------------------------------------------------------------------
# Tie-out against the team's reference totals
# ---------------------------------------------------------------------------

def _tie(per_store_mine: dict, refs: dict, log, out: RunResult) -> None:
    """Compare per-store metrics against the team's figures.

    Tags each finding as a genuine variance or as unverified, in the order the
    original loop produced them.
    """
    per_ref: dict[str, dict] = {}
    for k, v in (refs.get("per_store") or {}).items():
        acc = per_ref.setdefault(norm_store(k), {})
        for m, val in v.items():
            acc[m] = acc.get(m, 0.0) + float(val)

    for store, metrics in sorted(per_store_mine.items()):
        key = norm_store(store)
        ref = per_ref.get(key)
        if ref is None:
            # Team labels sometimes keep only the middle segment of long
            # underscore names — fall back to prefix matching.
            cands = [r for r in per_ref if len(r) >= 8 and (key.startswith(r) or r.startswith(key))]
            ref = per_ref[cands[0]] if len(cands) == 1 else None
        if ref is None:
            out.add_unverified(f"{store}: no team reference found")
            continue
        for metric, mine in metrics.items():
            expected = ref.get(metric)
            if expected is None:
                continue
            diff = mine - float(expected)
            status = "TIES" if abs(diff) < 1 else f"VARIANCE {diff:+,.0f}"
            log.add(f"  {store} · {metric}: mine {mine:,.0f} vs team "
                    f"{float(expected):,.0f} -> {status}")
            if abs(diff) >= 1:
                out.add_variance(f"{store} {metric}: {diff:+,.0f}")


def _tie_grand(entries: list[tuple[str, str, float]], refs: dict, log,
               out: RunResult, *, default_tol: float) -> None:
    """entries: (ref_key, display_name, mine).

    The key and the label differ for Lazada, whose reference keys are
    `pre_vat_105` / `pre_vat` / `pre_vat_110` while the log names the VAT rate
    ("GRAND pre_vat @1.05"). Kept as separate fields rather than derived, so the
    log text stays byte-identical.
    """
    grand = refs.get("grand") or {}
    tol = float(refs.get("grand_tolerance", default_tol))
    for key, name, mine in entries:
        if key not in grand or grand[key] is None:
            continue
        expected = float(grand[key])
        diff = mine - expected
        log.add(f"  {name}: mine {mine:,.2f} vs team {expected:,.2f} "
                f"({'TIES' if abs(diff) <= tol else f'VARIANCE {diff:+,.0f}'})")
        if abs(diff) > tol:
            out.add_variance(f"{name}: {diff:+,.0f}")


def window_meta(dates) -> dict:
    """Team-style window labels derived from the data itself, e.g.
    settlements 2026-07-08..2026-07-14 -> label '08 to 14T07'."""
    import pandas as pd
    d = pd.to_datetime(dates, errors="coerce").dropna()
    if d.empty:
        return {"label": "", "period_label": "", "month_label": ""}
    lo, hi = d.min(), d.max()
    label = f"{lo:%d} to {hi:%d}T{hi:%m}"
    return {"label": label,
            "period_label": f"{hi:%y}_{label}",
            "month_label": f"Laz {hi:%y}T{hi.month}"}


# ---------------------------------------------------------------------------
# Platform runs — compute only
# ---------------------------------------------------------------------------

def _run_tiktok(ctx: RunContext, out: RunResult) -> None:
    m, log, settings = out.metrics, ctx.log, ctx.settings
    d = ctx.input_dir

    with m.stage("read_parts:orders", "io", rows_out=lambda: len(out.frames.get("orders", ()))):
        orders = ingest.read_parts(d / "orders", config.column_map(settings, "tiktok", "orders"),
                                   "orders", settings, log, "tiktok")
        out.frames["orders"] = orders
    with m.stage("read_parts:income", "io", rows_out=lambda: len(out.frames.get("income", ()))):
        income = ingest.read_parts(d / "income", config.column_map(settings, "tiktok", "income"),
                                   "income", settings, log, "tiktok")
        out.frames["income"] = income

    with m.stage("derive_brand", "compute", rows_out=lambda: len(income)):
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
    with m.stage("apply_settlement_bounds", "compute", rows_out=lambda: len(income)):
        income = ingest.apply_settlement_bounds(income, ctx.period, settings, log)
    # M8/2.3: the roster is checked against the ORDERS file too, not income alone.
    # An orders export missing for a rostered store under-reports that storefront's
    # lines while its income still ties, which is the quietest of the shapes this
    # check exists to catch. Measured across all four rostered golden windows before
    # landing: the store set derived from orders is IDENTICAL to the one derived from
    # income on every one of them (symmetric difference 0), so this adds no hard stop
    # to any window that runs clean today. It can only ever ADD one, which is why it
    # was measured rather than reasoned about.
    ingest.check_stores(orders, "orders", "tiktok", settings, log)
    ingest.check_stores(income, "income", "tiktok", settings, log)

    with m.stage("classify", "compute", rows_out=lambda: len(cl)):
        cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    # Orders this window settles whose SKU lines were exported with an EARLIER window
    # (defect 2.12). Before the explode, because that is the join this is about. In
    # `report` mode `xw.orders is orders` and nothing moves; only `apply` extends the
    # frame, which is why the stage's row count is the honest place for the change to
    # show up.
    with m.stage("cross_window_orders", "io", rows_out=lambda: len(xw.orders)):
        xw = backfill.resolve(
            input_root=ctx.input_root, period=ctx.period, platform="tiktok",
            orders=orders, settled=good, money_col=TIKTOK_MONEY,
            colmap=config.column_map(settings, "tiktok", "orders"),
            settings=settings, log=log)
    with m.stage("explode_to_sku", "compute", rows_out=lambda: len(sku)):
        sku = calculate.explode_to_sku_tiktok(good, xw.orders, log)

    # Reference captured HERE — from the income frame, before the money math —
    # so everything compute_sku_columns does is inside the checked span.
    sku_keys = tieout.pairs(sku)
    reference, unmatched_money, unmatched_orders = tieout.partition(
        good, money_col=TIKTOK_MONEY, present_keys=sku_keys)
    coverage = tieout.coverage_by_store(good, money_col=TIKTOK_MONEY,
                                        present_keys=sku_keys)

    with m.stage("compute_sku_columns", "compute", rows_out=lambda: len(sku)):
        sku = calculate.compute_sku_columns_tiktok(sku, settings, log, ctx.vat_sku)
    # `orders` stays what THIS window exported; `borrowed_orders` is what came from a
    # predecessor. Kept apart rather than merged so that under `apply` mode both
    # questions are still answerable — "what did this window's files contain" and
    # "which lines did the explode get from elsewhere" — instead of one frame that
    # silently answers neither.
    out.frames.update(classified=cl, good=good, sku=sku, borrowed_orders=xw.borrowed)

    with m.stage("build_workbook", "serialize"):
        meta = window_meta(sku["statement_date"])
        # D3: the workbook says it is partial. From the relaxed set, never from
        # `expected - found` — config-level stores_optional must not stamp.
        meta["roster_relaxed"] = sorted(ctx.roster_relaxed)
        out.workbook, out.checks = finance_template.build_tiktok(
            sku, settings, meta, log)

    log.section("TIE-OUT")
    with m.stage("tieout", "compute"):
        out.tieout = tieout.run_checks_tiktok(
            sku, reference, settings, log, money_col=TIKTOK_MONEY,
            unmatched_money=unmatched_money, unmatched_orders=unmatched_orders,
            coverage=coverage, cross_window=xw)
    out.consume_tieout()

    per_store = {
        s: {"ok_good_settlement": float(g["net_revenue"].fillna(0).sum()),
            "ok_good_revenue": float(g["gross_revenue"].fillna(0).sum())}
        for s, g in good.groupby("store")
    }
    for s, g in cl[cl["final_status"] == classify.FINAL_TAKE_OUT].groupby("store"):
        per_store.setdefault(s, {})["takeout_settlement"] = float(g["net_revenue"].fillna(0).sum())
    # V1-style Total files pivot with Final_Status = All -> raw sums:
    for s, g in cl.groupby("store"):
        per_store.setdefault(s, {})["raw_settlement"] = float(g["net_revenue"].fillna(0).sum())
        per_store[s]["raw_revenue"] = float(g["gross_revenue"].fillna(0).sum())

    # Same (store, order_id) identity as the tie-out, for the same reason: keyed on
    # the id alone this sheet omitted an order whose id another store happened to
    # carry — the row an operator needs most, missing from the record of what left
    # the invoice (2.9).
    out.exceptions["unmatched_orders"] = good[
        ~tieout.pair_series(good).isin(tieout.pairs(sku))]
    # Per store, so the reconciling total stops being one anonymous number. The whole
    # ~21% on this platform's golden window is a SINGLE store, which is why a window
    # total could not distinguish ordinary traffic from defect 2.12.
    out.exceptions["order_coverage"] = coverage
    # Which order came from which earlier window, and whether this run used it.
    out.exceptions["cross_window_orders"] = backfill.exception_rows(xw)
    out.exceptions["tieout_breaches"] = out.tieout[out.tieout["result"] == "BREACH"]         if out.tieout is not None else None

    _tie(per_store, ctx.refs, log, out)
    _tie_grand([("pre_vat", "GRAND pre_vat", float(sku["amount_pre_vat"].sum())),
                ("with_vat", "GRAND with_vat", float(sku["amount_with_vat"].sum()))],
               ctx.refs, log, out, default_tol=1)


def _run_shopee(ctx: RunContext, out: RunResult) -> None:
    m, log, settings = out.metrics, ctx.log, ctx.settings
    d = ctx.input_dir

    with m.stage("read_parts:orders", "io", rows_out=lambda: len(orders)):
        orders = ingest.read_parts(d / "orders", config.column_map(settings, "shopee", "orders"),
                                   "orders", settings, log, "shopee")
    with m.stage("read_parts:income", "io", rows_out=lambda: len(income)):
        income = ingest.read_parts(d / "income", config.column_map(settings, "shopee", "income"),
                                   "income", settings, log, "shopee")

    with m.stage("derive_brand", "compute", rows_out=lambda: len(income)):
        orders = ingest.derive_brand(orders, settings, log)
        income = ingest.derive_brand(income, settings, log)
    with m.stage("apply_settlement_bounds", "compute", rows_out=lambda: len(income)):
        income = ingest.apply_settlement_bounds(income, ctx.period, settings, log)

    # Armed in M2. A 17-store roster had been configured and never checked,
    # so a Shopee run with 1 of 17 expected stores completed without complaint.
    # M8/2.3: the roster is checked against the ORDERS file too, not income alone.
    # An orders export missing for a rostered store under-reports that storefront's
    # lines while its income still ties, which is the quietest of the shapes this
    # check exists to catch. Measured across all four rostered golden windows before
    # landing: the store set derived from orders is IDENTICAL to the one derived from
    # income on every one of them (symmetric difference 0), so this adds no hard stop
    # to any window that runs clean today. It can only ever ADD one, which is why it
    # was measured rather than reasoned about.
    ingest.check_stores(orders, "orders", "shopee", settings, log)
    ingest.check_stores(income, "income", "shopee", settings, log)

    # Captured from the INCOME frame before any money math, so the whole
    # calculate stage sits inside the checked span — the same discipline as
    # TikTok's reference. `shopee_product_subsidy` is why this reads `income`
    # rather than the classified frame: classify aggregates a fixed column list
    # that does not include it.
    revenue_crossing = tieout.revenue_crossing_shopee(income, log)

    with m.stage("classify", "compute", rows_out=lambda: len(cl)):
        cl = classify.classify_shopee_income(income, log)
    # See the note in `_run_tiktok`. Shopee held July's single worst cell — `masan` in
    # `s4`, matching 33% of its income against the orders staged with it — so this is
    # not a TikTok-only shape.
    with m.stage("cross_window_orders", "io", rows_out=lambda: len(xw.orders)):
        xw = backfill.resolve(
            input_root=ctx.input_root, period=ctx.period, platform="shopee",
            orders=orders, settled=cl, money_col="net_revenue",
            colmap=config.column_map(settings, "shopee", "orders"),
            settings=settings, log=log)
    with m.stage("explode_to_sku", "compute", rows_out=lambda: len(sku)):
        sku = calculate.explode_to_sku_shopee(cl, xw.orders, log)
    with m.stage("compute_sku_columns", "compute", rows_out=lambda: len(sku)):
        sku = calculate.compute_sku_columns_shopee(sku, settings, log, ctx.vat_sku)
    # See the note in `_run_tiktok`: `orders` is this window's own export, borrowed
    # lines are recorded separately rather than folded in.
    out.frames.update(orders=orders, income=income, classified=cl, sku=sku,
                      borrowed_orders=xw.borrowed)

    with m.stage("build_workbook", "serialize"):
        meta = window_meta(sku["statement_date"])
        meta["roster_relaxed"] = sorted(ctx.roster_relaxed)   # D3, as on tiktok
        out.workbook, out.checks = finance_template.build_shopee(
            sku, settings, meta, log)

    ok = sku[sku["check_status"] == classify.SHOPEE_OK]
    out.frames["ok"] = ok

    log.section("TIE-OUT")
    sku_keys = tieout.pairs(sku)
    reference, unmatched_money, unmatched_orders = tieout.partition(
        cl, money_col="net_revenue", present_keys=sku_keys)
    coverage = tieout.coverage_by_store(cl, money_col="net_revenue",
                                        present_keys=sku_keys)
    with m.stage("tieout", "compute"):
        out.tieout = tieout.run_checks_shopee(
            sku, reference, settings, log, money_col=SHOPEE_MONEY,
            unmatched_money=unmatched_money, unmatched_orders=unmatched_orders,
            crossing=revenue_crossing, coverage=coverage, cross_window=xw)
    out.consume_tieout()
    per_store = {
        s: {"ok_pre_vat": float(g["amount_pre_vat"].sum()),
            "ok_with_vat": float(g["amount_with_vat"].sum())}
        for s, g in ok.groupby("store")
    }
    out.exceptions["tieout_breaches"] = out.tieout[out.tieout["result"] == "BREACH"]         if out.tieout is not None else None
    # Shopee had NO per-order record of settlement that reached no SKU line, while
    # TikTok has had one since M2 (`_run_tiktok`). So the platform whose worst July
    # cell was a 2,106,036,476 VND understatement — `masan` in `s4`, matching 33% of
    # its income against the orders staged with it — was the one where nothing named
    # the affected orders (defect 2.12). The INFO row gave a total and no identities,
    # and `service/exceptions.py` keys this sheet on (store, order_id), which is what
    # turns "the door's traffic changed this month" into a query rather than a memory.
    out.exceptions["unmatched_orders"] = cl[
        ~tieout.pair_series(cl).isin(tieout.pairs(sku))]
    out.exceptions["cross_window_orders"] = backfill.exception_rows(xw)
    out.exceptions["order_coverage"] = coverage

    _tie(per_store, ctx.refs, log, out)
    _tie_grand([("pre_vat", "GRAND pre_vat", float(ok["amount_pre_vat"].sum())),
                ("with_vat", "GRAND with_vat", float(ok["amount_with_vat"].sum()))],
               ctx.refs, log, out, default_tol=2000)


def _run_lazada(ctx: RunContext, out: RunResult) -> None:
    m, log, settings = out.metrics, ctx.log, ctx.settings

    with m.stage("load_masters", "io"):
        fee_types = lazada.load_fee_type_map(ctx.config_dir, log)
        vat_sku = lazada.load_vat_sku(ctx.config_dir, log)
    with m.stage("read_ledger", "io", rows_out=lambda: len(ledger)):
        ledger = lazada.read_ledger(ctx.input_dir, settings, log)
    # The roster check runs on all three platforms since M8/1.7. Lazada never
    # called it at all, so adding a roster alone would have changed nothing
    # (docs/14-PRODUCTION-READINESS.md A6). `expected_stores.lazada` is a BUSINESS
    # question and is still unanswered, and `check_stores` self-skips with a
    # warning while it is — so the wiring lands first and stays behaviour-neutral
    # until somebody populates the roster.
    ingest.check_stores(ledger, "ledger", "lazada", settings, log)
    with m.stage("classify_ledger", "compute", rows_out=lambda: len(cl)):
        cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    with m.stage("revenue_lines", "compute", rows_out=lambda: len(rev)):
        rev = lazada.revenue_lines(cl, settings, log)
    out.frames.update(ledger=ledger, classified=cl, revenue=rev, unmapped=unmapped)
    out.exceptions["unmapped_fees"] = unmapped

    with m.stage("build_workbook", "serialize"):
        meta = window_meta(cl["transaction_date"])
        meta["roster_relaxed"] = sorted(ctx.roster_relaxed)   # D3, as on tiktok
        out.workbook, out.checks = finance_template.build_lazada(
            rev, settings, meta, log, classified=cl)

    log.section("TIE-OUT")
    with m.stage("tieout", "compute"):
        out.tieout = tieout.run_checks_lazada(rev, cl, settings, log)
    out.consume_tieout()

    per_store: dict[str, dict] = {}
    for (s, b), g in cl.groupby(["store", "fee_bucket"]):
        per_store.setdefault(s, {})[b] = float(g["amount_incl_vat"].fillna(0).sum())
    _tie(per_store, ctx.refs, log, out)

    # The team's KA line sheets are per VAT rate — compare like for like.
    entries = [(k, f"GRAND pre_vat @{r}",
                float(rev.loc[rev["vat_rate"] == r, "check_no_vat"].sum()))
               for k, r in [("pre_vat_105", 1.05), ("pre_vat", 1.08), ("pre_vat_110", 1.10)]]
    _tie_grand(entries, ctx.refs, ctx.log, out, default_tol=1000)
    if len(unmapped):
        out.add_variance(f"{len(unmapped)} unmapped fee rows")


_RUNNERS = {"tiktok": _run_tiktok, "shopee": _run_shopee, "lazada": _run_lazada}


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

def _masters_finding(ctx: RunContext, out: RunResult) -> None:
    """Falling back to the CSV snapshots is a FINDING, not a log line.

    `config/Lib & VAT rate.xlsb` is a live file the team edits; the committed CSVs
    are point-in-time ports of it. A run that could not find the live file used
    whatever the snapshots last said about fee buckets and per-SKU VAT — and
    produced a workbook indistinguishable from one that read the current master.
    Until 2026-08-18 that was a single `log.warn` in a log nobody reads after a
    green run, and in the containerised deployment it fired on EVERY run because
    the mount and the lookup path disagreed (A11).

    Emitted before the platform runner so its position in `RunResult.findings` is
    fixed: the list is ordered and its interleaving is committed inside
    `variances.json`'s digest, so a finding that could appear at different points
    would move a golden depending on what else happened.
    """
    if ctx.masters_source != "csv":
        return
    searched = list(ctx.masters_searched)
    out.add_variance(
        "the team-owned master file was not found, so this run used the committed "
        "CSV snapshots — fee buckets and per-SKU VAT rates may be out of date"
        + (f" (looked in: {', '.join(searched)})" if searched else ""))


def run(ctx: RunContext) -> RunResult:
    """Execute one settlement window. **Writes nothing.**

    Returns a RunResult in every case, including failure — a caller must always
    be able to write the log, which is the only record of why a run died. That
    is also the fix for defect 1.7 (an unwritable output file killed the process
    with no log at all); the caller's `finally` now has something to write.
    """
    out = RunResult(context=ctx)
    try:
        ctx.log.section(f"FULL RUN {ctx.platform} {ctx.period}")
        _masters_finding(ctx, out)
        _RUNNERS[ctx.platform](ctx, out)
    except BaseException as exc:      # noqa: BLE001 — recorded, then re-raised by choice of caller
        out.error = exc
        ctx.log.section("HARD STOP — no finance file produced")
        ctx.log.add(f"  {type(exc).__name__}: {exc}")
    return out


def log_result(result: RunResult) -> None:
    """Write the RESULT section. Shared by the CLI and the worker.

    Moved out of `tools/full_run.py:main` in M4 for the same reason as
    `build_context`: two callers, and the copy in the worker would have been the
    one that drifted. The distinction it draws is the point of the section —
    variances and unchecked stores print under SEPARATE headings, because when
    they shared one list a run that was simply never compared printed one
    alarming line per store, which is how an operator learns to ignore a list
    (docs/08-KNOWN-DEFECTS.md#11).
    """
    log = result.context.log
    log.section("RESULT")
    if result.variances:
        log.add(f"  {len(result.variances)} VARIANCE(S):")
        for v in result.variances:
            log.add(f"    - {v}")
    if result.unverified:
        log.add(f"  {len(result.unverified)} store(s) NOT CHECKED (no team reference):")
        for v in result.unverified:
            log.add(f"    - {v}")
    if not result.all_findings:
        log.add("  ALL TIES")
    log.add(f"  status: {result.status.name}")


def _write_atomically(path: Path, write) -> None:
    """Write through a sibling temp file, then `os.replace`. Never a partial artifact.

    **What this buys.** A crash, a full disk or a killed worker mid-write leaves the
    temp file, not a truncated `finance_file.xlsx`. That mattered more than it
    looks: a half-written workbook still opens in Excel, so the failure mode being
    removed is a finance file that looks current and is short some tabs.

    **What it does NOT buy, and this is the honest half** (docs/08-KNOWN-DEFECTS.md#17,
    register D10): on Windows `os.replace` *also* raises `PermissionError` when the
    destination is held open — a finance file left open in Excel, which is routine
    operator behaviour. The difference is that the previous artifact now survives
    intact and the run reports the failure, instead of the file being clobbered
    halfway. `service/failures.py` already translates `PermissionError` into a
    sentence naming the file.

    The temp file is a SIBLING deliberately: `os.replace` is only atomic within one
    filesystem, so a temp directory elsewhere would silently degrade to copy-then-
    delete. `.tmp` lands in an already-gitignored output dir.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_artifacts(result: RunResult) -> list[Path]:
    """The only writer in the codebase. Returns what it wrote.

    Kept deliberately dumb: it makes no decisions about what a run *meant*, so
    a worker can substitute an object-store variant without re-implementing any
    judgement.

    Every write goes through `_write_atomically`, so composing writes stays this
    function's job — the four writers it calls each take a `write_to` override and
    none of them decides for itself where the bytes land.
    """
    ctx = result.context
    written: list[Path] = []
    ctx.output_dir.mkdir(parents=True, exist_ok=True)

    if result.workbook is not None:
        # Measured, and it matters: writing a 650,000-cell workbook is one of
        # the largest I/O costs in a run. Leaving it outside the metrics
        # understated io_s and correspondingly overstated compute_share — the
        # exact number the engine-port trigger reads (docs/06-DECISIONS.md#d27).
        with result.metrics.stage("write_workbook", "io"):
            _write_atomically(result.workbook_path, lambda p: finance_template.write_workbook(
                result.workbook, result.workbook_path, result.checks, ctx.log, write_to=p))
        written.append(result.workbook_path)

    # exceptions.xlsx — computed since the beginning and never written, so
    # every unmatched order and unmapped fee was dropped on the floor each run.
    # It is a NEW file: the finance workbook is untouched, so the golden gate
    # stays green (docs/06-DECISIONS.md#d12, Class A).
    populated = {k: v for k, v in result.exceptions.items() if v is not None and len(v)}
    if populated:
        from . import export
        path = ctx.output_dir / "exceptions.xlsx"
        rows = 0

        def _write_exceptions(p: Path) -> None:
            nonlocal rows
            rows = export.write_exceptions_file(path, populated, ctx.log, write_to=p)

        _write_atomically(path, _write_exceptions)
        written.append(path)
        ctx.log.add(f"  exceptions -> {path.name} ({rows:,} row(s) across "
                    f"{len(populated)} sheet(s))")

    # The metrics summary is emitted here rather than by the caller so that it
    # can include the workbook write above — the caller has no way to log a
    # stage that has not happened yet.
    if result.metrics.stages:
        import json
        ctx.log.section("METRICS")
        for line in result.metrics.summary_lines():
            ctx.log.add(line)
        path = ctx.output_dir / "run_metrics.json"
        payload = json.dumps(result.metrics.to_dict(), indent=2) + "\n"
        _write_atomically(path, lambda p: p.write_text(payload, encoding="utf-8"))
        written.append(path)

    # run_log.txt LAST and atomically, for the reason 1.7 exists: a failed run must
    # still leave a complete log. A partially written one is worse than none — it is
    # an audit trail that stops mid-sentence and looks like the run did too.
    log_path = ctx.output_dir / "run_log.txt"
    _write_atomically(log_path, lambda p: ctx.log.write(log_path, write_to=p))
    written.append(log_path)
    return written
