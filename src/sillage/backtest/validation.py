"""Trying to prove the strategy wrong.

Everything before this phase measured the strategy. This phase attacks it, and the
distinction matters: a backtest is a hypothesis, and a hypothesis nobody tried to break
is a story. Five different attacks, each aimed at a different way the result could be an
illusion.

**Held-out data.** Fit on the early years, look at the late ones once. The wrinkle here
is that nothing was fitted -- the parameters came from the literature -- so the split is
not protecting against *my* overfitting. It is protecting against the literature's, and
against the possibility that the effect simply stopped working.

**Parameter sensitivity.** Vary each knob and look at the shape of the result. A broad
plateau means the effect is real and the exact number does not matter. A lone spike at
the chosen value means the value was chosen because of the spike, whether or not anyone
did it on purpose.

**Start-date sensitivity.** The date a backtest begins is a free parameter that nobody
counts as one. Phase 2 found a single-asset book swinging 0.62% a year purely on when
the money went in; a strategy deserves the same suspicion.

**Bootstrap.** A Sharpe ratio computed once is a point estimate with no error bar.
Resampling the return series in blocks -- blocks, so that volatility clustering survives
-- gives the interval the point estimate came from.

**Deflation.** The most important and least intuitive. Run enough configurations and one
of them looks good by chance; the expected maximum Sharpe across N random strategies
rises with N. The Deflated Sharpe Ratio (Bailey & López de Prado, 2014) asks how
surprising the best result is *given how many were tried*, so the honest count of trials
has to be kept. That is why `Validator` counts them itself rather than trusting anyone
to remember.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import date
from math import e, sqrt
from statistics import NormalDist
from typing import TYPE_CHECKING

from sillage.backtest.metrics import SESSIONS_PER_YEAR, Metrics, daily_returns, from_nav, nav_series
from sillage.backtest.runner import BacktestConfig, BacktestResult, load_universe, run_backtest
from sillage.core.money import dec
from sillage.execution.costs import CostModel
from sillage.portfolio.rebalance import Rebalancer
from sillage.portfolio.sizing import VolatilityTarget
from sillage.strategy.momentum import DEFAULT_LOOKBACKS, DualMomentum, MomentumConfig
from sillage.strategy.tranche import Tranched

if TYPE_CHECKING:
    import pandas as pd

#: Euler-Mascheroni, in the expected-maximum-Sharpe expression.
GAMMA = 0.5772156649015329

#: Block length for the bootstrap, in sessions. About a month: long enough to carry
#: volatility clustering across the join, short enough that the draws differ.
DEFAULT_BLOCK = 21

#: A named parameter and the values to sweep it over.
Axis = tuple[str, Sequence[object]]


@dataclass(frozen=True, slots=True)
class Variant:
    """One configuration of the strategy. Every knob, so a trial can be named."""

    lookbacks: tuple[int, ...] = DEFAULT_LOOKBACKS
    top_n: int = 5
    trend_window: int = 200
    target_vol: str = "0.10"
    vol_lookback: int = 60
    band: str = "0.20"
    cost_scale: float = 1.0

    @property
    def label(self) -> str:
        return (
            f"lb{'/'.join(map(str, self.lookbacks))} top{self.top_n} "
            f"ma{self.trend_window} vol{self.target_vol} "
            f"win{self.vol_lookback} band{self.band} cost{self.cost_scale:g}"
        )

    def with_(self, knob: str, value: object) -> Variant:
        """This variant with one parameter changed.

        Validates the name, because a sweep over a misspelled knob would otherwise run
        the default configuration forty times and report a beautifully flat sensitivity
        curve. The cast is unavoidable -- the value's type depends on the field named at
        runtime -- so the name check is what stands in for it.
        """
        known = {f.name for f in fields(self)}
        if knob not in known:
            raise KeyError(f"unknown parameter {knob!r}; known: {sorted(known)}")
        # The value's type depends on which field was named, which no annotation can
        # express; the name check above is what stands in for the lost checking.
        return replace(self, **{knob: value})  # type: ignore[arg-type]

    def configure(self, base: BacktestConfig, *, tranched: bool) -> BacktestConfig:
        strategy = DualMomentum(
            base.universe,
            MomentumConfig(
                lookbacks=self.lookbacks,
                trend_window=self.trend_window,
                top_n=self.top_n,
            ),
            VolatilityTarget(target=dec(self.target_vol), lookback=self.vol_lookback),
        )
        return replace(
            base,
            strategy=Tranched(strategy) if tranched else strategy,
            rebalancer=Rebalancer(band=dec(self.band)),
            costs=CostModel().scaled(self.cost_scale),
        )


@dataclass(frozen=True, slots=True)
class Trial:
    """One configuration, run over one window, and what came of it."""

    variant: Variant
    start: date
    end: date
    metrics: Metrics
    turnover: float

    @property
    def label(self) -> str:
        return self.variant.label


@dataclass(frozen=True, slots=True)
class Interval:
    """A bootstrapped confidence interval."""

    point: float
    low: float
    high: float
    draws: int
    #: Every resampled Sharpe, so the shape of the distribution can be drawn rather
    #: than summarised. Two thousand floats is nothing next to what produced them.
    samples: tuple[float, ...] = ()

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0.0

    def __str__(self) -> str:
        return f"{self.point:.2f} (95% CI {self.low:.2f} to {self.high:.2f})"


@dataclass(frozen=True, slots=True)
class Deflation:
    """How surprising a Sharpe ratio is, given how many were tried."""

    observed: float
    #: The Sharpe the *best of N random strategies* would be expected to show.
    expected_maximum: float
    trials: int
    #: Probability the true Sharpe exceeds zero, after accounting for selection.
    probability: float

    @property
    def survives(self) -> bool:
        """The conventional bar: 95% confident the edge is not selection."""
        return self.probability > 0.95


class Validator:
    """Runs the battery, and counts what it ran.

    The trial count is kept here rather than passed in, because a number that has to be
    remembered is a number that will be wrong. Every configuration this object executes
    is recorded, and the deflated Sharpe reads that record.
    """

    def __init__(self, base: BacktestConfig, *, tranched: bool = True) -> None:
        self.base = base
        self.tranched = tranched
        self.bars = load_universe(base)
        self.trials: list[Trial] = []

    @property
    def trial_count(self) -> int:
        return len(self.trials)

    def run(
        self,
        variant: Variant | None = None,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> Trial:
        """Execute one configuration over one window, recording it as a trial."""
        variant = variant or Variant()
        config = replace(
            variant.configure(self.base, tranched=self.tranched),
            start=start or self.base.start,
            end=end or self.base.end,
        )
        result = run_backtest(config, bars=self.bars)
        trial = Trial(
            variant=variant,
            start=config.start,
            end=config.end,
            metrics=_metrics(result, variant.label),
            turnover=_turnover(result),
        )
        self.trials.append(trial)
        return trial

    # ------------------------------------------------------------------ the attacks

    def split(self, boundary: date, variant: Variant | None = None) -> tuple[Trial, Trial]:
        """The same configuration before and after a date it never saw.

        Reported as two trials rather than one number, because the interesting question
        is not "did it pass" but "by how much did the two halves differ".
        """
        return (
            self.run(variant, end=boundary),
            self.run(variant, start=boundary),
        )

    def sensitivity(self, knob: str, values: Sequence[object]) -> list[Trial]:
        """Vary one parameter, hold the rest at their defaults.

        The shape of the output is the finding. Flat means the strategy does not depend
        on the exact value, which is what a real effect looks like. A peak at the
        default means the default was chosen -- by someone, at some point -- because it
        peaked.
        """
        return [self.run(Variant().with_(knob, value)) for value in values]

    def grid(
        self, x: tuple[str, Sequence[object]], y: tuple[str, Sequence[object]]
    ) -> list[list[Trial]]:
        """Two parameters at once, for a heatmap. A plateau should be visible as a plateau."""
        x_knob, x_values = x
        y_knob, y_values = y
        return [
            [self.run(Variant().with_(y_knob, b).with_(x_knob, a)) for a in x_values]
            for b in y_values
        ]

    def start_dates(self, starts: Sequence[date]) -> list[Trial]:
        """The same strategy begun on different days.

        An arbitrary start date is a free parameter, and unlike the others nobody thinks
        to declare it. Later starts cover shorter windows, so compare the annualised
        figures rather than the totals.
        """
        return [self.run(start=start) for start in starts]


# ------------------------------------------------------------------ statistics


def block_bootstrap(
    result: BacktestResult,
    *,
    block: int = DEFAULT_BLOCK,
    draws: int = 2000,
    seed: int = 0,
    confidence: float = 0.95,
) -> Interval:
    """A confidence interval for the Sharpe ratio, by resampling in blocks.

    Blocks rather than individual days, because daily returns are not independent:
    volatile days cluster together, and resampling one day at a time would quietly
    destroy that and produce an interval far too narrow. A block of about a month keeps
    the clustering intact inside each block, at the cost of only shuffling where the
    clusters land.

    This measures the uncertainty in the Sharpe *given this return series*. It cannot
    tell you the series came from a repeatable process -- no resampling can.
    """
    import numpy as np

    returns = np.asarray(daily_returns(nav_series(result.nav_points)), dtype=float)
    if len(returns) < block * 2:
        raise ValueError(f"need at least {block * 2} sessions to bootstrap in blocks of {block}")

    rng = np.random.default_rng(seed)
    n = len(returns)
    per_draw = -(-n // block)  # ceiling division: blocks needed to cover the series
    starts = rng.integers(0, n - block + 1, size=(draws, per_draw))
    picks = (starts[:, :, None] + np.arange(block)).reshape(draws, -1)[:, :n]
    samples = returns[picks]

    deviations = samples.std(axis=1, ddof=1)
    sharpes = np.divide(
        samples.mean(axis=1) * sqrt(SESSIONS_PER_YEAR),
        deviations,
        out=np.zeros(draws),
        where=deviations > 0,
    )
    tail = (1.0 - confidence) / 2.0
    return Interval(
        point=float(returns.mean() / returns.std(ddof=1) * sqrt(SESSIONS_PER_YEAR)),
        low=float(np.quantile(sharpes, tail)),
        high=float(np.quantile(sharpes, 1.0 - tail)),
        draws=draws,
        samples=tuple(float(x) for x in sharpes),
    )


def deflated_sharpe(
    result: BacktestResult,
    *,
    trials: int,
    trial_sharpe_stdev: float | None = None,
) -> Deflation:
    """How much of the Sharpe survives having tried `trials` configurations.

    Bailey and López de Prado's construction, in two steps.

    First, the null: if you run N strategies that all truly have no edge, the best of
    them still shows a positive Sharpe, and how positive depends on N and on how spread
    out the results are. That expected maximum is the bar the observed Sharpe has to
    clear -- not zero.

    Second, the test: how confident can we be that the observed Sharpe exceeds that bar,
    given the length of the sample and the shape of its returns. Negative skew and fat
    tails both make a high Sharpe less impressive, because both are ways to look good
    until you suddenly do not, and the formula accounts for them explicitly.

    `trial_sharpe_stdev` is the spread of Sharpe ratios across the configurations tried.
    Without it there is nothing to estimate the null's scale from, and a conventional
    fallback is used -- worth knowing, since the result then depends on an assumption
    rather than on evidence.
    """
    import numpy as np

    if trials < 1:
        raise ValueError("there is no such thing as fewer than one trial")

    returns = np.asarray(daily_returns(nav_series(result.nav_points)), dtype=float)
    observations = len(returns)
    if observations < 3:
        raise ValueError("need at least three observations to deflate a Sharpe ratio")

    deviation = returns.std(ddof=1)
    if deviation <= 0:
        raise ValueError("a return series with no variance has no Sharpe ratio")

    # Everything below is in per-session units; the annualised figure is restored at the
    # end for reporting. Mixing the two is the usual way this formula goes wrong.
    per_session = float(returns.mean() / deviation)
    standardised = (returns - returns.mean()) / deviation
    skew = float((standardised**3).mean())
    kurtosis = float((standardised**4).mean())

    spread = trial_sharpe_stdev if trial_sharpe_stdev is not None else per_session / 2.0
    normal = NormalDist()
    expected_max = spread * (
        (1.0 - GAMMA) * normal.inv_cdf(1.0 - 1.0 / trials)
        + GAMMA * normal.inv_cdf(1.0 - 1.0 / (trials * e))
    )

    variance = 1.0 - skew * per_session + (kurtosis - 1.0) / 4.0 * per_session**2
    if variance <= 0:
        # Possible with extreme negative skew; the statistic is undefined rather than
        # zero, so say so instead of returning a number nobody can interpret.
        raise ValueError("return distribution too skewed for the deflation statistic")

    statistic = (per_session - expected_max) * sqrt(observations - 1) / sqrt(variance)
    annualise = sqrt(SESSIONS_PER_YEAR)
    return Deflation(
        observed=per_session * annualise,
        expected_maximum=expected_max * annualise,
        trials=trials,
        probability=normal.cdf(statistic),
    )


def sharpe_spread(trials: Sequence[Trial]) -> float:
    """Standard deviation of Sharpe across configurations, in per-session units.

    This is what the deflation needs to know how wide the null is. Taken from the
    configurations actually run rather than assumed, which is the whole point of
    counting them.
    """
    if len(trials) < 2:
        return 0.0
    per_session = [t.metrics.sharpe / sqrt(SESSIONS_PER_YEAR) for t in trials]
    mean = sum(per_session) / len(per_session)
    return sqrt(sum((s - mean) ** 2 for s in per_session) / (len(per_session) - 1))


def rolling_sharpe(result: BacktestResult, *, window_years: int = 3) -> pd.Series:
    """Sharpe over a rolling window. The stability view a single figure hides.

    A strategy with a good overall Sharpe built from one brilliant stretch and a decade
    of mediocrity is a different proposition from one that was steadily fine, and the
    aggregate cannot tell them apart.
    """
    nav = nav_series(result.nav_points)
    returns = daily_returns(nav)
    window = window_years * SESSIONS_PER_YEAR
    rolling = returns.rolling(window)
    return (rolling.mean() / rolling.std() * sqrt(SESSIONS_PER_YEAR)).dropna()


def _metrics(result: BacktestResult, label: str) -> Metrics:
    return from_nav(nav_series(result.nav_points), label=label)


def _turnover(result: BacktestResult) -> float:
    nav = nav_series(result.nav_points)
    if nav.empty:
        return 0.0
    years = float((nav.index[-1].date() - nav.index[0].date()).days) / 365.25
    average = float(nav.mean())
    if years <= 0 or average <= 0:
        return 0.0
    return float(result.traded_notional) / average / years  # both already floats


@dataclass(frozen=True, slots=True)
class Report:
    """Everything the battery produced, for a document or a chart."""

    baseline: Trial
    train: Trial
    test: Trial
    sensitivity: dict[str, list[Trial]] = field(default_factory=dict)
    grid: list[list[Trial]] = field(default_factory=list)
    grid_axes: tuple[Axis, Axis] | None = None
    starts: list[Trial] = field(default_factory=list)
    costs: list[Trial] = field(default_factory=list)
    interval: Interval | None = None
    deflation: list[Deflation] = field(default_factory=list)
    trials: int = 0

    @property
    def held_out_gap(self) -> float:
        """Test Sharpe minus train Sharpe. Negative is the worrying direction."""
        return self.test.metrics.sharpe - self.train.metrics.sharpe


#: The parameter ranges swept. Chosen to bracket each default generously rather than to
#: flatter it: if the strategy only works at exactly 200 days, sweeping 190 to 210 would
#: hide that, and hiding it is the entire failure mode this phase exists to catch.
SENSITIVITY_AXES: dict[str, Sequence[object]] = {
    "top_n": (3, 4, 5, 6, 8, 10),
    "trend_window": (100, 150, 200, 250, 300),
    "target_vol": ("0.06", "0.08", "0.10", "0.12", "0.15"),
    "vol_lookback": (20, 40, 60, 90, 120),
    "band": ("0.05", "0.10", "0.20", "0.30", "0.50"),
    "lookbacks": ((12,), (6, 12), (3, 6, 12), (1, 3, 6, 12), (3, 6, 9, 12)),
}

GRID_AXES: tuple[Axis, Axis] = (
    ("top_n", (3, 4, 5, 6, 8)),
    ("trend_window", (100, 150, 200, 250, 300)),
)

COST_SCALES: Sequence[object] = (0.0, 1.0, 2.0, 3.0, 5.0)

QUICK_AXES: dict[str, Sequence[object]] = {"top_n": (3, 5, 8), "trend_window": (150, 200, 250)}


def run_battery(
    base: BacktestConfig,
    *,
    boundary: date,
    tranched: bool = True,
    quick: bool = False,
    draws: int = 2000,
    progress: Callable[[str], None] | None = None,
) -> Report:
    """Run every attack in this module and collect the results.

    Ordered so the cheap and decisive things happen first: if the held-out half has
    fallen apart there is little point spending five minutes on a sensitivity grid.
    Nothing short-circuits automatically, though -- a disappointing result is a finding,
    not a reason to stop looking.
    """
    say = progress or (lambda _: None)
    validator = Validator(base, tranched=tranched)

    say("baseline")
    baseline = validator.run()

    say(f"held-out split at {boundary}")
    train, test = validator.split(boundary)

    axes = QUICK_AXES if quick else SENSITIVITY_AXES
    sensitivity = {}
    for knob, values in axes.items():
        say(f"sensitivity: {knob}")
        sensitivity[knob] = validator.sensitivity(knob, values)

    grid: list[list[Trial]] = []
    grid_axes: tuple[Axis, Axis] | None = None
    if not quick:
        say("grid: top_n x trend_window")
        grid_axes = GRID_AXES
        grid = validator.grid(*GRID_AXES)

    say("start dates")
    starts = validator.start_dates(_start_dates(base, quick))

    say("cost scaling")
    costs = validator.sensitivity("cost_scale", COST_SCALES[:3] if quick else COST_SCALES)

    say("bootstrap and deflation")
    result = run_backtest(Variant().configure(base, tranched=tranched), bars=validator.bars)
    spread = sharpe_spread(validator.trials)
    interval = block_bootstrap(result, draws=draws)
    # Reported at the honest count and at two larger ones. Picking a single N invites
    # picking the flattering one; showing the curve makes the assumption visible.
    deflation = [
        deflated_sharpe(result, trials=n, trial_sharpe_stdev=spread)
        for n in (validator.trial_count, 100, 1000)
    ]

    return Report(
        baseline=baseline,
        train=train,
        test=test,
        sensitivity=sensitivity,
        grid=grid,
        grid_axes=grid_axes,
        starts=starts,
        costs=costs,
        interval=interval,
        deflation=deflation,
        trials=validator.trial_count,
    )


def _shift_months(day: date, months: int) -> date:
    """`day` moved by whole months, clamped to the 28th so no month can overflow."""
    total = day.year * 12 + (day.month - 1) + months
    return date(total // 12, total % 12 + 1, min(day.day, 28))


def _start_dates(base: BacktestConfig, quick: bool) -> list[date]:
    """Later and later start dates, each still leaving a window worth judging.

    An arbitrary start is a free parameter, and unlike the others nobody thinks to
    declare it. The walk stops early enough to leave at least three years after the
    last start -- a two-month backtest would produce a Sharpe ratio, and it would mean
    nothing, which is worse than not producing one.
    """
    step = 36 if quick else 18
    horizon = min(_shift_months(base.start, 120), _shift_months(base.end, -36))
    dates, current = [base.start], _shift_months(base.start, step)
    while current <= horizon:
        dates.append(current)
        current = _shift_months(current, step)
    return dates
