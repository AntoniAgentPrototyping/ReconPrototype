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


def invoice_grouping(brand: str, brand_rules: dict) -> str:
    brands = brand_rules.get("brands") or {}
    rule = (brands.get(brand) or {}).get("invoice_grouping")
    return rule or (brand_rules.get("defaults") or {}).get("invoice_grouping", "combined")


def column_map(settings: dict, platform: str, kind: str) -> dict[str, str]:
    maps = settings.get("column_maps") or {}
    cmap = (maps.get(platform) or {}).get(kind)
    if not cmap:
        raise ReconHardStop(f"No column map configured for {platform}/{kind} in settings.yaml")
    return cmap
