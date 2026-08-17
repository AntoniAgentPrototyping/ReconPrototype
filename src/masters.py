"""Team-owned master data: fee-type mapping (Lib) and per-SKU VAT rates.

The team maintains "Lib & VAT rate.xlsb" (additive-only). At runtime the
pipeline reads that file when present (settings: masters_file, resolved
relative to config/); the ported CSVs (lazada_fee_types.csv,
lazada_vat_sku.csv) remain as fallback/snapshot, and any drift between the
live master and the snapshots is reported, never silently absorbed.

VAT model (all platforms): ONE default factor (vat_factors.default —
currently 1.08, a temporary tax concession; reverting to 1.10 is that one
line) plus per-SKU exceptions from the master's VAT sheet.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .runlog import RunLog


def _read_xlsb(path: Path) -> tuple[dict[str, dict], dict[str, float]]:
    from pyxlsb import open_workbook  # imported lazily: only needed with a master present

    fee_types: dict[str, dict] = {}
    vat_sku: dict[str, float] = {}
    with open_workbook(str(path)) as wb:
        with wb.get_sheet("Lib") as ws:
            rows = [[c.v for c in row][:6] for row in ws.rows()]
        status_by_bucket = {}
        for r in rows[1:]:
            r = list(r) + [None] * (6 - len(r))
            if r[0] and r[2]:
                status_by_bucket[str(r[0]).strip()] = str(r[2]).strip()
        for r in rows[1:]:
            r = list(r) + [None] * (6 - len(r))
            fee, bucket = r[4], r[5]
            if fee and bucket:
                fee_types[str(fee).strip()] = {
                    "bucket": str(bucket).strip(),
                    "status": status_by_bucket.get(str(bucket).strip(), ""),
                }
        with wb.get_sheet("VAT") as ws:
            for i, row in enumerate(ws.rows()):
                vals = [c.v for c in row][:3]
                if i == 0 or not vals or vals[0] is None:
                    continue
                if len(vals) > 2 and vals[2] is not None:
                    vat_sku[str(vals[0]).strip()] = float(vals[2])
    return fee_types, vat_sku


def _read_csv_snapshots(config_dir: Path) -> tuple[dict[str, dict], dict[str, float]]:
    fee_types: dict[str, dict] = {}
    p = config_dir / "lazada_fee_types.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                fee_types[row["fee_name"].strip()] = {"bucket": row["bucket"].strip(),
                                                      "status": row["status"].strip()}
    vat_sku: dict[str, float] = {}
    p = config_dir / "lazada_vat_sku.csv"
    if p.exists():
        with p.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                vat_sku[row["sku"].strip()] = float(row["rate"])
    return fee_types, vat_sku


def load_masters(config_dir: Path, settings: dict, log: RunLog) -> dict:
    """Returns {"fee_types", "vat_sku", "source"}. Live master preferred;
    CSV snapshots as fallback. Drift live-vs-snapshot is logged."""
    master_name = settings.get("masters_file", "Lib & VAT rate.xlsb")
    master = config_dir / master_name
    csv_fee, csv_vat = _read_csv_snapshots(config_dir)

    if master.exists():
        fee_types, vat_sku = _read_xlsb(master)
        log.add(f"  masters: live '{master.name}' ({len(fee_types)} fee names, "
                f"{len(vat_sku)} VAT SKUs, {sum(1 for v in vat_sku.values() if v != 1.08)} non-1.08)")
        drift = []
        for fee, m in fee_types.items():
            snap = csv_fee.get(fee)
            if snap is None:
                drift.append(f"fee '{fee}' new in master")
            elif snap["bucket"] != m["bucket"]:
                drift.append(f"fee '{fee}': bucket {snap['bucket']} -> {m['bucket']}")
        for fee in csv_fee:
            if fee not in fee_types:
                drift.append(f"fee '{fee}' missing from master (snapshot only)")
        for sku, rate in vat_sku.items():
            snap = csv_vat.get(sku)
            if snap is None:
                drift.append(f"VAT SKU '{sku}' new in master ({rate})")
            elif abs(snap - rate) > 1e-9:
                drift.append(f"VAT SKU '{sku}': {snap} -> {rate}")
        for sku in csv_vat:
            if sku not in vat_sku:
                drift.append(f"VAT SKU '{sku}' missing from master (snapshot only)")
        if drift:
            log.warn(f"master vs snapshot drift ({len(drift)} item(s)):")
            for d in drift[:20]:
                log.add(f"    drift: {d}")
        else:
            log.add("  masters: live master matches the CSV snapshots exactly")
        return {"fee_types": fee_types, "vat_sku": vat_sku, "source": "xlsb"}

    log.warn(f"masters file '{master_name}' not found — using CSV snapshots")
    return {"fee_types": csv_fee, "vat_sku": csv_vat, "source": "csv"}


def vat_factor_for(sku_series, settings: dict, vat_sku: dict[str, float]):
    """What the master says about each SKU's VAT — **NaN where it says nothing.**

    This used to end in `.fillna(default)`, which made "this SKU is standard
    rated" and "this SKU is absent from the master" the same value: a
    wrong-*tax* path with no signal (docs/08-KNOWN-DEFECTS.md#14). The default
    fall-through is still the team's rule and still applied — it just happens in
    `resolve_vat_factors`, one level up, where it can be counted and reported.

    `settings` is unread here and kept in the signature because callers and
    tests pass it positionally; the default it carries belongs to the resolver.
    """
    return sku_series.map(vat_sku)


def resolve_vat_factors(sku_series, settings: dict, vat_sku: dict[str, float],
                        log: RunLog, *, label: str = ""):
    """Apply the team's rule — master exception, else the default factor — and
    say out loud how much of the data the master actually covered.

    Returns `(factors, unmapped_mask)`. The numbers are unchanged from the
    silent version: fall-through to the default IS the team's behaviour and is
    row-verified against their files, so changing it would be inventing a rule
    ([D1](../docs/06-DECISIONS.md#d1)). What changes is that the fall-through
    is now quantified in the run log instead of being invisible.

    Measured on the three May windows, and the reason this is not a cosmetic
    log line: coverage is **zero**. None of TikTok's 224, Shopee's 461 or the
    sampled Lazada window's 92 distinct SKUs appear among the master's 660,
    so every line is invoiced at the default and a non-default-rate SKU
    trading on any of them would be taxed wrongly with no signal.
    """
    default = float((settings.get("vat_factors") or {}).get("default", 1.08))
    factors = vat_factor_for(sku_series, settings, vat_sku)
    unmapped = factors.isna()
    rows = len(factors)
    n_unmapped = int(unmapped.sum())
    matched = rows - n_unmapped
    distinct_unmapped = int(sku_series[unmapped].astype(str).str.strip().nunique())

    log.add(f"  VAT master coverage{label}: {matched:,} of {rows:,} row(s) matched a "
            f"master SKU; {n_unmapped:,} row(s) / {distinct_unmapped:,} distinct SKU(s) "
            f"fall back to the {default} default")
    if rows and matched == 0:
        log.warn(f"NO SKU{label} matched the VAT master ({len(vat_sku)} entries) — the "
                 f"per-SKU VAT exception mechanism is inert here and every line is "
                 f"taxed at {default}. A SKU on a different rate would be invoiced "
                 f"wrongly with no other signal (docs/08-KNOWN-DEFECTS.md#14)")
    return factors.fillna(default), unmapped
