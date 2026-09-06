"""Tests for the performance metrics.

Two kinds of assertion here. The first are hand-computed: a curve whose Sharpe or
drawdown can be worked out on paper, so a wrong formula fails rather than merely
producing a different plausible number. The second is the cross-check against
quantstats -- an independent implementation by people who had no sight of this one.

That cross-check is the point of the module. Sharpe has several defensible definitions
differing by a few percent, and a figure nobody else can reproduce is not evidence.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import pytest

from sillage.backtest.metrics import (
    SESSIONS_PER_YEAR,
    by_calendar_year,
    daily_returns,
    drawdown_series,
    from_nav,
    monthly_returns,
    monthly_table,
    nav_series,
)
from sillage.engine.journal import NavPoint

# quantstats prints deprecation noise from its own pandas usage on import.
warnings.filterwarnings("ignore")


def curve(values: list[float], *, start: str = "2020-01-01") -> pd.Series:
    """A NAV series on consecutive calendar days.

    Calendar days rather than sessions: the statistics being tested annualise by a
    fixed session count and do not inspect the gaps, so a real trading calendar would
    add nothing but noise to the arithmetic under test.
    """
    return pd.Series(
        values,
        index=pd.date_range(start, periods=len(values), freq="D", name="session"),
        name="nav",
    )


def scalar(value: float | pd.Series) -> float:
    """Narrow a quantstats result to a number.

    Its statistics return a float for a Series input and a Series for a DataFrame one,
    so the declared type is a union even when only one branch can occur here.
    """
    return float(value.iloc[0]) if isinstance(value, pd.Series) else float(value)


def random_walk(days: int = 3000, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0004, 0.011, days)
    return curve(list(100_000 * np.cumprod(1 + returns)))


# ------------------------------------------------------------------ series


def test_nav_series_carries_sessions_and_values() -> None:
    from datetime import UTC, date, datetime
    from decimal import Decimal

    points = [
        NavPoint(
            date(2024, 1, 2),
            datetime(2024, 1, 2, 21, tzinfo=UTC),
            Decimal(100),
            Decimal(0),
            Decimal(1),
        ),
        NavPoint(
            date(2024, 1, 3),
            datetime(2024, 1, 3, 21, tzinfo=UTC),
            Decimal(110),
            Decimal(0),
            Decimal(1),
        ),
    ]
    series = nav_series(points)
    assert list(series) == [100.0, 110.0]
    assert series.index[0].date() == date(2024, 1, 2)


def test_the_first_session_has_no_return_rather_than_a_zero() -> None:
    """A leading zero would be counted as an observation and drag volatility down."""
    assert len(daily_returns(curve([100, 110, 121]))) == 2


def test_drawdown_is_measured_from_the_running_peak() -> None:
    dd = drawdown_series(curve([100, 120, 60, 90, 130]))
    assert list(dd) == pytest.approx([0.0, 0.0, -0.5, -0.25, 0.0])


def test_monthly_returns_include_the_first_partial_month() -> None:
    """Dropping it would silently discard the month the money went in."""
    daily = curve([100.0] * 25 + [110.0] * 40, start="2020-01-01")
    monthly = monthly_returns(daily)
    assert len(monthly) == 3
    assert monthly.iloc[0] == pytest.approx(0.10)


def test_monthly_table_is_years_by_months() -> None:
    table = monthly_table(random_walk(800))
    assert table.index.name == "year"
    assert set(table.columns) <= set(range(1, 13))


# ------------------------------------------------------------------ hand-checkable


def test_a_flat_curve_has_no_return_and_no_risk() -> None:
    metrics = from_nav(curve([100.0] * 500))
    assert metrics.total_return == 0.0
    assert metrics.volatility == 0.0
    assert metrics.sharpe == 0.0
    assert metrics.max_drawdown == 0.0


def test_cagr_compounds_to_the_total_return() -> None:
    nav = curve([100.0 * (1.0002) ** i for i in range(int(SESSIONS_PER_YEAR * 4))])
    metrics = from_nav(nav)
    assert (1 + metrics.cagr) ** metrics.years == pytest.approx(1 + metrics.total_return, rel=1e-9)


def test_max_drawdown_is_reported_negative() -> None:
    """Matching quantstats. A positive drawdown reads as a gain to half of all readers."""
    assert from_nav(curve([100, 50, 100])).max_drawdown == pytest.approx(-0.5)


def test_longest_drawdown_runs_from_the_peak_to_the_recovery() -> None:
    # Peaks on day 1, back to 100 on day 5: four days to be whole again.
    metrics = from_nav(curve([100, 90, 80, 90, 100, 110]))
    assert metrics.longest_drawdown_days == 4


def test_a_curve_that_never_falls_has_no_drawdown() -> None:
    """A flat series is at its own peak every day and must not accrue a duration."""
    assert from_nav(curve([100.0] * 500)).longest_drawdown_days == 0
    assert from_nav(curve([100.0 * 1.01**i for i in range(50)])).longest_drawdown_days == 0


def test_an_unrecovered_drawdown_runs_to_the_end() -> None:
    metrics = from_nav(curve([100, 90, 85, 80]))
    assert metrics.longest_drawdown_days == 3


def test_sortino_ignores_upside_volatility() -> None:
    """Two curves with the same downside but different upside: Sortino must differ."""
    steady = from_nav(curve([100, 99, 100, 99, 100, 99, 100]))
    spiky = from_nav(curve([100, 99, 130, 99, 130, 99, 130]))
    assert spiky.sortino > steady.sortino


def test_needs_more_than_one_observation() -> None:
    with pytest.raises(ValueError, match="at least two"):
        from_nav(curve([100.0]))


# ------------------------------------------------------------------ quantstats


@pytest.fixture(scope="module")
def walk() -> pd.Series:
    return random_walk()


def test_sharpe_matches_quantstats(walk: pd.Series) -> None:
    import quantstats as qs

    assert from_nav(walk).sharpe == pytest.approx(scalar(qs.stats.sharpe(daily_returns(walk))))


def test_sortino_matches_quantstats(walk: pd.Series) -> None:
    import quantstats as qs

    assert from_nav(walk).sortino == pytest.approx(scalar(qs.stats.sortino(daily_returns(walk))))


def test_volatility_matches_quantstats(walk: pd.Series) -> None:
    import quantstats as qs

    assert from_nav(walk).volatility == pytest.approx(
        scalar(qs.stats.volatility(daily_returns(walk)))
    )


def test_max_drawdown_matches_quantstats(walk: pd.Series) -> None:
    import quantstats as qs

    assert from_nav(walk).max_drawdown == pytest.approx(scalar(qs.stats.max_drawdown(walk)))


def test_cagr_matches_quantstats_to_three_decimals(walk: pd.Series) -> None:
    """A deliberate hairline disagreement, documented rather than papered over.

    quantstats divides elapsed days by 365; this divides by 365.25, which is right once
    leap years are in the sample. Over twenty years that is about one and a half basis
    points -- invisible at three decimals, and the more correct of the two.
    """
    import quantstats as qs

    assert from_nav(walk).cagr == pytest.approx(
        scalar(qs.stats.cagr(daily_returns(walk))), abs=1e-3
    )


def test_calmar_matches_quantstats_to_three_decimals(walk: pd.Series) -> None:
    import quantstats as qs

    assert from_nav(walk).calmar == pytest.approx(
        scalar(qs.stats.calmar(daily_returns(walk))), abs=1e-3
    )


# ------------------------------------------------------------------ sub-periods


def test_by_calendar_year_splits_the_run(walk: pd.Series) -> None:
    """The breakdown that stops a good decade hiding a bad one."""
    years = by_calendar_year(walk, label="walk")
    assert [y.start.year for y in years] == sorted({d.year for d in walk.index})
    assert all(y.label.startswith("walk") for y in years)


def test_a_year_with_one_observation_is_skipped() -> None:
    """A single session cannot produce a return, and a zero there would be a lie."""
    nav = curve([100.0] * 400, start="2020-12-31")
    assert [y.start.year for y in by_calendar_year(nav)] == [2021, 2022]
