"""The team's own totals for a window — what a run gets to be checked against.

**Why a run needs this at all.** `src/pipeline.py` computes a window and compares it
to `refs`. With no refs there is nothing to compare against, so the run exits
`UNVERIFIED` (exit code 2) — it ran clean but nothing corroborated it. That is
honest, and it is also the state every browser-driven run has been in since M6: the
api has always accepted `refs` on a job, and no screen has ever sent any. The
reference figures lived in a JSON file a developer passed to `tools/devrun.py`.

**Window-scoped, not job-scoped.** `jobs.refs` is per job, so a re-run silently loses
them and the second run of the same window makes a weaker claim than the first. The
team's figures are a property of the settlement window, so they are stored against
the window and every run of it picks them up.

**Named fields, not a JSON blob.** The grand totals are a small closed set per
platform, and they are the same keys `_tie_grand` reads — defined HERE and read by
both the api and the UI, so a renamed key cannot leave a form field quietly writing
to a name nothing compares. A field the pipeline does not read would be a number
someone typed in believing it was checked.

**Not a second source of truth.** Nothing here computes anything. These are the
team's figures, recorded as given, so that a difference between their number and
ours becomes a finding rather than an argument. `_tie_grand` decides whether a
difference is a variance, using the same tolerances it always has.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    """One number the team can supply, named exactly as `_tie_grand` reads it.

    **Both languages live here, not in the web layer** (M8/5.3). The whole reason
    this spec is served rather than duplicated in TypeScript is that a field name
    drifting from the key `_tie_grand` reads would silently collect a number nothing
    compares. Putting the Vietnamese in the UI instead would re-create exactly that
    split, one language later.

    The Vietnamese uses the team's own words where they have them: `xuất HD` is what
    they write for issuing an invoice, and VAT is `VAT` in their workbooks rather
    than `thuế GTGT`.
    """

    key: str
    label: str
    help: str
    label_vi: str
    help_vi: str


# Keyed to `_tie_grand`'s first tuple element in each platform runner. Changing a key
# here without changing it there produces a field that is collected and never
# compared, which is worse than no field at all.
GRAND_FIELDS: dict[str, tuple[Field, ...]] = {
    "tiktok": (
        Field("pre_vat", "Total before VAT",
              "The team's grand total for this period, before VAT. Matches within 1 VND.",
              "Tổng trước VAT",
              "Tổng cộng của team cho kỳ này, chưa gồm VAT. Khớp trong phạm vi 1 VND."),
        Field("with_vat", "Total with VAT",
              "The team's grand total including VAT. Matches within 1 VND.",
              "Tổng đã gồm VAT",
              "Tổng cộng của team, đã gồm VAT. Khớp trong phạm vi 1 VND."),
    ),
    "shopee": (
        Field("pre_vat", "Total before VAT",
              "The team's grand total before VAT. Shopee matches within 2,000 VND — its "
              "settlement is net of platform fees, so the comparison is against a "
              "derived pair rather than a single settlement column.",
              "Tổng trước VAT",
              "Tổng cộng của team, chưa gồm VAT. Shopee khớp trong phạm vi 2.000 VND — "
              "số đối soát của Shopee đã trừ phí sàn, nên phải so với một cặp số suy ra "
              "chứ không so trực tiếp một cột."),
        Field("with_vat", "Total with VAT",
              "The team's grand total including VAT. Matches within 2,000 VND.",
              "Tổng đã gồm VAT",
              "Tổng cộng của team, đã gồm VAT. Khớp trong phạm vi 2.000 VND."),
    ),
    "lazada": (
        Field("pre_vat_105", "Total before VAT — VAT 5%",
              "Lazada's invoice sheets are split by VAT rate, so the comparison is "
              "like for like. Leave blank if that rate did not trade.",
              "Tổng trước VAT — VAT 5%",
              "Sheet xuất HD của Lazada tách theo từng mức VAT, nên so sánh đúng từng "
              "mức. Để trống nếu kỳ này không có mức đó."),
        Field("pre_vat", "Total before VAT — VAT 8%",
              "The 1.08 sheet. This is the default rate almost everything uses.",
              "Tổng trước VAT — VAT 8%",
              "Sheet 1.08. Đây là mức mặc định, gần như toàn bộ đơn dùng mức này."),
        Field("pre_vat_110", "Total before VAT — VAT 10%",
              "The 1.10 sheet. Leave blank if that rate did not trade.",
              "Tổng trước VAT — VAT 10%",
              "Sheet 1.10. Để trống nếu kỳ này không có mức đó."),
    ),
}

# `refs["grand_tolerance"]` overrides the platform default in `_tie_grand`. Offered
# because a team member may know a window is legitimately looser, and recorded as
# their decision — but it is not a form field by default: widening a tolerance to
# make a comparison pass is the failure mode this whole project exists to avoid.
TOLERANCE_KEY = "grand_tolerance"


class ReferenceError(ValueError):
    """A supplied reference cannot be recorded. Always says which field and why."""


def fields_for(platform: str) -> tuple[Field, ...]:
    if platform not in GRAND_FIELDS:
        raise ReferenceError(f"{platform!r} is not a platform this system runs")
    return GRAND_FIELDS[platform]


def payload_for(platform: str) -> list[dict]:
    """The field spec the UI renders. One definition, not a copy in TypeScript."""
    return [{"key": f.key, "label": f.label, "help": f.help,
             "label_vi": f.label_vi, "help_vi": f.help_vi}
            for f in fields_for(platform)]


def parse(platform: str, supplied: dict) -> dict:
    """Validate what a person typed into the form, and shape it the way `refs` is.

    Blank is not zero. An omitted field means "the team did not give us this number",
    and `_tie_grand` skips a key it does not find — whereas a zero would compare the
    window against 0 VND and report the whole window as a variance. The two must
    never be conflated, so a blank string is dropped rather than coerced.
    """
    if not isinstance(supplied, dict):
        raise ReferenceError("references must be supplied as named fields")
    known = {f.key for f in fields_for(platform)}
    unknown = sorted(set(supplied) - known - {TOLERANCE_KEY})
    if unknown:
        raise ReferenceError(
            f"nothing in the pipeline compares against {', '.join(unknown)} for "
            f"{platform}. A figure recorded under a name no check reads would look "
            f"verified and be ignored. Fields for {platform}: "
            f"{', '.join(sorted(known))}.")

    grand: dict[str, float] = {}
    for key in sorted(known):
        raw = supplied.get(key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        try:
            # Thousands separators are how these are read off a spreadsheet, and a
            # person copying a figure out of Excel will bring them.
            value = float(str(raw).replace(",", "").replace(" ", "").strip())
        except (TypeError, ValueError) as exc:
            raise ReferenceError(
                f"{key}: {raw!r} is not a number. Money here is VND with no minor "
                f"unit — 1234567, or 1,234,567.") from exc
        grand[key] = value

    refs: dict = {"grand": grand} if grand else {"grand": {}}
    tol = supplied.get(TOLERANCE_KEY)
    if tol not in (None, ""):
        try:
            refs[TOLERANCE_KEY] = float(str(tol).replace(",", "").strip())
        except (TypeError, ValueError) as exc:
            raise ReferenceError(f"{TOLERANCE_KEY}: {tol!r} is not a number") from exc
    return refs


def merge(window_refs: dict | None, job_refs: dict | None) -> dict:
    """What a run actually compares against.

    A job may still carry its own `refs` — the api has accepted them since M4 and
    `tools/devrun.py --refs` is the developer path. Those WIN over the window's,
    because a caller that passed figures for this specific run meant them. The
    window's figures are the standing answer, not an override of an explicit one.
    """
    merged = dict(window_refs or {})
    for key, value in (job_refs or {}).items():
        if key == "grand" and isinstance(value, dict):
            merged["grand"] = {**(merged.get("grand") or {}), **value}
        else:
            merged[key] = value
    return merged


def summarise(platform: str, refs: dict | None, lang: str = "en") -> str:
    """One sentence for the period screen. Says what is NOT covered, deliberately.

    A partial set is the dangerous middle: the screen looks answered, and the
    figures nobody supplied are the ones no check will ever run.
    """
    vi = lang == "vi"
    grand = (refs or {}).get("grand") or {}
    fields = fields_for(platform)
    total = len(fields)
    if not grand:
        return ("Chưa có số của team — chạy kỳ này sẽ ra kết quả CHƯA ĐỐI CHIẾU, "
                "nghĩa là không có gì xác nhận các con số."
                if vi else
                "No figures from the team — a run of this period will finish as NOT "
                "CHECKED, meaning nothing corroborated its numbers.")
    missing = [(f.label_vi if vi else f.label) for f in fields if f.key not in grand]
    if not missing:
        return (f"Đã nhập đủ {total} số." if vi
                else f"All {total} figures supplied.")
    return (f"Đã nhập {len(grand)}/{total} số. Còn thiếu: {', '.join(missing)}."
            if vi else
            f"{len(grand)} of {total} figures supplied. Not covered: "
            f"{', '.join(missing)}.")
