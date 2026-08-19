"""An OPT-IN Parquet cache for the test suite's Excel reads.

**The measurement that motivates it.** `pytest -m "not slow" --durations=25`, on
2026-08-18, over 730 tests taking 706s:

    269.23s  test_a_sanitized_renamed_window_produces_the_committed_golden[s1-shopee]
    200.88s  test_a_sanitized_renamed_window_produces_the_committed_golden[w1-tiktok]
     11.56s  test_golden_matches_committed_digest[s1/shopee]
      ... everything else is under 5s

Two tests are **67% of the suite**, and nearly all of it is decoding the same real
`.xlsx` exports over and over — 65,551 rows of Shopee income, 126,355 rows of TikTok
orders. Nothing about those bytes changes between runs.

**Why this is dangerous, and what makes it safe.** Those two tests are the *most*
valuable in the suite: they prove the upload sanitizer can rewrite a real export
and the pipeline still produces the committed golden. Caching a decode naively —
keyed on a path, or on a filename, or on mtime — would replace the thing under test
with a recording of its own output, which is the `oracle_rev` failure this project
deleted a gate over ([D26](../docs/06-DECISIONS.md#d26)).

Three rules keep it honest, and all three are load-bearing:

1. **OFF by default.** Only `RECON_TEST_IO_CACHE=1` turns it on, so CI, the golden
   gate and anyone verifying a claim get real reads. A cache that had to be
   remembered *off* would eventually be on for the run that mattered.
2. **Keyed on content, never on identity.** The key is
   `sha256(file bytes) + sheet + header_row + engine`. Edit an export by one byte,
   change a header row, switch calamine for openpyxl — different key, real read. A
   stale entry cannot be served, because there is no way to name one.
3. **It caches the DECODE, not the answer.** `read_excel_sheet` returns an
   all-string frame straight off the sheet; every rule the tests actually check —
   the column map, the NFC normalisation, the header row, the store-from-filename
   parse, dedupe, the money math — runs afterwards on the cached frame exactly as
   on a fresh one. What is skipped is only the XML decode.

**What it cannot help with, and why.** The sanitizer writes a new `.xlsx` per run
and openpyxl stamps a timestamp into `docProps/core.xml`, so the sanitized copy has
different bytes every time and never hits. That is not a limitation to work around
— it is rule 2 doing its job. The saving is therefore on the *source* reads only.

Parquet rather than pickle because the frames are all-string and Parquet round-trips
that exactly, and because a pickle cache would be a versioned Python object that a
pandas upgrade could silently reinterpret — under a `pandas<3` pin that exists
precisely because a pandas upgrade changes semantics.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

ENV_FLAG = "RECON_TEST_IO_CACHE"

# Outside the repo, next to the goldens, for the reason the goldens are there:
# it is derived from client data and must not land in a synced or committed tree.
CACHE_ROOT = Path(
    os.environ.get("RECON_TEST_IO_CACHE_DIR")
    or (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "recon-io-cache"))


def enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1"


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(path: Path, sheet: Any, header_row: int, engine: str | None) -> str:
    """Everything that decides what the decode returns, and nothing else.

    The file's CONTENT, not its name: `tests/service/test_uploads.py` copies and
    renames real exports into a tmp_path, so two different names routinely hold the
    same bytes and must share an entry — while one edited byte must not.
    """
    material = f"{_digest(path)}|{sheet!r}|{int(header_row)}|{engine or 'default'}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def install(monkeypatch=None) -> bool:
    """Wrap `ingest.read_excel_sheet` with the cache. Returns whether it engaged.

    Wraps the ONE reader both the pipeline and the upload sanitizer go through
    (`src/ingest.py` is public for exactly that reason), so there is no second
    decode path that quietly stays uncached and makes a timing comparison lie.
    """
    if not enabled():
        return False

    import pandas as pd

    from src import ingest

    original = ingest.read_excel_sheet
    if getattr(original, "_io_cached", False):                  # pragma: no cover
        return True
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    def cached(path, sheet, header_row: int, engine: str | None = None):
        path = Path(path)
        try:
            entry = CACHE_ROOT / f"{cache_key(path, sheet, header_row, engine)}.parquet"
        except OSError:                                         # pragma: no cover
            return original(path, sheet, header_row, engine)
        if entry.exists():
            try:
                return pd.read_parquet(entry)
            except Exception:                                   # noqa: BLE001
                # A corrupt or unreadable entry must never fail a test: drop it and
                # take the real read. The cache is an optimisation, never an oracle.
                entry.unlink(missing_ok=True)
        frame = original(path, sheet, header_row, engine)
        try:
            # Column names off a real export are not always unique or string-typed;
            # Parquet requires both. A frame it cannot represent is simply not
            # cached, rather than being coerced into something the reader would then
            # hand the pipeline in place of what the file actually said.
            if len(set(map(str, frame.columns))) == len(frame.columns):
                frame.to_parquet(entry, index=False)
        except Exception:                                       # noqa: BLE001
            entry.unlink(missing_ok=True)
        return frame

    cached._io_cached = True                                    # type: ignore[attr-defined]
    if monkeypatch is not None:
        monkeypatch.setattr(ingest, "read_excel_sheet", cached)
    else:
        ingest.read_excel_sheet = cached                        # type: ignore[assignment]
    return True


def clear() -> int:
    """Delete every entry. Returns how many. For when a real read is wanted once."""
    if not CACHE_ROOT.is_dir():
        return 0
    entries = list(CACHE_ROOT.glob("*.parquet"))
    for entry in entries:
        entry.unlink(missing_ok=True)
    return len(entries)
