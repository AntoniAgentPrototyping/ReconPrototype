"""The words the interface uses, pinned so they cannot drift back (**B6**, **B7**).

A lint over the web layer's source, not a rendering test — there is still no browser
automation here (defect 2.8), so this checks the only thing checkable without one:
that the jargon is *absent from the source* and that both languages are present.

The rule being enforced is narrow and worth stating exactly. These terms are all
correct, and several are load-bearing in `docs/`. What they are not is meaningful to
a finance user reading a settlement result. So they may appear in **comments**, in
**wire-format field names**, and in `docs/` — and not in a string the browser paints.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web" / "app"

# Terms that were user-visible before Phase 5. Each maps to why it had to go.
BANNED = {
    "Peak RSS": "an engine-port measurement, in a table finance reads",
    "exit code": "a number only `echo $?` cares about",
    "DataFrame": "the name of a Python library",
    "openpyxl": "the name of a Python library",
    "calamine": "the name of a Python library",
    "SHA-256": "an algorithm name where the reader needs a purpose",
    "service.admin": "a shell command, on a page for people with no shell",
    "hard stop": "borrowed from the command line",
}

# Comments are exempt, and they have to be stripped properly rather than by matching
# the first character of a line: a JSX comment is `{/* … */}` and routinely spans
# several lines, so a line-prefix test misses every continuation line. The first
# version of this lint did exactly that and reported three false positives inside the
# very comments explaining the change.
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE = re.compile(r"^\s*//")


def _visible_lines(path: Path):
    """Source lines that could plausibly become on-screen text.

    Block comments are blanked in place rather than deleted, so reported line
    numbers still point at the real file.
    """
    text = path.read_text(encoding="utf-8")
    text = _BLOCK.sub(lambda m: chr(10) * m.group().count(chr(10)), text)
    for n, line in enumerate(text.splitlines(), 1):
        if _LINE.match(line):
            continue
        yield n, line


def _tsx_files():
    return sorted(p for p in WEB.rglob("*.tsx") if "node_modules" not in p.parts)


@pytest.mark.parametrize("term,why", sorted(BANNED.items()))
def test_no_jargon_reaches_the_screen(term, why):
    """**The B6 list, one test per term.**

    Comments are exempt on purpose: `// B6: was "Peak RSS"` is the record of why the
    change happened, and a lint that deleted its own justification would be a poor
    trade.
    """
    offenders = [
        f"{p.relative_to(WEB)}:{n}"
        for p in _tsx_files()
        for n, line in _visible_lines(p)
        if term.lower() in line.lower()
    ]
    assert not offenders, f"{term!r} ({why}) still reachable at: {offenders}"


def test_the_dictionary_covers_both_languages():
    """A missing translation renders an empty label, which is worse than English."""
    words = (WEB.parent / "lib" / "words.ts").read_text(encoding="utf-8")
    en = len(re.findall(r"\ben:\s*[\"']", words))
    vi = len(re.findall(r"\bvi:\s*[\"']", words))
    assert en == vi, f"{en} English entries but {vi} Vietnamese ones"
    assert en > 40, "the dictionary looks truncated"


def test_the_four_verdicts_use_the_teams_own_words():
    """**The choice worth pinning.** `VERDICT_OK` and `VERDICT_BAD` in
    `src/finance_template.py` are what the finance team already writes in their own
    workbooks. The screen uses those exact phrases, so the interface and the file it
    produces do not read as two different systems — a more "correct" translation of
    *variance* would have been fluent and wrong."""
    template = (Path(__file__).resolve().parents[1] / "src" / "finance_template.py"
                ).read_text(encoding="utf-8")
    words = (WEB.parent / "lib" / "words.ts").read_text(encoding="utf-8")

    for constant in ("VERDICT_OK", "VERDICT_BAD"):
        phrase = re.search(rf'{constant} = "([^"]+)"', template).group(1)
        assert phrase in words, (
            f"{constant} is {phrase!r} in the workbook but the screen says something "
            f"else. The two must not diverge.")


def test_the_html_lang_attribute_is_not_hardcoded():
    """B7. It is what a screen reader picks a voice from, and it said `en` on a
    product whose users work in Vietnamese."""
    layout = (WEB / "layout.tsx").read_text(encoding="utf-8")
    assert '<html lang="en">' not in layout
    assert "<html lang={lang}>" in layout


def test_every_page_has_its_own_browser_tab_title():
    """B10. Eight pages shared one title, so four windows open at month end were
    four identical tabs."""
    pages = [p for p in _tsx_files() if p.name == "page.tsx"]
    missing = [str(p.relative_to(WEB)) for p in pages
               if "export const metadata" not in p.read_text(encoding="utf-8")]
    assert not missing, f"pages with no title of their own: {missing}"
