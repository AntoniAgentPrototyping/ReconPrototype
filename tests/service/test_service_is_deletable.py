"""M4's structural gate: `service/` is a wrapper, and deleting it changes nothing
the pipeline executes.

D24 says the CLI stays first-class and any service wrapper must be deletable
without changing a line the pipeline runs. Through M3 that was a promise. This
file makes it a check, in the same spirit as tests/test_io_boundary.py — which
exists because the property it protects was already true and nothing enforced it,
so it would have eroded (one `from service import ...` in `src/calculate.py` for
"just the run id" is a two-line change that makes month-end depend on a database).

Three directions, each with a different failure it prevents:

    src/    ->  service/    month-end would need Postgres to be up
    tools/  ->  service/    the CLI would need Postgres to be up
    service/ -> tools/      the container image ships src/ + service/, not tools/
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
SERVICE = ROOT / "service"


def py_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def imported_roots(path: Path) -> list[tuple[int, str]]:
    """[(line, top-level module)] for every import in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.append((node.lineno, node.module.split(".")[0]))
    return out


def test_the_service_directory_exists():
    """Guard against this whole file passing vacuously if the package moves."""
    assert len(py_files(SERVICE)) >= 6, f"expected the service modules under {SERVICE}"


@pytest.mark.parametrize("path", py_files(SRC), ids=lambda p: p.name)
def test_src_never_imports_the_service(path: Path):
    offenders = [f"{path.name}:{line}" for line, mod in imported_roots(path)
                 if mod == "service"]
    assert not offenders, (
        f"src/ must not import service/: {offenders}. The pipeline is what runs at "
        f"month end and it cannot acquire a dependency on a database being up "
        f"(docs/06-DECISIONS.md#d24).")


@pytest.mark.parametrize("path", py_files(TOOLS), ids=lambda p: p.name)
def test_tools_never_imports_the_service(path: Path):
    offenders = [f"{path.name}:{line}" for line, mod in imported_roots(path)
                 if mod == "service"]
    assert not offenders, (
        f"tools/ must not import service/: {offenders}. tools/full_run.py is the "
        f"CLI-only path and the single-maintainer mitigation; it has to work with "
        f"the service deleted.")


@pytest.mark.parametrize("path", py_files(SERVICE), ids=lambda p: p.name)
def test_the_service_never_imports_tools_or_tests(path: Path):
    """The deployable unit is `src/` + `service/`. An import of `tools/` would
    work on a laptop and fail in the container — which is exactly why
    `build_context` moved into `src/pipeline.py` in M4 rather than being imported
    from `tools/devrun.py`."""
    offenders = [f"{path.name}:{line} imports {mod}" for line, mod in imported_roots(path)
                 if mod in {"tools", "tests"}]
    assert not offenders, f"service/ must not import tools/ or tests/: {offenders}"


def test_the_one_sanctioned_reach_into_tests_is_declared_and_degrades_honestly():
    """`service/verification.py` reads `tests/goldens/cellset.py`, and this lint cannot
    see it.

    It manipulates `sys.path` and then does `from cellset import ...`, so the AST scan
    above records the module as `cellset`, not `tests`. That is a hole in the lint, and
    naming it here is better than leaving it invisible — the next such import would
    also pass silently.

    **Why it is allowed at all.** `cellset.py` is the single definition of "did a cell
    move". The alternative is duplicating its digest algorithm inside `service/`, which
    would be a second definition of the comparison the whole golden gate rests on, and
    the two would drift.

    **Why it is safe.** The container ships no `tests/`, and the module checks for the
    file and raises a message naming the problem — which `verify()` turns into the
    `unavailable` state. So a container reports "no claim can be made" rather than
    crashing or, far worse, reporting `verified`.
    """
    text = (SERVICE / "verification.py").read_text(encoding="utf-8")
    assert "from cellset import" in text, (
        "if verification.py stopped reaching for cellset, delete this test — but if it "
        "grew its own digest algorithm instead, that is a second definition of a moved "
        "cell and it must not stand")
    assert 'if not (goldens_dir / "cellset.py").is_file():' in text, (
        "the reach into tests/ must stay guarded by an existence check, or a container "
        "gets a traceback instead of the `unavailable` verdict")

    # And no OTHER service module may do the same thing. Checked as an IMPORT, not as
    # a substring: `artifacts.py` names `tests/goldens/cellset.py` in a docstring to
    # say that comparing workbooks is its job, which is a citation and not a dependency.
    for path in py_files(SERVICE):
        if path.name == "verification.py":
            continue
        offenders = [line for line, mod in imported_roots(path) if mod == "cellset"]
        assert not offenders, (
            f"{path.name}:{offenders} imports cellset; verification.py is the one "
            f"sanctioned place, precisely so there is one of them")


def test_the_pipeline_runs_with_the_service_unimportable(monkeypatch, tmp_path):
    """The strongest form of "deletable": break the import and run the pipeline.

    A lint over import statements catches the static case. This catches the one it
    cannot — a lazy `import service` inside a function body, which is how the
    dependency would actually arrive.
    """
    pytest.importorskip("pandas")
    import builtins

    real_import = builtins.__import__

    def deny(name, *args, **kwargs):
        if name == "service" or name.startswith("service."):
            raise ModuleNotFoundError(f"service/ is deleted in this test: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", deny)

    from src import pipeline
    ctx = pipeline.RunContext(
        platform="lazada", period="2026-05_l1",
        input_root=tmp_path / "input", output_root=tmp_path / "out",
        config_dir=tmp_path / "config", settings={}, log=_QuietLog())
    result = pipeline.run(ctx)

    # No input, so it hard-stops — on the missing ledger, NOT on the missing
    # service package.
    assert result.status is pipeline.RunStatus.HARD_STOP
    assert not isinstance(result.error, ModuleNotFoundError), result.error


class _QuietLog:
    """Local rather than the shared RecordingLog: this test denies imports, and
    reaching for a fixture from tests/conftest.py mid-monkeypatch is a needless
    import to have in flight."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.warnings: list[str] = []

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)

    def section(self, title: str) -> None:
        self.lines.append(title)

    def write(self, path) -> None:                          # pragma: no cover
        Path(path).write_text("\n".join(self.lines), encoding="utf-8")


def test_the_service_declares_its_own_dependencies():
    """fastapi/psycopg/uvicorn/argon2/boto3 belong to the OPTIONAL `service` extra,
    not to the pipeline's install. `pip install recon` on a machine that only needs
    to regenerate a golden must not pull a web framework, a password hasher or an
    object-storage client.

    Extend this tuple whenever a service-only dependency is added, or the test
    quietly stops covering the newest one — which is the failure mode that makes a
    green suite worse than no suite.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    core, _, rest = text.partition("[project.optional-dependencies]")
    for package in ("fastapi", "uvicorn", "psycopg", "argon2", "boto3"):
        assert package not in core, (
            f"{package} appears in the core dependency list; the CLI-only path "
            f"must not require it")
        assert package in rest, f"{package} is not declared in any optional extra"
