"""Turning target weights into orders.

The strategy says what it wants; this decides what to actually trade to get there, and
its main job is refusing to trade. A portfolio drifts continuously, so a rebalancer
that restored exact weights at every opportunity would generate a stream of tiny orders
whose commissions and spreads add up to a real drag for no benefit -- the position was
already close enough.

Two filters do the refusing.

**The no-trade band** ignores drift below a relative threshold. Twenty percent sounds
enormous until you notice it is relative: a 10% target only trades once it is outside
8-12%. This is the single largest lever on turnover in the system, and Phase 4 sweeps
it precisely because the tradeoff -- tracking error against cost -- has no obviously
right answer.

**The minimum notional** drops orders too small to be worth the commission's minimum
charge, which would otherwise make a $12 trade cost 3%.

Neither filter applies to closing a position. When the target is zero the strategy has
decided it does not want to own the thing at all, usually because a trend filter turned
against it, and "close enough" is not an acceptable answer to that.

**The band decides whether to rebalance, not which legs to trade.** That distinction
was learned the hard way. Applying the band per position independently looks obviously
right and is wrong: in a fully invested portfolio a purchase is funded by a sale, and
the band will happily suppress the sale while allowing the purchase. A 60/40 backtest
did exactly this fifty-five times -- SPY drifted 12% and stayed inside the band while
IEF drifted 21% and breached it, so the engine ordered bonds it had no cash to buy and
the broker refused. Restoring every position once any one of them breaches keeps
turnover just as low, because rebalances remain equally rare, and makes each trade set
self-funding by construction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from sillage.core.money import ZERO, dec, safe_div
from sillage.core.types import Instrument, Order, Portfolio

#: Twenty percent relative drift. Wide enough that ordinary market movement does not
#: generate trades, narrow enough that the book still resembles what the strategy asked
#: for. Phase 4 sweeps it, because the tracking-error-against-cost tradeoff behind this
#: number has no obviously right answer.
DEFAULT_BAND: Final = dec("0.20")
#: Below this, a commission's minimum charge is a meaningful fraction of the trade.
DEFAULT_MIN_NOTIONAL: Final = dec("100")


@dataclass(frozen=True, slots=True)
class _Leg:
    """One symbol's contribution to a rebalance.

    `triggers` is separate from `delta` because the two questions are different: what
    this position would trade, and whether it is far enough out to make the whole
    portfolio worth touching.
    """

    instrument: Instrument
    delta: Decimal
    triggers: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Rebalancer:
    """Diffs desired weights against actual holdings."""

    #: Relative drift tolerated before a position is traded. 0.2 means a position may
    #: sit anywhere within +/-20% of its target weight untouched.
    band: Decimal = DEFAULT_BAND
    #: Orders below this value in the base currency are not worth their commission.
    min_notional: Decimal = DEFAULT_MIN_NOTIONAL

    def __post_init__(self) -> None:
        if self.band < ZERO:
            raise ValueError("no-trade band must not be negative")
        if self.min_notional < ZERO:
            raise ValueError("minimum notional must not be negative")

    def diff(
        self,
        targets: Mapping[str, Decimal],
        *,
        portfolio: Portfolio,
        prices: Mapping[str, Decimal],
        instruments: Mapping[str, Instrument],
        ts: datetime | None = None,
    ) -> list[Order]:
        """Orders that move `portfolio` toward `targets`.

        Sized against NAV at the prices given -- the closing prices of the session the
        decision is made on. The fill happens at the next open, at a price nobody knows
        yet, so the resulting weights will be slightly off target. That error is real
        and is exactly what a live system experiences; correcting for it would require
        knowing tomorrow's price.
        """
        nav = portfolio.nav(prices)
        if nav <= ZERO:
            return []

        actual = portfolio.weights(prices)
        legs = self._legs(targets, portfolio, prices, instruments, actual, nav)
        if not any(leg.triggers for leg in legs):
            return []

        return [
            Order(
                instrument=leg.instrument,
                quantity=leg.delta,
                created_at=ts,
                reason=leg.reason,
            )
            for leg in legs
            if leg.delta != ZERO
        ]

    def _legs(
        self,
        targets: Mapping[str, Decimal],
        portfolio: Portfolio,
        prices: Mapping[str, Decimal],
        instruments: Mapping[str, Instrument],
        actual: Mapping[str, Decimal],
        nav: Decimal,
    ) -> list[_Leg]:
        """One entry per tradable symbol, each knowing its trade and whether it forces one."""
        legs: list[_Leg] = []

        for symbol in sorted(set(targets) | set(actual)):
            target = targets.get(symbol, ZERO)
            current = actual.get(symbol, ZERO)
            if target == ZERO and current == ZERO:
                continue

            instrument = instruments.get(symbol)
            price = prices.get(symbol)
            if instrument is None or price is None or price <= ZERO:
                # Unpriceable or unknown: not tradable, and silently skipping is right
                # here because the engine has already refused to include it in NAV.
                continue

            closing = target == ZERO
            desired = instrument.round_to_lot(safe_div(target * nav, price))
            delta = instrument.round_to_lot(desired - portfolio.position(instrument).quantity)
            if delta == ZERO:
                continue
            if not closing and abs(delta) * price < self.min_notional:
                continue

            legs.append(
                _Leg(
                    instrument=instrument,
                    delta=delta,
                    # A position the strategy has abandoned always forces a rebalance;
                    # a drifting one only does so once it is outside the band.
                    triggers=closing or self._breaches_band(current, target),
                    reason=(
                        f"close {symbol}"
                        if closing
                        else f"rebalance {symbol} {float(current):.2%} -> {float(target):.2%}"
                    ),
                )
            )

        return legs

    def _breaches_band(self, current: Decimal, target: Decimal) -> bool:
        """Whether drift is large enough to be worth trading.

        Measured relative to the target, so a 40% holding and a 2% holding are held to
        proportionally the same standard rather than the same absolute one. A target of
        zero never reaches here -- closing is handled separately -- so the division is
        safe.
        """
        return safe_div(abs(current - target), abs(target), default=dec(1)) > self.band
