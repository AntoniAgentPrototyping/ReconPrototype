"""M1 gate — file I/O stays confined to the modules that declare it.

The pipeline's one load-bearing structural property is that `src/` is
frame-in/frame-out except at a named boundary. That property is what makes it
testable without real data, embeddable in a worker that streams artifacts to
object storage instead of disk, and cheap to move to another compute engine if
volume ever demands it (docs/06-DECISIONS.md#d25).

Nothing enforced it, so it would have eroded silently — a `pd.read_csv` added
to `calculate.py` for "just one lookup table" is a two-line change that quietly
makes the pipeline un-runnable anywhere but a developer's laptop.

AST-based rather than a text grep on purpose: a grep matches the word
`read_excel` inside this module's own allowlist, inside docstrings, and inside
comments explaining why something is forbidden. Only real call sites count.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Call names that touch the filesystem. Attribute calls (`pd.read_excel(...)`,
# `wb.save(...)`) and bare calls (`open(...)`) are both matched by final name,
# which is deliberately blunt: the point is to notice, not to classify.
IO_NAMES = frozenset({
    "read_excel", "read_csv", "read_json", "read_parquet", "read_table",
    "to_excel", "to_csv", "to_json", "to_parquet",
    "ExcelFile", "ExcelWriter", "load_workbook", "open_workbook",
    "open", "save",
    "read_text", "write_text", "read_bytes", "write_bytes",
    "mkdir", "unlink", "rmtree",
})

# Who may do what. A module absent from this map may do NO file I/O at all.
#
# The grant is per-name rather than per-module so that widening it is a visible,
# reviewable act: adding `read_csv` to calculate.py means editing this table and
# writing down why, which is exactly the conversation the lint exists to force.
ALLOWED: dict[str, frozenset[str]] = {
    # --- the read boundary -------------------------------------------------
    "ingest.py": frozenset({"read_excel", "read_csv", "ExcelFile"}),
    "lazada.py": frozenset({"read_excel"}),
    "config.py": frozenset({"open"}),
    "masters.py": frozenset({"open", "open_workbook"}),
    # --- the write boundary ------------------------------------------------
    # finance_template builds the Workbook in memory (build_*) and writes it in
    # exactly one function (write_workbook -> wb.save). The split is what lets a
    # worker stream the artifact without a temp file.
    "finance_template.py": frozenset({"save", "mkdir"}),
    # pipeline.write_artifacts is the declared writer. The grant is module-wide
    # because that is the granularity of this table — which is too coarse for
    # the invariant that actually matters ("run() writes nothing"), so
    # test_run_writes_nothing below checks it per FUNCTION.
    "pipeline.py": frozenset({"mkdir", "write_text"}),
    # RunLog is the audit-trail sink, not compute. Writing run_log.txt from here
    # is the point of the class.
    "runlog.py": frozenset({"write_text"}),
    # export.write_exceptions_file was PENDING through M1 — it wrote from inside
    # src/ but nothing called it, so exception rows were computed and dropped
    # every run. M2 routed it through pipeline.write_artifacts, which makes it a
    # declared writer like finance_template rather than an unrouted one.
    "export.py": frozenset({"ExcelWriter", "to_excel"}),
}


# Modules that write from inside src/ and are deliberately NOT granted, because
# the intent is to change them rather than bless them. Pinned as strict xfails
# so the violation stays visible without failing the suite; when a file is
# deleted its parametrized case simply stops being generated, leaving no stale
# marker behind (export_platforms.py went that way in M1).
#
# Empty since M2: export.py was the last entry and became a declared writer once
# write_artifacts started calling it. An empty table is the goal state, not a
# sign the mechanism is unused.
PENDING_BOUNDARY_FIX: dict[str, str] = {}


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _io_params():
    out = []
    for p in _py_files():
        marks = []
        if reason := PENDING_BOUNDARY_FIX.get(p.name):
            marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)]
        out.append(pytest.param(p, marks=marks, id=p.name))
    return out


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _violations(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed = ALLOWED.get(path.name, frozenset())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in IO_NAMES and name not in allowed:
            out.append((node.lineno, name))
    return out


def test_src_modules_exist():
    """Guard against the whole suite passing vacuously if SRC moves."""
    files = _py_files()
    assert len(files) >= 8, f"expected the pipeline modules under {SRC}, found {len(files)}"


@pytest.mark.parametrize("path", _io_params())
def test_no_file_io_outside_the_declared_boundary(path: Path):
    found = _violations(path)
    assert not found, (
        f"{path.relative_to(ROOT).as_posix()} performs file I/O that its entry in "
        f"tests/test_io_boundary.py::ALLOWED does not grant: "
        + ", ".join(f"line {ln}: {name}(...)" for ln, name in found)
        + ". Either move the I/O to the boundary modules, or widen the grant "
          "deliberately and say why."
    )


def test_allowlist_has_no_dead_entries():
    """A grant for a module that no longer exists, or for a call it no longer
    makes, is stale permission — the lint would keep allowing something nobody
    reviews. Catch it here rather than letting the table rot."""
    names = {p.name for p in _py_files()}
    stale_modules = sorted((set(ALLOWED) | set(PENDING_BOUNDARY_FIX)) - names)
    assert not stale_modules, (
        f"the tables name modules that no longer exist: {stale_modules}. "
        f"A grant or a pin for a deleted file is permission nobody reviews.")

    unused: list[str] = []
    for module, grants in ALLOWED.items():
        path = SRC / module
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {_call_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for grant in sorted(grants - used):
            unused.append(f"{module}:{grant}")
    assert not unused, f"ALLOWED carries grants nothing uses: {unused}"


def test_run_writes_nothing():
    """The seam's central promise, checked at function granularity.

    `run(ctx)` reads inputs and returns a RunResult; `write_artifacts(result)`
    is the only writer. That split is what lets the CLI put artifacts on disk
    while a worker streams them to object storage, with no branch inside the
    pipeline — and it is the reason a hard stop can still leave a log behind.

    The module-level ALLOWED grant cannot express this, because both functions
    live in the same file. So walk the AST of `run` and every `_run_*` platform
    function and assert they contain no write call at all. Nested helpers
    defined inside them are covered too, since ast.walk descends.
    """
    path = SRC / "pipeline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    writers = {"save", "mkdir", "write_text", "write_bytes", "to_excel", "to_csv",
               "ExcelWriter", "unlink", "rmtree", "open"}

    checked = []
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not (node.name == "run" or node.name.startswith("_run_")):
            continue
        checked.append(node.name)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _call_name(inner) in writers:
                offenders.append(f"{node.name}:{inner.lineno} calls {_call_name(inner)}(...)")

    assert "run" in checked, "pipeline.run not found — has the seam been renamed?"
    assert len(checked) >= 4, f"expected run + three platform runners, found {checked}"
    assert not offenders, (
        "pipeline.run() must not write anything — move it to write_artifacts(): "
        + "; ".join(offenders))


def test_src_never_imports_tools_or_tests():
    """`src/` is the deployable unit; the container image ships it without
    `tools/` or `tests/`. An import in this direction would work on a laptop
    and fail in production."""
    bad = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods = [node.module]
            for m in mods:
                if m.split(".")[0] in {"tools", "tests"}:
                    bad.append(f"{path.name}:{node.lineno} imports {m}")
    assert not bad, f"src/ must not import from tools/ or tests/: {bad}"


# ---------------------------------------------------------------------------
# A second structural lint: does each script still know where the repo is?
# ---------------------------------------------------------------------------

PARENTS_CALL = ast.parse("Path(__file__).resolve().parents[0]").body[0].value


def _parents_depths(tree: ast.AST) -> list[tuple[int, int]]:
    """[(line, N)] for every `Path(__file__).resolve().parents[N]`."""
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "parents"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, int)):
            continue
        if "__file__" in ast.dump(node.value):
            found.append((node.lineno, node.slice.value))
    return found


@pytest.mark.parametrize(
    "path", sorted(p for p in (*(ROOT / "tools").rglob("*.py"),
                               *(ROOT / "tests").rglob("*.py"),
                               # service/ joined in M4. Same bug class, and it
                               # resolves ROOT the same way to find config/.
                               *(ROOT / "service").rglob("*.py"))
                   if "__pycache__" not in p.parts),
    ids=lambda p: p.relative_to(ROOT).as_posix())
def test_repo_root_depth_matches_the_files_own_nesting(path: Path):
    """`parents[N]` must count the directories the file actually sits under.

    M2.5 found `tools/stage_exports.py` still using `parents[2]` after moving up
    from `tools/parity/` in M1, so every path it computed pointed one level
    ABOVE the repo — it could not find `input/original exports` at all, and the
    deterministic stager the goldens are meant to be reproducible through had
    not run since that move. Nothing failed loudly; it just silently addressed
    the wrong tree.

    The bug class is "a file moved and its hardcoded depth did not", which is
    invisible in review and costs an afternoon to diagnose. This is the cheapest
    possible guard: the depth is checkable against the file's own location.
    """
    depth = len(path.relative_to(ROOT).parts) - 1        # dirs between it and ROOT
    for line, n in _parents_depths(ast.parse(path.read_text(encoding="utf-8"))):
        assert n == depth, (
            f"{path.relative_to(ROOT).as_posix()}:{line} uses parents[{n}] but sits "
            f"{depth} directory level(s) below the repo root — it is addressing "
            f"{'above' if n > depth else 'inside'} the repo")
