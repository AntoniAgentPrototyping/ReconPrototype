from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .errors import ReconHardStop
from .runlog import RunLog


def load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ReconHardStop(f"Config file not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(config_dir: Path) -> dict:
    return load_yaml(config_dir / "settings.yaml")


def load_brand_rules(config_dir: Path) -> dict:
    return load_yaml(config_dir / "brand_rules.yaml")


def invoice_grouping(brand: str, brand_rules: dict) -> str:
    brands = brand_rules.get("brands") or {}
    rule = (brands.get(brand) or {}).get("invoice_grouping")
    return rule or (brand_rules.get("defaults") or {}).get("invoice_grouping", "combined")


def load_sku_master(config_dir: Path, log: RunLog) -> dict[str, str]:
    """SKU ID → name. Unknown SKUs are flagged downstream, never a hard stop."""
    path = config_dir / "sku_master.csv"
    if not path.exists():
        log.warn(f"sku_master.csv not found at {path} — all SKUs will be flagged as unknown")
        return {}
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    if "sku_id" not in df.columns:
        raise ReconHardStop(f"sku_master.csv must have a 'sku_id' column (found: {list(df.columns)})")
    df = df.dropna(subset=["sku_id"])
    return dict(zip(df["sku_id"].str.strip(), df.get("sku_name", pd.Series(dtype=str)).fillna("")))


def column_map(settings: dict, platform: str, kind: str) -> dict[str, str]:
    maps = settings.get("column_maps") or {}
    cmap = (maps.get(platform) or {}).get(kind)
    if not cmap:
        raise ReconHardStop(f"No column map configured for {platform}/{kind} in settings.yaml")
    return cmap
