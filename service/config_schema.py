"""What `config/settings.yaml` contains, described well enough to render a form.

**The objection this has to answer.** The old config page argued *against* form
rendering, in its own words: "A form would show values stripped of the evidence for
them." That objection is correct. Half of `settings.yaml` is comments, and those
comments are the audit trail ([D2](../docs/06-DECISIONS.md#d2)) — an alias cites
the order-ID-overlap proof that justified it, a reader-engine choice cites the
specific malformed `<dimension>` tag it works around, a settlement bound cites the
mis-pulled export it deduplicates.

The answer is that **evidence is extracted, never copied.**
`config_store.evidence_for()` reads ruamel's own comment attribute out of *the same
bytes the form is editing* and hands each field its comment block verbatim. The
four-line VAT block renders directly above the box you type `1.10` into — strictly
more evidence at the point of decision than a `<pre>` where that comment sits 400
lines down and nobody scrolls. The verbatim file stays on the page underneath.

**No user ever sees a bracket.** A bare text input is the wrong affordance for two
thirds of this file and for a non-technical user it is worse than what it replaced.
Every section names a `widget` instead, and the dotted path exists only in the wire
format — it is never shown.

Two kinds of field are deliberately not editable, and both are recorded here rather
than silently omitted, because a field missing from a form is indistinguishable
from a field nobody thought about:

* `LOCKED` — `drop_unmapped_columns` is the PII control in two places, and its diff
  reads as an ordinary boolean flip rather than as "customer names and addresses
  now enter the pipeline". A privacy incident should not be two clicks.
* `DEAD` — `vat_rate` and `periods.rolling_window_months` are read by **nothing**.
  A control on a dead key invites an edit that appears to work and changes no
  behaviour, which is worse than no control.

`invalidates_goldens` is the input to the D26 verification run: applying a change
to any field carrying it triggers a re-run of a canary window under the new config,
compared cell-for-cell against a committed golden. See `service/verification.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PLATFORMS = ("tiktok", "shopee", "lazada")


class Widget:
    """The control a field renders as. A closed set — a new one is a UI change."""

    MONEY_VND = "money_vnd"           # number input, thousands separators, "VND"
    NUMBER = "number"                 # a bare number (a VAT factor)
    ENUM = "enum"                     # radio buttons with plain-English labels
    BOOL = "bool"                     # a toggle, with a sentence for each state
    TEXT = "text"                     # a short string
    STRING_LIST = "string_list"        # one row per value, ✕ to remove, "add" row
    STORE_ROSTER = "store_roster"     # one row per store, ✕ to remove, optional flag
    ALIAS_MAP = "alias_map"           # "name in the file" -> dropdown of the roster
    COLUMN_MAP = "column_map"         # "header in the export" -> dropdown of fields
    DATE_BOUNDS = "date_bounds"       # a window picker plus two date pickers
    PATTERN = "pattern"               # a regex plus a live filename tester
    LOCKED = "locked"                 # rendered disabled, with its reason
    DEAD = "dead"                     # read-only: "nothing reads this"


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]
    label: str
    widget: str
    # Which module reads it. Shown on every field: a setting whose reader cannot be
    # named is a setting nobody should be editing.
    reader: str
    help: str = ""
    # True where a change can move a cell in the finance workbook. Drives the
    # automatic verification run on apply (D26's replacement).
    invalidates_goldens: bool = False
    options: tuple[tuple[str, str], ...] = ()      # (value, plain-English label)
    # For BOOL: what on and off each mean, as a sentence rather than true/false.
    on_means: str = ""
    off_means: str = ""
    locked_reason: str = ""
    # New keys are permitted ONLY where the container is declared open AND this
    # names the reader that loops over it. See `allows_new_keys`.
    open_container: bool = False

    @property
    def dotted(self) -> str:
        """Wire format only. Never rendered — see the module docstring."""
        return ".".join(self.path)

    @property
    def editable(self) -> bool:
        return self.widget not in (Widget.LOCKED, Widget.DEAD)

    def allows_new_keys(self) -> bool:
        """Whether an operator may add a key inside this container.

        Stricter than `apply_edit`'s old rule, which was "the key must already
        exist" — a proxy for the real property. `vat_factors` is a closed mapping
        because `src/masters.py:144` reads exactly `.get("default")`, so adding a
        key there is refused where the old rule would have allowed it and produced
        config the pipeline silently ignores.
        """
        return self.open_container


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    blurb: str
    fields: tuple[Field, ...] = ()
    # Sections whose fields are generated per platform, so the schema does not
    # restate the same three entries three times and then drift on the fourth.
    per_platform: bool = False


# ---------------------------------------------------------------------------
# The canonical field names a column map may target
# ---------------------------------------------------------------------------

def canonical_fields(settings: dict) -> list[str]:
    """The closed set of names the pipeline understands, DERIVED not listed.

    The single biggest usability win in the editor: the right-hand side of a column
    map becomes a dropdown, so mapping a drifted header stops requiring anyone to
    know the pipeline's internal vocabulary.

    Derived from `src/ingest`'s own column lists plus every name already in use in
    the file, so it cannot drift from what the pipeline actually reads. A genuinely
    new canonical name needs a code change — which is correct, because a canonical
    name nothing reads is useless.
    """
    from src.ingest import DATE_COLUMNS, NUMERIC_COLUMNS, REQUIRED_COLUMNS

    names: set[str] = set()
    for table in (REQUIRED_COLUMNS, NUMERIC_COLUMNS, DATE_COLUMNS):
        for columns in table.values():
            names.update(columns)
    for platform_maps in (settings.get("column_maps") or {}).values():
        for kind_map in (platform_maps or {}).values():
            names.update(str(v) for v in (kind_map or {}).values())
    # Set by the pipeline itself, never by a map.
    names -= {"store", "source_file"}
    return sorted(names)


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------

def _tolerance_fields(settings: dict) -> tuple[Field, ...]:
    """Every tolerance actually present, so a per-platform addition appears without
    a schema edit — and so a tolerance that was REMOVED stops being offered."""
    out: list[Field] = []
    tolerances = settings.get("tolerances") or {}
    for key, value in tolerances.items():
        if isinstance(value, dict):
            for sub, _ in value.items():
                out.append(Field(
                    path=("tolerances", key, sub),
                    label=f"{key} · {sub.replace('_vnd', '').replace('_', ' ')}",
                    widget=Widget.MONEY_VND, reader="src/tieout.py",
                    help="A money tolerance in VND. Widening one to make a check "
                         "pass is how the original checks became worthless — "
                         "diff first and understand the number."))
        else:
            out.append(Field(
                path=("tolerances", key),
                label=key.replace("_vnd", "").replace("_", " "),
                widget=Widget.MONEY_VND, reader="src/tieout.py"))
    return tuple(out)


def _platform_kind_fields(settings: dict, top: str, *, label: str, widget: str,
                          reader: str, help_text: str,
                          invalidates: bool) -> tuple[Field, ...]:
    out: list[Field] = []
    for platform, kinds in (settings.get(top) or {}).items():
        for kind in (kinds or {}):
            out.append(Field(
                path=(top, platform, kind), label=f"{platform} · {kind} — {label}",
                widget=widget, reader=reader, help=help_text,
                invalidates_goldens=invalidates))
    return tuple(out)


def schema(settings: dict) -> list[Section]:
    """The sections, resolved against the file's current shape.

    Built from `settings` rather than hardcoded so a per-platform key that exists
    in the file is offered and one that does not is not — a form listing a
    tolerance nobody configured invites someone to configure it.
    """
    return [
        Section(
            key="vat", title="VAT", blurb=(
                "One default factor plus per-SKU exceptions from the team-owned "
                "master. The 8% concession reverting to 10% is a one-line change."),
            fields=(
                Field(path=("vat_factors", "default"), label="Default VAT factor",
                      widget=Widget.NUMBER, reader="src/masters.py:144",
                      invalidates_goldens=True,
                      help="1.08 today. Enter 1.10 when the concession ends. This is "
                           "a CLOSED mapping — per-SKU exceptions come from the "
                           "master file, not from here."),
                Field(path=("masters_file",), label="Team-owned master file",
                      widget=Widget.TEXT, reader="src/masters.py",
                      help="Read live at runtime; the committed CSV snapshots are "
                           "the fallback and drift is reported every run."),
                Field(path=("vat_rate",), label="vat_rate", widget=Widget.DEAD,
                      reader="nothing",
                      help="Read by NOTHING. It survives from the deleted "
                           "placeholder path. Editing it would appear to work and "
                           "change no behaviour."),
            )),

        Section(
            key="stores", title="Store roster", blurb=(
                "Which storefronts a window must contain. A mismatch stops the run — "
                "the check that caught a real window arriving with 16 of 17 stores "
                "absent. An optional store is one that legitimately appears in only "
                "some windows."),
            per_platform=True,
            fields=tuple(
                Field(path=("expected_stores", platform),
                      label=f"{platform} stores", widget=Widget.STORE_ROSTER,
                      reader="src/ingest.py:290 check_stores", open_container=True,
                      help="Add or remove a storefront. The 'optional' flag writes "
                           "into stores_optional rather than a second list you have "
                           "to keep in step.")
                for platform in PLATFORMS),
        ),

        Section(
            key="aliases", title="Store name aliases", blurb=(
                "When one storefront appears under two spellings. Every entry needs "
                "a reason, which is written into the file as a comment — that is what "
                "makes the alias defensible later."),
            per_platform=True,
            fields=tuple(
                Field(path=("store_aliases", platform),
                      label=f"{platform} aliases", widget=Widget.ALIAS_MAP,
                      reader="src/ingest.py:267 read_parts", open_container=True,
                      help="Left: the name as it appears in the file. Right: the "
                           "real store, chosen from the roster. TODO-HUMAN means "
                           "somebody still has to decide.")
                for platform in PLATFORMS),
        ),

        Section(
            key="columns", title="Export column names", blurb=(
                "How each export's headers map to what the pipeline calls things. "
                "This is the section a monthly format change touches, and the one "
                "where a mistake moves money — a change here triggers an automatic "
                "re-verification against a committed golden."),
            per_platform=True,
            fields=tuple(
                Field(path=("column_maps", platform, kind),
                      label=f"{platform} · {kind}", widget=Widget.COLUMN_MAP,
                      reader="src/ingest.py:212 read_parts", invalidates_goldens=True,
                      open_container=True,
                      help="Left: the header exactly as the export spells it. Right: "
                           "chosen from the closed set of names the pipeline "
                           "understands. When a header drifts, add the new spelling "
                           "as a PARALLEL entry rather than replacing the old one — "
                           "older windows still re-run.")
                for platform, kinds in ((p, (settings.get("column_maps") or {}).get(p) or {})
                                        for p in PLATFORMS)
                for kind in kinds),
        ),

        Section(
            key="filenames", title="Reading the store from a filename", blurb=(
                "TikTok and Shopee exports carry no store column, so the filename IS "
                "the store identity. These patterns have changed three times in four "
                "months; the tester below runs the real one against a name you paste."),
            fields=tuple(
                Field(path=("store_from_filename", platform),
                      label=f"{platform} filename pattern", widget=Widget.PATTERN,
                      reader="src/ingest.py:158 store_from_filename",
                      invalidates_goldens=True,
                      help="Group 1 is the store name. Paste a real filename to see "
                           "which store it resolves to before saving. Lazada's "
                           "pattern lives in src/lazada.py and is not editable here.")
                for platform in ("tiktok", "shopee")),
        ),

        Section(
            key="reading", title="How each file is read", blurb=(
                "Sheet names, header rows and Excel engines. Wrong values here do not "
                "produce an error — they produce a frame shifted by two rows, so a "
                "change triggers re-verification."),
            fields=(
                _platform_kind_fields(
                    settings, "sheet_names", label="sheet", widget=Widget.TEXT,
                    reader="src/ingest.py:175", invalidates=True,
                    help_text="The exact sheet name to read.")
                + _platform_kind_fields(
                    settings, "sheet_patterns", label="sheet pattern",
                    widget=Widget.TEXT, reader="src/ingest.py:180", invalidates=True,
                    help_text="A regex matching several sheets, concatenated in "
                              "workbook order — Shopee income splits across "
                              "'Doanh thu', 'Doanh thu - 1', …")
                + _platform_kind_fields(
                    settings, "header_rows", label="header row",
                    widget=Widget.NUMBER, reader="src/ingest.py:184", invalidates=True,
                    help_text="1-based row holding the real (leaf) headers. Shopee "
                              "income has two band rows above it, so 3.")
                + _platform_kind_fields(
                    settings, "skip_rows_after_header", label="rows to skip",
                    widget=Widget.NUMBER, reader="src/ingest.py:186", invalidates=True,
                    help_text="Junk rows directly under the header. TikTok orders "
                              "carry one.")
                + _platform_kind_fields(
                    settings, "reader_engine", label="Excel engine",
                    widget=Widget.ENUM, reader="src/ingest.py:148 read_excel_sheet",
                    invalidates=True,
                    help_text="calamine reads exports with a broken <dimension> tag "
                              "that openpyxl truncates to one column, and reads "
                              "Shopee in minutes rather than 45+.")
            )),

        Section(
            key="parsing", title="Number and date parsing", blurb=(
                "How amount and date cells are interpreted, and what happens to a "
                "cell that cannot be read at all."),
            fields=(
                Field(path=("number_style",), label="Amount format",
                      widget=Widget.ENUM, reader="src/ingest.py:84 to_number",
                      invalidates_goldens=True,
                      options=(("standard", "1,234,567.89 — comma thousands, dot decimal"),
                               ("vietnamese", "1.234.567,89 — dot thousands, comma decimal")),
                      help="Getting this wrong turns every amount into an unparseable "
                           "cell, which by default stops the run rather than silently "
                           "producing zeros."),
                Field(path=("numeric_coercion",), label="An amount that will not parse",
                      widget=Widget.ENUM, reader="src/ingest.py:125 report_unparseable",
                      options=(("hard_stop", "Stop the run and name the column and row count"),
                               ("warn", "Warn and keep going — the cell becomes 0 VND")),
                      help="'warn' is the old behaviour and is for someone who has "
                           "looked and decided. A settlement export never legitimately "
                           "contains an unparseable amount."),
                Field(path=("dedupe_rows",), label="Drop byte-identical duplicate rows",
                      widget=Widget.BOOL, reader="src/ingest.py:238 read_parts",
                      invalidates_goldens=True,
                      on_means="Identical rows across file parts are collapsed to one.",
                      off_means="Every row is kept. This is correct for the real "
                                "platforms: an order can legitimately contain two "
                                "identical SKU lines (duplicated gift items), and the "
                                "team's own Power Query never dedupes."),
            ) + tuple(
                Field(path=("dayfirst", platform),
                      label=f"{platform} dates are day-first",
                      widget=Widget.BOOL, reader="src/ingest.py:252",
                      invalidates_goldens=True,
                      on_means="31/12/2026 reads as 31 December.",
                      off_means="12/31/2026 reads as 31 December.")
                for platform in (settings.get("dayfirst") or {})
            )),

        Section(
            key="tolerances", title="Money tolerances", blurb=(
                "How far a check may be off before it is a variance. Every one of "
                "these was read out of the team's own workbook formulas — the comments "
                "cite the cell. Widening one to make a check pass is how the original "
                "checks became worthless."),
            fields=_tolerance_fields(settings)),

        Section(
            key="windows", title="Settlement window bounds", blurb=(
                "Deduplication of a pull artifact, NOT a rule. Add a window only when "
                "a raw export was pulled with the wrong dates AND its out-of-window "
                "rows are proven to be in the adjacent window already. The evidence "
                "goes in the reason."),
            fields=(
                Field(path=("window_settlement_bounds",),
                      label="Window date bounds", widget=Widget.DATE_BOUNDS,
                      reader="src/ingest.py:39 apply_settlement_bounds",
                      open_container=True, invalidates_goldens=True,
                      help="Rows with no settlement date are always KEPT and "
                           "reported — they cannot be attributed to a window."),
            )),

        Section(
            key="privacy", title="Privacy and file formats", blurb=(
                "The one control on this page that is deliberately not editable."),
            fields=(
                Field(path=("drop_unmapped_columns",),
                      label="Strip columns the contract does not name",
                      widget=Widget.LOCKED,
                      reader="src/ingest.py:224 and service/uploads.py",
                      locked_reason=(
                          "This is the PII control in two places: it is what stops "
                          "customer names, phone numbers and delivery addresses "
                          "reaching a DataFrame, and the upload sanitizer applies the "
                          "same rule at the door. Its diff reads as an ordinary "
                          "boolean flip rather than as a privacy incident, so it is "
                          "changed in a reviewed commit and not with two clicks."),
                      help="Currently on. Turning it off would let every unmapped "
                           "column — including customer names and addresses — into "
                           "the pipeline and onto disk."),
                Field(path=("file_formats",), label="Accepted file extensions",
                      widget=Widget.STRING_LIST, reader="src/ingest.py:170",
                      open_container=True,
                      help="Uploads are always stored as .xlsx regardless, so this "
                           "only governs what a window staged directly on disk may "
                           "contain."),
                Field(path=("periods", "rolling_window_months"),
                      label="rolling_window_months", widget=Widget.DEAD,
                      reader="nothing",
                      help="Read by NOTHING. Left visible rather than hidden, so it "
                           "is a known dead key rather than a forgotten one."),
            )),
    ]


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def all_fields(settings: dict) -> list[Field]:
    return [f for section in schema(settings) for f in section.fields]


def field_for(settings: dict, path: list[str] | tuple[str, ...]) -> Field | None:
    """The field governing this path, including a path INSIDE an open container.

    An edit to `expected_stores.tiktok.<n>` or `column_maps.shopee.income.<header>`
    is governed by the container's field — otherwise every operator action inside a
    list or a mapping would have no schema entry and would fall through whatever
    the default was.
    """
    wanted = tuple(path)
    fields = all_fields(settings)
    exact = {f.path: f for f in fields}
    if wanted in exact:
        return exact[wanted]
    # Longest declared prefix wins, so `column_maps.x.y` beats a hypothetical
    # `column_maps` entry.
    best: Field | None = None
    for f in fields:
        if wanted[:len(f.path)] == f.path and (best is None or len(f.path) > len(best.path)):
            best = f
    return best


def invalidates_goldens(settings: dict, paths: list[list[str]]) -> list[str]:
    """Which of these edits can move a workbook cell.

    Drives the automatic verification run on apply. An UNKNOWN path counts as
    invalidating: a path the schema does not describe is exactly the case where no
    claim can be made, and defaulting to "harmless" there is how a gate degrades
    into a skip — the failure that killed `oracle_rev` in M1
    ([D26](../docs/06-DECISIONS.md#d26)).
    """
    out: list[str] = []
    for path in paths:
        found = field_for(settings, path)
        if found is None or found.invalidates_goldens:
            out.append(".".join(path))
    return out


def payload(settings: dict, content: str) -> list[dict]:
    """The whole schema, with current values and extracted evidence, ready for JSON.

    `content` is the same bytes the form will edit, so the evidence shown is the
    evidence in the file being changed — not a copy that could be stale.
    """
    from . import config_store

    out: list[dict] = []
    for section in schema(settings):
        rendered: list[dict] = []
        for f in section.fields:
            rendered.append({
                "path": list(f.path),
                "dotted": f.dotted,
                "label": f.label,
                "widget": f.widget,
                "reader": f.reader,
                "help": f.help,
                "invalidates_goldens": f.invalidates_goldens,
                "options": [{"value": v, "label": lab} for v, lab in f.options],
                "on_means": f.on_means,
                "off_means": f.off_means,
                "locked_reason": f.locked_reason,
                "editable": f.editable,
                "allows_new_keys": f.allows_new_keys(),
                "value": _value_at(settings, f.path),
                # The comment block from the file itself, verbatim. This is the
                # whole answer to "a form would strip the evidence".
                "evidence": config_store.evidence_for(content, list(f.path)),
            })
        out.append({"key": section.key, "title": section.title, "blurb": section.blurb,
                    "per_platform": section.per_platform, "fields": rendered})
    return out


def _value_at(settings: dict, path: tuple[str, ...]) -> Any:
    node: Any = settings
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node
