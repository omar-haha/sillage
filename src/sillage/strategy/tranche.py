"""Tranching: spreading a strategy across several rebalance dates.

A strategy that rebalances monthly must rebalance on *some* day of the month, and
nothing makes the last session better than the third-to-last. But the choice is not
free: two runs of the identical strategy differing only in that date hold different
things for weeks at a time, and over twenty years the gap between the luckiest and
unluckiest date can be worth a percent a year. That is noise being reported as a result
(Hoffstein, "rebalance timing luck").

The fix is to stop choosing. Run four copies of the strategy on schedules a week apart,
give each a quarter of the capital, and hold the average of what they want. No single
date drives the book, and the variance from the choice falls by roughly the square root
of the number of tranches.

**Turnover barely rises**, which is the part people expect to be the catch. Each tranche
trades a quarter-sized book, and their trades partly cancel at the aggregate level --
one tranche buying what another is selling nets out before an order is ever produced,
because the engine only sees the blended target.

**What tranching cannot do.** It removes variance from an arbitrary choice; it does not
add return. If the strategy is bad, four staggered copies of it are bad more
consistently. And it only helps where the effect exists: a fixed-weight portfolio wants
the same weights whichever day it looks, so tranching a 60/40 changes almost nothing.
Timing luck is a property of *selection*.

**Warm-up ramps in.** A tranche with no opinion yet contributes nothing, so its quarter
of the capital sits in cash until it has enough history. That is not a defect being
tolerated -- it is what actually happens when you stage money into a strategy.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sillage.core.calendar import Calendar, TradingCalendar
from sillage.core.money import ZERO, dec
from sillage.engine.feed import DataSource
from sillage.strategy.base import AnyOf, Monthly, Schedule, Strategy, TargetWeights

#: Month end, and roughly one, two and three weeks earlier.
DEFAULT_OFFSETS: tuple[int, ...] = (0, 5, 10, 15)


class Tranched:
    """Several staggered copies of one strategy, averaged."""

    def __init__(
        self,
        prototype: Strategy,
        *,
        offsets: Sequence[int] = DEFAULT_OFFSETS,
        calendar: Calendar | None = None,
        name: str = "",
    ) -> None:
        if not offsets:
            raise ValueError("tranching needs at least one offset")
        if len(set(offsets)) != len(offsets):
            raise ValueError(f"duplicate tranche offsets: {sorted(offsets)}")

        self.offsets = tuple(offsets)
        # Deep copies, not references. Strategies carry state -- a schedule that has
        # fired, a log of decisions -- and four tranches sharing one object would
        # interleave into nonsense.
        self.tranches: tuple[Strategy, ...] = tuple(copy.deepcopy(prototype) for _ in self.offsets)
        for tranche, offset in zip(self.tranches, self.offsets, strict=True):
            tranche.schedule = Monthly(offset)

        self.calendar: Calendar = calendar or TradingCalendar()
        # Annotated as the protocol type, not the concrete one: the engine only
        # ever asks a schedule whether today is a rebalance day.
        self.schedule: Schedule = AnyOf([t.schedule for t in self.tranches])
        self.name = name or f"{prototype.name} ({len(self.offsets)} tranches)"
        self._held: list[TargetWeights | None] = [None] * len(self.offsets)

    @property
    def warmup_sessions(self) -> int:
        return max(t.warmup_sessions for t in self.tranches)

    def reset(self) -> None:
        for tranche in self.tranches:
            tranche.reset()
        self.schedule.reset()
        self._held = [None] * len(self.offsets)

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        """Refresh whichever tranches are due, then average all of them.

        The engine has already established that *some* tranche is due -- that is what
        the `AnyOf` schedule told it -- but not which. Asking each one again is cheaper
        than threading that answer through the strategy protocol, and it keeps
        `target_weights` a function of `as_of` alone.
        """
        session = as_of.date()
        for index, tranche in enumerate(self.tranches):
            if tranche.schedule.is_rebalance_session(session, self.calendar):
                self._held[index] = tranche.target_weights(as_of=as_of, data=data)

        if all(weights is None for weights in self._held):
            return None

        share = dec(1) / dec(len(self.tranches))
        blended: dict[str, Decimal] = {}
        for weights in self._held:
            if weights is None:
                # This tranche's share of capital is uninvested. Contributing nothing
                # is the correct representation of that.
                continue
            for symbol, weight in weights.items():
                blended[symbol] = blended.get(symbol, ZERO) + weight * share

        return {s: _round(w) for s, w in blended.items() if w > ZERO}

    def __repr__(self) -> str:
        return f"Tranched({self.name!r}, offsets={self.offsets})"


def _round(value: Decimal) -> Decimal:
    return value.quantize(dec("0.000001"))
