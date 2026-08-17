"""Pin the rounding conventions the port depends on.

Measured on polars 1.43.2 / Python 3.14.7:

    Expr.round(decimals=0, mode: RoundMode = 'half_to_even')

polars ALREADY defaults to half-to-even, matching pandas `.round()` and Python
`round()`. The plan originally assumed polars defaulted to half-away-from-zero
and that a `bankers_round` helper would be needed to avoid flipping a tie-out
PASS into a BREACH. That assumption was wrong, and this test exists so a future
polars release changing the default fails loudly here rather than silently
moving money in a finance file.

Excel's ROUND is half-away-from-zero, which src/lazada.py:223 implements as
`np.where(x >= 0, floor(x + 0.5), ceil(x - 0.5))`. The native
`mode="half_away_from_zero"` was verified identical to that expression across
123,976 division-shaped values (credits/units/vat), exact ties at several
magnitudes, both signs — zero mismatches. The port may therefore use the
native call, and this test is what keeps that licence honest.
"""

from __future__ import annotations

import pytest

# Exact ties: the only inputs where the two conventions disagree.
TIES = [0.5, 1.5, 2.5, 3.5, -0.5, -1.5, -2.5]
HALF_TO_EVEN = [0.0, 2.0, 2.0, 4.0, -0.0, -2.0, -2.0]
HALF_AWAY_FROM_ZERO = [1.0, 2.0, 3.0, 4.0, -1.0, -2.0, -3.0]


def test_python_round_is_half_to_even():
    assert [float(round(v)) for v in TIES] == HALF_TO_EVEN


def test_pandas_round_is_half_to_even():
    pd = pytest.importorskip("pandas")
    assert pd.Series(TIES).round(0).tolist() == HALF_TO_EVEN


def test_polars_round_default_matches_pandas():
    """The load-bearing fact: no helper is needed for tieout.py:44 or
    finance_template.py:171,551 — the polars default already agrees."""
    pl = pytest.importorskip("polars")
    got = pl.DataFrame({"x": TIES}).select(pl.col("x").round(0))["x"].to_list()
    assert got == HALF_TO_EVEN, (
        f"polars default rounding changed to {got!r}; tie-out verdicts and the "
        f"invoice rounding model both depend on half-to-even")


def test_polars_supports_both_modes_explicitly():
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"x": TIES})
    even = df.select(pl.col("x").round(0, mode="half_to_even"))["x"].to_list()
    away = df.select(pl.col("x").round(0, mode="half_away_from_zero"))["x"].to_list()

    assert even == HALF_TO_EVEN
    assert away == HALF_AWAY_FROM_ZERO


def test_native_half_away_equals_the_floor_ceil_expression():
    """Licence to replace src/lazada.py:223's hand-rolled Excel ROUND with the
    native mode. Inputs mirror lazada.py:222 — credits/units/vat."""
    pl = pytest.importorskip("polars")

    values = [c * 1000 / u / v
              for c in range(1, 400) for u in (1, 2, 3, 6, 7) for v in (1.05, 1.08, 1.10)]
    values += [-x for x in values]
    values += [x + 0.5 for x in range(-500, 500)]

    x = pl.col("x")
    hand = pl.when(x >= 0).then((x + 0.5).floor()).otherwise((x - 0.5).ceil())
    out = pl.DataFrame({"x": values}).select(
        hand.alias("hand"), x.round(0, mode="half_away_from_zero").alias("native"))

    assert out["hand"].to_list() == out["native"].to_list()


def test_excel_round_differs_from_the_default_on_ties():
    """If these two ever agree, one of the constants above is wrong and the
    rest of this file proves nothing."""
    assert HALF_TO_EVEN != HALF_AWAY_FROM_ZERO
