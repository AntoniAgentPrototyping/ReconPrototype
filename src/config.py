from __future__ import annotations

from pathlib import Path

import yaml

from .errors import ReconHardStop


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ReconHardStop(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def parse_settings(text: str) -> dict:
    """Parse settings from text rather than from a path.

    Added in M5 so a run can be given a *pinned* config — the exact bytes a
    previous run of the same window used — instead of whatever is on disk today
    (docs/08-KNOWN-DEFECTS.md 2.5). It deliberately goes through the same
    `yaml.safe_load` as `load_settings`: the service edits config with
    `ruamel.yaml` to preserve comments, and parsing a pinned config with ruamel's
    round-trip loader instead would hand the pipeline `CommentedMap` and
    `ScalarFloat` objects rather than the `dict` and `float` it has always been
    verified against. One parser for anything that reaches the money math.
    """
    return yaml.safe_load(text) or {}


def load_settings(config_dir: Path) -> dict:
    return load_yaml(config_dir / "settings.yaml")


def column_map(settings: dict, platform: str, kind: str) -> dict[str, str]:
    maps = settings.get("column_maps") or {}
    cmap = (maps.get(platform) or {}).get(kind)
    if not cmap:
        raise ReconHardStop(f"No column map configured for {platform}/{kind} in settings.yaml")
    return cmap


# ---------------------------------------------------------------------------
# Settings whose ABSENCE is not a neutral state (docs/14 A9)
# ---------------------------------------------------------------------------
#
# Each of these was read as `settings.get(key, <literal>)`, and for each the
# literal decided where money or PII went. Two of them were measured on
# 2026-08-21 to disagree with the configured value outright:
#
#   dedupe_rows                  code default True   vs configured false
#   cross_window_order_backfill  code default "off"  vs configured apply
#
# The second is the argument for refusing rather than defaulting. Its default was
# genuinely the safe direction when it was written — and stopped being safe the
# same day the mode was flipped to `apply` (2026-08-20), because from then on a
# missing key silently disabled 2.33B VND of measured July recovery. The default
# did not change; the world around it did, and nothing would have said so. A
# default is a claim about the configured value, and it goes stale on its own.
#
# `service/config_render.assert_complete` reads this same mapping, so a rendered
# contract missing one of these fails a test before any run starts, and `require`
# refuses it at read time for the disk/CLI path no renderer covers. One
# definition, two layers — `service/` may import `src/`, never the reverse.
REQUIRED_SETTINGS: dict[str, str] = {
    "dedupe_rows": (
        "it decides whether byte-identical order lines are dropped. They are "
        "legitimate — duplicated gift SKUs, where the team's quantity of 2 became "
        "1 under deduping — so an absent key understates revenue silently"),
    "drop_unmapped_columns": (
        "it decides whether columns the contract does not name are stripped at "
        "read time, which is where customer PII (Recipient, Phone #, Detail "
        "Address) leaves the frame. Absence is safe today and still unauditable: "
        "a config that does not state its PII posture cannot be reviewed"),
    "cross_window_order_backfill": (
        "it decides whether an order settled in this window may take its lines "
        "from an earlier window's order export (off | report | apply). Absence "
        "would mean `off`, which silently reverts defect 2.12's fix"),
}

# Of those, the ones that must be a real `bool`. `config_scalars.value` is jsonb
# and settings.yaml is free text, so nothing below this line stopped `false`
# arriving as the STRING "false" — truthy in Python, and a silent inversion of the
# flag. The service guards this at the row (M8/1.6); this is the same guard on the
# path that has no rows.
_BOOL_SETTINGS = ("dedupe_rows", "drop_unmapped_columns")


def require(settings: dict, key: str):
    """A setting whose absence must stop the run, not pick a side.

    Looking the consequence up in `REQUIRED_SETTINGS` rather than passing it in is
    deliberate: adding a call site without stating what the value decides raises
    `KeyError` here, which is the discipline this function exists to enforce.
    """
    consequence = REQUIRED_SETTINGS[key]
    if key not in settings or settings[key] is None:
        raise ReconHardStop(
            f"{key} is not configured, and there is deliberately no default for it: "
            f"{consequence}. Set it in the rules editor, or in "
            f"config/settings.yaml for a run started from the command line.")
    value = settings[key]
    if key in _BOOL_SETTINGS and not isinstance(value, bool):
        raise ReconHardStop(
            f"{key} is {value!r} ({type(value).__name__}), not true or false. "
            f"A quoted \"false\" is truthy in Python, which would invert the flag: "
            f"{consequence}.")
    return value


def vat_default(settings: dict) -> float:
    """`vat_factors.default` — the factor a SKU gets when the master says nothing.

    Read by two unrelated layers (`src/masters.py` resolving per-SKU factors,
    `src/finance_template.py` filling a zero pre-VAT line), which is why it lives
    here rather than beside `finance_template.vat_rates`. That accessor validates
    the rate list against the workbook's own control-row geometry and belongs with
    the template; this is a single number the whole pipeline shares.

    Hard-stops rather than falling back to 1.08 — the M8/1.7 rule, and the last
    fallback A14 left behind. `masters.resolve_vat_factors` reports 0% master
    coverage on every sampled window, so in practice this factor invoices
    *everything*: a code fallback would outlive the 8%->10% revert it is supposed
    to be one line of.
    """
    configured = (settings.get("vat_factors") or {}).get("default")
    if configured is None:
        raise ReconHardStop(
            "vat_factors.default is not configured. It is the VAT factor every SKU "
            "the master does not cover is invoiced at — which is every SKU traded "
            "in every window sampled so far — so a config without it cannot price "
            "a single line.")
    return float(configured)
