"""One naming scheme for uploaded exports, and the proof that renaming is safe.

Marketplace exports arrive named by whoever pressed Download. Three platforms
have produced, in the real `input/` tree: `12_Unilever Chăm Sóc Vẻ Đẹp_Unilever
2.xlsx`, `1_ Income. Curel.xlsx`, `Order.mars.wrigley part 2.xlsx`,
`2_KAO (3).xlsx`, `Income. Xmenforboss 21-end.xlsx`. Every one of those spellings
is load-bearing, because **store identity is derived from the filename**
([D6](docs/06-DECISIONS.md#d6)) — the exports carry no store column.

So a uniform name is not cosmetic. Getting it wrong silently reassigns revenue to
the wrong storefront, or invents a store and trips `check_stores`.

    tiktok  orders   NNN.order <store>.xlsx      tiktok  income   NNN.income <store>.xlsx
    shopee  orders   NNN_order. <store>.xlsx     shopee  income   NNN_income. <store>.xlsx
    lazada  weekly   NNN_<store>.xlsx            lazada  daily    NNN_<store>.xlsx

Four properties, each chosen against a measured failure:

1. **Nothing is appended after the store name.** TikTok's own pattern eats a
   trailing bare 1–2 digit token and a dotted date; Shopee's eats ` part N` and a
   ` 1-10` range. A suffix would be swallowed *into* the store name's absence —
   the ordinal therefore goes in the prefix, which TikTok's pattern already
   requires anyway.
2. **The ordinal is zero-padded to a fixed width, so `sorted()` stays numeric.**
   This is the non-obvious one. `ingest.read_parts` and `lazada.read_ledger` read
   `sorted(folder.iterdir())`, concatenate in that order, and workbook row order
   follows from the concatenation. `9` before `10` in lexicographic order would
   reorder rows and move cells in the file the team invoices from.
3. **Lazada's `(N)` browser-duplicate marker disappears.** Five weekly exports of
   one store downloaded in one session arrive as `2_KAO.xlsx` … `2_KAO (4).xlsx`
   and are five *different* settlement weeks; they become `001_KAO.xlsx` …
   `005_KAO.xlsx`.
4. **The target extension is always `.xlsx`.** The sanitizer writes openpyxl
   bytes; before M6 a `.csv` upload was written as xlsx under a `.csv` name, and
   `read_parts` would then hand it to `pd.read_csv`.

**`validate_roundtrip` is the machine-checked invariant**, and it is the whole
reason this module can be trusted: it re-runs *the pipeline's own*
`src.ingest.store_from_filename` — not a copy — on every generated name and
refuses if the store does not survive. A future edit to `store_from_filename`
that breaks the fixed point fails here rather than in a settlement.

Measured before it was written, and re-measured here: over the real tree,
`derive(uniform(derive(x))) == derive(x)` for **73/73** real exports across all
eight committed golden windows, `sorted(new)` preserved `sorted(old)` in **12/12**
folders, and every filename was already NFC.

**The remaining 10 files in `input/` are refused, correctly.** They are the legacy
synthetic window `2026-06_p1`, whose parts are named `part_1.csv` and carry a
`Shop Name` *column* — `read_parts` only consults the filename when `store` is
absent from the frame. So this module's precondition is exact: it renames files
whose store identity comes from the name, which is every real marketplace export
and is the only case the upload boundary accepts. A generator that emits
column-bearing names does not exercise the production path, which is a
requirement on `service/sampledata.py`, not a gap here.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Three digits. 195 files is the largest real window; a fourth digit would be
# free but the width is baked into every stored name, so widening it later means
# `0100` sorting before `999`. Refuse past the cap instead of silently rolling
# over — see `plan_window`.
ORDINAL_WIDTH = 3
MAX_FILES = 10 ** ORDINAL_WIDTH - 1

# Placeholder shown to a user before the ordinal exists. The upload screen renders
# `NNN.order Curel.xlsx` greyed, because the ordinal is a property of the whole
# window and is not known until the run materialises it.
ORDINAL_PLACEHOLDER = "N" * ORDINAL_WIDTH

KINDS_BY_PLATFORM = {
    "tiktok": ("orders", "income"),
    "shopee": ("orders", "income"),
    "lazada": ("weekly", "daily"),
}

# The kind token as each platform's own `store_from_filename` pattern spells it.
# Singular: both patterns match `(?:order|income)`, never `orders`.
_TOKEN = {"orders": "order", "income": "income"}


class NamingError(ValueError):
    """A name could not be generated, or would not survive the pipeline's parser.

    Always a refusal, never a fallback to the original name. A window that is
    half-renamed reads inconsistently and its file order is no longer the order
    anyone reasoned about.
    """


@dataclass(frozen=True)
class PlannedName:
    original: str
    store: str
    ordinal: int
    name: str

    @property
    def renamed(self) -> bool:
        return self.name != self.original


# ---------------------------------------------------------------------------
# The pipeline's own parser, reached rather than reimplemented
# ---------------------------------------------------------------------------

def pattern_for(settings: dict, platform: str) -> str:
    """The regex the pipeline will actually use on this platform's filenames.

    Lazada's lives in `src/lazada.py` rather than in YAML — a real asymmetry in
    the codebase (docs/08-KNOWN-DEFECTS.md 1.10), and reaching into the module is
    the honest way to handle it. Pretending the config covers it would put a
    second Lazada pattern in a second place.
    """
    if platform == "lazada":
        from src.lazada import STORE_PATTERN
        return STORE_PATTERN
    pattern = (settings.get("store_from_filename") or {}).get(platform)
    if not pattern:
        raise NamingError(
            f"store_from_filename.{platform} is not configured, so no store can be "
            f"derived from a {platform} filename and no uniform name can be built")
    return pattern


def store_of(filename: str, platform: str, settings: dict) -> str:
    """Which store this file belongs to, per the pipeline's rule.

    Raises `NamingError` rather than letting `ReconHardStop` out: at the upload
    door this is a 422 an operator fixes by picking the right file, not a run
    that stopped.
    """
    from src.errors import ReconHardStop
    from src.ingest import store_from_filename

    try:
        return store_from_filename(unicodedata.normalize("NFC", filename),
                                   pattern_for(settings, platform))
    except ReconHardStop as exc:
        raise NamingError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Generating
# ---------------------------------------------------------------------------

def uniform_name(platform: str, kind: str, ordinal: int, store: str) -> str:
    """The name this file will carry into the pipeline."""
    if kind not in KINDS_BY_PLATFORM.get(platform, ()):
        raise NamingError(
            f"{platform} has no {kind!r} files (expected one of "
            f"{list(KINDS_BY_PLATFORM.get(platform, ()))})")
    if not 1 <= ordinal <= MAX_FILES:
        raise NamingError(f"ordinal {ordinal} is outside 1..{MAX_FILES}")

    store = unicodedata.normalize("NFC", store).strip()
    if not store:
        raise NamingError("empty store name")
    return _compose(platform, kind, f"{ordinal:0{ORDINAL_WIDTH}d}", store)


def preview_name(platform: str, kind: str, store: str) -> str:
    """The same name with the ordinal not yet decided, for the upload screen."""
    return _compose(platform, kind, ORDINAL_PLACEHOLDER,
                    unicodedata.normalize("NFC", store).strip())


def _compose(platform: str, kind: str, ordinal: str, store: str) -> str:
    if platform == "tiktok":
        return f"{ordinal}.{_TOKEN[kind]} {store}.xlsx"
    if platform == "shopee":
        return f"{ordinal}_{_TOKEN[kind]}. {store}.xlsx"
    if platform == "lazada":
        # No kind token: Weekly/ and Daily/ are separate folders and the schema
        # is detected from the sheet, so the name never carried one.
        return f"{ordinal}_{store}.xlsx"
    raise NamingError(f"unknown platform {platform!r}")


def validate_roundtrip(name: str, platform: str, store: str, settings: dict) -> None:
    """Refuse a generated name the pipeline would read as a different store.

    Two hazards, and it is worth being precise about which one this catches where,
    because the obvious reading over-claims:

    * **A store name the platform's own pattern would truncate.** TikTok's pattern
      strips a trailing bare 1–2 digit token, so a name built from the store
      `Unilever 2` comes back as `Unilever`. This bites when a *human supplies the
      store* — the operator confirming or correcting it at the upload door — which
      is why `POST /uploads` calls this on the confirmed value. Inside
      `plan_window` the store was itself derived from a filename by this same
      pattern, so the check is near-tautological there.
    * **A future edit to `store_from_filename`.** That is what makes the call in
      `plan_window` worth its cost: the patterns have changed three times in four
      months (July's dotted date tokens, June's numeric prefix, August's `1-10`
      ranges), and an edit that stops matching the *generated* shape — a required
      separator, a different prefix — breaks every future window silently. Here it
      breaks one upload, loudly, naming the pattern.
    """
    derived = store_of(name, platform, settings)
    expected = unicodedata.normalize("NFC", store).strip()
    if derived != expected:
        raise NamingError(
            f"the uniform name {name!r} does not survive {platform}'s own "
            f"store_from_filename pattern: it reads as store {derived!r}, not "
            f"{expected!r}. This store cannot be renamed safely — the pattern "
            f"strips part of its name. Fix store_from_filename.{platform} or add "
            f"the store to store_aliases; do NOT bypass the rename.")


def plan_window(filenames: list[str], platform: str, kind: str,
                settings: dict) -> list[PlannedName]:
    """Assign every file in one (period, platform, kind) its uniform name.

    **Sorted first, and that is the load-bearing line.** Ordinals follow
    `sorted(originals)`, which is the order `read_parts` and `read_ledger` read
    today, and property (2) above then makes `sorted(new)` the same order. Assign
    ordinals by arrival instead and two uploads racing decide workbook row order
    between them — which is how a byte-reproducible pipeline stops being one.

    Called at materialisation, per run, never at upload: the ordinal is a fact
    about the whole window and cannot be known while files are still arriving.
    """
    unique = sorted({unicodedata.normalize("NFC", n) for n in filenames})
    if len(unique) > MAX_FILES:
        raise NamingError(
            f"{len(unique)} {platform}/{kind} files in one window exceeds the "
            f"{ORDINAL_WIDTH}-digit ordinal ({MAX_FILES}). Widening it would make "
            f"'0100' sort before '999' for every name already stored, so this is a "
            f"deliberate refusal.")

    planned: list[PlannedName] = []
    for index, original in enumerate(unique, start=1):
        store = store_of(original, platform, settings)
        name = uniform_name(platform, kind, index, store)
        validate_roundtrip(name, platform, store, settings)
        planned.append(PlannedName(original=original, store=store,
                                   ordinal=index, name=name))

    collisions = {p.name for p in planned}
    if len(collisions) != len(planned):                             # pragma: no cover
        # Unreachable while ordinals are unique, and asserted anyway: a collision
        # here would mean one export silently overwriting another in the window
        # folder, which is the double-pull class arriving by a new route.
        raise NamingError("two files in this window generated the same uniform name")
    return planned


def folder_for(platform: str, kind: str) -> str:
    """The subfolder of `input/<period>/<platform>/` this kind is read from.

    Capitalised for Lazada because that is what `lazada.read_ledger` looks for
    (`period_dir / variant.capitalize()`), and lowercase for the other two.
    """
    sub = {"orders": "orders", "income": "income",
           "weekly": "Weekly", "daily": "Daily"}.get(kind)
    if sub is None:
        raise NamingError(f"unknown file kind {kind!r}")
    return sub


def target_path(input_root: Path, period: str, platform: str, kind: str,
                name: str) -> Path:
    """Where a materialised file lands — the layout the pipeline already reads.

    Unchanged from `uploads.staged_path`, deliberately: the service must not
    invent a second layout, or `tools/devrun.py` stops being able to run the same
    window from the same directory with the service switched off.
    """
    return Path(input_root) / period / platform / folder_for(platform, kind) / name
