"""Lazada's reading rules, read the way the pipeline reads them.

Several tests build a synthetic Lazada export and then assert that the upload
sanitizer keeps exactly the columns the reader will look for. Until M8/1.7 they did
that by importing `lazada.WEEKLY_MAP` and `lazada.SHEETS` — module constants that
were Lazada's half of the domain contract living in Python
(`docs/14-PRODUCTION-READINESS.md` D4).

Those constants are gone. A test that hardcoded the same spellings instead would be
a *third* copy, and the first one to drift silently; reaching through the contract
is what keeps "the sanitizer writes what the reader expects" a real claim.

Not named `conftest.py` and prefixed so pytest does not collect it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def domain() -> dict:
    """The real contract, parsed exactly as a run parses it."""
    from src import config as src_config
    return src_config.load_settings(ROOT / "config")


def lazada_headers(variant: str = "weekly", settings: dict | None = None) -> list[str]:
    """The raw header spellings a Lazada export of this variant carries."""
    from src import lazada
    return list(lazada.column_map(settings or domain(), variant))


def lazada_sheet(variant: str = "weekly", settings: dict | None = None) -> str:
    from src import lazada
    return lazada.sheet_name(settings or domain(), variant)


def lazada_store_pattern(settings: dict | None = None) -> str:
    from src import lazada
    return lazada.store_pattern(settings or domain())
