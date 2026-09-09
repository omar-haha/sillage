"""Running several strategies as one fund.

The only reliable way to raise a Sharpe ratio is to add something that does well when
the thing you already own does badly. Two sleeves each at Sharpe 0.8 with a correlation
of 0.6 combine to about 0.89; the same two at a correlation of 0.2 combine to 1.03.
Individual quality barely moves that arithmetic. Correlation is the whole game.

Which is why this is a combinator rather than a strategy. It holds sub-strategies, lets
each keep its own schedule and its own opinion, and holds a fixed fraction of capital in
each. Everything downstream -- sizing, the no-trade band, risk, execution -- sees one set
of target weights and does not know or care that several strategies produced it.

**Trend and a static allocation are natural partners**, and measurably so. Dual momentum
and a 60/40 correlate at 0.58 over this sample, and a half-and-half blend of them beats
*both* on risk-adjusted terms and returns more than the momentum sleeve alone. The
reason is that their bad years are different ones: trend bleeds in long calm bull
markets, which is exactly when a static allocation is compounding quietly.

**What this cannot do.** It cannot manufacture diversification that is not there. Every
long-only strategy on the same thirteen ETFs correlates with every other one at 0.5 or
more, which caps the achievable blend somewhere near 0.95 no matter how many are added.
Getting past that needs a sleeve that is not long-only or not this universe -- see
`docs/strategy.md`.

**Warm-up ramps in**, exactly as it does for tranching: a sleeve with no opinion yet
contributes nothing and its share of capital sits in cash, which is what actually
happens when money is staged into a strategy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sillage.core.calendar import Calendar, TradingCalendar
from sillage.core.money import ZERO, dec
from sillage.engine.feed import DataSource
from sillage.strategy.base import AnyOfSleeves, Schedule, Strategy, TargetWeights


class Blend:
    """Several strategies, each holding a fixed share of the fund."""

    def __init__(
        self,
        sleeves: Sequence[Strategy],
        weights: Sequence[Decimal | str | float] | None = None,
        *,
        calendar: Calendar | None = None,
        name: str = "",
    ) -> None:
        if not sleeves:
            raise ValueError("a blend needs at least one strategy")
        self.sleeves = tuple(sleeves)

        shares = (
            [dec(w) for w in weights]
            if weights is not None
            else [dec(1) / dec(len(self.sleeves))] * len(self.sleeves)
        )
        if len(shares) != len(self.sleeves):
            raise ValueError(f"{len(self.sleeves)} sleeves but {len(shares)} weights")
        if any(s < ZERO for s in shares):
            raise ValueError("sleeve weights must not be negative")
        total = sum(shares, start=ZERO)
        if total > dec("1.000001"):
            raise ValueError(f"sleeve weights sum to {total}, which would require leverage")
        self.weights = tuple(shares)

        self.calendar: Calendar = calendar or TradingCalendar()
        self.schedule: Schedule = AnyOfSleeves(self.sleeves)
        self.name = name or " + ".join(s.name for s in self.sleeves)
        self._held: list[TargetWeights | None] = [None] * len(self.sleeves)

    @property
    def warmup_sessions(self) -> int:
        return max(s.warmup_sessions for s in self.sleeves)

    def reset(self) -> None:
        for sleeve in self.sleeves:
            sleeve.reset()
        self.schedule.reset()
        self._held = [None] * len(self.sleeves)

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        """Refresh whichever sleeves are due, then combine all of them.

        The engine has established that *some* sleeve is due -- that is what the `AnyOf`
        schedule told it -- but not which, so each is asked again. Cheaper than threading
        the answer through the strategy protocol, and it keeps `target_weights` a
        function of `as_of` alone.
        """
        session = as_of.date()
        for index, sleeve in enumerate(self.sleeves):
            if sleeve.schedule.is_rebalance_session(session, self.calendar):
                answer = sleeve.target_weights(as_of=as_of, data=data)
                if answer is None:
                    # The sleeve declined; hand its firing back so a schedule that only
                    # fires once does not spend it on a warm-up. See `Schedule.defer`.
                    sleeve.schedule.defer()
                else:
                    self._held[index] = answer

        if all(held is None for held in self._held):
            return None

        blended: dict[str, Decimal] = {}
        for share, held in zip(self.weights, self._held, strict=True):
            if held is None:
                # This sleeve's share of capital is uninvested, which is the correct
                # representation of a strategy that has not started yet.
                continue
            for symbol, weight in held.items():
                blended[symbol] = blended.get(symbol, ZERO) + weight * share

        return {s: _round(w) for s, w in blended.items() if w > ZERO}

    def __repr__(self) -> str:
        parts = ", ".join(
            f"{s.name} {float(w):.0%}" for s, w in zip(self.sleeves, self.weights, strict=True)
        )
        return f"Blend({parts})"


def _round(value: Decimal) -> Decimal:
    return value.quantize(dec("0.000001"))
