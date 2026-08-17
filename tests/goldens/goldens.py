"""Locating goldens and the committed manifest.

A normal module rather than conftest, so it can be imported by name without
colliding with tests/conftest.py.

Manifests were once keyed by `oracle_rev` (a hash of src/ + config/ + deps), so
a golden was bound to the exact tree that produced it. That was dropped in M1
(docs/06-DECISIONS.md#d26): because config changes most months, every edit
orphaned every golden and the gate silently degraded to a skip. One flat
manifest always compares; moving it is an explicit act.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# tools/ holds fingerprint.py (digest_json), which the integrity test needs in
# order to recompute committed digests.
for extra in (ROOT, ROOT / "tools"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

MANIFEST = ROOT / "tests" / "goldens" / "manifest.json"


def default_goldens_dir() -> Path:
    """Goldens live OUTSIDE the repo — they derive from client data and are
    never committed. Only their digests are."""
    if env := os.environ.get("RECON_GOLDENS"):
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "recon-goldens" if local else Path.home() / ".recon-goldens"


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def committed_windows() -> dict[str, dict]:
    """{"<period>/<platform>": entry} — every window with a committed digest."""
    return dict((load_manifest().get("windows") or {}))


def discover_windows() -> dict[str, tuple[Path, dict]]:
    """Committed windows whose golden is actually present on this machine.

    A committed entry with no local golden is not an error — goldens are not
    distributed. It skips, and `test_every_committed_window_has_a_golden`
    reports the coverage so the skip cannot pass for a pass.
    """
    found: dict[str, tuple[Path, dict]] = {}
    root = default_goldens_dir()
    for key, entry in committed_windows().items():
        golden_dir = root / key
        if (golden_dir / "cellset.jsonl").is_file():
            found[key] = (golden_dir, entry)
    return found
