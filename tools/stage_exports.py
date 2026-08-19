"""Stage raw exports into the layout the pipeline expects — M2.5's normalizer.

    input/original exports/<platform>/**  ->  input/<window>/<platform>/...

## What this replaces

`tools/stage_july.ps1`: absolute paths from one developer's machine, a
hand-maintained folder-name -> window-label table rewritten every month, routing
on the substrings "rder"/"ncome", and no validation of any kind. Staging was
recorded as "the single biggest fragility" in docs/04-DATA-FLOW.md, and every
month's dump needed the script edited before it would run.

## The idea: the window comes from the DATA, not from a folder name

A settlement window is a real thing — a date range the platform paid out for —
and every income/ledger export states its own range. So the window is *derived*:
read the settlement dates, group the files, sort the groups chronologically and
index them (`2026-05_s1`, `_s2`, ...). Nothing is inferred from a folder name,
which is what made the July Lazada window need a manual restage
(docs/12-CHANGE-HISTORY.md).

Deriving it also reproduces the labels a human chose by hand for the eight
windows currently staged, which is what `tests/test_staging.py` pins.

## Refuses rather than guesses

Staging is where a whole store, or a whole extra settlement block, silently
enters a month. So this **plans by default and copies only with `--apply`**, and
it refuses outright on:

  - a file it cannot classify, read, or derive a store name from
  - identical content (SHA-256) headed for two different windows, or already
    staged under another window — the double-pull class, 5.97B VND of
    double-invoicing risk when it last happened
  - an export whose settlement range extends past its siblings' — the shape of
    that same mis-pull, caught at staging instead of by ad-hoc analysis later

Copies, never moves: the raw exports stay untouched. Idempotent by digest, so
re-running cannot perturb a golden already generated from a staged tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# parents[1], not [2]: this file moved from tools/parity/ to tools/ in M1
# (D26) and kept the old depth, so every path it computed pointed one level
# ABOVE the repo and it could not find `input/original exports` at all.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC_DIR = ROOT / "input" / "original exports"
IN_DIR = ROOT / "input"

LAZADA_SHEET_TO_VARIANT = {
    "Transaction Overview": "Weekly",
    "Income Overview": "Daily",
}
# The window label's platform letter (docs/04-DATA-FLOW.md#window-naming).
PLATFORM_LETTER = {"tiktok": "w", "shopee": "s", "lazada": "l"}
# Kinds that STATE a settlement window. Order exports deliberately span earlier
# months (the cross-period stitch needs the prior-month re-pull), so they can
# never define one; they inherit their group's.
WINDOW_DEFINING = ("income", "Weekly", "Daily")


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def kind_from_name(name: str) -> str | None:
    low = name.lower()
    if re.search(r"income", low):
        return "income"
    if re.search(r"order", low):
        return "orders"
    return None


def lazada_variant(path: Path) -> str | None:
    """Weekly vs Daily from the SHEET NAME, never the folder name."""
    from python_calamine import CalamineWorkbook
    try:
        sheets = CalamineWorkbook.from_path(path).sheet_names
    except Exception:                                   # noqa: BLE001
        # Unreadable. Returning None routes it into the "cannot classify" refusal
        # WITH its name, rather than crashing with a traceback that names no file.
        #
        # The real case behind this — one of five KAO weekly exports — was recorded
        # as "password-protected" from Aug 2026 until 2026-08-19, when the bytes were
        # actually looked at. It is encrypted by a Microsoft **sensitivity label**,
        # not a password, and `ingest.rights_protected` now says so. The difference
        # decides what anyone does about it: a password is something you ask a
        # colleague for; a label is org policy that no re-save can lift.
        return None
    for sheet, variant in LAZADA_SHEET_TO_VARIANT.items():
        if sheet in sheets:
            return variant
    return None


# ---------------------------------------------------------------------------
# Probing: what does this file say about itself?
# ---------------------------------------------------------------------------

@dataclass
class Probe:
    """One raw file, described by its own contents. No cell values are kept."""

    path: Path
    platform: str
    kind: str | None = None          # income | orders | Weekly | Daily
    store: str | None = None
    rows: int = 0
    first: date | None = None
    last: date | None = None
    sha: str = ""
    problem: str | None = None

    @property
    def defines_window(self) -> bool:
        return self.kind in WINDOW_DEFINING and self.first is not None

    @property
    def subdir(self) -> str:
        """Where it lands under input/<window>/<platform>/."""
        return self.kind if self.kind in ("income", "orders") else str(self.kind)


def _date_header(platform: str, kind: str, settings: dict) -> str | None:
    """The raw header holding the settlement date, from the column maps — so
    this tool never becomes a second source of truth for a header spelling."""
    if platform == "lazada":
        # Through the pipeline's own accessor, so this tool never becomes a second
        # source of truth for a header spelling. Lazada's map moved from constants
        # in src/lazada.py into the contract in M8/1.7; the accessor is what both
        # sides read.
        from src import lazada
        m = lazada.column_map(settings, "weekly" if kind == "Weekly" else "daily")
        return next((s for s, d in m.items() if d == "transaction_date"), None)
    from src import config
    cmap = config.column_map(settings, platform, kind) or {}
    return next((s for s, d in cmap.items() if d == "statement_date"), None)


def _read_dates(path: Path, platform: str, kind: str, settings: dict):
    """(row_count, min_date, max_date) for one export, or a problem string.

    Deliberately NOT ingest.read_parts: that enforces required columns and hard
    stops, and the whole point here is to *detect* a file it would reject —
    a Summary-only Shopee export with no revenue sheet needed manual removal ten
    times in July.
    """
    import pandas as pd

    header = _date_header(platform, kind, settings)
    if not header:
        return None, None, None, f"no settlement-date header configured for {platform}/{kind}"

    if platform == "lazada":
        from src import lazada
        sheets = [lazada.sheet_name(settings, "weekly" if kind == "Weekly" else "daily")]
        header_row, engine = 1, "calamine"
    else:
        header_row = int(((settings.get("header_rows") or {}).get(platform) or {}).get(kind, 1))
        eng = ((settings.get("reader_engine") or {}).get(platform) or {}).get(kind)
        engine = "calamine" if eng == "calamine" else None
        pattern = ((settings.get("sheet_patterns") or {}).get(platform) or {}).get(kind)
        named = ((settings.get("sheet_names") or {}).get(platform) or {}).get(kind)
        try:
            available = pd.ExcelFile(path, engine=engine).sheet_names
        except Exception as exc:                                    # noqa: BLE001
            return None, None, None, f"unreadable ({type(exc).__name__})"
        if pattern:
            sheets = [s for s in available if re.search(pattern, s)]
            if not sheets:
                return None, None, None, (
                    f"no sheet matching /{pattern}/ — an export with no data sheet "
                    f"(sheets present: {len(available)})")
        else:
            sheets = [named] if named and named in available else [available[0]]

    frames = []
    for sheet in sheets:
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=header_row - 1,
                               dtype=str, engine=engine or "calamine")
        except Exception as exc:                                    # noqa: BLE001
            return None, None, None, f"unreadable sheet {sheet!r} ({type(exc).__name__})"
        df.columns = [str(c).strip() for c in df.columns]
        frames.append(df)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not len(df):
        return 0, None, None, "no data rows"
    if header not in df.columns:
        return len(df), None, None, f"settlement-date column {header!r} absent"

    # Through the pipeline's own accessor, for the same reason `_date_header` is:
    # this tool must never become a second source of truth for how a settlement
    # date is spelled. It was one until 2026-08-19 by omission — it read
    # `dayfirst` directly, so TikTok July's `%Y/%m/%d` income parsed as `%Y/%d/%m`
    # and every folder's window came out spanning January to September. Windows
    # could not be derived at all, and the same-named prior-month re-pull in each
    # folder then collided as a false double-pull.
    from src.ingest import date_format

    fmt = date_format(settings, platform, "weekly" if kind == "Weekly"
                      else "daily" if kind == "Daily" else kind)
    dayfirst = bool((settings.get("dayfirst") or {}).get(platform, False))
    if fmt:
        d = pd.to_datetime(df[header], errors="coerce", format=fmt).dropna()
    else:
        d = pd.to_datetime(df[header], errors="coerce", dayfirst=dayfirst).dropna()
    if d.empty:
        # No date parsed. Before calling that broken, ask whether there is any
        # DATA here at all: TikTok emits one all-blank row for a store that
        # settled nothing in a window, and three July 2026 exports are exactly
        # that (Merries and Reckitt 29-31, Nutifood-Varna-Life 22-28 — one row,
        # 65 columns, not a single non-blank cell, zero revenue). Reported as
        # "no parseable dates" they read as a BROKEN export and held their whole
        # window back; recognised as empty they join Shopee's zero-revenue
        # "part 2" exports in the self-declared-empty class (`_is_window_member`),
        # and `check_stores` still fires if the store has genuinely vanished.
        #
        # Tested only on this path, and only then, because it is a full
        # stringify: an income file can be 674,000 rows x 65 columns and paying
        # for that on every healthy file would dominate staging.
        if _is_blank(df):
            return 0, None, None, "no data rows"
        return len(df), None, None, f"no parseable dates in {header!r}"
    return len(df), d.min().date(), d.max().date(), None


def _is_blank(df) -> bool:
    """True when every cell is empty, whitespace or a null marker.

    Whitespace matters: the row that blocked `2026-07_w4` held a single space in
    each cell, so an `== ""` test called it data.
    """
    import pandas as pd

    if df.empty:
        return True
    blank = df.apply(lambda s: s.astype(str).str.strip()
                     .isin(("", "nan", "None", "NaT", "<NA>")))
    return bool(blank.to_numpy().all())


def probe(path: Path, platform: str, settings: dict) -> Probe:
    p = Probe(path=path, platform=platform, sha=sha256_of(path))
    p.kind = lazada_variant(path) if platform == "lazada" else kind_from_name(path.name)
    if p.kind is None:
        p.problem = ("unreadable or not a recognised Lazada schema"
                     if platform == "lazada" else
                     "file name says neither 'order' nor 'income'")
        return p

    # One lookup for all three platforms since M8/1.7 — Lazada's pattern is in the
    # contract rather than in `src/lazada.py`, so there is no second place to edit.
    pattern = (settings.get("store_from_filename") or {}).get(platform)
    if pattern:
        m = re.match(pattern, path.name, flags=re.IGNORECASE)
        if m and (m.group(1) or "").strip():
            p.store = m.group(1).strip()
        else:
            p.problem = "no store name derivable from the file name"
            return p

    if p.kind in WINDOW_DEFINING:
        rows, first, last, problem = _read_dates(path, platform, p.kind, settings)
        p.rows, p.first, p.last, p.problem = rows or 0, first, last, problem
    return p


# ---------------------------------------------------------------------------
# Deriving windows from the probed dates
# ---------------------------------------------------------------------------

def group_key(p: Probe, source: Path) -> str:
    """Which files belong to one settlement window.

    TikTok/Shopee dumps arrive one folder per window with orders and income
    together, and an income export is split into `part 1..N` whose ranges are
    consecutive — so the folder is the grouping and the parts merge into one
    span. Files at the source root form their own group.

    Lazada has one ledger file per store per window and no parts, and a store's
    five weekly exports commonly arrive in ONE folder (browser-numbered
    `2_KAO (1).xlsx` ...). Folder grouping would collapse five windows into one,
    so Lazada groups by the file's own date range instead, which also puts two
    different stores' same-week exports in the same window.
    """
    if p.platform == "lazada":
        return f"{p.first}..{p.last}"
    try:
        parts = p.path.relative_to(source).parts
    except ValueError:
        return p.path.parent.as_posix()
    # The TOP-level folder, not the immediate parent: a real Shopee dump nests
    # "11_20 - Xmen/Doanh Thu/" and "11_20 - Xmen/Order New/" separately, and
    # keying on the parent put the order files in a group with no income file at
    # all — so their window could not be derived. Files at the root group as ".".
    return parts[0] if len(parts) > 1 else "."


def derive_windows(probes: list[Probe], source: Path) -> tuple[dict[str, str], dict[str, tuple]]:
    """{group_key: period_label}, {group_key: (first, last)}.

    Groups are ordered by their settlement span and indexed from 1, which is
    exactly how the team's own labels run (`01-10` -> s1, `11-20` -> s2 ...).
    """
    groups: dict[str, list[Probe]] = defaultdict(list)
    for p in probes:
        groups[group_key(p, source)].append(p)

    spans: dict[str, tuple[date, date]] = {}
    for key, members in groups.items():
        dated = [m for m in members if m.defines_window]
        if dated:
            spans[key] = (min(m.first for m in dated), max(m.last for m in dated))

    if not spans:
        return {}, {}
    platform = probes[0].platform
    letter = PLATFORM_LETTER[platform]
    # The month label is the modal year-month of the window ends, so one export
    # that laps into the next month cannot rename the whole batch.
    month = Counter(f"{last:%Y-%m}" for _, last in spans.values()).most_common(1)[0][0]

    # A window is a DATE RANGE, not a folder — so groups whose spans overlap are
    # one window even when they arrived as separate folders. Two stores of the
    # same Shopee window do exactly that (Masan at the source root, Xmen under
    # `1_10/`), and they must not become s1 and s2. Adjacent-but-not-overlapping
    # spans stay separate, which is what keeps Lazada's consecutive weeks
    # (01-03, 04-10, 11-17, 18-24) as four windows rather than one.
    clusters: list[tuple[date, date, list[str]]] = []
    for key, (lo, hi) in sorted(spans.items(), key=lambda kv: kv[1]):
        if clusters and lo <= clusters[-1][1]:
            c_lo, c_hi, members = clusters[-1]
            clusters[-1] = (min(c_lo, lo), max(c_hi, hi), [*members, key])
        else:
            clusters.append((lo, hi, [key]))

    labels, cluster_spans = {}, {}
    for i, (lo, hi, members) in enumerate(clusters, start=1):
        label = f"{month}_{letter}{i}"
        for key in members:
            labels[key] = label
            cluster_spans[key] = (lo, hi)
    return labels, cluster_spans


def find_outliers(probes: list[Probe], source: Path) -> list[str]:
    """Files whose settlement range extends past their siblings'.

    This is the shape of the July mis-pull: an export that also contained the
    whole previous settlement block, 18,352 orders / 5,973,070,353 VND of
    double-invoicing risk, found only by ad-hoc cross-window analysis. With two
    or more dated files in a group the outlier is visible at staging time.
    """
    groups: dict[str, list[Probe]] = defaultdict(list)
    for p in probes:
        if p.defines_window:
            groups[group_key(p, source)].append(p)

    notes: list[str] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        starts = Counter(m.first for m in members)
        modal_start = starts.most_common(1)[0][0]
        for m in members:
            if m.first < modal_start:
                notes.append(
                    f"{m.path.name}: settles from {m.first} while its {len(members)-1} "
                    f"sibling(s) in '{key}' start at {modal_start} — candidate mis-pull "
                    f"carrying an earlier settlement block. Verify before staging; if "
                    f"confirmed, declare it under window_settlement_bounds (D9).")
    return notes


def find_duplicates(assignment: list[tuple[Probe, str]], staged_root: Path,
                    platform: str) -> list[str]:
    """The double-pull class, by content digest.

    Two checks, and they are deliberately NOT the same check:

    **Into one window — always a problem, whatever the kind.** The same bytes
    twice in a window duplicates its rows. For an income export that double-counts
    revenue directly; for an order export it fans out the SKU explode
    (`calculate.explode_to_sku_*` joins income to orders on order_id) and
    double-counts `amount_pre_vat`. Either way the window is wrong.

    **Across two windows — a problem only for the kinds that DEFINE a window.**
    This was `!=` on the period for every kind until 2026-08-19, and it is why
    TikTok July could not be staged at all: TikTok ships each store's prior-month
    order re-pull in *every* weekly folder, byte-identical, because the
    cross-period stitch needs it (`WINDOW_DEFINING` says the same thing from the
    other side — order exports deliberately span earlier months and can never
    define a window). Twenty-three such files were reported as double-pulls in one
    dump. They cannot double-count revenue: money comes from the income frame,
    each window's run reads only its own folder, and orders merely supply the SKU
    lines for order_ids that window's income already selected. Flagging them
    taught the reader to reach for --pattern until the tool went quiet, which is
    the failure mode this whole file is written against.

    An income/Weekly/Daily export in two windows is still reported, because that
    one really is the 5.97B VND shape.
    """
    problems: list[str] = []
    by_sha: dict[str, list[tuple[Probe, str]]] = defaultdict(list)
    for p, period in assignment:
        by_sha[p.sha].append((p, period))
    for sha, entries in by_sha.items():
        if len(entries) > 1:
            where = ", ".join(f"{p.path.name} -> {period}" for p, period in entries)
            problems.append(f"identical content ({sha[:12]}) staged more than once: {where}")

    if not staged_root.is_dir():
        return problems
    # Only window-defining kinds can double-count revenue across windows.
    planned = {p.sha: (p, period) for p, period in assignment
               if p.kind in WINDOW_DEFINING}
    if not planned:
        return problems
    for existing in sorted(staged_root.glob(f"*/{platform}/**/*")):
        if not existing.is_file() or existing.suffix.lower() not in (".xlsx", ".csv"):
            continue
        # `input/original exports/` lives INSIDE `input/`, so this glob reaches
        # the raw dump and every file matched itself as its own double-pull.
        if existing.is_relative_to(SRC_DIR):
            continue
        sha = sha256_of(existing)
        hit = planned.get(sha)
        if hit is None:
            continue
        p, period = hit
        existing_period = existing.relative_to(staged_root).parts[0]
        if existing_period != period:
            problems.append(
                f"identical content ({sha[:12]}) is already staged as "
                f"{existing.relative_to(staged_root).as_posix()} but would also go to "
                f"{period}/{platform}/{p.subdir}/{p.path.name} — double-pull")
    return problems


# ---------------------------------------------------------------------------
# Plan / apply
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    assignment: list[tuple[Probe, str]] = field(default_factory=list)
    unusable: list[Probe] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    outliers: list[str] = field(default_factory=list)
    spans: dict[str, tuple] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    blocked: dict[str, list[str]] = field(default_factory=dict)   # period -> reasons
    unplaceable: list[str] = field(default_factory=list)          # no window derivable

    @property
    def stageable(self) -> list[tuple[Probe, str]]:
        """Everything bound for a window that is not blocked."""
        return [(p, period) for p, period in self.assignment if period not in self.blocked]

    @property
    def ok(self) -> bool:
        return not self.blocked


def _is_window_member(p: Probe) -> bool:
    """Would a staged window be INCOMPLETE without this file?

    Two kinds of unusable file are not members, and blocking a window on them
    would be wrong rather than cautious:

    - **Unidentifiable** (`kind is None`): the name says neither order nor income,
      or it is not a Lazada schema at all. A team analysis workbook that happens
      to sit in the dump folder is not a missing export.
    - **Self-declared empty**: nine Shopee "part 2" income exports in July had no
      revenue sheet and each declared total 0 in its own Summary. The team's
      conclusion was to leave them out of staging, not to hold the window back —
      and `check_stores` still fires if a store genuinely vanishes.

    Anything else — an order/income export that will not open, one whose store
    name cannot be derived, one missing its settlement-date column — IS a member,
    and its window is held back.
    """
    if p.kind is None:
        return False
    empty = ("no data rows", "no sheet matching")
    return not (p.problem and any(marker in p.problem for marker in empty))


def build_plan(files: list[Path], platform: str, settings: dict, source: Path,
               *, period: str | None = None, staged_root: Path = IN_DIR) -> Plan:
    plan = Plan()
    probes = [probe(f, platform, settings) for f in files]
    plan.unusable = [p for p in probes if p.problem]
    usable = [p for p in probes if not p.problem]
    if not usable:
        return plan

    if period:
        # Explicit single-window staging: the caller has already decided, so no
        # derivation runs. Kept because a subset window (one store, for a
        # golden) is a legitimate thing to stage deliberately.
        plan.assignment = [(p, period) for p in usable]
    else:
        plan.labels, plan.spans = derive_windows(usable, source)
        for p in usable:
            key = group_key(p, source)
            label = plan.labels.get(key)
            if label is None:
                p.problem = ("no settlement dates in this group, so its window cannot be "
                             "derived — stage it with an explicit --period")
                plan.unusable.append(p)
            else:
                plan.assignment.append((p, label))

    plan.outliers = find_outliers(usable, source)
    plan.duplicates = find_duplicates(plan.assignment, staged_root, platform)

    # Block the affected WINDOW, not the whole run. "Never stage a partial
    # window" is the guarantee worth keeping; refusing everything because one
    # file elsewhere in the dump is unreadable just teaches people to reach for
    # --pattern until it goes quiet. An unusable file whose group still maps to
    # a window blocks that window — a Shopee income part that will not open
    # means s1 is incomplete. One that cannot be placed at all (an unreadable
    # Lazada ledger has no date, so no window) is reported and skipped: no
    # staged window is missing anything because of it.
    for p in plan.unusable:
        label = plan.labels.get(group_key(p, source)) if not period else period
        if label and _is_window_member(p):
            plan.blocked.setdefault(label, []).append(f"{p.path.name}: {p.problem}")
        else:
            plan.unplaceable.append(f"{p.path.name}: {p.problem}")
    for note in plan.duplicates:
        for label in sorted(set(plan.labels.values()) | ({period} if period else set())):
            if label and label in note:
                plan.blocked.setdefault(label, []).append(note)
    return plan


def print_plan(plan: Plan, platform: str, source: Path) -> None:
    print(f"\n{source}  ->  {IN_DIR}/<window>/{platform}/")
    if plan.labels:
        print("\nwindows derived from the exports' own settlement dates:")
        for key, label in sorted(plan.labels.items(), key=lambda kv: kv[1]):
            lo, hi = plan.spans[key]
            print(f"  {label:<14} settles {lo}..{hi}   (group {key!r})")

    by_period: dict[str, list[Probe]] = defaultdict(list)
    for p, period in plan.assignment:
        by_period[period].append(p)
    for period in sorted(by_period):
        members = by_period[period]
        print(f"\n  {period}/{platform}/")
        for sub in sorted({m.subdir for m in members}):
            group = [m for m in members if m.subdir == sub]
            rows = sum(m.rows for m in group)
            stores = sorted({m.store for m in group if m.store})
            print(f"    {sub}/  {len(group)} file(s)"
                  + (f", {rows:,} rows" if rows else "")
                  + f", {len(stores)} store(s)")

    for note in plan.outliers:
        print(f"\n  WARNING  {note}")
    for period, reasons in sorted(plan.blocked.items()):
        print(f"\n  BLOCKED  {period} will NOT be staged:")
        for r in reasons:
            print(f"             {r}")
    for note in plan.unplaceable:
        print(f"\n  SKIPPED  {note}\n           (no window derivable, so no staged "
              f"window is missing it — but check that on purpose)")


def write_manifest(period: str, platform: str, members: list[Probe]) -> Path:
    """Provenance for a staged window: what came from where, and its digest.

    Staging used to leave no record at all, so "is this window complete?" was
    unanswerable after the fact. Not read by the pipeline (`file_formats` covers
    .xlsx/.csv only) — it exists for the human and for a re-stage.
    """
    path = IN_DIR / period / platform / "staging.json"
    # MERGE, never overwrite: a window is legitimately staged in several passes
    # (two stores arriving in different dump folders is the normal case), and an
    # overwriting manifest would describe only the last pass while looking
    # complete.
    existing: dict[tuple[str, str], dict] = {}
    if path.is_file():
        for entry in (json.loads(path.read_text(encoding="utf-8")).get("files") or []):
            existing[(entry.get("subdir", ""), entry.get("name", ""))] = entry
    for m in members:
        existing[(m.subdir, m.path.name)] = {
            "name": m.path.name, "subdir": m.subdir, "store": m.store,
            "rows": m.rows, "sha256": m.sha,
            "settles_from": str(m.first) if m.first else None,
            "settles_to": str(m.last) if m.last else None,
            "source": m.path.as_posix()}
    doc = {
        "period": period,
        "platform": platform,
        "files": sorted(existing.values(), key=lambda e: (e["subdir"], e["name"])),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def apply_plan(plan: Plan, platform: str) -> int:
    copied = skipped = 0
    by_period: dict[str, list[Probe]] = defaultdict(list)
    for p, period in plan.stageable:
        target_dir = IN_DIR / period / platform / p.subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / p.path.name
        # Idempotent: identical content is left alone so re-staging cannot
        # perturb a golden that was already generated from this tree.
        if target.exists() and target.stat().st_size == p.path.stat().st_size \
                and sha256_of(target) == p.sha:
            skipped += 1
        else:
            shutil.copy2(p.path, target)
            copied += 1
        by_period[period].append(p)
    for period, members in sorted(by_period.items()):
        write_manifest(period, platform, members)
    print(f"\n  copied {copied}, already present {skipped}, "
          f"{len(by_period)} window(s), manifest written per window")
    return copied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=["tiktok", "shopee", "lazada"])
    ap.add_argument("--source", default=None, help=f"default: {SRC_DIR}/<platform>")
    ap.add_argument("--period", default=None,
                    help="Stage everything matched into ONE named window instead of "
                         "deriving windows from the data. For deliberately staging a "
                         "subset (e.g. one store, for a golden).")
    ap.add_argument("--pattern", default="*",
                    help="Filename glob limiting what is staged, e.g. '*Mars*'.")
    ap.add_argument("--apply", action="store_true",
                    help="Actually copy. Without it this prints the plan and copies "
                         "nothing — staging is where a whole store or an extra "
                         "settlement block silently enters a month.")
    ap.add_argument("--dry-run", action="store_true", help="Explicit no-op (the default).")
    args = ap.parse_args(argv)

    from src import config
    settings = config.load_settings(ROOT / "config")

    source = Path(args.source) if args.source else SRC_DIR / args.platform
    if not source.is_dir():
        raise SystemExit(f"source not found: {source}")
    files = [p for p in sorted(source.rglob(args.pattern))
             if p.is_file() and p.suffix.lower() in (".xlsx", ".csv")]
    if not files:
        raise SystemExit(f"no .xlsx/.csv files matching {args.pattern!r} under {source}")

    plan = build_plan(files, args.platform, settings, source, period=args.period)
    print_plan(plan, args.platform, source)

    if not plan.stageable:
        raise SystemExit(
            "\nREFUSING to stage: nothing is stageable. A partially staged window is "
            "worse than none — the run that follows looks fine and is simply missing "
            "data. Resolve the items above, or narrow --pattern.")
    if not args.apply:
        print(f"\n  plan only — nothing copied. Re-run with --apply to stage "
              f"{len({p for _, p in plan.stageable})} window(s).")
        return 0
    apply_plan(plan, args.platform)
    # Exit 1 when some windows were held back, so a caller cannot read "staged"
    # as "staged everything".
    return 1 if plan.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
