"""Strategies that exist to be compared against.

A backtest result on its own means nothing. "14% a year" is excellent from 2010 to 2020
and mediocre from 1995 to 2000, and the only way to know which a number is, is to run
the same engine, over the same dates, with the same costs, on something simple -- and
see whether the complicated thing beat it.

These are that something simple. They are also the calibration instruments: a
buy-and-hold backtest whose result is already known independently is what proves the
accounting works, and it is the first thing a knowledgeable reader will check.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

from sillage.core.money import ZERO, dec
from sillage.engine.feed import DataSource
from sillage.strategy.base import Monthly, Once, Schedule, TargetWeights


class StaticWeights:
    """Hold a fixed set of weights, restored on whatever schedule is given.

    With `Once` this is buy-and-hold: bought at inception and never touched, so it
    drifts with the market exactly as a real untouched portfolio does. With `Monthly`
    it is a rebalanced fixed-weight fund -- 60/40 and equal-weight are this -- which
    behaves quite differently, because rebalancing systematically sells what rose and
    buys what fell.
    """

    def __init__(
        self,
        weights: Mapping[str, Decimal | str | float],
        *,
        schedule: Schedule | None = None,
        name: str = "static",
    ) -> None:
        self.weights: TargetWeights = {s: dec(w) for s, w in weights.items()}
        if not self.weights:
            raise ValueError("a static strategy needs at least one weight")
        if any(w < ZERO for w in self.weights.values()):
            raise ValueError("static weights must not be negative")
        total = sum(self.weights.values(), start=ZERO)
        if total > dec(1):
            raise ValueError(f"static weights sum to {total}, which would require leverage")
        self.schedule = schedule or Monthly()
        self.name = name

    @property
    def warmup_sessions(self) -> int:
        # One bar per asset: enough to know a price exists, which is all a fixed
        # allocation needs. Everything downstream of this is signal-free.
        return 1

    def reset(self) -> None:
        self.schedule.reset()

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        """The fixed weights, once every asset in them has a price.

        Waiting for the whole set matters. Investing in the assets that exist yet and
        leaving the rest in cash would quietly become a different, time-varying
        strategy at the start of every backtest, and the difference would be invisible
        in the results.
        """
        for symbol in self.weights:
            if data.latest(symbol, as_of=as_of) is None:
                return None
        return dict(self.weights)

    def __repr__(self) -> str:
        holdings = ", ".join(f"{s} {float(w):.0%}" for s, w in sorted(self.weights.items()))
        return f"StaticWeights({holdings}, {self.schedule.name})"


def buy_and_hold(symbol: str = "SPY", weight: Decimal | str | float = 1) -> StaticWeights:
    """Buy once at the start, hold to the end. The calibration benchmark."""
    return StaticWeights({symbol: dec(weight)}, schedule=Once(), name=f"buy-and-hold {symbol}")


def sixty_forty(equity: str = "SPY", bonds: str = "IEF") -> StaticWeights:
    """The default thing a sensible person owns, rebalanced monthly.

    This is the benchmark that actually matters. Beating buy-and-hold SPY on raw return
    is easy in the wrong ways and beating it on risk-adjusted terms is not the claim;
    beating a 60/40 after costs is the bar a systematic multi-asset fund has to clear
    to justify existing.
    """
    return StaticWeights(
        {equity: dec("0.6"), bonds: dec("0.4")},
        schedule=Monthly(),
        name=f"60/40 {equity}/{bonds}",
    )


def equal_weight(symbols: Iterable[str], *, schedule: Schedule | None = None) -> StaticWeights:
    """Equal weight across a set, rebalanced monthly.

    Naive diversification, and a surprisingly hard benchmark to beat. If the momentum
    strategy cannot outperform simply owning the whole universe in equal parts, its
    selection step is adding nothing but turnover.
    """
    listed = sorted(set(symbols))
    if not listed:
        raise ValueError("equal_weight needs at least one symbol")
    share = dec(1) / dec(len(listed))
    return StaticWeights(
        # Truncated rather than rounded: a third rounded to eight places and tripled
        # comes to 1.00000001, which the leverage check would reject. The lost dust
        # stays in cash, which is the correct place for it.
        {s: share.quantize(dec("0.00000001"), rounding=ROUND_DOWN) for s in listed},
        schedule=schedule or Monthly(),
        name=f"equal-weight ({len(listed)})",
    )
