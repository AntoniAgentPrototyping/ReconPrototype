"""An unreadable date is COUNTED and named, not silently dropped.

The date half of [defect 1.6](../docs/08-KNOWN-DEFECTS.md). The numeric half was
closed in M2.5 — an amount that will not parse hard-stops rather than becoming
0 VND — while dates went through a bare `errors="coerce"` with no counter at all.

Why it matters more than it looks. An unreadable date does not produce a *wrong*
number, it produces a **missing** one: `src/finance_template.py` groups on
`.dt.month`, `NaT` becomes a `NaN` group key, and pandas drops a `NaN` key by
default. The row's money leaves the invoice quietly. That is the failure mode this
project treats as worse than a loud one.

Two behaviours are pinned:

1. an unreadable date is counted, named per column, and reported;
2. when the file's own format contradicts `dayfirst`, the disagreement is logged
   rather than left on stderr. That fires on **real TikTok income today** —
   measured, `%Y/%m/%d` against `dayfirst.tiktok: true`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pandas")

from src import ingest                                            # noqa: E402
from src.errors import ReconHardStop                              # noqa: E402
from src.runlog import RunLog                                     # noqa: E402


@pytest.fixture
def log():
    return RunLog()


def test_an_unreadable_date_is_counted_and_named(log):
    """The counter that did not exist. `10 rows in, 3 undated` is the difference
    between an operator seeing a format change and not seeing it."""
    import pandas as pd

    series = pd.Series(["01/05/2026", "not a date", "02/05/2026", ""])
    parsed = ingest.parse_dates(series, True, "tiktok/orders", "order_created_at", log)
    assert int(parsed.isna().sum()) == 2

    ingest.report_undated({"order_created_at": 2}, "tiktok/orders", True, {}, log)
    text = "\n".join(log.lines)
    assert "2 date cell(s) could not be read" in text
    assert "order_created_at" in text


def test_the_default_is_warn_not_hard_stop(log):
    """Deliberately gentler than the money rule. A settlement export never
    legitimately contains an unreadable AMOUNT, so that hard-stops. A date can
    legitimately be blank — `apply_settlement_bounds` already keeps and reports
    undated income rows — so stopping on one would refuse windows that are fine."""
    ingest.report_undated({"statement_date": 5}, "shopee/income", False, {}, log)
    assert any("could not be read" in line for line in log.lines)


def test_an_operator_can_ask_for_a_hard_stop(log):
    """`date_coercion: hard_stop`, mirroring `numeric_coercion: warn` in the other
    direction — the setting exists so the posture is a decision, not a default."""
    with pytest.raises(ReconHardStop, match="could not be read"):
        ingest.report_undated({"statement_date": 1}, "shopee/income", False,
                              {"date_coercion": "hard_stop"}, log)


def test_nothing_is_said_when_every_date_read(log):
    ingest.report_undated({}, "lazada/ledger", False, {}, log)
    assert not [line for line in log.lines if "could not be read" in line]


def test_a_file_that_contradicts_dayfirst_says_so(log):
    """**This fires on real data today.** TikTok income is `%Y/%m/%d` while
    `dayfirst.tiktok` is `true`; pandas detects the year-first format, ignores the
    setting, and warns. That warning is the exact signal that the contract and the
    file disagree — and it went to stderr and died there until M8.
    """
    import pandas as pd

    # Day 13 FIRST, matching the real file. That is not incidental: pandas infers
    # the format from the first element, so an unambiguous one (day > 12) is what
    # makes it detect %Y/%m/%d and override dayfirst. Had the first row been day 1
    # this column would silently transpose instead — the case below.
    series = pd.Series(["2026/05/13", "2026/05/01", "2026/05/20"])
    parsed = ingest.parse_dates(series, True, "tiktok/income", "statement_date", log)

    assert int(parsed.isna().sum()) == 0, "the dates themselves still read correctly"
    assert parsed.dt.month.unique().tolist() == [5]
    assert any("dayfirst" in line for line in log.lines), (
        "the disagreement between the file and the contract must be logged")


def test_an_ambiguous_column_is_where_dayfirst_actually_decides(log):
    """Why the warning above is worth capturing rather than silencing.

    pandas infers a format from the FIRST element. When that element is ambiguous,
    `dayfirst` decides — and the same column shape then parses a different way.
    Measured on pandas 2.3.3: this is the case where nothing warns and the answer
    is simply different.
    """
    import pandas as pd

    ambiguous = pd.Series(["2026/05/01", "2026/05/02"])
    day_first = ingest.parse_dates(ambiguous, True, "x", "c", log)
    month_first = ingest.parse_dates(ambiguous, False, "x", "c", log)
    assert day_first.dt.month.tolist() == [1, 2]
    assert month_first.dt.month.tolist() == [5, 5]
    assert day_first.tolist() != month_first.tolist(), (
        "one setting, two readings of identical bytes — which is why an explicit "
        "per-platform date format is the durable fix (register, date_formats)")
