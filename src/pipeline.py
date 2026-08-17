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
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import calculate, classify, config, finance_template, ingest, lazada, masters, tieout
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

    @property
    def input_dir(self) -> Path:
        return self.input_root / self.period / self.platform

    @property
    def output_dir(self) -> Path:
        return self.output_root / self.period / self.platform


def apply_partial_roster(settings: dict, platform: str) -> int:
    """Make every expected store optional for THIS run. Returns how many.

    Roster relaxation is a property of the run, never a config fork
    (docs/06-DECISIONS.md#d23): editing `config/settings.yaml` to generate a
    single-store golden would misreport what produced it, and would relax the
    check for every later run too. So the expected list is folded into
    `stores_optional` in the in-memory settings dict and the file on disk is
    untouched.

    The UNEXPECTED-store check stays armed. That is the asymmetry that makes this
    safe: a window may legitimately hold a subset of the roster, but a store
    nobody has confirmed must still stop the run (docs/06-DECISIONS.md#d3).
    """
    expected = (settings.get("expected_stores") or {}).get(platform) or []
    optional = settings.setdefault("stores_optional", {})
    optional[platform] = sorted(set(optional.get(platform) or []) | set(expected))
    return len(expected)


def build_context(platform: str, period: str, *, root: Path | None = None,
                  config_dir: Path | None = None, input_root: Path | None = None,
                  output_root: Path | None = None, refs: dict | None = None,
                  log: "Any" = None, partial_roster: bool = False,
                  settings_text: str | None = None) -> RunContext:
    """Assemble a RunContext the way production does.

    Moved out of `tools/full_run.py` in M4. It had three callers — the CLI, the
    golden generator, and now the service worker — and the worker cannot reach
    it there: `src/` is the deployable unit and the container image ships it
    without `tools/` (tests/test_io_boundary.py::test_src_never_imports_tools_or_tests).
    Duplicating it was the alternative, and the thing that would have been
    duplicated is the `_vat_sku` back-channel below, which silently changes
    numbers if it diverges.

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
    # settings["_vat_sku"] is a data channel, not configuration: calculate.py
    # reads it. Building it per call is what keeps two concurrent runs from
    # cross-contaminating each other's VAT rates — a worker that cached one
    # settings dict across jobs would reintroduce exactly that
    # (docs/02-ARCHITECTURE.md#import-hygiene).
    settings["_vat_sku"] = masters.load_masters(config_dir, settings, log)["vat_sku"]

    if partial_roster:
        count = apply_partial_roster(settings, platform)
        log.warn(f"PARTIAL ROSTER: {count} expected {platform} store(s) made optional "
                 f"for this run; the unexpected-store check stays armed. This run "
                 f"covers a SUBSET of the roster and its totals are not the month's.")

    return RunContext(
        platform=platform, period=period,
        input_root=input_root, output_root=output_root, config_dir=config_dir,
        settings=settings, log=log, refs=refs or {},
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
        return self.context.output_dir / "finance_file.xlsx"


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
    ingest.check_stores(income, "income", "tiktok", settings, log)

    with m.stage("classify", "compute", rows_out=lambda: len(cl)):
        cl = classify.classify_tiktok_income(income, log)
    good = cl[cl["check_status"] == classify.CHECK_GOOD]
    with m.stage("explode_to_sku", "compute", rows_out=lambda: len(sku)):
        sku = calculate.explode_to_sku_tiktok(good, orders, log)

    # Reference captured HERE — from the income frame, before the money math —
    # so everything compute_sku_columns does is inside the checked span.
    reference, unmatched_money, unmatched_orders = tieout.partition(
        good, money_col=TIKTOK_MONEY, present_order_ids=sku["order_id"])

    with m.stage("compute_sku_columns", "compute", rows_out=lambda: len(sku)):
        sku = calculate.compute_sku_columns_tiktok(sku, settings, log)
    out.frames.update(classified=cl, good=good, sku=sku)

    with m.stage("build_workbook", "serialize"):
        out.workbook, out.checks = finance_template.build_tiktok(
            sku, settings, window_meta(sku["statement_date"]), log)

    log.section("TIE-OUT")
    with m.stage("tieout", "compute"):
        out.tieout = tieout.run_checks_tiktok(
            sku, reference, settings, log, money_col=TIKTOK_MONEY,
            unmatched_money=unmatched_money, unmatched_orders=unmatched_orders)
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

    out.exceptions["unmatched_orders"] = good[~good["order_id"].astype(str).isin(
        sku["order_id"].astype(str))]
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
    ingest.check_stores(income, "income", "shopee", settings, log)

    # Captured from the INCOME frame before any money math, so the whole
    # calculate stage sits inside the checked span — the same discipline as
    # TikTok's reference. `shopee_product_subsidy` is why this reads `income`
    # rather than the classified frame: classify aggregates a fixed column list
    # that does not include it.
    revenue_crossing = tieout.revenue_crossing_shopee(income, log)

    with m.stage("classify", "compute", rows_out=lambda: len(cl)):
        cl = classify.classify_shopee_income(income, log)
    with m.stage("explode_to_sku", "compute", rows_out=lambda: len(sku)):
        sku = calculate.explode_to_sku_shopee(cl, orders, log)
    with m.stage("compute_sku_columns", "compute", rows_out=lambda: len(sku)):
        sku = calculate.compute_sku_columns_shopee(sku, settings, log)
    out.frames.update(orders=orders, income=income, classified=cl, sku=sku)

    with m.stage("build_workbook", "serialize"):
        out.workbook, out.checks = finance_template.build_shopee(
            sku, settings, window_meta(sku["statement_date"]), log)

    ok = sku[sku["check_status"] == classify.SHOPEE_OK]
    out.frames["ok"] = ok

    log.section("TIE-OUT")
    reference, unmatched_money, unmatched_orders = tieout.partition(
        cl, money_col="net_revenue", present_order_ids=sku["order_id"])
    with m.stage("tieout", "compute"):
        out.tieout = tieout.run_checks_shopee(
            sku, reference, settings, log, money_col=SHOPEE_MONEY,
            unmatched_money=unmatched_money, unmatched_orders=unmatched_orders,
            crossing=revenue_crossing)
    out.consume_tieout()
    per_store = {
        s: {"ok_pre_vat": float(g["amount_pre_vat"].sum()),
            "ok_with_vat": float(g["amount_with_vat"].sum())}
        for s, g in ok.groupby("store")
    }
    out.exceptions["tieout_breaches"] = out.tieout[out.tieout["result"] == "BREACH"]         if out.tieout is not None else None

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
    with m.stage("classify_ledger", "compute", rows_out=lambda: len(cl)):
        cl, unmapped = lazada.classify_ledger(ledger, fee_types, vat_sku, settings, log)
    with m.stage("revenue_lines", "compute", rows_out=lambda: len(rev)):
        rev = lazada.revenue_lines(cl, log)
    out.frames.update(ledger=ledger, classified=cl, revenue=rev, unmapped=unmapped)
    out.exceptions["unmapped_fees"] = unmapped

    with m.stage("build_workbook", "serialize"):
        out.workbook, out.checks = finance_template.build_lazada(
            rev, settings, window_meta(cl["transaction_date"]), log, classified=cl)

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


def write_artifacts(result: RunResult) -> list[Path]:
    """The only writer in the codebase. Returns what it wrote.

    Kept deliberately dumb: it makes no decisions about what a run *meant*, so
    a worker can substitute an object-store variant without re-implementing any
    judgement.
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
            finance_template.write_workbook(result.workbook, result.workbook_path,
                                            result.checks, ctx.log)
        written.append(result.workbook_path)

    # exceptions.xlsx — computed since the beginning and never written, so
    # every unmatched order and unmapped fee was dropped on the floor each run.
    # It is a NEW file: the finance workbook is untouched, so the golden gate
    # stays green (docs/06-DECISIONS.md#d12, Class A).
    populated = {k: v for k, v in result.exceptions.items() if v is not None and len(v)}
    if populated:
        from . import export
        path = ctx.output_dir / "exceptions.xlsx"
        rows = export.write_exceptions_file(path, populated, ctx.log)
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
        path.write_text(json.dumps(result.metrics.to_dict(), indent=2) + "\n",
                        encoding="utf-8")
        written.append(path)

    log_path = ctx.output_dir / "run_log.txt"
    ctx.log.write(log_path)
    written.append(log_path)
    return written
