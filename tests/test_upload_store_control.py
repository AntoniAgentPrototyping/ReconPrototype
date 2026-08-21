"""D7 — the per-file store correction is rendered, posted and read as one field.

The defect this pins is not a bug in any one file. Every piece worked:
`POST /uploads` accepted `store` to confirm or correct what the filename pattern
found; `web/app/actions.ts` read `store:<filename>` per file and forwarded it. The
two ends agreed on a contract **that nothing rendered an input for**, so the
documented affordance was unreachable and an operator whose file parsed to the wrong
storefront could only rename it on disk (register D7).

A three-sided contract with no test is a contract that can lose a side silently, and
it did — for the whole of M6, M8 and five register phases. There is still no browser
automation in this project (register E2), so this cannot prove the control *works*;
it proves the field name is the same string in all three places, which is exactly
what went missing.

Text-level on purpose, and it is the same technique `tests/test_ui_vocabulary.py`
uses for the same reason: the alternative is a browser, and there is not one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

pytestmark = pytest.mark.skipif(not (WEB / "app").is_dir(),
                                reason="the web app is not present in this checkout")

# The one field name, as each side spells it. A template literal on both sides,
# because each iterates its own list of files.
POSTED = re.compile(r"""form\.get\(\s*`store:\$\{\s*file\.name\s*\}`\s*\)""")
RENDERED = re.compile(r"""name=\{\s*`store:\$\{\s*file\.filename\s*\}`\s*\}""")


def _read(rel: str) -> str:
    return (WEB / rel).read_text(encoding="utf-8")


def test_the_upload_action_reads_a_per_file_store_field():
    """The half that existed all along."""
    assert POSTED.search(_read("app/actions.ts")), (
        "web/app/actions.ts no longer reads `store:<filename>` per file. If the "
        "field was renamed, rename it in the form and in POST /uploads too — the "
        "three have to be one string.")


def test_something_renders_an_input_with_that_field_name():
    """The half that did not, and that is what D7 was.

    Deliberately a search across the app rather than a named file: the point is
    that SOME screen offers the control, not that a particular component does.
    """
    rendered = [p for p in (WEB / "app").rglob("*.tsx") if RENDERED.search(p.read_text(encoding="utf-8"))]
    assert rendered, (
        "no screen renders an input named `store:<filename>`, so the store "
        "correction POST /uploads accepts is unreachable again — this is exactly "
        "register D7, which was closed on 2026-08-21")


def test_the_api_accepts_the_field_the_form_posts():
    """The third side. `store` is optional and means "confirm or correct"; the
    upload derives its own value when it is absent, which is why losing the input
    was invisible."""
    api = (ROOT / "service" / "api.py").read_text(encoding="utf-8")
    assert re.search(r"store:\s*str\s*\|\s*None\s*=\s*Form\(", api), (
        "POST /uploads no longer takes an optional `store` form field")


def test_the_form_asks_the_api_what_the_names_resolve_to_rather_than_guessing():
    """The rule that makes the control safe rather than merely present.

    Store identity comes from the filename (docs/06-DECISIONS.md#d6) and is
    derived by `ingest.store_from_filename` through `naming.store_of`. A regex in
    the browser would be a second definition of it, free to drift from the one that
    decides whose revenue a file becomes — so the form calls the preview route and
    the app must contain no store-parsing pattern of its own.
    """
    form = _read("app/windows/[platform]/[period]/upload-form.tsx")
    assert "previewStores" in form, "the form must ask the API for the derived store"

    actions = _read("app/actions.ts")
    assert "/uploads/store-preview" in actions, (
        "the preview action must call the API route, not compute an answer")

    # `store_from_filename` is the contract key whose VALUE is the regex. If that
    # key's name appears in the web app, something client-side is reading the
    # pattern — which is the second-implementation hazard even if it then hands it
    # to a browser regex engine rather than writing one out.
    for path in (WEB / "app").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("*") or stripped.startswith("//"):
                continue        # a comment may name it; that is how it is explained
            assert "store_from_filename" not in line, (
                f"{path.relative_to(WEB)} reads the filename pattern client-side")
