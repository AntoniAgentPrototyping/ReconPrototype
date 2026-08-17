"""Applying several edits to `settings.yaml` as one change.

**Why several and not one.** `apply_edit` set a single dotted path, which meant
adding a store to the roster and its alias in the same breath was two proposals,
two approvals and two commits — so people would do it in one hand-edit instead, and
the audit trail would record nothing. A form that shows a whole section needs to
submit a whole section.

**Parse once, mutate one document, dump once.** That is what preserves byte-identity
under N edits: N sequential `apply_edit` calls would round-trip the file N times,
and while the round trip is measured identity on this file today, depending on it N
times is depending on it N times.

The five operations are deliberately not "set anything anywhere":

    set                a scalar leaf
    set_map_entry      a key inside an OPEN mapping (with an optional why-comment)
    remove_map_entry   ditto, removing
    append_list_item   an item in a list (with an optional why-comment)
    remove_list_item   ditto, removing

**The honesty rule is stricter than it was, not looser.** `apply_edit` refused a
new key on the grounds that "the key must already exist" — a proxy for the real
property. The real property is declared in `service/config_schema.py`: a new key is
permitted only where the container is marked `open_container`, *and that marking
names the module that loops over the container*. So `vat_factors` is closed, because
`src/masters.py:144` reads exactly `.get("default")` and any other key there is
config the pipeline silently ignores — refused here, where the old rule allowed it.

Verified against the real 411-line file before this was written (six canaries, now
regression tests in `tests/service/test_config_editor.py`): load→dump with no edit
is byte-identical and preserves all 200 comment lines; appending to a list leaves
interleaved comments at their offsets and everything outside the block
byte-identical; an appended note is written as an EOL comment on the new item;
removing a commented item takes its comment with it, 200→199; setting
`vat_factors.default` changes exactly one line.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import config_schema, config_store
from .config_store import ConfigEditError

# The operations a proposal may contain. A closed set: an operation not listed here
# has no schema rule governing it, and an ungoverned mutation of the domain
# contract is the thing this module exists to prevent.
OPS = ("set", "set_map_entry", "remove_map_entry", "append_list_item",
       "remove_list_item")


class OrphanedEvidence(ConfigEditError):
    """Removing this entry would leave its justification captioning a different one.

    A distinct exception rather than a generic refusal because the api turns it into
    a question the operator can answer, not a dead end. See `_comment_block_above`.
    """

    def __init__(self, message: str, *, block: list[str], target: str) -> None:
        super().__init__(message)
        self.block = block
        self.target = target


@dataclass(frozen=True)
class Edit:
    op: str
    path: tuple[str, ...]
    value: Any = None
    key: str | None = None
    # Written into the file as a comment beside the new entry. This is how an alias
    # keeps citing the order-ID-overlap proof that justified it.
    comment: str | None = None
    # What to do with a comment BLOCK sitting above an entry being removed:
    # "remove" (it described only this entry) or "keep" (it describes the group).
    # Required when such a block exists — see `_comment_block_above`.
    comment_disposition: str | None = None

    @classmethod
    def parse(cls, raw: dict) -> "Edit":
        op = str(raw.get("op") or "")
        if op not in OPS:
            raise ConfigEditError(
                f"unknown edit operation {op!r}; expected one of {list(OPS)}")
        path = tuple(str(p) for p in (raw.get("path") or ()))
        if not path:
            raise ConfigEditError("an edit with an empty path would rewrite the "
                                  "whole document; refused")
        if len(path) > 8:
            raise ConfigEditError(f"config path is implausibly deep: {'.'.join(path)}")
        key = raw.get("key")
        if op in ("set_map_entry", "remove_map_entry") and not str(key or "").strip():
            raise ConfigEditError(f"{op} needs the key it is setting or removing")
        comment = (str(raw["comment"]).strip() or None) if raw.get("comment") else None
        disposition = raw.get("comment_disposition")
        if disposition is not None and disposition not in ("keep", "remove"):
            raise ConfigEditError(
                f"comment_disposition must be 'keep' or 'remove', not {disposition!r}")
        return cls(op=op, path=path, value=raw.get("value"),
                   key=str(key) if key is not None else None, comment=comment,
                   comment_disposition=disposition)

    @property
    def dotted(self) -> str:
        return ".".join(self.path + ((self.key,) if self.key else ()))

    def describe(self) -> str:
        """One line for the proposal summary, in words rather than in wire format."""
        where = ".".join(self.path)
        if self.op == "set":
            return f"set {where} to {self.value!r}"
        if self.op == "set_map_entry":
            return f"in {where}, map {self.key!r} to {self.value!r}"
        if self.op == "remove_map_entry":
            return f"in {where}, remove {self.key!r}"
        if self.op == "append_list_item":
            return f"add {self.value!r} to {where}"
        return f"remove {self.value!r} from {where}"


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def _walk(data: Any, path: tuple[str, ...], *, create: bool = False) -> Any:
    node = data
    for i, key in enumerate(path):
        if not isinstance(node, dict):
            raise ConfigEditError(
                f"{'.'.join(path[:i])} is not a mapping, so {'.'.join(path)} cannot "
                f"be edited through this form")
        if key not in node:
            if not create:
                raise ConfigEditError(
                    f"no such config path: {'.'.join(path[:i + 1])}. This editor "
                    f"changes existing settings; it does not invent them.")
            node[key] = {}
        node = node[key]
    return node


def _try_walk(data: Any, path: tuple[str, ...]) -> Any:
    """The node at `path`, or None if any step is absent. Never raises."""
    node = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _check_may_add(settings: dict, path: tuple[str, ...], what: str) -> None:
    field = config_schema.field_for(settings, path)
    if field is None:
        # The schema may describe keys INSIDE this container without declaring the
        # container itself open — `vat_factors.default` is a field, `vat_factors` is
        # not. That is a deliberate closure, not an omission, and saying so is more
        # useful than "undescribed".
        inner = [f for f in config_schema.all_fields(settings)
                 if len(f.path) > len(path) and f.path[:len(path)] == path]
        if inner:
            raise ConfigEditError(
                f"{'.'.join(path)} is a closed {what}: {inner[0].reader} reads "
                f"specific keys from it, so a new one would be config the pipeline "
                f"silently ignores. Changing which keys exist there is a code change.")
        raise ConfigEditError(
            f"{'.'.join(path)} is not described in the config schema, so this editor "
            f"cannot safely add to it. Adding a key nothing reads produces config "
            f"the pipeline ignores, which then looks like a bug in the pipeline.")
    if not field.allows_new_keys():
        raise ConfigEditError(
            f"{'.'.join(path)} is a closed {what}: {field.reader} reads specific keys "
            f"from it, so a new one would be config the pipeline silently ignores. "
            f"Changing which keys exist there is a code change.")


def _open_container(data: Any, settings: dict, path: tuple[str, ...], *,
                    kind: type, what: str) -> Any:
    """The container at `path`, created if absent — but only where the schema says.

    Measured need, not speculation: `expected_stores.lazada` and
    `store_aliases.lazada` are **absent** from the real file (Lazada's roster has
    never been populated, which is why its store check is skipped). Without this, a
    form offering "add a Lazada store" would refuse with "no such config path" —
    the field would render, accept input, and fail on submit.

    Creating it is exactly as safe as adding to it: `allows_new_keys()` is only true
    where the schema names the module that loops over the container.
    """
    field = config_schema.field_for(settings, path)
    if field is not None and not field.editable:
        raise ConfigEditError(
            f"{'.'.join(path)} is not editable through this form. "
            f"{field.locked_reason or 'Nothing reads it.'}")
    _check_may_add(settings, path, what)
    node = data
    for i, key in enumerate(path[:-1]):
        if not isinstance(node, dict):
            raise ConfigEditError(f"{'.'.join(path[:i])} is not a mapping")
        if key not in node or node[key] is None:
            node[key] = {}
        node = node[key]
    leaf = path[-1]
    if not isinstance(node, dict):
        raise ConfigEditError(f"{'.'.join(path[:-1])} is not a mapping")
    if leaf not in node or node[leaf] is None:
        node[leaf] = kind()
    container = node[leaf]
    if not isinstance(container, kind):
        raise ConfigEditError(
            f"{'.'.join(path)} is a {type(container).__name__}, not a {what}, so "
            f"this edit does not apply to it")
    return container


# ---------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------

def _attach_comment(container: Any, key: Any, comment: str | None) -> None:
    """Write the why beside the entry, in the file, as a comment.

    Measured safe on the real file: ruamel writes this as an EOL comment on the new
    item and everything outside the edited block stays byte-identical. This is the
    mechanism that keeps an alias citing its own justification — without it the
    editor would produce entries that are correct and undefendable.
    """
    if not comment:
        return
    try:
        container.yaml_add_eol_comment(f"{comment}", key)
    except Exception:                                           # noqa: BLE001
        # A plain dict (a mapping created by this very edit) has no comment API.
        # Losing the note is bad; failing the whole change over it is worse, and
        # `config_proposals.summary` still records the reason.
        pass


def _apply_one(data: Any, settings: dict, edit: Edit) -> None:
    if edit.op == "set":
        parent = _walk(data, edit.path[:-1])
        leaf = edit.path[-1]
        if not isinstance(parent, dict) or leaf not in parent:
            raise ConfigEditError(
                f"no such config path: {edit.dotted}. This editor changes existing "
                f"settings; it does not invent them.")
        field = config_schema.field_for(settings, edit.path)
        if field is not None and not field.editable:
            raise ConfigEditError(
                f"{edit.dotted} is not editable through this form. "
                f"{field.locked_reason or 'Nothing reads it.'}")
        parent[leaf] = edit.value
        return

    if edit.op == "set_map_entry":
        existing = _try_walk(data, edit.path)
        if isinstance(existing, dict) and edit.key in existing:
            # Changing an entry that is already there needs no add permission —
            # correcting an alias's target is not the same act as inventing a key.
            existing[edit.key] = edit.value
            _attach_comment(existing, edit.key, edit.comment)
            return
        container = _open_container(data, settings, edit.path, kind=dict,
                                    what="mapping")
        container[edit.key] = edit.value
        _attach_comment(container, edit.key, edit.comment)
        return

    if edit.op == "remove_map_entry":
        container = _walk(data, edit.path)
        if not isinstance(container, dict) or edit.key not in container:
            raise ConfigEditError(
                f"{'.'.join(edit.path)} has no entry {edit.key!r} to remove")
        del container[edit.key]
        return

    if edit.op == "append_list_item":
        container = _open_container(data, settings, edit.path, kind=list, what="list")
        if edit.value in container:
            raise ConfigEditError(
                f"{edit.value!r} is already in {'.'.join(edit.path)}")
        container.append(edit.value)
        _attach_comment(container, len(container) - 1, edit.comment)
        return

    # remove_list_item
    container = _walk(data, edit.path)
    if not isinstance(container, list):
        raise ConfigEditError(f"{'.'.join(edit.path)} is not a list")
    if edit.value not in container:
        raise ConfigEditError(
            f"{edit.value!r} is not in {'.'.join(edit.path)}, so it cannot be removed")
    container.remove(edit.value)


# ---------------------------------------------------------------------------
# Orphaned evidence
# ---------------------------------------------------------------------------

def _comment_block_above(content: str, path: tuple[str, ...], *,
                         value: Any = None, key: str | None = None) -> list[str]:
    """The contiguous `#` block immediately above an entry, if any.

    **Measured, and it corrects a claim the M6 plan made.** The plan asserted that
    removing a commented item takes its comment with it ("200 → 199, which is the
    desired semantics"). That is true only for an **EOL** comment. For a comment
    **block** above the item, ruamel leaves the block exactly where it is — so
    removing `"Merries"` from `stores_optional.tiktok` leaves its two-line July-w5
    justification captioning `"Veet & Reckitt Personal Care"`, a store it does not
    describe. Verified against the real file.

    That is the worst available outcome for this module: evidence that looks
    authoritative and is now attached to the wrong thing. So it is detected, and the
    operator is asked which it was.
    """
    lines = content.splitlines()
    data = config_store.parse(content)
    container = _try_walk(data, path)
    if container is None:
        return []

    index: int | None = None
    try:
        if key is not None and isinstance(container, dict):
            index = container.lc.key(key)[0]
        elif isinstance(container, list):
            index = container.lc.item(list(container).index(value))[0]
    except Exception:                                           # noqa: BLE001
        return []
    if index is None or not 0 <= index < len(lines):
        return []

    block: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].strip().startswith("#"):
        block.append(lines[cursor])
        cursor -= 1
    block.reverse()
    return block


def _strip_lines(content: str, block: list[str]) -> str:
    """Delete an exact run of lines. Text-level on purpose.

    ruamel has no API for "detach this comment block", and reconstructing one would
    mean rebuilding the container's comment tokens by hand — far more likely to move
    unrelated lines than deleting the exact lines that were read out of the file.
    The round trip is measured identity, so the reparse that follows is safe.
    """
    if not block:
        return content
    lines = content.splitlines(keepends=True)
    needle = [b + "\n" for b in block]
    for start in range(len(lines) - len(needle) + 1):
        if lines[start:start + len(needle)] == needle:
            return "".join(lines[:start] + lines[start + len(needle):])
    return content                                              # pragma: no cover


def _resolve_removals(content: str, edits: list[Edit]) -> str:
    """Deal with orphaned evidence before any structural edit happens.

    Runs first, over the original text, so the line offsets a block was found at are
    still the offsets it is deleted from.
    """
    for edit in edits:
        if edit.op not in ("remove_list_item", "remove_map_entry"):
            continue
        block = _comment_block_above(content, edit.path, value=edit.value,
                                     key=edit.key)
        if not block:
            continue
        target = edit.key if edit.key is not None else repr(edit.value)
        if edit.comment_disposition is None:
            quoted = "\n".join(f"    {line.strip()}" for line in block)
            raise OrphanedEvidence(
                f"the comment above {target} in {'.'.join(edit.path)} would be left "
                f"describing whatever follows it:\n{quoted}\n"
                f"Say whether it described only this entry (remove it too) or the "
                f"whole group (keep it). This is asked because ruamel leaves such a "
                f"block in place, and evidence silently re-attached to the wrong "
                f"entry is worse than none.",
                block=[line.strip() for line in block], target=str(target))
        if edit.comment_disposition == "remove":
            content = _strip_lines(content, block)
    return content


def _reflow_appended_items(content: str, edits: list[Edit]) -> str:
    """Move a newly appended item ABOVE any comment block it landed under.

    **Measured.** `expected_stores.tiktok` ends with a two-line block that introduces
    the *next* key (`# Shopee: 17 stores per the May data...`). ruamel appends a new
    item after that block, so a new TikTok store rendered underneath a comment
    announcing Shopee's roster — visually a Shopee store, and it would then trip
    `OrphanedEvidence` on its own removal.

    A comment block that ended a list before an append was introducing whatever came
    next, never the item that did not yet exist. So the item moves above it. The
    operator's own justification is unaffected: `comment=` writes an EOL comment on
    the item's own line, which travels with it.
    """
    appended = [e for e in edits if e.op == "append_list_item"]
    if not appended:
        return content

    for edit in appended:
        lines = content.splitlines(keepends=True)
        target = _rendered_item_line(lines, edit.value)
        if target is None:
            continue
        start = target
        while start - 1 >= 0 and lines[start - 1].strip().startswith("#"):
            start -= 1
        if start == target:
            continue                    # nothing above it; already in place
        moved = lines[:start] + [lines[target]] + lines[start:target] + lines[target + 1:]
        content = "".join(moved)
    return content


def _rendered_item_line(lines: list[str], value: Any) -> int | None:
    """The line holding a freshly written list item, identified as a list entry.

    Matches `- <value>` rather than a substring anywhere, so a store name that also
    appears inside a comment cannot be mistaken for the item.
    """
    needle = str(value)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].split(" #", 1)[0].strip().strip('"').strip("'")
        if item == needle:
            return i
    return None


def apply_edits(content: str, edits: list[Edit]) -> str:
    """Apply every edit to one parsed document, then dump once.

    Order is preserved as given: an operator who removes a store and adds its
    replacement expects those to happen in that order, and reordering them could
    turn a valid pair into a duplicate-key refusal.
    """
    if not edits:
        raise ConfigEditError("a proposal with no edits changes nothing")
    if len(edits) > 200:
        raise ConfigEditError(
            f"{len(edits)} edits in one proposal is not reviewable. Split it — the "
            f"point of a proposal is that somebody reads the diff.")

    # Orphaned evidence first, over the ORIGINAL text: a comment block's line
    # offsets are only valid against the bytes it was found in.
    content = _resolve_removals(content, edits)

    data = config_store.parse(content)
    # The plain-dict view, for schema lookups. Parsed from the same bytes, so the
    # schema's idea of the file and the document being mutated cannot disagree.
    settings = config_store.parse(content)

    for edit in edits:
        _apply_one(data, settings, edit)
    return _reflow_appended_items(config_store.dump(data), edits)


def parse_all(raw_edits: list[dict]) -> list[Edit]:
    if not isinstance(raw_edits, list) or not raw_edits:
        raise ConfigEditError("edits must be a non-empty list")
    return [Edit.parse(e) for e in raw_edits]


def summarise(edits: list[Edit]) -> str:
    return "; ".join(e.describe() for e in edits)


def paths_touched(edits: list[Edit]) -> list[list[str]]:
    """Every path an edit reaches, for the goldens-invalidation check."""
    return [list(e.path) for e in edits]
