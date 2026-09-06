"""Turning an equity curve into the numbers that decide whether it was any good.

A total return on its own is close to meaningless. Doubling your money is excellent if
the worst moment along the way was a 12% dip and unremarkable if you first had to sit
through losing 60% of it, because almost nobody actually sits through that. Everything
here exists to put a number on the second half of that sentence.

**This is the one layer where money becomes a float.** Elsewhere the system uses
`Decimal` because cents must not drift. Here the operations are square roots,
logarithms and standard deviations -- where `Decimal` buys no accuracy that survives
the statistics and costs a great deal of speed. NAV endpoints stay exact; the ratios
computed from them do not need to be.

**Conventions are quantstats'**, deliberately. Sharpe and Sortino have several
defensible definitions that differ by a few percent, and a number nobody can reproduce
is not evidence. `tests/` asserts agreement with quantstats to three decimals, so the
figures here mean what a reader assumes they mean.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from sillage.core.money import ZERO, safe_div

if TYPE_CHECKING:
    import pandas as pd

    from sillage.backtest.runner import BacktestResult
    from sillage.engine.journal import NavPoint

#: Trading days in a year. The conventional figure, used to annualise daily statistics.
SESSIONS_PER_YEAR = 252

#: Days in a year including the fractional one. Used for elapsed time, not for scaling
#: daily statistics -- mixing the two is a common way to get a CAGR that is subtly wrong.
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True, slots=True)
class Metrics:
    """Return and risk, computed from an equity curve alone.

    Everything here can be derived from a NAV series, which means the same function
    describes a strategy, a benchmark, or a single asset. Trading statistics -- costs,
    turnover -- need the fills as well and live in `Trading`.
    """

    label: str
    start: date
    end: date
    years: float
    initial_nav: Decimal
    final_nav: Decimal

    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    #: Negative, as quantstats reports it: -0.55 means the fund lost 55% peak to trough.
    max_drawdown: float
    calmar: float
    #: Calendar days spent below the previous high-water mark, at the worst. This is
    #: the number that actually makes people abandon a strategy -- a 30% drawdown that
    #: recovers in four months is a different experience from one that takes five years.
    longest_drawdown_days: int

    best_month: float
    worst_month: float
    positive_months: float
    best_day: float
    worst_day: float

    def __str__(self) -> str:
        return (
            f"{self.label or 'strategy'}: {self.cagr:+.2%}/yr, "
            f"vol {self.volatility:.1%}, Sharpe {self.sharpe:.2f}, "
            f"maxDD {self.max_drawdown:.1%}"
        )


@dataclass(frozen=True, slots=True)
class Trading:
    """What the strategy did to achieve that curve, and what it paid to do it."""

    fills: int
    rejections: int
    traded_notional: Decimal
    commission: Decimal
    slippage: Decimal
    #: Notional traded per year as a multiple of average NAV. Counts both sides, so
    #: selling the whole book and buying a new one once a year reads as 2.0.
    annual_turnover: float
    #: What costs took off the annualised return. The single most useful number for
    #: deciding whether a strategy is real, because it is what disappears in live trading
    #: when the cost assumptions turn out to have been optimistic.
    cost_drag: float
    average_exposure: float

    @property
    def total_costs(self) -> Decimal:
        return self.commission + self.slippage


@dataclass(frozen=True, slots=True)
class Performance:
    """Everything a report needs about one run."""

    metrics: Metrics
    trading: Trading
    by_year: list[Metrics]
    nav: pd.Series
    drawdown: pd.Series
    monthly: pd.Series
    #: Gross exposure at every close. Carried alongside NAV because "what fraction was
    #: actually invested" explains most of the gap between a strategy's return and its
    #: benchmark's, and is invisible in the equity curve.
    exposure: pd.Series

    @property
    def label(self) -> str:
        return self.metrics.label


# ------------------------------------------------------------------ series


def exposure_series(points: Sequence[NavPoint]) -> pd.Series:
    """Gross exposure at every session close, as a fraction of NAV."""
    import pandas as pd

    if not points:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="session"))
    return pd.Series(
        [float(p.gross_exposure) for p in points],
        index=pd.DatetimeIndex([p.session for p in points], name="session"),
        name="exposure",
    )


def nav_series(points: Sequence[NavPoint]) -> pd.Series:
    """The equity curve as a float series indexed by session date."""
    import pandas as pd

    if not points:
        return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="session"))
    return pd.Series(
        [float(p.nav) for p in points],
        index=pd.DatetimeIndex([p.session for p in points], name="session"),
        name="nav",
    )


def daily_returns(nav: pd.Series) -> pd.Series:
    """Session-over-session simple returns.

    The first session has no prior NAV to compare against and is dropped rather than
    filled with a zero. A leading zero would be counted as a real observation of "the
    fund did not move", which drags the volatility estimate down by a fraction and, over
    a short backtest, noticeably.
    """
    return nav.pct_change().dropna()


def drawdown_series(nav: pd.Series) -> pd.Series:
    """How far below the previous high-water mark the fund is, at every point."""
    if nav.empty:
        return nav
    return nav / nav.cummax() - 1.0


def monthly_returns(nav: pd.Series) -> pd.Series:
    """Month-by-month returns, indexed by month end.

    Computed from month-end NAV rather than by compounding daily returns. The two agree
    to floating-point noise, but the former is what a statement from a broker would say.
    """
    import pandas as pd

    if nav.empty:
        return pd.Series(dtype="float64")
    month_end = nav.resample("ME").last()
    # The first month is measured from the fund's opening value, not from the previous
    # month end, which does not exist. Dropping it instead would silently discard the
    # month containing the initial investment.
    opening = pd.Series([nav.iloc[0]], index=[month_end.index[0] - pd.offsets.MonthEnd(1)])
    return pd.concat([opening, month_end]).pct_change().dropna()


def monthly_table(nav: pd.Series) -> pd.DataFrame:
    """Monthly returns as a years x months grid, for the heatmap."""
    import pandas as pd

    monthly = monthly_returns(nav)
    if monthly.empty:
        return pd.DataFrame()
    # Re-wrapped rather than used directly: `Series.index` is statically a plain
    # Index, and the calendar fields only exist on the datetime subclass.
    stamps = pd.DatetimeIndex(monthly.index)
    frame = pd.DataFrame({"year": stamps.year, "month": stamps.month, "ret": monthly.to_numpy()})
    return frame.pivot(index="year", columns="month", values="ret").sort_index()


# ------------------------------------------------------------------ statistics


def _longest_drawdown_days(nav: pd.Series) -> int:
    """The longest stretch from a peak to the day the fund got back to it.

    Peak-to-recovery, not merely days spent below water, because the felt experience
    of a drawdown is "how long until I was whole again" -- and that is the number that
    decides whether a strategy gets abandoned. A drawdown still open at the end of the
    run is measured to the last session, which understates it: it had not finished.
    """
    if nav.empty:
        return 0

    at_peak = nav >= nav.cummax()
    longest = 0
    peak_date = nav.index[0]
    under_water = False

    for stamp, recovered in at_peak.items():
        if not recovered:
            under_water = True
            continue
        if under_water:
            longest = max(longest, (stamp - peak_date).days)
            under_water = False
        peak_date = stamp

    if under_water:
        longest = max(longest, (nav.index[-1] - peak_date).days)
    return longest


def from_nav(
    nav: pd.Series,
    *,
    label: str = "",
    risk_free_rate: float = 0.0,
) -> Metrics:
    """Return and risk statistics for an equity curve.

    `risk_free_rate` is annual and is converted to a per-session figure before being
    subtracted, matching quantstats. Leaving it at zero reports a raw Sharpe, which is
    what most published figures are, whether or not they say so.
    """
    import numpy as np

    if len(nav) < 2:
        raise ValueError("need at least two NAV observations to compute metrics")

    start, end = nav.index[0].date(), nav.index[-1].date()
    years = (end - start).days / DAYS_PER_YEAR
    returns = daily_returns(nav)
    excess = returns - risk_free_rate / SESSIONS_PER_YEAR

    total_return = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    growth = float(nav.iloc[-1] / nav.iloc[0])
    cagr = growth ** (1.0 / years) - 1.0 if years > 0 and growth > 0 else 0.0

    deviation = float(returns.std())
    volatility = deviation * np.sqrt(SESSIONS_PER_YEAR)
    sharpe = float(excess.mean() / deviation * np.sqrt(SESSIONS_PER_YEAR)) if deviation else 0.0

    # Downside deviation counts only the losses, so a strategy is not penalised for
    # upside surprises. Divided by the full observation count, not the loss count --
    # quantstats' convention, and the one that keeps Sortino comparable across
    # strategies with different loss frequencies.
    downside = np.sqrt((np.minimum(excess, 0.0) ** 2).sum() / len(excess))
    sortino = float(excess.mean() / downside * np.sqrt(SESSIONS_PER_YEAR)) if downside else 0.0

    drawdown = drawdown_series(nav)
    max_drawdown = float(drawdown.min())
    monthly = monthly_returns(nav)

    return Metrics(
        label=label,
        start=start,
        end=end,
        years=years,
        initial_nav=Decimal(str(round(float(nav.iloc[0]), 2))),
        final_nav=Decimal(str(round(float(nav.iloc[-1]), 2))),
        total_return=total_return,
        cagr=cagr,
        volatility=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown,
        calmar=cagr / abs(max_drawdown) if max_drawdown else 0.0,
        longest_drawdown_days=_longest_drawdown_days(nav),
        best_month=float(monthly.max()) if len(monthly) else 0.0,
        worst_month=float(monthly.min()) if len(monthly) else 0.0,
        positive_months=float((monthly > 0).mean()) if len(monthly) else 0.0,
        best_day=float(returns.max()) if len(returns) else 0.0,
        worst_day=float(returns.min()) if len(returns) else 0.0,
    )


def by_calendar_year(nav: pd.Series, *, label: str = "") -> list[Metrics]:
    """The same statistics, year by year.

    Worth more than it looks. A strategy family's reputation is usually built in a few
    specific years -- trend following's on 2000-02 and 2008 -- and an aggregate figure
    spanning those years tells you nothing about how it behaved in the fifteen since.
    The regime you will actually trade in is the recent one.
    """
    years: list[Metrics] = []
    import pandas as pd

    for year, section in nav.groupby(pd.DatetimeIndex(nav.index).year):
        if len(section) < 2:
            continue
        years.append(from_nav(section, label=f"{label} {year}".strip()))
    return years


def trading_stats(result: BacktestResult, nav: pd.Series) -> Trading:
    """Costs, turnover and exposure, which need the fills as well as the curve."""
    average_nav = Decimal(str(float(nav.mean()))) if len(nav) else ZERO
    years = (nav.index[-1].date() - nav.index[0].date()).days / DAYS_PER_YEAR if len(nav) else 0.0

    turnover = 0.0
    if average_nav > ZERO and years > 0:
        turnover = float(safe_div(result.traded_notional, average_nav)) / years

    # Costs expressed as an annual rate on average capital: what the strategy would
    # have earned per year had execution been free.
    drag = 0.0
    if average_nav > ZERO and years > 0:
        drag = float(safe_div(result.total_costs, average_nav)) / years

    exposures = [float(p.gross_exposure) for p in result.nav_points]

    return Trading(
        fills=len(result.fills),
        rejections=len(result.rejections),
        traded_notional=result.traded_notional,
        commission=result.total_commission,
        slippage=result.total_slippage,
        annual_turnover=turnover,
        cost_drag=drag,
        average_exposure=sum(exposures) / len(exposures) if exposures else 0.0,
    )


def analyse(result: BacktestResult, *, risk_free_rate: float = 0.0) -> Performance:
    """Everything a report needs about one backtest."""
    nav = nav_series(result.nav_points)
    return Performance(
        metrics=from_nav(nav, label=result.name, risk_free_rate=risk_free_rate),
        trading=trading_stats(result, nav),
        by_year=by_calendar_year(nav, label=result.name),
        nav=nav,
        drawdown=drawdown_series(nav),
        monthly=monthly_returns(nav),
        exposure=exposure_series(result.nav_points),
    )
