"""Load `config/` into the normalized config tables (migration 007).

    python -m service.config_import --database-url "$RECON_DATABASE_URL"

**Idempotent by truncate-and-load, inside one transaction.** An upsert would leave a
key that was REMOVED from the file still sitting in the table, which is the failure
this whole migration exists to stop — config that exists and is read by nothing. The
tables are small (the largest is 668 VAT SKUs) so there is no argument for being
clever, and `config_versions` already holds the immutable history that a truncate
would otherwise destroy.

**It refuses on an unrecognised key rather than skipping it.** A settings key this
module does not model would silently stop reaching the pipeline the moment rendering
becomes the source of truth — the same class of bug as a column map that quietly
drops a header. `KNOWN_KEYS` is the whitelist and the refusal names the key.

**Evidence comes from `config_store.evidence_for`, not from a second parser.** That
function reads the comment block out of the raw file text rather than out of
ruamel's `.ca`, because `.ca` attaches a block to the key *preceding* the one it
documents — measured, and the reason the config editor renders correct captions
today ([D42](../docs/06-DECISIONS.md#d42)). Re-implementing that here would produce a
second, worse answer to "what justifies this value".
"""

from __future__ import annotations

import csv
import json
import unicodedata
from pathlib import Path
from typing import Any

from . import config_store

# Every top-level key this module understands. A key in the file and not here is a
# hard error: see the module docstring.
KNOWN_KEYS = frozenset({
    "vat_factors", "invoice_buckets", "fee_buckets",
    "masters_file", "reader_engine", "tolerances",
    "window_settlement_bounds", "number_style", "numeric_coercion",
    "drop_unmapped_columns", "dedupe_rows", "dayfirst", "date_formats",
    "skip_rows_after_header",
    "file_formats", "sheet_names", "sheet_patterns", "header_rows",
    "store_from_filename", "stores_optional", "expected_stores", "store_to_brand",
    "store_aliases", "column_maps",
    "cross_window_order_backfill",
})

# Scalars, in render order. (dotted key, reader module, label, help).
SCALARS: tuple[tuple[str, str, str, str], ...] = (
    ("vat_factors.default", "src/masters.py", "Default VAT factor",
     "1.08 today. Enter 1.10 when the concession ends. Per-SKU exceptions come "
     "from the master file, not from here."),
    ("vat_factors.rates", "src/finance_template.py", "VAT rates the workbooks split by",
     "The closed set of rates the finance workbooks lay out per-rate control rows "
     "and pivot tabs for. The Shopee and Lazada templates hard-wire their row "
     "geometry to exactly these three, so a different list stops those runs with "
     "a sentence rather than dropping a rate's rows out of the layout."),
    ("masters_file", "src/masters.py", "Team-owned master file",
     "Read live at runtime; the snapshot rows are the fallback and drift is "
     "reported every run."),
    ("number_style", "src/ingest.py", "Amount format",
     "How a money cell is spelled in the export."),
    ("numeric_coercion", "src/ingest.py", "Unparseable amount",
     "A settlement export never legitimately contains an unparseable amount."),
    ("drop_unmapped_columns", "src/ingest.py", "Strip columns the contract does not name",
     "Currently on. Turning it off would let every unmapped column — including "
     "customer names and addresses — into the pipeline and onto disk."),
    ("dedupe_rows", "src/ingest.py", "Drop byte-identical rows",
     "Off. Byte-identical order lines are legitimate — duplicated gift SKUs — and "
     "the team's own Power Query never dedupes."),
    ("file_formats", "src/ingest.py", "Accepted file extensions",
     "Uploads are always stored as .xlsx regardless; this governs a window staged "
     "directly on disk."),
    ("cross_window_order_backfill", "src/backfill.py",
     "Orders whose lines are in an earlier period's file",
     "'off' ignores them. 'report' measures them and names the file that has them, "
     "changing no number. 'apply' uses those lines when building the invoice, which "
     "DOES change totals. July was short 4,527,401,608 VND because nothing looked."),
)

# The PII control, and the only locked row.
LOCKED_REASON = (
    "This is the PII control in two places: it is what stops customer names, phone "
    "numbers and delivery addresses reaching a DataFrame, and the upload sanitizer "
    "strips on the same column map at the door. Its diff reads as an ordinary "
    "boolean flip rather than as a privacy incident, so it changes in a reviewed "
    "commit and not with two clicks.")

# Rows a change to which cannot move a workbook cell.
NON_INVALIDATING = frozenset({"masters_file", "file_formats"})

# Tolerances src/tieout.py reads and settings.yaml never configured, with the code
# literal each currently falls back to. Importing them at their own default values
# is behaviour-neutral BY CONSTRUCTION — the point is that the contract stops
# lying about which numbers are in force.
#
# Precise about who reads what: run_checks (tiktok + shopee) reads conservation_vnd,
# grand_vnd and per_store_vnd; run_checks_lazada is a separate function that reads
# conservation_vnd and price_ka_rounding_vnd only. Adding a row for a tolerance its
# platform never reads would recreate exactly the inert-config problem being fixed.
UNCONFIGURED_TOLERANCES: tuple[tuple[str, str, float], ...] = (
    ("tiktok", "conservation_vnd", 1),
    ("tiktok", "grand_vnd", 1),
    ("tiktok", "per_store_vnd", 1),
    ("shopee", "grand_vnd", 1),
    ("shopee", "per_store_vnd", 1),
    ("lazada", "conservation_vnd", 1),
    ("lazada", "price_ka_rounding_vnd", 1000),
)

_UNCONFIGURED_NOTE = (
    "Read by src/tieout.py and never configured before 2026-08-18 — this row records "
    "the code literal that was already in force, so the value is unchanged and the "
    "contract stops being silent about it.")

CONFIG_TABLES = (
    "config_scalars", "config_platforms", "config_reading", "config_column_maps",
    "config_stores", "config_store_aliases", "config_store_brands",
    "config_tolerances", "config_settlement_bounds", "config_fee_types",
    "config_vat_sku", "config_invoice_buckets", "config_fee_buckets",
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", str(text))


def _evidence(content: str, path: list[str]) -> str:
    """The comment block justifying one key, as one string."""
    return "\n".join(config_store.evidence_for(content, path))


def _dig(settings: dict, dotted: str) -> Any:
    node: Any = settings
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def check_known_keys(settings: dict) -> None:
    unknown = sorted(set(settings) - KNOWN_KEYS)
    if unknown:
        raise ValueError(
            f"settings.yaml holds {len(unknown)} key(s) this importer does not "
            f"model: {unknown}. Add them to service/config_import.KNOWN_KEYS and to "
            f"the renderer, or delete them — an unmodelled key stops reaching the "
            f"pipeline once rendering is the source of truth.")


# ---------------------------------------------------------------------------
# The import itself
# ---------------------------------------------------------------------------

def import_settings(conn, config_dir: Path, *, changed_by: str,
                    source: str = "import") -> dict[str, int]:
    """Replace the config tables from `config_dir`. Returns rows written per table.

    The caller commits. One transaction, so a refusal half way leaves the previous
    contents intact rather than a partial contract.
    """
    import yaml

    config_dir = Path(config_dir)
    content = config_store.read_text(config_dir)
    settings = yaml.safe_load(content) or {}
    check_known_keys(settings)

    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in CONFIG_TABLES:
            cur.execute(f"delete from {table}")

        counts["config_scalars"] = _load_scalars(cur, settings, content, changed_by, source)
        counts["config_platforms"] = _load_platforms(cur, settings, content, changed_by, source)
        counts["config_reading"] = _load_reading(cur, settings, content, changed_by, source)
        counts["config_column_maps"] = _load_column_maps(cur, settings, content, changed_by, source)
        counts["config_stores"] = _load_stores(cur, settings, content, changed_by, source)
        counts["config_store_aliases"] = _load_aliases(cur, settings, content, changed_by, source)
        counts["config_store_brands"] = _load_brands(cur, settings, config_dir, changed_by, source)
        counts["config_tolerances"] = _load_tolerances(cur, settings, content, changed_by, source)
        counts["config_settlement_bounds"] = _load_bounds(cur, settings, content, changed_by, source)
        counts["config_fee_types"] = _load_fee_types(cur, config_dir, changed_by, source)
        counts["config_vat_sku"] = _load_vat_sku(cur, config_dir, changed_by, source)
        counts["config_invoice_buckets"] = _load_invoice_buckets(
            cur, settings, content, changed_by, source)
        counts["config_fee_buckets"] = _load_fee_buckets(
            cur, settings, content, changed_by, source)
    return counts


def _moves_a_cell(table: str) -> bool:
    """Whether a change to a row of this table can move a workbook cell.

    Read from `config_rows.TABLES`, which is where the decision is argued, so a
    freshly seeded deployment and a database migrated in place by 008 agree. Two
    tables are argued down to false — the roster decides whether a run STOPS rather
    than what a cell holds, and a tolerance is read by `src/tieout.py`, which
    reports variances and writes no cell. Everything else defaults to true, because
    a change no claim can be made about is exactly where "harmless" turns a gate
    into a skip.
    """
    from . import config_rows
    return config_rows.TABLES[table].invalidates_goldens


def _load_scalars(cur, settings, content, who, source) -> int:
    n = 0
    for order, (key, reader, label, help_text) in enumerate(SCALARS):
        value = _dig(settings, key)
        if value is None:
            continue
        locked = key == "drop_unmapped_columns"
        cur.execute(
            """insert into config_scalars
                 (key, value, reader, label, help, locked, locked_reason,
                  invalidates_goldens, evidence, changed_by, source, sort_order)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (key, json.dumps(value), reader, label, help_text,
             locked, LOCKED_REASON if locked else "",
             key not in NON_INVALIDATING,
             _evidence(content, key.split(".")), who, source, order))
        n += 1
    return n


def _load_platforms(cur, settings, content, who, source) -> int:
    dayfirst = settings.get("dayfirst") or {}
    patterns = settings.get("store_from_filename") or {}
    n = 0
    for order, platform in enumerate(sorted(set(dayfirst) | set(patterns))):
        evidence = _evidence(content, ["store_from_filename", platform]) \
            or _evidence(content, ["dayfirst", platform])
        cur.execute(
            """insert into config_platforms
                 (platform, dayfirst, store_from_filename, evidence, changed_by,
                  source, sort_order)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            # Absent from `dayfirst` means the reader does not consume one, which
            # is a different statement from "false" and is stored as NULL so the
            # renderer does not invent a key nothing reads (migration 009).
            (platform, bool(dayfirst[platform]) if platform in dayfirst else None,
             patterns.get(platform), evidence, who, source, order))
        n += 1
    return n


def _load_reading(cur, settings, content, who, source) -> int:
    """Seven sibling maps keyed the same way, collapsed into one row per platform/kind."""
    names = settings.get("sheet_names") or {}
    patterns = settings.get("sheet_patterns") or {}
    headers = settings.get("header_rows") or {}
    skips = settings.get("skip_rows_after_header") or {}
    engines = settings.get("reader_engine") or {}
    formats = settings.get("date_formats") or {}

    pairs: set[tuple[str, str]] = set()
    for top in (names, patterns, headers, skips, engines, formats):
        for platform, kinds in (top or {}).items():
            for kind in (kinds or {}):
                pairs.add((platform, kind))

    n = 0
    for order, (platform, kind) in enumerate(sorted(pairs)):
        def at(top: dict, default=None):
            return ((top or {}).get(platform) or {}).get(kind, default)
        evidence = (_evidence(content, ["sheet_names", platform, kind])
                    or _evidence(content, ["header_rows", platform, kind])
                    or _evidence(content, ["reader_engine", platform, kind])
                    or _evidence(content, ["date_formats", platform, kind]))
        cur.execute(
            """insert into config_reading
                 (platform, kind, sheet_name, sheet_pattern, header_row,
                  skip_rows_after_header, reader_engine, date_format, evidence,
                  changed_by, source, sort_order)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (platform, kind, at(names), at(patterns), int(at(headers, 1)),
             int(at(skips, 0)), at(engines), at(formats), evidence, who, source, order))
        n += 1
    return n


def _load_column_maps(cur, settings, content, who, source) -> int:
    n = 0
    order = 0
    for platform, kinds in sorted((settings.get("column_maps") or {}).items()):
        for kind, mapping in sorted((kinds or {}).items()):
            for raw, canonical in (mapping or {}).items():
                cur.execute(
                    """insert into config_column_maps
                         (platform, kind, raw_header, canonical, evidence,
                          changed_by, source, sort_order)
                       values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (platform, kind, _nfc(raw), str(canonical),
                     _evidence(content, ["column_maps", platform, kind, str(raw)]),
                     who, source, order))
                order += 1
                n += 1
    return n


def _load_stores(cur, settings, content, who, source) -> int:
    """expected_stores and stores_optional, merged onto one row with a flag."""
    expected = settings.get("expected_stores") or {}
    optional = settings.get("stores_optional") or {}
    n = 0
    order = 0
    for platform in sorted(set(expected) | set(optional)):
        opt = {str(s) for s in (optional.get(platform) or [])}
        listed = list(expected.get(platform) or [])
        # A store named optional but absent from the roster is still a store the
        # pipeline knows about — ingest reads both keys — so it gets a row too.
        for store in listed + [s for s in sorted(opt) if s not in set(map(str, listed))]:
            cur.execute(
                """insert into config_stores
                     (platform, store, optional, invalidates_goldens, evidence,
                      changed_by, source, sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (platform, store) do nothing""",
                (platform, str(store), str(store) in opt, _moves_a_cell("config_stores"),
                 _evidence(content, ["expected_stores", platform]), who, source, order))
            order += 1
            n += cur.rowcount
    return n


def _load_aliases(cur, settings, content, who, source) -> int:
    n = 0
    order = 0
    for platform, mapping in sorted((settings.get("store_aliases") or {}).items()):
        for raw, canonical in (mapping or {}).items():
            # "TODO-HUMAN" was a sentinel meaning "nobody has decided". A real NULL
            # says the same thing without three modules special-casing a string.
            resolved = None if str(canonical) == "TODO-HUMAN" else str(canonical)
            cur.execute(
                """insert into config_store_aliases
                     (platform, raw, canonical, evidence, changed_by, source,
                      sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (platform, _nfc(raw), _nfc(resolved) if resolved else None,
                 _evidence(content, ["store_aliases", platform, str(raw)]),
                 who, source, order))
            order += 1
            n += 1
    return n


def _load_brands(cur, settings, config_dir: Path, who, source) -> int:
    """`store_to_brand` — one mapping, per platform, since 2026-08-21 (D12).

    It was two: this key was `{}` while `config/brand_map.csv` held 60 rows that
    only the month-end master read, and `in_pipeline_contract` marked which was in
    force. The CSV is deleted, its rows are the contract, and the column is gone
    (migration `022`). A run and the master now resolve a brand through the same
    `ingest.store_brands`.

    `note` lands in `evidence`, which is where a row's justification belongs and
    what the editor serves (D42) — the CSV's own column was called `note` and meant
    exactly that.
    """
    n = 0
    order = 0
    for platform, stores in (settings.get("store_to_brand") or {}).items():
        for store, value in (stores or {}).items():
            # The shape check that matters lives in `ingest.store_brands`, which is
            # what the pipeline reads; refusing here too would be a second
            # definition of the same rule, so this only skips what it cannot store.
            if not isinstance(value, dict) or not value.get("brand"):
                continue
            confidence = str(value.get("confidence") or "confirmed").strip().lower()
            cur.execute(
                """insert into config_store_brands
                     (platform, store, brand, confidence,
                      evidence, changed_by, source, sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (platform, store) do update
                     set brand = excluded.brand, confidence = excluded.confidence""",
                (platform, _nfc(store), _nfc(str(value["brand"])),
                 "needs_confirmation" if confidence.startswith("need") else "confirmed",
                 _nfc(str(value.get("note") or "")), who, source, order))
            order += 1
            n += 1
    return n


def _load_tolerances(cur, settings, content, who, source) -> int:
    n = 0
    order = 0
    configured: set[tuple[str, str]] = set()
    for platform, values in sorted((settings.get("tolerances") or {}).items()):
        if not isinstance(values, dict):
            raise ValueError(
                f"tolerances.{platform} is a bare value. Every remaining tolerance "
                f"is per-platform; the two file-wide ones were read by nothing and "
                f"were deleted on 2026-08-18.")
        for name, vnd in values.items():
            configured.add((platform, str(name)))
            cur.execute(
                """insert into config_tolerances
                     (platform, name, vnd, reader, invalidates_goldens, evidence,
                      changed_by, source, sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (platform, str(name), float(vnd), "src/tieout.py",
                 _moves_a_cell("config_tolerances"),
                 _evidence(content, ["tolerances", platform, str(name)]),
                 who, source, order))
            order += 1
            n += 1

    for platform, name, vnd in UNCONFIGURED_TOLERANCES:
        if (platform, name) in configured:
            continue
        cur.execute(
            """insert into config_tolerances
                 (platform, name, vnd, reader, invalidates_goldens, evidence,
                  changed_by, source, sort_order)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (platform, name, float(vnd), "src/tieout.py",
             _moves_a_cell("config_tolerances"), _UNCONFIGURED_NOTE,
             who, source, order))
        order += 1
        n += 1
    return n


def _load_bounds(cur, settings, content, who, source) -> int:
    n = 0
    for order, (period, bounds) in enumerate(
            sorted((settings.get("window_settlement_bounds") or {}).items())):
        evidence = _evidence(content, ["window_settlement_bounds", str(period)]) \
            or _evidence(content, ["window_settlement_bounds"])
        cur.execute(
            """insert into config_settlement_bounds
                 (period, from_date, to_date, evidence, changed_by, source, sort_order)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (str(period), (bounds or {}).get("from"), (bounds or {}).get("to"),
             evidence, who, source, order))
        n += 1
    return n


def _dedupe(rows: list[tuple[str, Any]], *, what: str, path: Path) -> dict[str, Any]:
    """Collapse repeated keys, but REFUSE if two rows disagree.

    Measured 2026-08-18 on the committed snapshots: `lazada_vat_sku.csv` has 668
    rows for 660 SKUs — 7 SKUs repeat — and **every repeat agrees on its rate**, so
    collapsing them loses nothing. `lazada_fee_types.csv` has no repeats at all.

    That is exactly why this refuses instead of taking the first or the last. Today
    the duplicates are harmless noise; the day one carries a different rate, a
    silent first-wins would pick a VAT factor for a SKU by row order, and the run
    would produce a defensible-looking number nobody chose. `src/masters.py` builds
    the same dict and has the same last-wins exposure — this is the stricter of the
    two readings, and the one a money value deserves.
    """
    out: dict[str, Any] = {}
    for key, value in rows:
        if key in out and out[key] != value:
            raise ValueError(
                f"{path.name} gives {what} {key!r} two different values "
                f"({out[key]!r} and {value!r}). Collapsing them would pick one by "
                f"row order. Fix the file.")
        out[key] = value
    return out


def _load_fee_types(cur, config_dir: Path, who, source) -> int:
    path = config_dir / "lazada_fee_types.csv"
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        raw = [(_nfc((r.get("fee_name") or "").strip()),
                ((r.get("bucket") or "").strip(), (r.get("status") or "").strip()))
               for r in csv.DictReader(fh) if (r.get("fee_name") or "").strip()]
    collapsed = _dedupe(raw, what="fee name", path=path)
    # `executemany`, not a loop of `execute`: these two CSV loaders are 778 of the
    # ~950 rows an import writes, and one round trip per row made seeding cost ~5s.
    # That is paid on every test that needs a populated contract.
    cur.executemany(
        """insert into config_fee_types
             (fee_name, bucket, status, changed_by, source, sort_order)
           values (%s, %s, %s, %s, %s, %s)""",
        [(fee, bucket, status, who, source, order)
         for order, (fee, (bucket, status)) in enumerate(collapsed.items())])
    return len(collapsed)


def _load_vat_sku(cur, config_dir: Path, who, source) -> int:
    path = config_dir / "lazada_vat_sku.csv"
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8-sig", newline="") as fh:
        raw = [((r.get("sku") or "").strip(), float(r["rate"]))
               for r in csv.DictReader(fh) if (r.get("sku") or "").strip()]
    collapsed = _dedupe(raw, what="SKU", path=path)
    cur.executemany(
        """insert into config_vat_sku (sku, rate, changed_by, source, sort_order)
           values (%s, %s, %s, %s, %s)""",
        [(sku, rate, who, source, order)
         for order, (sku, rate) in enumerate(collapsed.items())])
    return len(collapsed)


def _load_invoice_buckets(cur, settings, content, who, source) -> int:
    """`invoice_buckets.<platform>.match` rows plus one NULL-needle catch-all row
    per platform, in file order — walk order is match order, so it is meaning."""
    n = 0
    order = 0
    for platform, spec in sorted((settings.get("invoice_buckets") or {}).items()):
        rows: list[tuple[str | None, Any]] = \
            [(str(needle), bucket) for needle, bucket in ((spec or {}).get("match") or {}).items()]
        default = (spec or {}).get("default")
        if default is not None:
            rows.append((None, default))
        for needle, bucket in rows:
            # evidence_for inherits the parent's block when a key has none of its
            # own, so a per-needle comment wins and the platform block is the
            # fallback — no explicit `or` chain needed.
            path = (["invoice_buckets", platform, "match", needle]
                    if needle is not None else ["invoice_buckets", platform, "default"])
            evidence = _evidence(content, path)
            cur.execute(
                """insert into config_invoice_buckets
                     (platform, needle, bucket, evidence, changed_by, source,
                      sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (platform, needle, str(bucket), evidence, who, source, order))
            order += 1
            n += 1
    return n


def _load_fee_buckets(cur, settings, content, who, source) -> int:
    """`fee_buckets.<platform>` — one revenue row, any number of promo rows.

    The YAML shape (a single `revenue:` scalar) makes a second revenue row
    unrepresentable on this path; the row editor is the path that could add one,
    and `config_rows._table_rules` refuses it there with the reason.
    """
    n = 0
    order = 0
    for platform, spec in sorted((settings.get("fee_buckets") or {}).items()):
        rows: list[tuple[str, str]] = []
        revenue = (spec or {}).get("revenue")
        if revenue is not None:
            rows.append(("revenue", str(revenue)))
        rows += [("promo", str(b)) for b in ((spec or {}).get("promo") or [])]
        for role, bucket in rows:
            evidence = _evidence(content, ["fee_buckets", platform, role])
            cur.execute(
                """insert into config_fee_buckets
                     (platform, role, bucket, evidence, changed_by, source,
                      sort_order)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (platform, role, bucket, evidence, who, source, order))
            order += 1
            n += 1
    return n


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    import argparse

    import psycopg

    from .config import ServiceSettings

    ap = argparse.ArgumentParser(
        prog="python -m service.config_import", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--changed-by", default="import",
                    help="recorded on every row; use a real identity when seeding "
                         "a deployment somebody will be asked to trust")
    args = ap.parse_args(argv)

    settings = ServiceSettings.from_env()
    url = args.database_url or settings.database_url
    config_dir = Path(args.config_dir) if args.config_dir else settings.config_dir

    with psycopg.connect(url) as conn:
        counts = import_settings(conn, config_dir, changed_by=args.changed_by)
        conn.commit()
    total = sum(counts.values())
    print(f"imported {total} row(s) from {config_dir}")
    for table, n in counts.items():
        print(f"  {n:>5}  {table}")
    return 0


if __name__ == "__main__":                                      # pragma: no cover
    import sys
    sys.exit(main())
