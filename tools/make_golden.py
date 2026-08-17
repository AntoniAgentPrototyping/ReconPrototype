"""Generate the workbook golden for one window.

    python tools/make_golden.py --period 2026-05_l1 --platform lazada

Writes, under <goldens>/<period>/<platform>/ (outside the repo, never committed
— it derives from client data):
    cellset.jsonl      canonical workbook content, floats as float.hex()
    fingerprint.json   per-stage row counts / nullness / column sums
    variances.json     the run's variance list (ties against the team's refs)

and merges DIGESTS ONLY into tests/goldens/manifest.json, which IS committed:
one-way hashes plus shapes and aggregate row counts. No values, no store names,
no PII.

**Moving a baseline is deliberate.** Regenerating a window whose digests already
differ requires --rebaseline with a --reason, which is recorded in the manifest.
Without it the run refuses and points at the differ. A golden that silently
re-baselines is not a gate; it is a very slow way of writing down whatever the
code happens to do today.

Design note — the pipeline is not modified in order to be measured. Stage
functions are wrapped on their module objects for observation only, and the
real `src/pipeline.run` seam is what actually executes, assembled by
`tools/devrun.build_context` exactly as `service/worker.py` assembles it. That
keeps the verified code path verbatim and avoids a duplicated orchestrator that
could drift away from production.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools", ROOT / "tests" / "goldens"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from cellset import dump_jsonl, load_cellset, manifest  # noqa: E402  (tests/goldens)
from fingerprint import RunFingerprint, digest_json, provenance  # noqa: E402

from devrun import build_context  # noqa: E402  (tools/)
from src import calculate, classify, ingest, lazada, pipeline  # noqa: E402
from src.runlog import RunLog  # noqa: E402

# Stages worth fingerprinting, per platform, in execution order. Only
# DataFrame-returning functions — the boundaries where a divergence can first
# become visible.
STAGE_TARGETS: dict[str, list[tuple[object, str]]] = {
    "lazada": [
        (lazada, "read_ledger"),
        (lazada, "classify_ledger"),
        (lazada, "revenue_lines"),
    ],
    "tiktok": [
        (ingest, "read_parts"),
        (ingest, "derive_brand"),
        (ingest, "apply_settlement_bounds"),
        (classify, "classify_tiktok_income"),
        (calculate, "explode_to_sku_tiktok"),
        (calculate, "compute_sku_columns_tiktok"),
    ],
    "shopee": [
        (ingest, "read_parts"),
        (ingest, "derive_brand"),
        (ingest, "apply_settlement_bounds"),
        (classify, "classify_shopee_income"),
        (calculate, "explode_to_sku_shopee"),
        (calculate, "compute_sku_columns_shopee"),
    ],
}


def default_goldens_dir() -> Path:
    if env := os.environ.get("RECON_GOLDENS"):
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    return Path(local) / "recon-goldens" if local else Path.home() / ".recon-goldens"


@contextlib.contextmanager
def instrumented(fp: RunFingerprint, platform: str):
    """Wrap stage functions for observation, then restore them."""
    patches: list[tuple[object, str, object]] = []

    def wrap(module, attr, on_result):
        original = getattr(module, attr)

        @functools.wraps(original)
        def observer(*a, **kw):
            result = original(*a, **kw)
            on_result(result)
            return result

        setattr(module, attr, observer)
        patches.append((module, attr, original))

    for module, attr in STAGE_TARGETS[platform]:
        wrap(module, attr, functools.partial(fp.record, attr))

    # The template control-block verdicts used to be captured by wrapping
    # write_workbook. Since M1 they are simply RunResult.checks, so that patch
    # is gone — one fewer piece of monkeypatching between the golden and the
    # code it claims to measure.
    try:
        yield
    finally:
        for module, attr, original in reversed(patches):
            setattr(module, attr, original)


MANIFEST = ROOT / "tests" / "goldens" / "manifest.json"

# Fields that describe the RUN rather than the output. A change here is not a
# regression, so it must not trip the rebaseline guard — and, because they are
# not produced by an ordinary run, they must be CARRIED FORWARD rather than
# dropped when one happens. Found the hard way: regenerating a window that
# already matched erased the reason its baseline had been moved, which is the
# audit trail D26 claims `git diff` on this file provides.
_NON_OUTPUT_FIELDS = {"rebaselined"}


def merge_manifest(path: Path, prov: dict, key: str, entry: dict,
                   *, rebaseline: bool, reason: str | None) -> None:
    """Accumulate windows into one committed manifest.

    Refuses to overwrite an existing entry whose digests differ unless the
    caller explicitly asked to re-baseline and said why. This is the whole
    difference between a gate and a log.
    """
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8"))
    else:
        doc = {"schema": 2, "provenance": prov, "windows": {}}

    previous = (doc.get("windows") or {}).get(key)
    if previous is not None:
        before = {k: v for k, v in previous.items() if k not in _NON_OUTPUT_FIELDS}
        after = {k: v for k, v in entry.items() if k not in _NON_OUTPUT_FIELDS}
        if before != after:
            moved = sorted(k for k in set(before) | set(after)
                           if before.get(k) != after.get(k))
            if not rebaseline:
                raise SystemExit(
                    f"\nREFUSING to move the golden for {key}.\n"
                    f"  fields that changed: {', '.join(moved)}\n\n"
                    f"  This window already has a committed baseline and the new run "
                    f"does not match it.\n"
                    f"  That is the gate doing its job. Find the moved cell first:\n"
                    f"      pytest tests/goldens -q\n"
                    f"  Then, if the change is intended, re-run with:\n"
                    f"      --rebaseline --reason \"<why the output legitimately moved>\"\n")
            entry = dict(entry)
            entry["rebaselined"] = {"reason": reason, "changed": moved}
            print(f"  REBASELINED {key}: {', '.join(moved)}\n    reason: {reason}")
        else:
            # Digests match, so this run changed nothing — keep whatever the
            # previous entry recorded about how it got here.
            carried = {k: previous[k] for k in _NON_OUTPUT_FIELDS if k in previous}
            if carried:
                entry = {**carried, **entry}

    doc.setdefault("windows", {})[key] = entry
    doc["windows"] = dict(sorted(doc["windows"].items()))
    doc["provenance"] = prov
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", required=True, choices=sorted(STAGE_TARGETS))
    ap.add_argument("--period", required=True)
    ap.add_argument("--refs", default=None, help="team reference totals JSON")
    ap.add_argument("--goldens-dir", default=None)
    ap.add_argument(
        "--partial-roster", action="store_true",
        help="This window deliberately covers a SUBSET of the store roster. "
             "Makes every expected store optional for this run, so "
             "ingest.check_stores stops failing on absent stores — while the "
             "UNEXPECTED-store check stays armed. Recorded in the manifest "
             "entry, because a subset golden must never be mistaken for a "
             "full-roster one. Config on disk is not touched.")
    ap.add_argument(
        "--rebaseline", action="store_true",
        help="Permit overwriting an existing baseline whose digests differ. "
             "Requires --reason. Without this the run refuses, which is the "
             "point: a golden that silently re-baselines is not a gate.")
    ap.add_argument("--reason", default=None,
                    help="Why the output legitimately moved. Recorded in the manifest.")
    args = ap.parse_args(argv)

    if args.rebaseline and not args.reason:
        ap.error("--rebaseline requires --reason (it is recorded in the manifest)")

    prov = provenance()
    goldens = (Path(args.goldens_dir) if args.goldens_dir else default_goldens_dir())
    out_dir = goldens / args.period / args.platform
    print(f"python {prov['python']}  ·  pandas {prov['deps'].get('pandas')}")
    print(f"goldens -> {out_dir}")

    log = RunLog()
    # Built through the production helper, so the golden cannot drift from what
    # a real run does — including the _vat_sku back-channel, which is
    # load-bearing and would silently change numbers if it diverged.
    # partial_roster is applied INSIDE build_context since M4, so the CLI, this
    # generator and the service worker share one implementation of the
    # relaxation rather than three that can drift apart.
    ctx = build_context(args.platform, args.period, args.refs, log=log, root=ROOT,
                        partial_roster=args.partial_roster)
    if args.partial_roster:
        expected = (ctx.settings.get("expected_stores") or {}).get(args.platform) or []
        print(f"  PARTIAL ROSTER: {len(expected)} expected {args.platform} store(s) made "
              f"optional for this run; unexpected-store check still enforced")

    fp = RunFingerprint(args.period, args.platform,
                        f"pandas-{prov['deps'].get('pandas')}/py{prov['python']}")

    with instrumented(fp, args.platform):
        result = pipeline.run(ctx)
    if result.error is not None:
        raise SystemExit(f"run failed: {type(result.error).__name__}: {result.error}")

    fp.record_checks(result.checks)
    pipeline.write_artifacts(result)
    variances = result.all_findings

    workbook = result.workbook_path
    if not workbook.is_file():
        raise SystemExit(f"run produced no workbook at {workbook}")

    cells = load_cellset(workbook)
    out_dir.mkdir(parents=True, exist_ok=True)
    dump_jsonl(cells, out_dir / "cellset.jsonl")

    fp_doc = fp.to_dict()
    (out_dir / "fingerprint.json").write_text(
        json.dumps(fp_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    var_doc = {"period": args.period, "platform": args.platform,
               "variances": list(variances), "used_refs": bool(ctx.refs)}
    (out_dir / "variances.json").write_text(
        json.dumps(var_doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "run_log.txt").write_text("\n".join(log.lines), encoding="utf-8")

    wb_manifest = manifest(cells)
    merge_manifest(
        MANIFEST, prov, f"{args.period}/{args.platform}",
        {
            "workbook": wb_manifest,
            "fingerprint_digest": digest_json(fp_doc),
            "variances_digest": digest_json(var_doc),
            "stage_row_counts": fp.row_counts(),
            "variance_count": len(variances),
            "used_refs": bool(ctx.refs),
            # Coverage caveats travel WITH the golden. A subset window that
            # looked like a full one would make the parity gate claim more than
            # it verified.
            "partial_roster": bool(args.partial_roster),
            "stores_seen": fp.stores_seen(),
        },
        rebaseline=args.rebaseline, reason=args.reason,
    )

    print(f"\nstages fingerprinted: {len(fp_doc['stages'])}")
    for s in fp_doc["stages"]:
        print(f"  {s['stage']:<28} {s['rows']:>9,} rows x {len(s['cols']):>3} cols")
    print(f"template checks captured: {len(fp_doc['checks'])}")
    print(f"workbook: {len(wb_manifest['sheets'])} sheet(s), "
          f"{sum(s['cell_count'] for s in wb_manifest['sheets']):,} cells")
    print(f"variances: {len(variances)}"
          + ("" if ctx.refs else "  (no --refs: ties against the team were NOT checked)"))
    print(f"manifest -> {MANIFEST.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
