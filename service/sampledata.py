"""A believable demo window across all three platforms, generated from nothing.

    python -m service.sampledata --out .scratch/demo

**Why it lives in `service/` and not in `tools/`.** A deployed container ships
`src/` + `service/` only, and the whole point is that an admin can seed a working
demo from the browser — on a machine with no client data, which is every machine
except one.

The CLI is *here* rather than in `tools/` for a second reason, and the lint found it:
a `tools/make_sample_data.py` wrapper would have to import `service`, and `tools/`
must survive `service/` being deleted
(`tests/service/test_service_is_deletable.py`). The service's own CLIs live inside
the service — the same shape as `service/admin.py`.

**Deterministic, and verified as such.** One `random.Random(SEED)` in one ordered
pass, so two generations produce the same numbers. Checked by comparing **cellset
digests** of the resulting workbooks, never file hashes: openpyxl stamps timestamps
into `docProps/core.xml`, so two runs producing identical numbers produce different
bytes ([D16](../docs/06-DECISIONS.md#d16)).

**The synthetic roster is made legitimate through the existing pin mechanism**, not
by editing the real contract. A demo settings text is built by ruamel round-trip
(every comment survives), recorded as a `config_version`, and pinned to the demo
window. `config/settings.yaml` on disk is never touched and the real rosters never
apply to the demo — so the demo runs cleanly under the store-count hard stop that
workstream C deliberately keeps.

**Per-platform traps that otherwise silently produce wrong data.** Each is a
measured property of the real exports, not a guess:

* **TikTok orders need one junk row directly under the header**, because
  `skip_rows_after_header.tiktok.orders: 1` drops the first data row. Omit it and
  every order shifts by one.
* **Shopee income needs two band rows above the leaf header** (`header_rows: 3`)
  and **two sheets matching `/Doanh thu/`**, because `read_parts` concatenates every
  matching sheet.
* **Shopee's 1-VND revenue crossing must be derived, never randomised.** The five
  components are generated as integers and the total is set to their exact sum —
  `tieout.revenue_crossing_shopee` asserts that relation at 1 VND because it was
  measured exact, not because 1 VND is a nice number.
* **One Shopee order file carries NFD headers**, because real exports do: 9 of 63
  headers in a real file are non-NFC, and that is the bug `ingest.read_parts`
  normalises for.
* **Lazada fee names come from `config/lazada_fee_types.csv`**, the discipline
  `tools/smoke_test.py::_fee_names` already uses. A fee name absent from the master
  lands in the unmapped exception frame and leaves the revenue tabs empty — a green
  run that proved nothing.
* **Filenames carry the store**, never a column. The legacy generator emitted
  `part_1.csv` with a `Shop Name` column, which exercises a path production never
  takes (see `service/naming.py`).

**The anomalies are the point.** ~15% prior-month settlement (dropped by
`apply_settlement_bounds`), ~8% fully returned, ~5% zero-revenue, two ghost income
lines per TikTok store with no matching order — the real ~21% unmatched class — SKUs
absent from the VAT master so the fall-through is counted, an accounting-format dash
for zero in a Shopee money column, one Shopee `Sku` line per few orders so the
drop path is exercised, and one unmapped Lazada fee. Measured outcome: Lazada
**VARIANCE** with 2 unmapped fees, TikTok **UNVERIFIED** with 4 unmatched orders,
Shopee **UNVERIFIED** and tying exactly. **An empty exception queue teaches nothing**,
and this one is not empty.

**Two anomalies the plan asked for are deliberately NOT here**, and the reasons
matter more than the anomalies would:

* **No deliberate tie-out breach.** One was intended. But the first run of this
  generator produced a *real* breach — the Shopee crossing, off by exactly the two
  subsidies — and telling that apart from a manufactured one took reading the check's
  source. A demo that ships a breach teaches an operator that breaches are normal,
  which is precisely the habit M2's tie-out rebuild existed to reverse. The
  exception queue is populated by the unmapped fee and the ghost orders instead.
* **No duplicate row across Shopee parts.** `dedupe_rows` is `false` for the real
  platforms because byte-identical order lines are legitimate, so a planted duplicate
  would not be *detected* — it would just silently inflate revenue and make the demo
  wrong rather than instructive. The double-pull class is demonstrated where it is
  actually caught: a byte-identical re-upload is refused at the door.
"""

from __future__ import annotations

import csv
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# Fixed, and not configurable. A seed a caller could change would make "the demo
# window" a different thing on different machines, and the determinism test would be
# asserting a property of its own argument.
SEED = 20260817

PERIOD = "2026-05_demo"
MONTH = date(2026, 5, 1)

# Synthetic, and deliberately not near a real client name. One carries Vietnamese
# diacritics so the demo exercises `SAFE_FILENAME`'s `À-ỹ` class, NFC normalisation
# and the Excel sheet-name path — a demo of an entirely ASCII world would not.
STORES = ("Demo Alpha", "Demo Đông Á")

# Enough rows to make a workbook with real pivots, few enough to run in ~2 seconds.
ORDERS_PER_STORE = 60


@dataclass
class Window:
    """One platform's generated files, as (relative path, DataFrame writer)."""

    platform: str
    files: list[tuple[str, str]] = field(default_factory=list)   # (kind, filename)


def _rng() -> random.Random:
    return random.Random(SEED)


def _fee_names(config_dir: Path) -> tuple[str, str]:
    """A revenue fee and a cost fee, read from the committed CSV snapshot.

    Not hardcoded, for the reason in the module docstring: a fee name absent from the
    master leaves every revenue tab empty and the run still passes.
    """
    revenue = cost = None
    path = Path(config_dir) / "lazada_fee_types.csv"
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            bucket = row["bucket"].strip()
            if bucket.startswith("1.") and revenue is None:
                revenue = row["fee_name"].strip()
            elif bucket.startswith("6.") and cost is None:
                cost = row["fee_name"].strip()
    if not revenue or not cost:                                 # pragma: no cover
        raise RuntimeError(
            f"{path} holds no 1.* revenue bucket or no 6.* cost bucket, so a "
            f"generated Lazada window would have empty revenue tabs")
    return revenue, cost


def _sku(index: int) -> str:
    return f"DEMO-SKU-{index:03d}"


# ---------------------------------------------------------------------------
# TikTok
# ---------------------------------------------------------------------------

def _tiktok(rng: random.Random) -> dict[tuple[str, str], list[dict]]:
    """Order lines and income lines, keyed by (kind, store)."""
    out: dict[tuple[str, str], list[dict]] = {}

    for store_index, store in enumerate(STORES):
        orders: list[dict] = []
        income: list[dict] = []

        # The junk row `skip_rows_after_header: 1` will drop. It has to look like a
        # row, not like a header, or the reader would treat it as data.
        orders.append({
            "Order ID": "--- do not read this row ---",
            "Seller SKU": "", "Product Name": "", "Quantity": "",
            "SKU Unit Original Price": "", "Created Time": "", "Order Status": "",
            "Order Refund Amount": "", "SKU Subtotal After Discount": "",
            "SKU Seller Discount": "", "Cancelation/Return Type": "",
        })

        for n in range(1, ORDERS_PER_STORE + 1):
            order_id = f"TT{store_index}{n:05d}"
            settled = MONTH + timedelta(days=rng.randrange(1, 17))
            # ~15% settle in the PRIOR month, which is what
            # apply_settlement_bounds exists to drop.
            if rng.random() < 0.15:
                settled = MONTH - timedelta(days=rng.randrange(1, 12))
            created = settled - timedelta(days=rng.randrange(1, 6))

            lines = rng.randrange(1, 4)
            gross = 0
            for line in range(lines):
                quantity = rng.randrange(1, 4)
                unit = rng.randrange(50, 400) * 1000
                subtotal = quantity * unit
                discount = int(subtotal * rng.choice((0, 0, 0.05, 0.1)))
                gross += subtotal - discount
                orders.append({
                    "Order ID": order_id,
                    "Seller SKU": _sku((n + line) % 12),
                    "Product Name": f"Demo product {(n + line) % 12}",
                    "Quantity": str(quantity),
                    "SKU Unit Original Price": str(unit),
                    "Created Time": created.strftime("%Y/%m/%d %H:%M:%S"),
                    "Order Status": "Completed",
                    "Order Refund Amount": "0",
                    "SKU Subtotal After Discount": str(subtotal - discount),
                    "SKU Seller Discount": str(discount),
                    "Cancelation/Return Type": "",
                })

            roll = rng.random()
            if roll < 0.08:
                # Fully returned: subtotal_before + refund_before == 0 makes this
                # CHECK_TOTAL_RETURN, which is taken out rather than invoiced.
                refund_before, before = -gross, gross
                net, revenue = 0, 0
            elif roll < 0.13:
                # Zero revenue — a real class, and it must not read as an error.
                refund_before, before = 0, 0
                net, revenue = 0, 0
            else:
                refund_before, before = 0, gross
                net = int(gross * 0.88)
                revenue = gross

            income.append({
                "Order/adjustment ID": order_id,
                "Type": "Order",
                "Order created time": created.strftime("%Y/%m/%d %H:%M:%S"),
                "Order settled time": settled.strftime("%Y/%m/%d %H:%M:%S"),
                "Total Revenue": str(revenue),
                "Total settlement amount": str(net),
                "Customer refund": "0",
                # The column the 1-VND conservation check reconciles against.
                "Subtotal after seller discounts": str(gross),
                "Subtotal before discounts": str(before),
                "Refund subtotal after seller discounts": str(refund_before),
                "Refund subtotal before seller discounts": str(refund_before),
            })

        # Two GHOST income lines: settlement with no matching order lines. This is
        # the ~21% class that TikTok really has (11,765 orders, 3.45B VND) and it
        # must appear in the exception queue rather than vanish.
        for ghost in range(2):
            order_id = f"TT{store_index}GHOST{ghost}"
            settled = MONTH + timedelta(days=8 + ghost)
            income.append({
                "Order/adjustment ID": order_id,
                "Type": "Order",
                "Order created time": (settled - timedelta(days=2)).strftime("%Y/%m/%d %H:%M:%S"),
                "Order settled time": settled.strftime("%Y/%m/%d %H:%M:%S"),
                "Total Revenue": "1200000",
                "Total settlement amount": "1050000",
                "Customer refund": "0",
                "Subtotal after seller discounts": "1200000",
                "Subtotal before discounts": "1200000",
                "Refund subtotal after seller discounts": "0",
                "Refund subtotal before seller discounts": "0",
            })

        out[("orders", store)] = orders
        out[("income", store)] = income
    return out


# ---------------------------------------------------------------------------
# Shopee
# ---------------------------------------------------------------------------

def _shopee(rng: random.Random) -> dict[tuple[str, str], list[dict]]:
    out: dict[tuple[str, str], list[dict]] = {}

    for store_index, store in enumerate(STORES):
        orders: list[dict] = []
        income: list[dict] = []

        for n in range(1, ORDERS_PER_STORE + 1):
            order_id = f"SP{store_index}{n:05d}"
            settled = MONTH + timedelta(days=rng.randrange(1, 11))
            if rng.random() < 0.15:
                settled = MONTH - timedelta(days=rng.randrange(1, 10))
            created = settled - timedelta(days=rng.randrange(1, 5))

            quantity = rng.randrange(1, 4)
            unit = rng.randrange(40, 300) * 1000
            gross = quantity * unit
            seller_subsidy = int(gross * rng.choice((0, 0, 0.04)))
            shopee_subsidy = int(gross * rng.choice((0, 0.03)))

            orders.append({
                "Mã đơn hàng": order_id,
                "SKU phân loại hàng": _sku(n % 12),
                "Tên sản phẩm": f"Demo product {n % 12}",
                "Số lượng": str(quantity),
                "Giá gốc": str(unit),
                # **ISO, not dd/mm/yyyy, and that is a deliberate abstention.**
                # `dayfirst.shopee` is `false` in the real config carrying a
                # "TODO verify when Shopee is mapped" comment — so the true Shopee
                # date format is an OPEN QUESTION. Emitting dd/mm/yyyy would make
                # the demo assert an answer nobody has verified (and warns under
                # dayfirst=false); ISO is unambiguous under either setting.
                "Ngày đặt hàng": created.strftime("%Y-%m-%d %H:%M"),
                "Trạng Thái Đơn Hàng": "Hoàn thành",
                "Người bán trợ giá": str(seller_subsidy),
                "Được Shopee trợ giá": str(shopee_subsidy),
            })

            # **The crossing is DERIVED, never randomised**, and getting the
            # direction wrong is silent. `tieout.revenue_crossing_shopee` compares
            #
            #   income:  gross_revenue + shopee_product_subsidy
            #   orders:  (unit_price x quantity) - seller_subsidy
            #
            # at 1 VND, because it was measured EXACT on four real windows. So the
            # income "product price" is net of BOTH subsidies and adding the
            # Shopee-funded part back gives the seller's revenue. Writing the order
            # file's gross into income instead breaches by exactly the two subsidies
            # — which is what this generator did on its first run, at 59,640 VND per
            # order, indistinguishable from a real regression.
            income_gross = gross - seller_subsidy - shopee_subsidy

            cofund = int(gross * rng.choice((0, 0.02)))
            seller_voucher = int(gross * rng.choice((0, 0.01)))
            coin = int(gross * rng.choice((0, 0.005)))
            ship = int(gross * rng.choice((0, 0.02)))
            refund = 0
            if rng.random() < 0.08:
                # A full return. These are HELD OUT of the crossing by name rather
                # than absorbed into a tolerance: the order export carries the full
                # ordered quantity while income is reduced for returned units, so
                # they do not tie by construction.
                refund = gross
            net = income_gross - cofund - seller_voucher - coin - ship - refund

            # `Đơn hàng / Sản phẩm` holds the literal English "Order" / "Sku", NOT
            # Vietnamese — `classify_shopee_income` filters on `== "Order"`. Getting
            # this wrong is silent: every row is dropped, the SKU frame comes out
            # empty, and the run reports OK with an empty exception queue. It did,
            # the first time this generator ran.
            income.append({
                "Mã đơn hàng": order_id,
                "Đơn hàng / Sản phẩm": "Order",
                "Ngày đặt hàng": created.strftime("%Y-%m-%d %H:%M"),
                "Ngày hoàn thành thanh toán": settled.strftime("%Y-%m-%d %H:%M"),
                "Tổng tiền đã thanh toán": str(net),
                "Giá sản phẩm": str(income_gross),
                "Số tiền hoàn lại": str(refund),
                "Mã ưu đãi Đồng Tài Trợ do Người Bán chịu": str(cofund),
                "Sản phẩm được trợ giá từ Shopee": str(shopee_subsidy),
                "Mã ưu đãi do Người Bán chịu": str(seller_voucher),
                "Mã hoàn xu do Người Bán chịu": str(coin),
                # Accounting-format dash for zero, which a real export writes in
                # 46,972 of 83,134 rows — the case ZERO_TOKENS exists for.
                "Phí vận chuyển - Người bán hỗ trợ": str(ship) if ship else "-",
            })

            # A product-level "Sku" line, which the team's own M code filters out and
            # `classify_shopee_income` drops. Present so the drop path is exercised
            # and its count appears in the log — a demo where nothing is ever
            # discarded hides the step that discards things.
            if rng.random() < 0.3:
                income.append({
                    **income[-1],
                    "Đơn hàng / Sản phẩm": "Sku",
                    "Tổng tiền đã thanh toán": "0",
                })

        out[("orders", store)] = orders
        out[("income", store)] = income
    return out


# ---------------------------------------------------------------------------
# Lazada
# ---------------------------------------------------------------------------

def _lazada(rng: random.Random, config_dir: Path) -> dict[tuple[str, str], list[dict]]:
    revenue_fee, cost_fee = _fee_names(config_dir)
    out: dict[tuple[str, str], list[dict]] = {}

    for store_index, store in enumerate(STORES):
        rows: list[dict] = []
        for n in range(1, ORDERS_PER_STORE + 1):
            order = f"LZ{store_index}{n:05d}"
            day = MONTH + timedelta(days=rng.randrange(1, 25))
            for line, sku_offset in enumerate(("A", "B")):
                amount = rng.randrange(60, 350) * 1000
                rows.append({
                    "Transaction Date": day.strftime("%Y-%m-%d"),
                    "Fee Name": revenue_fee,
                    "Details": f"Demo product {n % 12}{sku_offset}",
                    # One SKU absent from the VAT master, so the fall-through is
                    # exercised and counted rather than assumed.
                    "Seller SKU": _sku((n + line) % 12),
                    "Lazada SKU": f"LZD-{n:04d}-{sku_offset}",
                    "Amount": str(amount),
                    "VAT in Amount": str(int(amount - amount / 1.08)),
                    "Order No.": order,
                    "Order Item No.": f"{order}-{sku_offset}",
                    "Paid Quantity": "1",
                })
            rows.append({
                "Transaction Date": day.strftime("%Y-%m-%d"),
                "Fee Name": cost_fee,
                "Details": "", "Seller SKU": "", "Lazada SKU": "",
                "Amount": str(-rng.randrange(2, 20) * 1000),
                "VAT in Amount": "0",
                "Order No.": order, "Order Item No.": "", "Paid Quantity": "",
            })

        # One deliberately UNMAPPED fee, so the exception queue has something in it
        # and the `unmapped_fees` sheet is exercised. An empty exception queue
        # teaches nothing.
        rows.append({
            "Transaction Date": (MONTH + timedelta(days=20)).strftime("%Y-%m-%d"),
            "Fee Name": "Demo Fee Nobody Has Mapped",
            "Details": "", "Seller SKU": "", "Lazada SKU": "",
            "Amount": "-99000", "VAT in Amount": "0",
            "Order No.": f"LZ{store_index}UNMAPPED", "Order Item No.": "",
            "Paid Quantity": "",
        })
        out[("weekly", store)] = rows
    return out


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


def _write_sheet(path: Path, rows: list[dict], *, sheet: str, band_rows: int = 0,
                 nfd_headers: bool = False, extra_sheet: str | None = None,
                 split_at: int | None = None) -> None:
    """Write one export in the real shape.

    `band_rows` reproduces Shopee income's two group rows above the leaf header;
    `split_at` plus `extra_sheet` reproduces its multi-sheet income split, which
    `read_parts` concatenates.
    """
    import pandas as pd

    if not rows:                                                # pragma: no cover
        raise ValueError("refusing to write an empty export")
    frame = pd.DataFrame(rows)
    if nfd_headers:
        # Real Shopee ORDER exports deliver Vietnamese headers decomposed. 9 of 63
        # headers in a real file are non-NFC, and they are byte-unequal to the
        # visually identical config keys.
        frame.columns = [_nfd(str(c)) for c in frame.columns]

    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if split_at is not None and extra_sheet is not None:
            frame.iloc[:split_at].to_excel(writer, sheet_name=sheet, index=False,
                                          startrow=band_rows)
            frame.iloc[split_at:].to_excel(writer, sheet_name=extra_sheet, index=False,
                                          startrow=band_rows)
        else:
            frame.to_excel(writer, sheet_name=sheet, index=False, startrow=band_rows)


def generate(target_root: Path, *, config_dir: Path, period: str = PERIOD) -> list[Path]:
    """Write the demo window under `target_root/<period>/<platform>/...`.

    Returns the files written, in a stable order. The filenames are the **uniform
    scheme** already (`service/naming.py`), so the demo also demonstrates what an
    uploaded window looks like after a run has renamed it.
    """
    from . import naming

    rng = _rng()
    target_root = Path(target_root)
    written: list[Path] = []

    # One ordered pass, so the seed determines everything. Platform order is fixed
    # rather than dict order for the same reason.
    tiktok = _tiktok(rng)
    shopee = _shopee(rng)
    lazada = _lazada(rng, config_dir)

    def write(platform: str, kind: str, store: str, ordinal: int, rows: list[dict],
              **kwargs) -> None:
        name = naming.uniform_name(platform, kind, ordinal, store)
        path = naming.target_path(target_root, period, platform, kind, name)
        _write_sheet(path, rows, **kwargs)
        written.append(path)

    for ordinal, store in enumerate(STORES, start=1):
        write("tiktok", "orders", store, ordinal, tiktok[("orders", store)],
              sheet="OrderSKUList")
        write("tiktok", "income", store, ordinal, tiktok[("income", store)],
              sheet="Order details")

    for ordinal, store in enumerate(STORES, start=1):
        write("shopee", "orders", store, ordinal, shopee[("orders", store)],
              sheet="orders",
              # The first store's order export carries NFD headers, because real
              # exports do and that is what ingest normalises for.
              nfd_headers=(ordinal == 1))
        rows = shopee[("income", store)]
        write("shopee", "income", store, ordinal, rows,
              sheet="Doanh thu", extra_sheet="Doanh thu - 1",
              split_at=len(rows) // 2,
              # Two band rows above the leaf header: `header_rows.shopee.income: 3`.
              band_rows=2)

    for ordinal, store in enumerate(STORES, start=1):
        write("lazada", "weekly", store, ordinal, lazada[("weekly", store)],
              sheet="Transaction Overview")

    return written


# ---------------------------------------------------------------------------
# The demo's own config version
# ---------------------------------------------------------------------------

def demo_settings_text(config_dir: Path) -> str:
    """The real settings, with the demo's synthetic roster substituted.

    **Built by ruamel round-trip, so every comment survives** — the demo config is a
    real config version and has to be as defensible as any other. And
    `config/settings.yaml` on disk is never touched: the demo roster reaches a run
    through the pin mechanism, which is the same path a re-run of May uses. Without
    this the demo stores would have to be added to the real roster, and every real
    window would then expect two storefronts that do not exist.
    """
    from . import config_edits, config_store

    content = config_store.read_text(config_dir)
    edits: list[config_edits.Edit] = []
    for platform in ("tiktok", "shopee", "lazada"):
        current = (config_store.parse(content).get("expected_stores") or {}).get(platform) or []
        for store in current:
            edits.append(config_edits.Edit(
                op="remove_list_item", path=("expected_stores", platform), value=store,
                # The roster's interleaved comments describe the REAL stores and are
                # meaningless once they are gone. Answered explicitly rather than
                # left to a default — see config_edits.OrphanedEvidence.
                comment_disposition="remove"))
        for store in STORES:
            edits.append(config_edits.Edit(
                op="append_list_item", path=("expected_stores", platform), value=store,
                comment="synthetic demo store — service/sampledata.py"))
    return config_edits.apply_edits(content, edits)


def seed(repo, settings, *, target_root: Path | None = None,
         period: str = PERIOD, seeded_by: str = "demo") -> dict:
    """Generate the window, store it as uploads, and pin the demo config to it.

    The files go through the object store exactly as a browser upload would, so the
    demo exercises the real materialisation path rather than a shortcut past it.
    """
    import shutil
    import tempfile

    from . import naming, objects as object_lib, uploads as upload_lib

    scratch = Path(tempfile.mkdtemp(prefix="recon-demo-"))
    store = object_lib.upload_store(settings)
    domain_text = demo_settings_text(settings.config_dir)

    from src import config as src_config
    domain = src_config.parse_settings(domain_text)

    created: list[dict] = []
    try:
        for path in generate(scratch, config_dir=settings.config_dir, period=period):
            platform = path.parent.parent.name
            kind = {"orders": "orders", "income": "income",
                    "Weekly": "weekly", "Daily": "daily"}[path.parent.name]
            data = path.read_bytes()
            digest = upload_lib.digest_bytes(data)
            key = f"{object_lib.UPLOAD_PREFIX}/{period}/{platform}/{kind}/{digest}.xlsx"
            ref = store.put(key, data)
            store_name = naming.store_of(path.name, platform, domain)
            try:
                created.append(repo.record_upload(
                    filename=path.name, sha256=digest, bytes_=len(data),
                    uploaded_by=seeded_by, platform=platform, period=period,
                    kind=kind, sanitized=True, uri=ref.uri, object_key=key,
                    state="stored", store=store_name, store_canonical=store_name))
            except Exception:                                   # noqa: BLE001
                # Already seeded. Idempotent by construction: the key is the content
                # digest, so re-seeding overwrites the same object.
                continue
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    version = repo.record_config_version(domain_text, source="proposal",
                                         created_by=f"{seeded_by} (demo seed)")
    repo.pin_period_config(
        "tiktok", period, version["id"], pinned_by=seeded_by,
        reason="synthetic demo window — its roster is synthetic and must not leak "
               "into a real window")
    for platform in ("shopee", "lazada"):
        repo.pin_period_config(platform, period, version["id"], pinned_by=seeded_by,
                               reason="synthetic demo window")

    return {"period": period, "uploads": len(created),
            "config_version_id": version["id"], "stores": list(STORES)}


def unseed(repo, settings, *, period: str = PERIOD) -> dict:
    """Remove the demo window's uploads and objects. Leaves runs alone.

    A run that happened is history and stays: deleting the record of a run because
    its input was synthetic would make the demo the one part of the system that can
    rewrite the past.
    """
    from . import objects as object_lib

    store = object_lib.upload_store(settings)
    removed = 0
    for row in repo.uploads_for_window("tiktok", period) + \
            repo.uploads_for_window("shopee", period) + \
            repo.uploads_for_window("lazada", period):
        if row.get("object_key"):
            store.delete(row["object_key"])
        removed += 1
    repo.delete_uploads_for_period(period)
    for platform in ("tiktok", "shopee", "lazada"):
        repo.unpin_period_config(platform, period)
    return {"period": period, "uploads_removed": removed}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: "list[str] | None" = None) -> int:
    """Write the demo window to a directory, for a developer with no database.

    Deliberately does NOT offer a `--check-determinism` flag. The determinism gate is
    `tests/service/test_sampledata.py::test_two_generations_are_identical_by_cellset_digest`,
    and it needs `tests/goldens/cellset.py` to compare digests — which the container
    does not ship. A CLI flag duplicating it would either import `tests/` from
    `service/` (the thing the deletable lint forbids) or grow a second definition of
    "did a cell move". One definition, in the test.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m service.sampledata", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None,
                    help="directory to write <period>/<platform>/... under "
                         "(default: ./.scratch/demo)")
    ap.add_argument("--config-dir", default=None, help="default: ./config")
    ap.add_argument("--period", default=PERIOD)
    args = ap.parse_args(argv)

    # A Windows console defaults to cp1252, which cannot encode the `Đ` in one demo
    # store's name. Not cosmetic: the tool wrote every file correctly and then
    # crashed printing the list, which looks like the generation failed.
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):                            # pragma: no cover
            pass

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / ".scratch" / "demo"
    config_dir = Path(args.config_dir) if args.config_dir else root / "config"

    written = generate(out, config_dir=config_dir, period=args.period)
    print(f"wrote {len(written)} file(s) under {out}")
    for path in written:
        print(f"  {path.relative_to(out)}")
    print("\nRun it with:")
    print(f"  python tools/devrun.py --platform lazada --period {args.period}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
