"""Measuring rebalance timing luck.

A strategy that rebalances monthly has to rebalance on *some* day of the month, and
nothing makes the last session better than the third-to-last. But the choice is not
harmless: two runs of the identical strategy differing only in that date will hold
different things for weeks at a time, and over twenty years the gap between the
luckiest and unluckiest date can exceed a percent a year. It is pure noise, and a
backtest reporting one date reports one draw from that distribution as though it were
the answer.

This module runs the same configuration across a spread of rebalance dates and reports
the range. It does not fix anything -- it sizes the problem, which has to come first.

**The remedy, and why it is not built yet.** The standard fix is overlapping portfolios
("tranching"): split capital into several sub-portfolios on staggered schedules and
average their targets, so no single date drives the whole book. It is cheap here,
because `target_weights` is already a pure function of `as_of`.

It is deliberately not written yet, because there is nothing to validate it against.
Timing luck comes from *selection* -- from the strategy holding different assets
depending on when it looked. A fixed-weight benchmark like 60/40 wants the same 60/40
on every date, so it has almost no timing luck by construction, and a tranching
implementation tested only against those would be untested. It belongs with the
momentum strategy in Phase 3, where the effect exists to be removed.

Running this on the benchmarks now is still worth something: it should report a spread
near zero, which is the check that the harness is measuring the strategy rather than
inventing variance of its own.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, replace
from statistics import mean, pstdev

from sillage.backtest.metrics import Metrics, analyse
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.strategy.base import Monthly

#: Month end, and roughly one, two and three weeks earlier. Four offsets a week apart
#: is what a four-way tranche would use, so the spread measured here is the spread that
#: tranching would be averaging away.
DEFAULT_OFFSETS: tuple[int, ...] = (0, 5, 10, 15)


@dataclass(frozen=True, slots=True)
class TimingLuck:
    """The same strategy, run on several rebalance dates."""

    offsets: tuple[int, ...]
    metrics: tuple[Metrics, ...]

    def _spread(self, field: str) -> tuple[float, float, float]:
        """Min, max and standard deviation of one statistic across the dates."""
        values = [float(getattr(m, field)) for m in self.metrics]
        return min(values), max(values), pstdev(values) if len(values) > 1 else 0.0

    @property
    def cagr_range(self) -> float:
        low, high, _ = self._spread("cagr")
        return high - low

    @property
    def sharpe_range(self) -> float:
        low, high, _ = self._spread("sharpe")
        return high - low

    @property
    def cagr_stdev(self) -> float:
        return self._spread("cagr")[2]

    @property
    def mean_cagr(self) -> float:
        return mean(float(m.cagr) for m in self.metrics)

    @property
    def luckiest(self) -> int:
        return self.offsets[max(range(len(self.metrics)), key=lambda i: self.metrics[i].cagr)]

    @property
    def unluckiest(self) -> int:
        return self.offsets[min(range(len(self.metrics)), key=lambda i: self.metrics[i].cagr)]

    def summary(self) -> str:
        return (
            f"{len(self.offsets)} rebalance dates: CAGR {self.mean_cagr:+.2%} on average, "
            f"spanning {self.cagr_range:.2%} between the best and worst date "
            f"(offset {self.luckiest} vs {self.unluckiest})"
        )


def study(
    config: BacktestConfig,
    *,
    offsets: Sequence[int] = DEFAULT_OFFSETS,
) -> TimingLuck:
    """Run `config` once per rebalance offset and collect the results.

    The strategy is shallow-copied for each run and given its own schedule. Copying
    matters: schedules can carry state, and sharing one across runs would leak the
    first run's history into the second.

    A strategy on a non-monthly schedule is not silently rewritten -- that would answer
    a question nobody asked. It raises instead.
    """
    if not offsets:
        raise ValueError("need at least one rebalance offset to compare")
    if not isinstance(config.strategy.schedule, Monthly):
        raise TypeError(
            f"timing luck is defined for monthly rebalancing; "
            f"{config.strategy.name!r} uses a {config.strategy.schedule.name!r} schedule"
        )

    results = []
    for offset in offsets:
        strategy = copy.copy(config.strategy)
        strategy.schedule = Monthly(offset)
        strategy.reset()
        result = run_backtest(replace(config, strategy=strategy))
        results.append(analyse(result).metrics)

    return TimingLuck(offsets=tuple(offsets), metrics=tuple(results))
