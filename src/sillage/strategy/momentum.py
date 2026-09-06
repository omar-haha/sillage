"""Dual momentum: the strategy this whole project was built to test honestly.

Two well-documented effects, applied together.

**Relative momentum** decides *what* to own. Rank every asset by how much it has gone up
recently and hold the leaders. The effect is among the most persistent anomalies in
finance -- documented across two centuries and every asset class -- and it is what nearly
every managed-futures fund is built on. It is also not magic: it has long, painful
stretches, and it works on average rather than reliably.

**Absolute momentum**, the trend filter, decides *whether* to own anything. An asset must
also be above its own 200-day average; if it is not, its money goes to Treasury bills
instead. This is the half that cuts drawdowns, because it is what takes the portfolio out
of a market that has already turned.

Three choices in here are worth the argument they will get.

**Lookbacks are blended, not picked.** The famous formulation is 12-1 momentum: the last
twelve months' return, skipping the most recent one. Twelve is not special. Three, six and
twelve all work, each is right in different regimes, and there is no way to know in
advance which. So all three are computed and their *ranks* are averaged. Averaging ranks
rather than returns matters: a 12-month return is numerically much larger than a 3-month
one, so averaging the raw numbers would silently be a 12-month strategy wearing a blend's
clothes.

**The most recent month is skipped.** Over one-month horizons momentum reverses -- last
month's winners tend to be next month's losers. Including it does not just add noise, it
adds a signal pointing the wrong way.

**The trend filter is not a second, independent layer of safety.** An asset with strong
12-month momentum is usually already above its 200-day average, so the filter mostly
binds at turning points. That is exactly where it is wanted, and exactly why it should
not be counted twice when reasoning about how protected the portfolio is.

Sizing lives in `portfolio.sizing` and is composed in rather than reimplemented, so the
question "what do I own" stays separate from "how much of it".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sillage.core.money import ZERO, dec
from sillage.core.types import Instrument
from sillage.data.universe import Universe
from sillage.engine.feed import DataSource
from sillage.portfolio.sizing import (
    VolatilityTarget,
    daily_returns,
    diversification_ratio,
    shrunk_covariance,
)
from sillage.strategy.base import Monthly, Schedule, TargetWeights

#: Trading sessions in a month. The conventional approximation; using calendar months
#: would make the lookback length depend on which months a window happened to span.
SESSIONS_PER_MONTH = 21

DEFAULT_LOOKBACKS: tuple[int, ...] = (3, 6, 12)
DEFAULT_SKIP_MONTHS = 1
DEFAULT_TREND_WINDOW = 200
DEFAULT_TOP_N = 5


@dataclass(frozen=True, slots=True)
class MomentumConfig:
    """Every knob, in one place, so a run can be reproduced from a record of it."""

    lookbacks: tuple[int, ...] = DEFAULT_LOOKBACKS
    skip_months: int = DEFAULT_SKIP_MONTHS
    trend_window: int = DEFAULT_TREND_WINDOW
    top_n: int = DEFAULT_TOP_N

    def __post_init__(self) -> None:
        if not self.lookbacks:
            raise ValueError("need at least one momentum lookback")
        if any(m <= 0 for m in self.lookbacks):
            raise ValueError("momentum lookbacks must be positive")
        if self.skip_months < 0:
            raise ValueError("skip months must not be negative")
        if self.top_n <= 0:
            raise ValueError("must select at least one asset")
        if self.trend_window <= 0:
            raise ValueError("trend window must be positive")

    @property
    def required_sessions(self) -> int:
        """History needed before the strategy can say anything at all."""
        longest = max(self.lookbacks) * SESSIONS_PER_MONTH
        return max(longest + self.skip_months * SESSIONS_PER_MONTH + 1, self.trend_window)


@dataclass(frozen=True, slots=True)
class Selection:
    """What the strategy decided and why, for one rebalance.

    Kept because a weight on its own is unexplainable after the fact. This is what
    `docs/strategy.md` and the research log are written from.
    """

    as_of: datetime
    scores: dict[str, float]
    selected: tuple[str, ...]
    rejected_by_trend: tuple[str, ...]
    weights: dict[str, Decimal]
    ex_ante_volatility: float
    diversification: float
    cash_weight: Decimal


class DualMomentum:
    """Rank, filter, size. Monthly."""

    def __init__(
        self,
        universe: Universe,
        config: MomentumConfig | None = None,
        sizer: VolatilityTarget | None = None,
        *,
        schedule: Schedule | None = None,
        name: str = "dual momentum",
    ) -> None:
        self.universe = universe
        self.config = config or MomentumConfig()
        self.sizer = sizer or VolatilityTarget()
        self.schedule: Schedule = schedule or Monthly()
        self.name = name
        #: Every decision made this run, in order. Diagnostics, never an input.
        self.decisions: list[Selection] = []

    @property
    def warmup_sessions(self) -> int:
        return max(self.config.required_sessions, self.sizer.lookback + 1)

    @property
    def candidates(self) -> tuple[Instrument, ...]:
        """The risky sleeve: everything except the thing money retreats into."""
        return tuple(i for i in self.universe.instruments if i != self.universe.cash_proxy)

    def reset(self) -> None:
        self.schedule.reset()
        self.decisions = []

    # ------------------------------------------------------------------ the decision

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        closes = self._closes(as_of, data)
        scores = self._scores(closes)
        if not scores:
            # Not enough history for anything to be ranked. `None`, not `{}` -- an
            # empty mapping would order the book liquidated on day one.
            return None

        selected = self._top(scores)
        passing = tuple(s for s in selected if self._in_uptrend(closes[s]))
        rejected = tuple(s for s in selected if s not in passing)

        # Inverse-vol shares are computed across all the selected assets, then the
        # rejected ones' shares are moved to cash rather than redistributed. Sharing
        # them out among the survivors would concentrate the book precisely when the
        # trend filter is telling it to take less risk.
        returns = {s: daily_returns(closes[s]) for s in selected}
        sizing = self.sizer.size(returns)

        weights = {s: w for s, w in sizing.weights.items() if s in passing}
        cash = dec(1) - sum(weights.values(), start=ZERO)

        covariance = shrunk_covariance({s: returns[s] for s in weights}, self.sizer.shrinkage)
        self.decisions.append(
            Selection(
                as_of=as_of,
                scores=scores,
                selected=selected,
                rejected_by_trend=rejected,
                weights=dict(weights),
                ex_ante_volatility=sizing.ex_ante_volatility,
                diversification=diversification_ratio(weights, covariance),
                cash_weight=cash,
            )
        )

        if cash > ZERO:
            weights[self.universe.cash_proxy.symbol] = cash
        return weights

    # ------------------------------------------------------------------ the pieces

    def _closes(self, as_of: datetime, data: DataSource) -> dict[str, list[Decimal]]:
        """Closing prices per candidate, oldest first, as of this instant."""
        needed = self.warmup_sessions
        series: dict[str, list[Decimal]] = {}
        for instrument in self.candidates:
            bars = data.history(instrument.symbol, as_of=as_of, count=needed)
            if len(bars) >= needed:
                series[instrument.symbol] = [b.close for b in bars]
        return series

    def _scores(self, closes: dict[str, list[Decimal]]) -> dict[str, float]:
        """Average rank across the lookbacks. Higher is stronger.

        Ranks rather than returns, because a twelve-month return dwarfs a three-month
        one and averaging the raw figures would let the longest lookback decide.
        """
        if len(closes) < 2:
            return {}

        totals: dict[str, float] = dict.fromkeys(closes, 0.0)
        for months in self.config.lookbacks:
            returns = {s: self._momentum(prices, months) for s, prices in closes.items()}
            for symbol, rank in _average_ranks(returns).items():
                totals[symbol] += rank
        return {s: v / len(self.config.lookbacks) for s, v in totals.items()}

    def _momentum(self, prices: Sequence[Decimal], months: int) -> float:
        """Total return over `months`, ending `skip_months` ago."""
        skip = self.config.skip_months * SESSIONS_PER_MONTH
        end = len(prices) - 1 - skip
        start = end - months * SESSIONS_PER_MONTH
        if start < 0 or prices[start] <= ZERO:
            return float("-inf")  # ranked last; it cannot be measured
        return float(prices[end] / prices[start]) - 1.0

    def _top(self, scores: dict[str, float]) -> tuple[str, ...]:
        ordered = sorted(scores, key=lambda s: (-scores[s], s))
        return tuple(ordered[: self.config.top_n])

    def _in_uptrend(self, prices: Sequence[Decimal]) -> bool:
        window = prices[-self.config.trend_window :]
        if len(window) < self.config.trend_window:
            return False
        average = sum(window, start=ZERO) / dec(len(window))
        return prices[-1] > average

    def __repr__(self) -> str:
        return (
            f"DualMomentum(top {self.config.top_n} of {len(self.candidates)}, "
            f"lookbacks {self.config.lookbacks}, target {float(self.sizer.target):.0%} vol)"
        )


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    """Rank ascending from 1, with ties sharing the average of the ranks they span.

    Ties are not a technicality here: two assets with identical scores must not be
    ordered by whichever happened to sort first, or selection would silently depend on
    the alphabet.
    """
    ordered = sorted(values, key=lambda s: values[s])
    ranks: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        stop = index
        while stop + 1 < len(ordered) and values[ordered[stop + 1]] == values[ordered[index]]:
            stop += 1
        shared = (index + stop) / 2 + 1
        for position in range(index, stop + 1):
            ranks[ordered[position]] = shared
        index = stop + 1
    return ranks


def build(
    universe: Universe,
    *,
    lookbacks: Iterable[int] = DEFAULT_LOOKBACKS,
    top_n: int = DEFAULT_TOP_N,
    target_vol: Decimal | str | float = "0.10",
    schedule: Schedule | None = None,
) -> DualMomentum:
    """The strategy as specified in docs/ROADMAP.md section 4."""
    return DualMomentum(
        universe,
        MomentumConfig(lookbacks=tuple(lookbacks), top_n=top_n),
        VolatilityTarget(target=dec(target_vol)),
        schedule=schedule,
    )
