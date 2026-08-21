"""Editing configuration as ROWS, not as paths into a file.

M8/1.2 made the `config_*` tables the thing that is *rendered*; the editor still
edited YAML text, and `service/api.py::_reimport_config` re-read the applied file
back into the tables afterwards. That bridge worked, and it was the wrong shape for
three reasons the register names:

* **Per-entry evidence could not be served.** The justification for one alias, one
  column-map spelling, lives in its row's `evidence` column — but the editor read
  comments out of the rendered text, and the rendered file only carries top-level
  blocks. So the editor showed the roster's evidence against every store in it.
* **A comment can be orphaned; a column cannot.** `config_edits.OrphanedEvidence`
  existed because removing one list item left the block above it captioning its
  neighbour — measured, not theoretical. A row deletes its evidence with it, so the
  whole question disappears, and `comment_disposition` with it.
* **"Can this move a cell" was inferred from a dotted path.** `field_for` resolved a
  path back to a declared `Field` and read the flag off that, which is correct only
  while the schema and the tables agree about what exists. Since migration 008 the
  row answers for itself.

**The operations are two, and they are closed.** `upsert` and `delete`. What each
table permits is declared in `TABLES` below, and that declaration is the honesty
rule `config_edits._check_may_add` used to state as `open_container`: a table that
refuses new rows says *why*, in a sentence, and the refusal quotes it.
`config_scalars` is closed because `src/` reads specific keys and a new one would be
config the pipeline silently ignores; `config_tolerances` is closed because a
tolerance nothing reads is exactly what Phase 1.1 deleted three of.

**Nothing here commits.** `apply` runs inside the caller's transaction, and
`render_after` applies, renders and rolls back — which is how a preview and a
proposal's diff are produced by the very code that would produce the change.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .config_store import ConfigEditError

OPS = ("upsert", "delete")

PLATFORMS = ("tiktok", "shopee", "lazada")


class RowEditError(ConfigEditError):
    """A refused row edit. Rendered to the operator verbatim, so it says why."""


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

class Kind:
    """How a column is typed on the wire, and which control draws it.

    A closed set. `json` exists for exactly one column — `config_scalars.value` —
    which holds a bool, a number, a string and one small list, and whose type is
    fixed by the row already there (see `_coerce_scalar_value`).
    """

    TEXT = "text"
    BOOL = "bool"
    INT = "int"
    MONEY_VND = "money_vnd"
    NUMBER = "number"
    DATE = "date"
    ENUM = "enum"
    JSON = "json"


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    label: str
    help: str = ""
    nullable: bool = False
    options: tuple[tuple[str, str], ...] = ()
    default: Any = None


@dataclass(frozen=True)
class TableSpec:
    """One config table, described well enough to render it and to police a write."""

    name: str
    title: str
    blurb: str
    key: tuple[Column, ...]
    columns: tuple[Column, ...]
    # Listing order, and the tiebreak that keeps rendering byte-stable.
    order_by: str
    # Rows grouped per platform in the UI, so a roster reads as three lists rather
    # than one 42-row table with a platform column nobody scans.
    grouped_by: str | None = None
    may_insert: bool = True
    may_delete: bool = True
    # Quoted verbatim when an insert or delete is refused. A table that is closed
    # and cannot say why is a table nobody can argue with.
    closed_reason: str = ""
    # The value a NEW row gets. Migration 008 holds the same decisions for rows that
    # already exist and argues both; they are repeated here because this is where a
    # new row acquires one.
    invalidates_goldens: bool = True
    require_evidence: bool = True

    @property
    def key_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.key)

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> Column | None:
        for c in self.key + self.columns:
            if c.name == name:
                return c
        return None


_ENGINES = (
    ("openpyxl", "openpyxl — the default reader"),
    ("calamine", "calamine — ignores a broken <dimension> tag, and much faster on "
                 "Shopee"),
)

_PLATFORM_KEY = Column("platform", Kind.ENUM, "Platform",
                       options=tuple((p, p) for p in PLATFORMS))


TABLES: dict[str, TableSpec] = {

    "config_scalars": TableSpec(
        name="config_scalars",
        title="Single settings",
        blurb="One value each: the VAT factor, how an amount is spelled, what "
              "happens to a cell that will not parse.",
        key=(Column("key", Kind.TEXT, "Setting"),),
        columns=(Column("value", Kind.JSON, "Value"),),
        order_by="sort_order",
        may_insert=False, may_delete=False,
        closed_reason=(
            "src/ reads these keys by name. A key added here would be config the "
            "pipeline silently ignores, and a key removed would fall back to a code "
            "default — which for drop_unmapped_columns and dedupe_rows is the "
            "OPPOSITE of the configured value. Adding or removing one is a code "
            "change."),
    ),

    "config_platforms": TableSpec(
        name="config_platforms",
        title="Reading the store from a filename",
        blurb="TikTok and Shopee exports carry no store column, so the filename IS "
              "the store identity. Group 1 of the pattern is the store name.",
        key=(_PLATFORM_KEY,),
        columns=(
            Column("store_from_filename", Kind.TEXT, "Filename pattern",
                   help="A regex whose group 1 is the store. Test it against a real "
                        "filename before proposing it — a wrong capture reassigns a "
                        "storefront's revenue.",
                   nullable=True),
            Column("dayfirst", Kind.BOOL, "Dates are day-first",
                   help="On: 31/12/2026 reads as 31 December.", default=False),
        ),
        order_by="sort_order, platform",
        may_insert=False, may_delete=False,
        closed_reason=(
            "A platform is a code path — src/ingest.py for TikTok and Shopee, the "
            "self-contained vertical in src/lazada.py for Lazada. A fourth row here "
            "would configure a pipeline that does not exist."),
    ),

    "config_reading": TableSpec(
        name="config_reading",
        title="How each file is read",
        blurb="Sheet names, header rows and Excel engines. A wrong value here does "
              "not raise — it produces a frame shifted by two rows.",
        key=(_PLATFORM_KEY,
             Column("kind", Kind.TEXT, "Export kind",
                    help="orders, income, weekly or daily.")),
        columns=(
            Column("sheet_name", Kind.TEXT, "Exact sheet", nullable=True,
                   help="The one sheet to read. Leave empty to read the first."),
            Column("sheet_pattern", Kind.TEXT, "Sheet pattern", nullable=True,
                   help="A regex matching several sheets, concatenated in workbook "
                        "order — Shopee income splits across 'Doanh thu', "
                        "'Doanh thu - 1', … Cannot be set together with an exact "
                        "sheet: the reader prefers the pattern and would silently "
                        "ignore the name."),
            Column("header_row", Kind.INT, "Header row", default=1,
                   help="1-based row holding the real (leaf) headers. Shopee income "
                        "has two band rows above it, so 3."),
            Column("skip_rows_after_header", Kind.INT, "Rows to skip", default=0,
                   help="Junk rows directly under the header. TikTok orders carry a "
                        "per-column description row."),
            Column("reader_engine", Kind.ENUM, "Excel engine", nullable=True,
                   options=_ENGINES,
                   help="calamine reads exports with a broken <dimension> tag that "
                        "openpyxl truncates to one column."),
            Column("date_format", Kind.TEXT, "Date format", nullable=True,
                   help="How this export spells a date, e.g. %Y/%m/%d for "
                        "2026/07/01 or %d-%b-%Y for 03-Jul-2026. Leave empty to let "
                        "the reader work it out. Setting it is safer: left to "
                        "itself the reader decides from the FIRST date in the file, "
                        "so the same setting can read one month correctly and the "
                        "next month's day and month the wrong way round."),
        ),
        order_by="sort_order, platform, kind",
        grouped_by="platform",
    ),

    "config_column_maps": TableSpec(
        name="config_column_maps",
        title="Export column names",
        blurb="How each export's headers map to what the pipeline calls things. "
              "This is what a monthly format change touches, and where a mistake "
              "moves money. When a header drifts, add the new spelling as a "
              "PARALLEL row and retire the old one rather than deleting it — older "
              "windows still re-run.",
        key=(_PLATFORM_KEY,
             Column("kind", Kind.TEXT, "Export kind"),
             Column("raw_header", Kind.TEXT, "Header in the export")),
        columns=(
            Column("canonical", Kind.ENUM, "What the pipeline calls it",
                   help="Chosen from the closed set of names src/ingest.py "
                        "understands."),
            Column("active", Kind.BOOL, "Still in use", default=True,
                   help="Off means the platform stopped writing this spelling. The "
                        "row stays for provenance and is not rendered into the "
                        "contract."),
            Column("retired_at", Kind.DATE, "Retired on", nullable=True,
                   help="When this spelling stopped appearing. Only for a row that "
                        "is no longer in use."),
        ),
        order_by="sort_order, platform, kind, raw_header",
        grouped_by="platform",
    ),

    "config_stores": TableSpec(
        name="config_stores",
        title="Store roster",
        blurb="Which storefronts a window must contain. A mismatch stops the run — "
              "the check that caught a real window arriving with 16 of 17 stores "
              "absent.",
        key=(_PLATFORM_KEY, Column("store", Kind.TEXT, "Storefront")),
        columns=(
            Column("optional", Kind.BOOL, "Optional", default=False,
                   help="An optional storefront WARNS when it is absent instead of "
                        "stopping the run. For one that legitimately does not trade "
                        "in every window — onboarded mid-month, or a three-day "
                        "window whose income export is header-only."),
            Column("active", Kind.BOOL, "In the roster", default=True,
                   help="Off takes it out of the contract without losing the row "
                        "or the reason it was there."),
        ),
        order_by="sort_order, platform, store",
        grouped_by="platform",
        # See migration 008: the roster decides whether a run STOPS, not what a
        # cell holds, so adding one cannot move a golden generated without it.
        invalidates_goldens=False,
    ),

    "config_store_aliases": TableSpec(
        name="config_store_aliases",
        title="Store name aliases",
        blurb="When one storefront appears under two spellings. An alias reassigns "
              "a whole file's rows to a different storefront, so every entry needs "
              "a reason.",
        key=(_PLATFORM_KEY,
             Column("raw", Kind.TEXT, "Name as it appears in the file")),
        columns=(
            Column("canonical", Kind.TEXT, "Real storefront", nullable=True,
                   help="Chosen from the roster. Leave it empty for 'nobody has "
                        "decided yet' — the run then warns on sight rather than "
                        "guessing."),
        ),
        order_by="sort_order, platform, raw",
        grouped_by="platform",
    ),

    "config_store_brands": TableSpec(
        name="config_store_brands",
        title="Storefront to brand",
        blurb="The brand a storefront invoices under. Two mappings exist today and "
              "disagree; only rows marked as part of the pipeline contract are read "
              "by a settlement run.",
        key=(_PLATFORM_KEY, Column("store", Kind.TEXT, "Storefront")),
        columns=(
            Column("brand", Kind.TEXT, "Brand"),
            Column("confidence", Kind.ENUM, "Confidence", default="confirmed",
                   options=(("confirmed", "Confirmed"),
                            ("needs_confirmation", "Needs confirmation"))),
            Column("in_pipeline_contract", Kind.BOOL, "Used by the pipeline",
                   default=False,
                   help="Off means the month-end master reads it and a settlement "
                        "run does not. Turning these on for the 60 imported rows "
                        "would change the brand of 28 storefronts — its own "
                        "reviewed change, with the delta stated in advance."),
        ),
        order_by="sort_order, platform, store",
        grouped_by="platform",
    ),

    "config_invoice_buckets": TableSpec(
        name="config_invoice_buckets",
        title="Invoice buckets",
        blurb="Which invoice bucket a storefront's lines land in. The match text "
              "is compared against the lowercased store name, first hit wins; the "
              "entry with no match text is the catch-all for every store nothing "
              "matches. The workbook's tabs themselves are fixed to the team's "
              "template — naming a bucket the template has no tab for stops the "
              "run with a sentence, and removing a catch-all does the same.",
        key=(_PLATFORM_KEY,
             Column("needle", Kind.TEXT, "Match text", nullable=True,
                    help="A fragment of the store name, e.g. 'kao'. Leave it empty "
                         "to address the platform's catch-all entry.")),
        columns=(
            Column("bucket", Kind.TEXT, "Bucket",
                   help="Must be a bucket the workbook template lays out a tab "
                        "for; anything else stops the next run rather than "
                        "leaking money into a drift breach."),
        ),
        order_by="sort_order, platform, needle",
        grouped_by="platform",
    ),

    "config_tolerances": TableSpec(
        name="config_tolerances",
        title="Money tolerances",
        blurb="How far a check may be off before it is reported as a variance. "
              "Every one was read out of the team's own workbook formulas. Widening "
              "one to make a check pass is how the original checks became worthless "
              "— diff first and understand the number.",
        key=(Column("platform", Kind.ENUM, "Platform", nullable=True,
                    options=tuple((p, p) for p in PLATFORMS)),
             Column("name", Kind.TEXT, "Check")),
        columns=(Column("vnd", Kind.MONEY_VND, "Tolerance"),),
        order_by="sort_order, platform, name",
        grouped_by="platform",
        may_insert=False, may_delete=False,
        closed_reason=(
            "Each row is a number src/tieout.py reads by name. A tolerance nothing "
            "reads is inert config — exactly what Phase 1.1 deleted three of. "
            "Adding one means adding the check that reads it."),
        # See migration 008: read by src/tieout.py, which reports variances and
        # writes no workbook cell.
        invalidates_goldens=False,
    ),

    "config_settlement_bounds": TableSpec(
        name="config_settlement_bounds",
        title="Settlement window bounds",
        blurb="Deduplication of a PULL ARTIFACT, never a rule. Add a window only "
              "when a raw export was pulled with the wrong dates AND its "
              "out-of-window rows are proven to be in the adjacent window already. "
              "Rows carrying no settlement date are always kept and reported.",
        key=(Column("period", Kind.TEXT, "Window"),),
        columns=(
            Column("from_date", Kind.DATE, "From", nullable=True),
            Column("to_date", Kind.DATE, "To", nullable=True),
        ),
        order_by="sort_order, period",
    ),

    "config_fee_types": TableSpec(
        name="config_fee_types",
        title="Fee types",
        blurb="Which invoice bucket each Lazada fee name falls into. The snapshot "
              "fallback for the team-owned master, which still wins at runtime.",
        key=(Column("fee_name", Kind.TEXT, "Fee name"),),
        columns=(Column("bucket", Kind.TEXT, "Bucket"),
                 Column("status", Kind.TEXT, "Status", nullable=True)),
        order_by="sort_order, fee_name",
    ),

    "config_fee_buckets": TableSpec(
        name="config_fee_buckets",
        title="Ledger bucket roles",
        blurb="Which Lazada ledger bucket is revenue, and which ones net into the "
              "invoiced unit price as promotions. Removing a promotion bucket "
              "OVER-states every affected invoice line — the credit stops being "
              "subtracted — so every change here needs its reason.",
        key=(Column("platform", Kind.ENUM, "Platform",
                    options=(("lazada", "lazada"),)),
             Column("role", Kind.ENUM, "Role",
                    options=(("revenue", "Revenue — the lines that are invoiced"),
                             ("promo", "Promotion — nets into the unit price"))),
             Column("bucket", Kind.TEXT, "Bucket name",
                    help="As the fee-type mapping spells it, e.g. '1.Doanh Thu'.")),
        columns=(),
        order_by="sort_order, platform, role, bucket",
    ),

    "config_vat_sku": TableSpec(
        name="config_vat_sku",
        title="Per-SKU VAT exceptions",
        blurb="The snapshot fallback for the team-owned master. Worth knowing: "
              "these SKUs match none of the SKUs traded in any sampled window, so "
              "this override has never fired and everything invoices at the default.",
        key=(Column("sku", Kind.TEXT, "SKU"),),
        columns=(Column("rate", Kind.NUMBER, "VAT factor"),),
        order_by="sort_order, sku",
    ),
}


# ---------------------------------------------------------------------------
# One edit
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RowEdit:
    table: str
    op: str
    key: dict[str, Any]
    values: dict[str, Any]
    evidence: str | None = None

    @classmethod
    def parse(cls, raw: Any) -> "RowEdit":
        if not isinstance(raw, dict):
            raise RowEditError(f"an edit must be an object, not {type(raw).__name__}")
        table = str(raw.get("table") or "")
        if table not in TABLES:
            raise RowEditError(
                f"unknown config table {table!r}; expected one of {sorted(TABLES)}")
        op = str(raw.get("op") or "")
        if op not in OPS:
            raise RowEditError(
                f"unknown edit operation {op!r}; expected one of {list(OPS)}")
        key = raw.get("key")
        if not isinstance(key, dict) or not key:
            raise RowEditError(
                f"an edit to {TABLES[table].title} needs the key of the row it "
                f"changes")
        values = raw.get("values") or {}
        if not isinstance(values, dict):
            raise RowEditError(f"`values` for {TABLES[table].title} must be an object")
        evidence = raw.get("evidence")
        evidence = str(evidence).strip() if evidence is not None else None
        return cls(table=table, op=op, key=dict(key), values=dict(values),
                   evidence=evidence or None)

    def as_json(self) -> dict:
        """The wire form, for `config_proposals.edits` — so a stale proposal can be
        replayed against current rows instead of retyped from memory."""
        return {"table": self.table, "op": self.op, "key": self.key,
                "values": self.values, "evidence": self.evidence}

    @property
    def spec(self) -> TableSpec:
        return TABLES[self.table]

    def key_text(self) -> str:
        """The row, in the operator's words. Never a dotted path."""
        return " · ".join(
            str(self.key[c]) if self.key.get(c) is not None else "—"
            for c in self.spec.key_names)

    def describe(self) -> str:
        spec = self.spec
        if self.op == "delete":
            return f"{spec.title}: remove {self.key_text()}"
        if not self.values:
            return f"{spec.title}: {self.key_text()} — reason updated"
        changes = ", ".join(
            f"{(spec.column(k).label if spec.column(k) else k).lower()} → {_render(v)}"
            for k, v in self.values.items())
        return f"{spec.title}: {self.key_text()} — {changes}"


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None or value == "":
        return "empty"
    return str(value)


def parse_all(raw_edits: Any) -> list[RowEdit]:
    if not isinstance(raw_edits, list) or not raw_edits:
        raise RowEditError("edits must be a non-empty list")
    if len(raw_edits) > 200:
        raise RowEditError(
            f"{len(raw_edits)} edits in one proposal is not reviewable. Split it — "
            f"the point of a proposal is that somebody reads the diff.")
    return [RowEdit.parse(e) for e in raw_edits]


def summarise(edits: list[RowEdit]) -> str:
    return "; ".join(e.describe() for e in edits)


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------

def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _coerce(column: Column, value: Any) -> Any:
    if _blank(value):
        if column.nullable:
            return None
        if column.default is not None:
            return column.default
        raise RowEditError(f"{column.label} cannot be empty")

    if column.kind == Kind.BOOL:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
        raise RowEditError(f"{column.label} must be on or off, not {value!r}")

    if column.kind == Kind.INT:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise RowEditError(
                f"{column.label} must be a whole number, not {value!r}") from None

    if column.kind in (Kind.NUMBER, Kind.MONEY_VND):
        try:
            number = float(str(value).strip().replace(",", ""))
        except (TypeError, ValueError):
            raise RowEditError(
                f"{column.label} must be a number, not {value!r}") from None
        if column.kind == Kind.MONEY_VND and number < 0:
            raise RowEditError(f"{column.label} cannot be negative")
        return number

    if column.kind == Kind.DATE:
        if isinstance(value, dt.date):
            return value
        try:
            return dt.date.fromisoformat(str(value).strip())
        except ValueError:
            raise RowEditError(
                f"{column.label} must be a date as YYYY-MM-DD, not "
                f"{value!r}") from None

    if column.kind == Kind.ENUM and column.options:
        text = str(value).strip()
        allowed = [v for v, _ in column.options]
        if text not in allowed:
            raise RowEditError(
                f"{column.label} must be one of {allowed}, not {text!r}")
        return text

    return str(value).strip()


def _coerce_scalar_value(existing: Any, value: Any) -> Any:
    """A scalar keeps the TYPE it was seeded with.

    `config_scalars.value` is jsonb, so nothing at the database level stops
    `dedupe_rows` becoming the string `"false"` — which is truthy in Python and would
    silently invert the flag, dropping legitimate duplicate order lines and
    understating revenue. The seeded row is the type declaration; this is where it
    is enforced.
    """
    if isinstance(existing, bool):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
        raise RowEditError(f"this setting is on or off; {value!r} is neither")

    if isinstance(existing, (int, float)):
        try:
            number = float(str(value).strip())
        except (TypeError, ValueError):
            raise RowEditError(
                f"this setting is a number; {value!r} is not") from None
        if isinstance(existing, int) and number == int(number):
            return int(number)
        return number

    if isinstance(existing, list):
        if not isinstance(value, list):
            raise RowEditError("this setting is a list of values")
        # A list keeps its ELEMENT type too. `vat_factors.rates` is a list of
        # numbers, and the browser control submits text — "1.05" stored as a string
        # would compare equal to no `vat_factor.round(2)` and silently zero every
        # per-rate row, the same shape as `dedupe_rows` becoming "false".
        if existing and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            for v in existing):
            numbers = []
            for v in value:
                try:
                    numbers.append(float(str(v).strip()))
                except (TypeError, ValueError):
                    raise RowEditError(
                        f"this setting is a list of numbers; {v!r} is not") from None
            return numbers
        return [str(v).strip() for v in value if str(v).strip()]

    if isinstance(value, (list, dict)):
        raise RowEditError("this setting holds a single value, not a list or a map")
    text = str(value).strip()
    if not text:
        raise RowEditError("this setting cannot be emptied")
    return text


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

# Columns that exist on some tables only and are served to the editor when they do.
_EXTRA = {
    "config_scalars": ("reader", "label", "help", "locked", "locked_reason"),
    "config_tolerances": ("reader",),
}


def read_rows(conn, table: str) -> list[dict]:
    """Every row of one config table, in render order, each with its own evidence."""
    spec = TABLES[table]
    columns = (list(spec.key_names) + list(spec.column_names)
               + ["evidence", "changed_by", "changed_at", "source",
                  "invalidates_goldens"]
               + list(_EXTRA.get(table, ())))
    with conn.cursor() as cur:
        cur.execute(f"select {', '.join(columns)} from {spec.name} "   # noqa: S608
                    f"order by {spec.order_by}")
        names = [d.name for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _where(spec: TableSpec) -> str:
    # `is not distinct from` rather than `=`, because config_tolerances.platform is
    # nullable and a file-wide tolerance would otherwise match no row and read as
    # "not there" rather than as itself.
    return " and ".join(f"{c} is not distinct from %s" for c in spec.key_names)


def _existing(conn, spec: TableSpec, key: dict) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(f"select * from {spec.name} where {_where(spec)}",   # noqa: S608
                    tuple(key.get(c) for c in spec.key_names))
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d.name for d in cur.description], row))


def _roster(conn, platform: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("select store from config_stores where platform = %s and active",
                    (platform,))
        return {r[0] for r in cur.fetchall()}


def canonical_fields(conn) -> list[str]:
    """The closed set of names a column map may target, derived from the pipeline.

    Deliberately not a table: a table would be a second definition of what the
    pipeline understands, free to drift from the code that consumes it (007). It is
    the right-hand side of every column-map dropdown, so being wrong here is
    expensive.
    """
    from src.ingest import DATE_COLUMNS, NUMERIC_COLUMNS, REQUIRED_COLUMNS

    names: set[str] = set()
    for table in (REQUIRED_COLUMNS, NUMERIC_COLUMNS, DATE_COLUMNS):
        for columns in table.values():
            names.update(columns)
    with conn.cursor() as cur:
        cur.execute("select distinct canonical from config_column_maps")
        names.update(r[0] for r in cur.fetchall())
    # Set by the pipeline itself, never by a map.
    names -= {"store", "source_file"}
    return sorted(names)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_key(spec: TableSpec, key: dict) -> dict:
    unknown = sorted(set(key) - set(spec.key_names))
    if unknown:
        raise RowEditError(
            f"{spec.title}: {unknown} does not identify a row here; the key is "
            f"{list(spec.key_names)}")
    out: dict[str, Any] = {}
    for column in spec.key:
        if column.name not in key or _blank(key[column.name]):
            if column.nullable:
                out[column.name] = None
                continue
            raise RowEditError(f"{spec.title}: {column.label} is missing")
        out[column.name] = _coerce(column, key[column.name])
    return out


def _validate_row(conn, spec: TableSpec, key: dict, values: dict,
                  existing: dict | None) -> dict:
    """Coerce and police one row's non-key columns. Returns what to write."""
    unknown = sorted(set(values) - set(spec.column_names))
    if unknown:
        raise RowEditError(
            f"{spec.title}: {unknown} is not something this form changes")

    out: dict[str, Any] = {}
    for column in spec.columns:
        if column.name in values:
            if spec.name == "config_scalars" and column.name == "value":
                out[column.name] = _coerce_scalar_value(
                    existing["value"] if existing else None, values[column.name])
            else:
                out[column.name] = _coerce(column, values[column.name])
        elif existing is None:
            # A new row takes the declared default; a column with neither a default
            # nor a value is required, and saying so beats a not-null violation.
            if column.nullable or column.default is not None:
                out[column.name] = column.default
            else:
                raise RowEditError(f"{spec.title}: {column.label} is required")

    _table_rules(conn, spec, key, out, existing)
    return out


def _table_rules(conn, spec: TableSpec, key: dict, values: dict,
                 existing: dict | None) -> None:
    """The refusals that are about meaning rather than about types.

    Every one of these is also a database constraint where it can be — this layer
    exists so the operator reads a sentence rather than a constraint name.
    """
    merged = {**(existing or {}), **values}

    if spec.name == "config_column_maps":
        allowed = canonical_fields(conn)
        canonical = merged.get("canonical")
        if canonical and canonical not in allowed:
            raise RowEditError(
                f"{canonical!r} is not a name src/ingest.py understands. A column "
                f"map may only target the pipeline's own vocabulary — mapping a "
                f"header to an invented name produces a column nothing reads. "
                f"Choose from: {', '.join(allowed)}")
        if merged.get("retired_at") and merged.get("active"):
            raise RowEditError(
                "a retired spelling cannot still be in use — turn 'still in use' "
                "off, or clear the retirement date")

    if spec.name == "config_reading":
        if merged.get("sheet_name") and merged.get("sheet_pattern"):
            raise RowEditError(
                "set an exact sheet OR a sheet pattern, not both: read_parts "
                "prefers the pattern and would silently ignore the exact name, "
                "which is the 'configured and inert' state this table exists to "
                "make impossible")
        if int(merged.get("header_row") or 1) < 1:
            raise RowEditError("the header row is 1-based, so it cannot be below 1")
        if int(merged.get("skip_rows_after_header") or 0) < 0:
            raise RowEditError("rows to skip cannot be negative")

    if spec.name == "config_store_aliases":
        raw, canonical = key.get("raw"), merged.get("canonical")
        if canonical and canonical == raw:
            raise RowEditError(
                "an alias to itself is a no-op that reads as a decision")
        roster = _roster(conn, str(key.get("platform")))
        if canonical and roster and canonical not in roster:
            raise RowEditError(
                f"{canonical!r} is not in the {key.get('platform')} roster, so this "
                f"alias would point a file at a storefront no run expects. Add the "
                f"storefront first — in this same proposal, which is what a basket "
                f"of edits is for.")

    if spec.name == "config_settlement_bounds":
        start, end = merged.get("from_date"), merged.get("to_date")
        if start is None and end is None:
            raise RowEditError(
                "a window bound needs a from date, a to date, or both")
        if start and end and start > end:
            raise RowEditError("the from date is after the to date")

    if spec.name == "config_vat_sku" and float(merged.get("rate") or 0) <= 0:
        raise RowEditError("a VAT factor must be greater than zero")

    if spec.name == "config_fee_buckets" and key.get("role") == "revenue" \
            and existing is None:
        with conn.cursor() as cur:
            cur.execute("""select bucket from config_fee_buckets
                           where platform = %s and role = 'revenue'""",
                        (key.get("platform"),))
            held = [r[0] for r in cur.fetchall()]
        if held:
            raise RowEditError(
                f"{key.get('platform')} already names {held[0]!r} as its revenue "
                f"bucket. Revenue is ONE bucket — two would make every invoice "
                f"line ambiguous. Remove the current one in this same proposal if "
                f"the ledger's vocabulary really changed.")


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------

# `config_settlement_bounds` enforces `length(trim(evidence)) >= 40` in the schema,
# because a bound silently drops settlement rows from a finance file and its
# evidence is the proof rather than a helpful note. Everywhere else a sentence is
# enough, and this is the number that makes "a reason" mean something.
MIN_EVIDENCE = 10
MIN_EVIDENCE_BY_TABLE = {"config_settlement_bounds": 40}


def apply(conn, edits: list[RowEdit], *, who: str, source: str = "proposal") -> None:
    """Apply every edit to the config tables. Does NOT commit — the caller owns that.

    Order is preserved as given: an operator who adds a storefront and then the
    alias pointing at it expects those in that order, and the alias's roster check
    depends on it.
    """
    for edit in edits:
        spec = edit.spec
        key = _validate_key(spec, edit.key)
        existing = _existing(conn, spec, key)

        if spec.name == "config_scalars" and existing and existing.get("locked"):
            raise RowEditError(
                f"{existing.get('label') or key['key']} is not editable through this "
                f"form. {existing.get('locked_reason') or ''}".strip())

        if edit.op == "delete":
            if existing is None:
                raise RowEditError(
                    f"{spec.title}: there is no {edit.key_text()} to remove")
            if not spec.may_delete:
                raise RowEditError(
                    f"{spec.title} does not allow removing a row. "
                    f"{spec.closed_reason}")
            with conn.cursor() as cur:
                cur.execute(
                    f"delete from {spec.name} where {_where(spec)}",   # noqa: S608
                    tuple(key[c] for c in spec.key_names))
            continue

        if existing is None:
            if not spec.may_insert:
                raise RowEditError(
                    f"{spec.title} does not allow adding a row. "
                    f"{spec.closed_reason}")
            values = _validate_row(conn, spec, key, edit.values, existing)
            _require_evidence(spec, edit)
            _insert(conn, spec, key, values, edit.evidence or "", who, source)
        else:
            values = _validate_row(conn, spec, key, edit.values, existing)
            if not values and edit.evidence is None:
                raise RowEditError(
                    f"{spec.title}: {edit.key_text()} — nothing to change")
            _update(conn, spec, key, values, edit.evidence, who, source)


def _require_evidence(spec: TableSpec, edit: RowEdit) -> None:
    if not spec.require_evidence:
        return
    minimum = MIN_EVIDENCE_BY_TABLE.get(spec.name, MIN_EVIDENCE)
    if len((edit.evidence or "").strip()) < minimum:
        raise RowEditError(
            f"{spec.title}: adding {edit.key_text()} needs a reason of at least "
            f"{minimum} characters. It is what makes the entry defensible in six "
            f"months, and it is stored against the row rather than as a comment "
            f"that can end up captioning its neighbour.")


def _bind(spec: TableSpec, name: str, value: Any) -> Any:
    """One value, ready for psycopg.

    `config_scalars.value` is jsonb and holds a bool, a number, a string or a small
    list. Passing a Python float straight at it is a type mismatch the server
    refuses, and passing a JSON *string* would store `"1.1"` rather than `1.1` —
    which the renderer would then emit as a quoted VAT factor.
    """
    column = spec.column(name)
    if column is not None and column.kind == Kind.JSON:
        return Jsonb(value)
    return value


def _insert(conn, spec: TableSpec, key: dict, values: dict, evidence: str,
            who: str, source: str) -> None:
    columns = (list(spec.key_names) + list(values)
               + ["evidence", "changed_by", "source", "invalidates_goldens"])
    params = ([key[c] for c in spec.key_names]
              + [_bind(spec, c, v) for c, v in values.items()]
              + [evidence, who, source, spec.invalidates_goldens])
    placeholders = ", ".join(["%s"] * len(params))
    with conn.cursor() as cur:
        # sort_order is APPENDED and never renumbered. Rendering has to be
        # byte-stable for a given row set or `config_versions` mints a version per
        # render and pin de-duplication stops working (007). Where a new row lands
        # in a rendered list is not meaning: expected_stores and file_formats are
        # both consumed as sets.
        cur.execute(
            f"insert into {spec.name} ({', '.join(columns)}, sort_order) "  # noqa: S608
            f"values ({placeholders}, "
            f"(select coalesce(max(sort_order), -1) + 1 from {spec.name}))",
            tuple(params))


def _update(conn, spec: TableSpec, key: dict, values: dict, evidence: str | None,
            who: str, source: str) -> None:
    sets = [f"{c} = %s" for c in values]
    params: list[Any] = [_bind(spec, c, v) for c, v in values.items()]
    if evidence is not None:
        sets.append("evidence = %s")
        params.append(evidence)
    # changed_by/changed_at/source are not optional on a write: a row whose value
    # moved and whose author did not is a row nobody can attribute.
    sets += ["changed_by = %s", "changed_at = now()", "source = %s"]
    params += [who, source]
    params += [key[c] for c in spec.key_names]
    with conn.cursor() as cur:
        cur.execute(
            f"update {spec.name} set {', '.join(sets)} "                # noqa: S608
            f"where {_where(spec)}", tuple(params))
        if cur.rowcount != 1:                                   # pragma: no cover
            raise RowEditError(
                f"{spec.title}: {list(key.values())} matched {cur.rowcount} rows")


# ---------------------------------------------------------------------------
# What a set of edits would produce
# ---------------------------------------------------------------------------

def render_after(conn, edits: list[RowEdit], *, who: str) -> str:
    """The contract as it would read after these edits. Changes nothing.

    Applying and rolling back rather than simulating: a simulation is a second
    implementation of the write, free to disagree with it, and the diff an operator
    approves has to be the diff they get. The same argument that keeps the worker
    writing artifacts through `write_artifacts` (D31).
    """
    from . import config_render

    rendered: str | None = None
    try:
        with conn.transaction() as tx:
            apply(conn, edits, who=who, source="proposal")
            rendered = config_render.render(conn)
            raise psycopg.Rollback(tx)
    except psycopg.Rollback:                                    # pragma: no cover
        pass
    assert rendered is not None                                 # noqa: S101
    return rendered


def payload(conn) -> list[dict]:
    """Every table, its columns and its rows, ready for JSON. The editor's input.

    **No user ever sees a table name, a column name or a dotted path.** Those exist
    in the wire format because the API has to name what is being changed; every
    string the UI renders is a `label`, a `title`, a `blurb` or a `help`, and
    `test_a_wire_name_is_never_part_of_the_rendered_payload` holds that line.

    **Evidence comes from the row.** That is the whole difference from the M6
    payload, which read comment blocks out of the file and could therefore only
    caption a top-level key — so the roster's evidence appeared against all 42
    stores in it. One alias's justification now travels with that alias, and is
    deleted with it.
    """
    dynamic = {"config_column_maps": {"canonical": tuple(
        (name, name) for name in canonical_fields(conn))}}

    out: list[dict] = []
    for name, spec in TABLES.items():
        options = dynamic.get(name, {})
        rows: list[dict] = []
        for row in read_rows(conn, name):
            rows.append({
                "key": {c: _jsonable(row[c]) for c in spec.key_names},
                "values": {c: _jsonable(row[c]) for c in spec.column_names},
                "evidence": row.get("evidence") or "",
                "changed_by": row.get("changed_by"),
                "changed_at": _jsonable(row.get("changed_at")),
                "source": row.get("source"),
                "invalidates_goldens": bool(row.get("invalidates_goldens")),
                # config_scalars carries its own label, help, reader and lock;
                # everywhere else those belong to the column, not the row.
                "label": row.get("label"),
                "help": row.get("help"),
                "reader": row.get("reader"),
                "locked": bool(row.get("locked")),
                "locked_reason": row.get("locked_reason") or "",
            })
        out.append({
            "table": name,
            "title": spec.title,
            "blurb": spec.blurb,
            "key": [_column_payload(c, options) for c in spec.key],
            "columns": [_column_payload(c, options) for c in spec.columns],
            "grouped_by": spec.grouped_by,
            "may_insert": spec.may_insert,
            "may_delete": spec.may_delete,
            "closed_reason": spec.closed_reason,
            "invalidates_goldens": spec.invalidates_goldens,
            "require_evidence": spec.require_evidence,
            "min_evidence": MIN_EVIDENCE_BY_TABLE.get(name, MIN_EVIDENCE),
            "rows": rows,
        })
    return out


def _column_payload(column: Column, dynamic: dict[str, tuple]) -> dict:
    options = dynamic.get(column.name, column.options)
    return {"name": column.name, "kind": column.kind, "label": column.label,
            "help": column.help, "nullable": column.nullable,
            "options": [{"value": v, "label": lab} for v, lab in options],
            "default": _jsonable(column.default)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Decimal):
        # `numeric` comes back as Decimal. A tolerance of 1000 must reach the wire
        # as 1000 and not as 1000.0: the editor echoes it straight back on the next
        # edit, and a float that renders differently would look like a change.
        number = float(value)
        return int(number) if number == int(number) else number
    return value


def invalidating(conn, edits: list[RowEdit]) -> list[str]:
    """Which of these edits can move a workbook cell, in the operator's words.

    Read off the row where one exists and off the table's declared default where it
    does not — never inferred from a path. An UNKNOWN still counts as invalidating,
    because a change no claim can be made about is exactly where defaulting to
    "harmless" turns a gate into a skip ([D26](../docs/06-DECISIONS.md#d26)).
    """
    out: list[str] = []
    for edit in edits:
        spec = edit.spec
        try:
            key = _validate_key(spec, edit.key)
        except RowEditError:
            out.append(spec.title)
            continue
        existing = _existing(conn, spec, key)
        flag = (existing.get("invalidates_goldens") if existing
                else spec.invalidates_goldens)
        if flag and spec.title not in out:
            out.append(spec.title)
    return out
