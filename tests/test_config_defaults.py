"""A9 — a code default may not disagree with the configured value.

The register named one instance: `settings.get("dedupe_rows", True)` against a
contract that says `false`, so a settings dict that forgot the key dropped
legitimate duplicate order lines and understated revenue. Fixing that one line
would have left the *class* open, and the class is what bit twice — planning the
fix on 2026-08-21 found a second live inversion nobody had named:
`cross_window_order_backfill` defaulted to `"off"` against a contract that says
`apply`, silently reverting defect 2.12's fix and 2.33B VND of measured July
recovery.

The second one is the argument for this test existing. That default was correct
and safe on the day it was written; the mode was flipped to `apply` later the
*same day* and the default was not revisited. **A default is a claim about the
configured value, and it goes stale on its own** — so something has to compare
the two on every run of the suite.

AST-based for the reason `test_io_boundary.py` is: a grep matches the key names
inside this module's own allowlist and inside the comments explaining them.

What this walk deliberately does NOT see, so the next session does not read its
silence as coverage:

- `(settings.get("vat_factors") or {}).get("default", 1.08)` — nested reads are
  matched only for a literal sub-key, and after A9 that call site is
  `config.vat_default`, which hard-stops. A new nested default IS caught.
- `(settings.get("dayfirst") or {}).get(platform, False)` — a per-platform read
  whose default cannot be compared against a single configured scalar. It is a
  real inversion (`dayfirst.tiktok: true`), and a dead one: `date_formats` takes
  precedence for both platforms that read it (D54). Recorded here rather than
  worked around.
- `tol.get("pv_sum_vnd", 12000)` and friends — the receiver is not `settings`.
  Those seven literals are the A12 tolerances, now configured rows carrying the
  literal each already fell back to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# Keys read with a literal default and no configured value, where the literal IS
# the contract. Each needs a reason, because "nothing configures it" is exactly
# how `dedupe_rows` would look if the contract stopped mentioning it.
CODE_LITERAL_IS_THE_CONTRACT = {
    "date_coercion": (
        "not in the contract at all. The default `warn` is the deliberate posture "
        "(D53) — a blank settlement date is legitimate, unlike a blank amount — and "
        "`hard_stop` is available for a caller that wants it. Modelling it as a "
        "config row is a change to what an operator can do, not a defect"),
}


def _settings() -> dict:
    return yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))


def _literal(node: ast.AST):
    """The value of a literal default, or `_NOT_LITERAL` for anything else."""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return _NOT_LITERAL


class _NotLiteral:
    def __repr__(self) -> str:                                  # pragma: no cover
        return "<not a literal>"


_NOT_LITERAL = _NotLiteral()


def _defaulted_reads(path: Path) -> list[tuple[int, tuple[str, ...], object]]:
    """Every `settings.get(<str>, <literal>)` in one module, as (line, path, default).

    Handles the two shapes that exist in `src/`: a top-level read on `settings`
    itself, and the `(settings.get("outer") or {}).get("inner", default)` idiom.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, tuple[str, ...], object]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            continue
        default = _literal(node.args[1])
        if default is _NOT_LITERAL:
            continue
        key = node.args[0].value
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "settings":
            found.append((node.lineno, (key,), default))
            continue
        # `(settings.get("outer") or {}).get("inner", default)`
        outer = _outer_key(receiver)
        if outer is not None:
            found.append((node.lineno, (outer, key), default))
    return found


def _outer_key(node: ast.AST) -> str | None:
    if not (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or) and node.values):
        return None
    inner = node.values[0]
    if not (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
            and inner.func.attr == "get"
            and isinstance(inner.func.value, ast.Name) and inner.func.value.id == "settings"
            and len(inner.args) == 1 and isinstance(inner.args[0], ast.Constant)
            and isinstance(inner.args[0].value, str)):
        return None
    return inner.args[0].value


def _resolve(settings: dict, keys: tuple[str, ...]):
    node = settings
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return _NOT_LITERAL
        node = node[key]
    return node


ALL_READS = sorted(
    (path.name, line, keys, default)
    for path in sorted(SRC.glob("*.py"))
    for line, keys, default in _defaulted_reads(path)
)


def test_the_walk_finds_something():
    """A parser that silently matches nothing would make every assertion below
    vacuous — the failure mode `strict=True` exists to catch elsewhere."""
    assert len(ALL_READS) >= 4, ALL_READS


def test_the_walk_sees_both_shapes_that_bit(tmp_path):
    """The negative control, and it is the load-bearing test in this file.

    Both inversions are written out here verbatim — the `dedupe_rows` shape the
    register named and the nested shape the vat factor used — so the parser is
    proven against them rather than trusted. Without this, deleting a branch of
    `_defaulted_reads` would make the whole module pass by seeing nothing.
    """
    module = tmp_path / "probe.py"
    module.write_text(
        "def f(settings, platform, tol):\n"
        "    a = settings.get('dedupe_rows', True)\n"
        "    b = (settings.get('vat_factors') or {}).get('default', 1.08)\n"
        "    c = settings.get('file_formats', ['.xlsx', '.csv'])\n"
        "    d = (settings.get('dayfirst') or {}).get(platform, False)\n"
        "    e = tol.get('pv_sum_vnd', 12000)\n"
        "    g = settings.get('store_aliases') or {}\n"
        "    return a, b, c, d, e, g\n",
        encoding="utf-8")

    found = {keys: default for _, keys, default in _defaulted_reads(module)}
    assert found == {
        ("dedupe_rows",): True,
        ("vat_factors", "default"): 1.08,
        ("file_formats",): [".xlsx", ".csv"],
    }, found


@pytest.mark.parametrize(("module", "line", "keys", "default"), ALL_READS,
                         ids=[f"{m}:{ln}:{'.'.join(k)}" for m, ln, k, _ in ALL_READS])
def test_a_code_default_agrees_with_the_configured_value(module, line, keys, default):
    dotted = ".".join(keys)
    configured = _resolve(_settings(), keys)

    if configured is _NOT_LITERAL:
        assert dotted in CODE_LITERAL_IS_THE_CONTRACT, (
            f"{module}:{line} reads {dotted!r} with a default of {default!r} and "
            f"config/settings.yaml does not configure it. Either add it to the "
            f"contract, or add it to CODE_LITERAL_IS_THE_CONTRACT in this file with "
            f"the reason the literal is authoritative. 'Nobody configures it' is "
            f"how dedupe_rows would have looked too.")
        return

    assert configured == default, (
        f"{module}:{line} defaults {dotted!r} to {default!r} while "
        f"config/settings.yaml says {configured!r}. A settings dict missing this key "
        f"therefore behaves the OPPOSITE of the contract, and nothing says so at "
        f"runtime. Either make it required (src/config.py REQUIRED_SETTINGS + "
        f"`require`) or make the default match. This is docs/14 A9, which has "
        f"already been found twice.")


def test_every_required_setting_is_actually_in_the_contract():
    """The other direction: a key `require` refuses to default must be present, or
    every run hard-stops. Cheap, and it fails the moment an export drops one."""
    from src.config import REQUIRED_SETTINGS

    settings = _settings()
    missing = [k for k in REQUIRED_SETTINGS if k not in settings]
    assert not missing, (
        f"config/settings.yaml is missing {missing}, which src/config.require "
        f"hard-stops on — every CLI run would stop before reading a file")


def test_a_required_setting_hard_stops_rather_than_picking_a_side():
    from src.config import REQUIRED_SETTINGS, require
    from src.errors import ReconHardStop

    for key in REQUIRED_SETTINGS:
        with pytest.raises(ReconHardStop, match=key):
            require({}, key)
        with pytest.raises(ReconHardStop, match=key):
            require({key: None}, key)


def test_a_quoted_boolean_is_refused_not_believed():
    """`config_scalars.value` is jsonb and settings.yaml is text, so `false` can
    arrive as the STRING "false" — truthy in Python, and a silent inversion. The
    service guards this at the row (M8/1.6); this is the same guard on the path
    that has no rows."""
    from src.config import require
    from src.errors import ReconHardStop

    with pytest.raises(ReconHardStop, match="not true or false"):
        require({"dedupe_rows": "false"}, "dedupe_rows")
    assert require({"dedupe_rows": False}, "dedupe_rows") is False


def test_the_vat_default_has_no_code_fallback():
    """A14 moved the rate list and the bucket lists into the contract and left one
    bare 1.08 behind in three places. The 8%->10% revert is meant to be one config
    line; a fallback in code would outlive it."""
    from src.config import vat_default
    from src.errors import ReconHardStop

    with pytest.raises(ReconHardStop, match="vat_factors.default"):
        vat_default({})
    with pytest.raises(ReconHardStop, match="vat_factors.default"):
        vat_default({"vat_factors": {"rates": [1.08]}})
    assert vat_default({"vat_factors": {"default": 1.1}}) == 1.1
