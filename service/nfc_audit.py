"""How much non-NFC text is actually in the identity values we hash?

Read-only, and it reports **counts only, never values** — the identity columns
include store names, and this may be pointed at a real database.

`service/exceptions.py::_norm` did not NFC-normalise, so an identity value changing
Unicode form silently orphans every stored fingerprint and detaches its history.
Before changing that, measure: the M6 plan's "0 impact" claim was measured over
**filenames** (166/166 NFC), which says nothing about `fee_name` — a Vietnamese value
from Lazada exports and a genuine NFD candidate.

    python -m service.nfc_audit                       # config + masters only
    python -m service.nfc_audit --database-url ...    # also run_exceptions rows

**In `service/`, not `tools/`, and that placement is the invariant talking.** It
audits `service/exceptions.py` and a service database, so it depends on `service/`
— and `tools/` must survive `service/` being deleted
(`tests/service/test_service_is_deletable.py`). A first draft of this lived in
`tools/` and the lint caught it, which is what the lint is for. Same reasoning as
`service/admin.py`: the service's own CLIs live inside the service.

Exit code is 0 always: this reports, it does not gate.
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _non_nfc(value: object) -> bool:
    text = str(value)
    return bool(text) and not unicodedata.is_normalized("NFC", text)


def audit_settings(config_dir: Path) -> dict[str, int]:
    """Store names in the roster and the alias map."""
    from src import config as src_config

    settings = src_config.load_settings(config_dir)
    counts = {"expected_stores": 0, "stores_optional": 0,
              "store_aliases_keys": 0, "store_aliases_values": 0, "total": 0}
    for key in ("expected_stores", "stores_optional"):
        for stores in (settings.get(key) or {}).values():
            counts[key] += sum(1 for s in (stores or []) if _non_nfc(s))
    for mapping in (settings.get("store_aliases") or {}).values():
        for raw, canonical in (mapping or {}).items():
            counts["store_aliases_keys"] += int(_non_nfc(raw))
            counts["store_aliases_values"] += int(_non_nfc(canonical))
    counts["total"] = sum(v for k, v in counts.items() if k != "total")
    return counts


def audit_fee_names(config_dir: Path) -> dict[str, int]:
    """`fee_name` from the committed CSV snapshot AND the live `.xlsb`.

    The candidate the plan's filename measurement said nothing about.
    """
    out = {"csv_rows": 0, "csv_non_nfc": 0, "xlsb_rows": 0, "xlsb_non_nfc": 0}

    path = config_dir / "lazada_fee_types.csv"
    if path.is_file():
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                out["csv_rows"] += 1
                out["csv_non_nfc"] += int(_non_nfc(row.get("fee_name", "")))

    try:
        from src import lazada
        from src.runlog import RunLog
        live = lazada.load_fee_type_map(config_dir, RunLog())
        out["xlsb_rows"] = len(live)
        out["xlsb_non_nfc"] = sum(1 for name in live if _non_nfc(name))
    except Exception as exc:                                    # noqa: BLE001
        out["xlsb_error"] = type(exc).__name__                  # type: ignore[assignment]
    return out


def audit_stored_exceptions(database_url: str) -> dict[str, int]:
    """`run_exceptions` rows whose stored identity values are non-NFC.

    Counts and fingerprint prefixes only. Never a value.
    """
    import psycopg
    from psycopg.rows import dict_row

    from . import exceptions as exc_module

    out = {"rows": 0, "non_nfc_rows": 0, "fingerprints_affected": 0}
    affected: set[str] = set()
    with psycopg.connect(database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("select fingerprint, sheet, payload from run_exceptions")
        for row in cur:
            out["rows"] += 1
            columns = exc_module.IDENTITY_COLUMNS.get(row["sheet"]) or ()
            payload = row["payload"] or {}
            values = [payload.get(c) for c in columns] or list(payload.values())
            if any(v is not None and _non_nfc(v) for v in values):
                out["non_nfc_rows"] += 1
                affected.add(row["fingerprint"])
    out["fingerprints_affected"] = len(affected)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config-dir", default=str(ROOT / "config"))
    ap.add_argument("--database-url", default=None,
                    help="also audit run_exceptions in this database (read-only)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]

    config_dir = Path(args.config_dir)
    print("NFC audit — counts only, never values\n")

    stores = audit_settings(config_dir)
    print("settings.yaml store names")
    for key, count in stores.items():
        print(f"  {key:24} {count}")

    fees = audit_fee_names(config_dir)
    print("\nLazada fee names")
    for key, count in fees.items():
        print(f"  {key:24} {count}")

    total = stores["total"] + fees.get("csv_non_nfc", 0) + fees.get("xlsb_non_nfc", 0)
    if args.database_url:
        stored = audit_stored_exceptions(args.database_url)
        print("\nrun_exceptions")
        for key, count in stored.items():
            print(f"  {key:24} {count}")
        total += stored["non_nfc_rows"]

    print(f"\nnon-NFC identity values found: {total}")
    if total == 0:
        print("  -> normalising `_norm` is a no-op on today's data. Do it anyway: it "
              "costs nothing and the next Vietnamese value that arrives decomposed "
              "would otherwise orphan its own history silently.")
    else:
        print("  -> 006_exception_nfc.sql must recompute the affected fingerprints, "
              "or their history detaches at the moment _norm changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
