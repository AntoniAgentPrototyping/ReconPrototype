"""Regenerate REVIEW_PACKAGE.md: every doc and every source file inline in
one reviewable document, built from the CURRENT tree so it can never drift
from the code again (the first hand-built edition went stale within weeks).

Usage: python tools/build_review_package.py
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = ["HANDOFF.md", "COMPLETION_REPORT.md", "README.md",
        "EVALUATION_DOSSIER/PROJECT_OVERVIEW.md",
        "EVALUATION_DOSSIER/WHAT_WAS_BUILT.md",
        "EVALUATION_DOSSIER/VERIFICATION_RECORD.md",
        "EVALUATION_DOSSIER/CHALLENGES_AND_FINDINGS.md",
        "EVALUATION_DOSSIER/HOW_TO_RUN.md",
        "EVALUATION_DOSSIER/OPEN_QUESTIONS_FOR_EVALUATOR.md"]

CODE_GLOBS = ["recon.py", "src/*.py", "tools/*.py", "config/settings.yaml",
              "config/brand_map.csv", "config/lazada_fee_types.csv",
              "config/lazada_vat_sku.csv", ".gitignore"]

LANG = {".py": "python", ".yaml": "yaml", ".csv": "", ".gitignore": ""}


def main() -> int:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    out = [f"# REVIEW PACKAGE — full documentation + source in one file\n",
           f"Generated {date.today()} from commit `{head}` by "
           f"`tools/build_review_package.py`. Regenerate rather than edit.\n"]

    out.append("\n\n# PART A — DOCUMENTATION\n")
    for rel in DOCS:
        p = ROOT / rel
        if not p.exists():
            out.append(f"\n\n---\n\n*(missing: {rel})*\n")
            continue
        out.append(f"\n\n---\n\n<!-- ===== {rel} ===== -->\n\n")
        out.append(p.read_text(encoding="utf-8"))

    out.append("\n\n# PART B — SOURCE\n")
    files: list[Path] = []
    for g in CODE_GLOBS:
        files.extend(sorted(ROOT.glob(g)))
    for p in files:
        rel = p.relative_to(ROOT).as_posix()
        lang = LANG.get(p.suffix, LANG.get(p.name, ""))
        out.append(f"\n\n---\n\n## `{rel}`\n\n```{lang}\n")
        out.append(p.read_text(encoding="utf-8"))
        out.append("\n```\n")

    target = ROOT / "REVIEW_PACKAGE.md"
    target.write_text("".join(out), encoding="utf-8")
    print(f"wrote {target} ({target.stat().st_size:,} bytes, "
          f"{len(DOCS)} docs + {len(files)} source files, commit {head})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
