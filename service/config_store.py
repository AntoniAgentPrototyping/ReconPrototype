"""Reading, versioning and editing `config/settings.yaml` — with its comments.

This file is the domain contract, and **its in-line comments are the audit
trail** ([D2](docs/06-DECISIONS.md#d2)): an alias entry cites the order-ID-overlap
proof that justified it, a reader-engine choice cites the specific malformed
`<dimension>` tag it works around, a settlement bound cites the mis-pulled export
it dedupes. A form-based editor that parsed this file, showed fields, and wrote
it back would destroy every one of those and leave a file that looks the same and
can no longer be defended.

So two rules hold everywhere in this module:

* **`ruamel.yaml` in round-trip mode, never `PyYAML`.** PyYAML's `safe_load` +
  `dump` cycle silently discards every comment, reorders keys and rewrites
  quoting. Reaching for it here is the single most damaging shortcut available.
* **Git stays canonical.** The database holds *proposals* and the *audit trail*;
  the file on disk remains the source of truth. Moving the source of truth into
  Postgres would make month end depend on the app being up, which is exactly what
  [D24](docs/06-DECISIONS.md#d24) forbids.

The approval model is no longer a setting: see the note where `ApprovalPolicy`
used to be.
"""

from __future__ import annotations

import difflib
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

SETTINGS_FILENAME = "settings.yaml"


def _yaml() -> YAML:
    """Round-trip mode, configured to leave the file alone.

    `preserve_quotes` matters more than it looks: several column-map keys are
    Vietnamese header strings whose quoting is load-bearing, and a bare rewrite
    can change what a key IS.
    """
    y = YAML()                      # default is round-trip ('rt')
    y.preserve_quotes = True
    y.width = 4096                  # never re-wrap a long line into a new shape
    y.indent(mapping=2, sequence=4, offset=2)
    return y


class ConfigEditError(RuntimeError):
    pass


# `ApprovalPolicy` and `ApprovalDenied` are DELETED (M6). They existed only because
# open question 13 was unanswered, so the approval model was made configurable
# rather than assumed. It is answered now (docs/11-OPEN-QUESTIONS.md #13, closing
# defect 2.7): `recon.user` and `recon.admin` propose, `recon.viewer` cannot, and
# only `recon.admin` approves, rejects or applies — expressed where every other
# authorization rule in this service is, as the role on the route.
#
# Self-approval is permitted and RECORDED, via the generated
# `config_proposals.self_approved` column. Forbidding it deadlocks a single-admin
# deployment and pushes the edit back to hand-editing settings.yaml, which has no
# audit trail at all. The honest form of closing 2.7: this is recorded evidence,
# not separation of duties — no schema can invent a second person.


# ---------------------------------------------------------------------------
# Reading and writing the file
# ---------------------------------------------------------------------------

def settings_path(config_dir: Path) -> Path:
    return Path(config_dir) / SETTINGS_FILENAME


def read_text(config_dir: Path) -> str:
    """The file verbatim — comments, blank lines, key order and all."""
    return settings_path(config_dir).read_text(encoding="utf-8")


def parse(content: str) -> Any:
    return _yaml().load(content)


def dump(data: Any) -> str:
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return stream.getvalue()


def round_trip(content: str) -> str:
    """Load and re-dump without editing. Used as a *canary*, not a transform.

    If this is not the identity for a given file, then any edit through this
    module will move unrelated lines and its diff will be unreadable. That is
    worth discovering in a test rather than in a review of a VAT-rate change.
    """
    return dump(parse(content))


def diff(before: str, after: str, *, label: str = SETTINGS_FILENAME) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{label}", tofile=f"b/{label}", n=3))


def apply_edit(content: str, path: list[str], value: Any) -> str:
    """Set one dotted path to a value, leaving the rest of the file untouched.

    Structured rather than free-text on purpose: accepting a whole YAML document
    from a browser makes the api a way to replace the domain contract wholesale,
    and no diff review reliably catches a subtle change in a 300-line file.

    Creating new keys is refused. Every key in this file exists because something
    reads it; inventing one through a UI produces config the pipeline ignores,
    which then looks like a bug in the pipeline.
    """
    if not path:
        raise ConfigEditError("an empty path edits the whole document; refused")

    data = parse(content)
    node = data
    for i, key in enumerate(path[:-1]):
        if not isinstance(node, dict) or key not in node:
            raise ConfigEditError(
                f"no such config path: {'.'.join(path[:i + 1])}. This editor "
                f"changes existing settings; it does not invent them.")
        node = node[key]

    leaf = path[-1]
    if not isinstance(node, dict) or leaf not in node:
        raise ConfigEditError(
            f"no such config path: {'.'.join(path)}. This editor changes "
            f"existing settings; it does not invent them.")

    node[leaf] = value
    return dump(data)


# ---------------------------------------------------------------------------
# Evidence: the comment block belonging to a key
# ---------------------------------------------------------------------------

def _line_of(data: Any, path: list[str]) -> int | None:
    """The 0-based line a key sits on, via ruamel's `lc`."""
    node = data
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    lc = getattr(node, "lc", None)
    if lc is None or not path:
        return None
    try:
        return lc.key(path[-1])[0]
    except Exception:                                           # noqa: BLE001
        # Not every node carries position data — a flow-style mapping does not.
        return None


def evidence_for(content: str, path: list[str]) -> list[str]:
    """The comment block a human would say belongs to this key, verbatim.

    **This is the whole answer to "a form would strip the evidence for every value
    it displays".** The comments in `settings.yaml` ARE the audit trail
    ([D2](../docs/06-DECISIONS.md#d2)), and this extracts them from the same bytes
    the form is editing — so the four-line VAT block renders directly above the box
    you type `1.10` into, which is strictly more evidence at the point of decision
    than a `<pre>` where that comment sits 400 lines down.

    **Read from the TEXT, not from ruamel's `.ca`, and that is deliberate.**
    Measured: ruamel attaches a comment block to the key *preceding* the one it
    visually documents. `vat_rate`'s own comment slot holds the four-line VAT-model
    block that belongs to `vat_factors`; `tolerances`' slot holds the block above
    its first child; `expected_stores` has no slot at all because its block landed
    on the previous top-level key. Rendering `.ca` directly would caption almost
    every field with the *previous* field's justification, which is worse than
    showing none — it would look authoritative and be wrong.

    So ruamel locates the line and the raw text supplies the block: walk up over
    contiguous `#` lines, then take any inline comment on the key's own line. That
    is exactly what a reader does, and it cannot mis-attribute.

    A key with no block of its own **inherits its parent's**, because that is also
    what a reader does: `vat_factors.default` is documented by the block above
    `vat_factors`, and `expected_stores.tiktok` by the block above
    `expected_stores`.
    """
    lines = content.splitlines()
    index = _line_of(parse(content), path)
    if index is None or not 0 <= index < len(lines):
        return _parent_evidence(content, path)

    block: list[str] = []
    cursor = index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if not stripped.startswith("#"):
            break
        block.append(stripped.lstrip("#").strip())
        cursor -= 1
    block.reverse()

    own = lines[index]
    # Only a comment that follows the value, and only outside quotes — a `#` inside
    # a quoted Vietnamese header is part of the key, not a comment.
    inline = _inline_comment(own)
    if inline:
        block.append(inline)

    return block or _parent_evidence(content, path)


def _parent_evidence(content: str, path: list[str]) -> list[str]:
    if len(path) <= 1:
        return []
    return evidence_for(content, list(path[:-1]))


def _inline_comment(line: str) -> str:
    """The trailing comment on a line, ignoring `#` inside quotes.

    `"Phone #": phone` is a real column-map key in this file, so splitting on the
    first `#` would turn half a key into a caption.
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[i + 1:].strip()
    return ""


def read_value(content: str, path: list[str]) -> Any:
    node = parse(content)
    for i, key in enumerate(path):
        if not isinstance(node, dict) or key not in node:
            raise ConfigEditError(f"no such config path: {'.'.join(path[:i + 1])}")
        node = node[key]
    return node


# ---------------------------------------------------------------------------
# Committing to git — where the source of truth actually lives
# ---------------------------------------------------------------------------

def git_commit_of(config_dir: Path) -> str | None:
    """The commit the settings file is currently at, or None outside a repo.

    Recorded against every config version so a database row can be tied back to
    a reviewable commit. Failure is not an error: a container may well not ship
    `.git`, and losing the commit reference is not worth losing the run over.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", SETTINGS_FILENAME],
            cwd=str(config_dir), capture_output=True, text=True, timeout=10, check=False)
        commit = out.stdout.strip()
        return commit or None
    except (OSError, subprocess.SubprocessError):
        return None


def write_and_commit(config_dir: Path, content: str, *, message: str,
                     author: str | None = None) -> str | None:
    """Write the file and commit it. Returns the new commit hash, or None.

    **The write happens whether or not the commit does.** If `git` is missing —
    a container without `.git`, a checkout without an identity configured — the
    config still lands on disk and the database still records the version and who
    approved it. Refusing the change because the audit *nicety* failed would be
    the wrong trade: the database row is the audit record that matters, and git
    is the reviewable form of it.
    """
    path = settings_path(config_dir)
    path.write_text(content, encoding="utf-8")

    env_author = author or "recon-service"
    try:
        subprocess.run(["git", "add", "--", SETTINGS_FILENAME], cwd=str(config_dir),
                       capture_output=True, text=True, timeout=15, check=True)
        subprocess.run(
            ["git", "-c", f"user.name={env_author}",
             "-c", "user.email=recon-service@local", "commit", "-m", message,
             "--", SETTINGS_FILENAME],
            cwd=str(config_dir), capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return git_commit_of(config_dir)


# ---------------------------------------------------------------------------
# Resolving which config a run should use
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedConfig:
    content: str
    version_id: int
    pinned: bool
    sha256: str


def resolve_for_window(repo, config_dir: Path, platform: str, period: str) -> ResolvedConfig:
    """The config a run of this window must use.

    Pinned version if the window has one, otherwise whatever is on disk — and the
    disk copy is recorded as a version either way, so every run can name the rules
    it ran under afterwards (defect 2.5).

    The asymmetry is deliberate. A window is pinned the first time it is *run*,
    not the first time it is configured, so an ordinary first run behaves exactly
    as it did before M5 and only a **re-run** is protected from a config that
    moved underneath it.
    """
    pinned = repo.pinned_config(platform, period)
    if pinned is not None:
        return ResolvedConfig(content=pinned["content"], version_id=pinned["id"],
                              pinned=True, sha256=pinned["sha256"])

    content = read_text(config_dir)
    version = repo.record_config_version(
        content, source="disk", git_commit=git_commit_of(config_dir))
    return ResolvedConfig(content=content, version_id=version["id"], pinned=False,
                          sha256=version["sha256"])
