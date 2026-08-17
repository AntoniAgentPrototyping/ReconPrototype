"""Workbook comparison for the parity gate.

Tolerance rationale (do not loosen without redoing this arithmetic):

Bit-exact parity across engines is NOT achievable — pandas float64 `sum` uses
pairwise summation, polars uses chunked SIMD, so reduction order genuinely
differs. Asserting it would produce a gate that fails for reasons that carry
no financial meaning.

The bound is calculable. Money is ~1e9 VND; float64 eps is 2.2e-16, so 1 ulp
is ~2e-7 VND. Worst-case accumulation over ~288,000 rows is
    288_000 * 2.2e-16 * 1e9  ~=  0.06 VND
so `abs_vnd = 0.5` sits ~8x above the theoretical worst case and 20x below the
team's tightest business tolerance (10 VND on the Shopee return rule). It
cannot mask a real error: a dropped or duplicated row moves totals by
thousands, and VND has no minor unit.

The one discontinuity epsilon must NOT absorb is the invoice rounding model
(src/finance_template.py:171-178): `aveg = (pre_exact/qty).round(0)` then
`pre = aveg*qty`. If `pre_exact/qty` sits within an ulp of x.5, a last-bit
difference flips the round and `pre` moves by `qty` — hundreds or thousands of
VND. Those groups are enumerated by the golden generator and allowlisted
explicitly by key, never covered by widening the epsilon.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from cellset import TAG_NUMBER, Cell, CellSet, Sheet, as_cellset


class DiffKind(str, Enum):
    MISSING_SHEET = "MISSING_SHEET"
    EXTRA_SHEET = "EXTRA_SHEET"
    SHEET_ORDER = "SHEET_ORDER"
    DIM = "DIM"
    NULLNESS = "NULLNESS"
    TYPE = "TYPE"
    VALUE = "VALUE"
    FORMAT = "FORMAT"
    KNIFE_EDGE = "KNIFE_EDGE"


@dataclass(frozen=True)
class TolerancePolicy:
    abs_vnd: float = 0.5
    rel: float = 1e-9
    exact_number_formats: bool = True
    # Keys of pivot groups known to sit on a rounding knife edge. Populated by
    # the golden generator and COMMITTED, so a newly-appearing knife edge
    # fails the build instead of being quietly tolerated.
    knife_edge: frozenset[str] = frozenset()


DEFAULT_POLICY = TolerancePolicy()

# A policy for triage only — classifies a failure as order-only vs value-only.
# Never used as a gate: see the row-order note in diff_report().
LENIENT_FORMATS = TolerancePolicy(exact_number_formats=False)


@dataclass(frozen=True)
class SheetIssue:
    kind: DiffKind
    sheet: str
    detail: str


@dataclass(frozen=True)
class CellDiff:
    kind: DiffKind
    sheet: str
    ref: str
    golden: object
    candidate: object
    delta: float | None = None

    def __str__(self) -> str:
        d = f"  d {self.delta:+,.2f}" if self.delta is not None else ""
        return (f"[{self.kind.value:<12}] '{self.sheet}'!{self.ref:<8} "
                f"golden {self.golden!r}  cand {self.candidate!r}{d}")


@dataclass
class WorkbookDiff:
    compared: int = 0
    sheet_issues: list[SheetIssue] = field(default_factory=list)
    cells: list[CellDiff] = field(default_factory=list)
    policy: TolerancePolicy = DEFAULT_POLICY

    @property
    def ok(self) -> bool:
        return not self.sheet_issues and not self.cells

    def kinds(self) -> set[DiffKind]:
        return {i.kind for i in self.sheet_issues} | {c.kind for c in self.cells}

    def report(self, max_report: int = 25) -> str:
        if self.ok:
            return f"PARITY OK — {self.compared:,} cells compared, no differences"
        p = self.policy
        out = [
            "PARITY FAIL",
            f"  policy    abs<={p.abs_vnd} VND · rel<={p.rel:g} · "
            f"formats {'exact' if p.exact_number_formats else 'ignored'}",
            f"  cells     {self.compared:,} compared · {len(self.cells)} differ",
        ]
        for issue in self.sheet_issues:
            out.append(f"  [{issue.kind.value:<12}] '{issue.sheet}' {issue.detail}")
        shown = self.cells if max_report == 0 else self.cells[:max_report]
        out.extend(str(c) for c in shown)
        if len(shown) < len(self.cells):
            out.append(f"  ... {len(self.cells) - len(shown)} more "
                       f"(max_report=0 for all)")
        return "\n".join(out)


def _numbers_differ(g: float, c: float, policy: TolerancePolicy) -> tuple[bool, float]:
    delta = float(c) - float(g)
    if delta == 0.0:
        return False, 0.0
    scale = max(abs(float(g)), abs(float(c)))
    within = abs(delta) <= policy.abs_vnd or abs(delta) <= policy.rel * scale
    return (not within), delta


def _compare_cell(sheet_name: str, ref: str, g: Cell | None, c: Cell | None,
                  policy: TolerancePolicy) -> CellDiff | None:
    if g is None and c is None:
        return None
    if g is None or c is None:
        # A value where the other side is blank. Never tolerated: `None` and
        # `0.0` are different answers to "was there revenue here".
        return CellDiff(DiffKind.NULLNESS, sheet_name, ref,
                        g.value if g else None, c.value if c else None)
    if g.tag != c.tag:
        return CellDiff(DiffKind.TYPE, sheet_name, ref,
                        f"{g.tag}:{g.value!r}", f"{c.tag}:{c.value!r}")

    if g.tag == TAG_NUMBER:
        differs, delta = _numbers_differ(g.value, c.value, policy)  # type: ignore[arg-type]
        if differs:
            kind = (DiffKind.KNIFE_EDGE if f"{sheet_name}!{ref}" in policy.knife_edge
                    else DiffKind.VALUE)
            return CellDiff(kind, sheet_name, ref, g.value, c.value, delta)
    elif g.value != c.value:
        # Strings compare exactly, including the Vietnamese verdict strings —
        # "OK" vs "check lai sai roi" is the whole point of a control block.
        return CellDiff(DiffKind.VALUE, sheet_name, ref, g.value, c.value)

    if policy.exact_number_formats and g.number_format != c.number_format:
        # Accounting vs plain changes how negatives read to finance, and no
        # value comparison catches it.
        return CellDiff(DiffKind.FORMAT, sheet_name, ref,
                        g.number_format, c.number_format)
    return None


def _compare_sheet(g: Sheet, c: Sheet, policy: TolerancePolicy,
                   diff: WorkbookDiff) -> None:
    if (g.max_row, g.max_col) != (c.max_row, c.max_col):
        diff.sheet_issues.append(SheetIssue(
            DiffKind.DIM, g.name,
            f"golden {g.max_row}x{g.max_col} vs cand {c.max_row}x{c.max_col}"))

    for key in sorted(set(g.cells) | set(c.cells)):
        diff.compared += 1
        cd = _compare_cell(g.name, g.ref(*key), g.cells.get(key), c.cells.get(key), policy)
        if cd is not None:
            diff.cells.append(cd)


def compare_workbooks(golden: Path | CellSet, candidate: Path | CellSet, *,
                      policy: TolerancePolicy = DEFAULT_POLICY) -> WorkbookDiff:
    """Compare two workbooks cell by cell.

    Sheet ORDER is compared, not just membership: tools/build_master_summary.py
    reads Lazada's per-VAT-rate tabs positionally, so a reordered workbook is
    wrong for its consumer even when every total ties.
    """
    g, c = as_cellset(golden), as_cellset(candidate)
    diff = WorkbookDiff(policy=policy)

    g_by, c_by = g.by_name(), c.by_name()
    for name in g.names:
        if name not in c_by:
            diff.sheet_issues.append(SheetIssue(DiffKind.MISSING_SHEET, name, "absent from candidate"))
    for name in c.names:
        if name not in g_by:
            diff.sheet_issues.append(SheetIssue(DiffKind.EXTRA_SHEET, name, "not in golden"))

    common = [n for n in g.names if n in c_by]
    if [n for n in g.names if n in c_by] != [n for n in c.names if n in g_by]:
        diff.sheet_issues.append(SheetIssue(
            DiffKind.SHEET_ORDER, "<workbook>",
            f"golden {[n for n in g.names if n in c_by]} vs "
            f"candidate {[n for n in c.names if n in g_by]}"))

    for name in common:
        _compare_sheet(g_by[name], c_by[name], policy, diff)
    return diff
