"""A simulated venue.

This is the broker every backtest runs against, and -- pointed at a live clock -- it is
also the paper-trading engine for Phase 5a. Building it rather than renting somebody
else's paper account means the fill model is inspectable, which matters because the fill
model is precisely where a backtest becomes optimistic.

What it models: the price concession from crossing the spread, market impact scaled to
the order's share of daily volume, commission, cash-account buying power, and the
inability to trade an asset that did not print that session.

What it deliberately does not model, and is dishonest to pretend otherwise about:
queue position, partial fills from thin books, price improvement, trading halts, and
the fact that a real order sent at 09:30:00 does not fill at exactly the official
opening price. Those need a real venue to observe, which is the entire justification
for Phase 5b existing as a separate phase.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Final

from sillage.core.money import ZERO, dec, quantize_price
from sillage.core.types import Fill, Order, Portfolio
from sillage.engine.feed import ExecutionFeed
from sillage.execution.broker import ExecutionReport, Rejection
from sillage.execution.costs import CostModel

#: No venue absorbs an order worth a tenth of the day's entire volume at the open
#: without moving badly. At this portfolio's size against these ETFs the cap never
#: binds; it exists so that it would bind before the impact model is extrapolated
#: somewhere it was never calibrated.
MAX_PARTICIPATION: Final = dec("0.10")


class SimulatedBroker:
    """Fills orders at the session open, adjusted for costs."""

    name = "simulated"

    def __init__(
        self,
        feed: ExecutionFeed,
        costs: CostModel | None = None,
        *,
        allow_short: bool = False,
        enforce_buying_power: bool = True,
        max_participation: Decimal = MAX_PARTICIPATION,
    ) -> None:
        self.feed = feed
        self.costs = costs or CostModel()
        self.allow_short = allow_short
        self.enforce_buying_power = enforce_buying_power
        self.max_participation = max_participation

    def execute(
        self,
        orders: Sequence[Order],
        *,
        portfolio: Portfolio,
        session: date,
        ts: datetime,
    ) -> ExecutionReport:
        fills: list[Fill] = []
        rejections: list[Rejection] = []
        # Sells before buys, so proceeds are available to fund the purchases in the
        # same rebalance. This is what a real rebalancing algorithm does, and doing it
        # the other way round would have the simulator reject buys for lack of cash
        # that was moments away from arriving.
        cash = portfolio.cash

        for order in sorted(orders, key=lambda o: o.quantity):
            outcome = self._execute_one(order, portfolio, cash, session, ts)
            match outcome:
                case Rejection() as rejection:
                    rejections.append(rejection)
                case Fill() as fill:
                    fills.append(fill)
                    cash += fill.cash_impact

        return ExecutionReport(fills=fills, rejections=rejections)

    def _execute_one(
        self,
        order: Order,
        portfolio: Portfolio,
        cash: Decimal,
        session: date,
        ts: datetime,
    ) -> Fill | Rejection:
        symbol = order.instrument.symbol

        reference = self.feed.open_price(symbol, session)
        if reference is None:
            return Rejection(order, f"no open price for {symbol} on {session}", ts)

        quantity = order.quantity
        if not self.allow_short:
            held = portfolio.position(order.instrument).quantity
            if quantity < ZERO and quantity < -held:
                # Trim rather than reject: selling everything held is a coherent
                # response to "sell more than you have" in a long-only account, and
                # rejecting outright would leave a position the strategy wanted closed.
                quantity = -held
                if quantity == ZERO:
                    return Rejection(order, f"nothing held in {symbol} to sell", ts)

        volume = self.feed.average_volume(symbol, session, self.costs.adv_window)
        if volume is not None and volume > ZERO:
            cap = volume * self.max_participation
            if abs(quantity) > cap:
                capped = order.instrument.round_to_lot(cap)
                if capped == ZERO:
                    return Rejection(
                        order, f"order exceeds {self.max_participation:.0%} of ADV", ts
                    )
                quantity = capped if quantity > ZERO else -capped

        if quantity > ZERO and self.enforce_buying_power:
            quantity = self._affordable(order, quantity, reference, volume, cash)
            if quantity == ZERO:
                return Rejection(order, "insufficient cash", ts)

        quantity = order.instrument.round_to_lot(quantity)
        if quantity == ZERO:
            return Rejection(order, "rounds to zero at this lot size", ts)

        price = quantize_price(self.costs.fill_price(symbol, reference, quantity, volume))
        return Fill(
            instrument=order.instrument,
            ts=ts,
            quantity=quantity,
            price=price,
            commission=self.costs.commission(quantity, price),
            # Recorded as a positive cost in currency, kept out of the price so Phase 4
            # can subtract it back out and ask what the strategy would earn if
            # execution were free.
            slippage=abs(price - reference) * abs(quantity),
            order_id=order.client_order_id,
        )

    def _affordable(
        self,
        order: Order,
        quantity: Decimal,
        reference: Decimal,
        volume: Decimal | None,
        cash: Decimal,
    ) -> Decimal:
        """Shrink a buy until the account can pay for it.

        Solved by stepping down rather than algebraically, because commission has a
        minimum and impact grows with size, so the cost of a purchase is not linear in
        its quantity and there is no closed form worth trusting. Two iterations settle
        it in practice; the loop is bounded so a pathological cost model cannot hang
        the backtest.
        """
        if cash <= ZERO:
            return ZERO

        for _ in range(8):
            price = self.costs.fill_price(order.instrument.symbol, reference, quantity, volume)
            cost = quantity * price + self.costs.commission(quantity, price)
            if cost <= cash:
                return quantity
            # Scale to what the cash covers at the price just computed, then take a
            # further sliver off so a rounding error cannot leave it a cent short.
            quantity = order.instrument.round_to_lot(quantity * (cash / cost) * dec("0.999"))
            if quantity <= ZERO:
                return ZERO
        return ZERO

    def __repr__(self) -> str:
        return f"SimulatedBroker(costs={self.costs!r}, allow_short={self.allow_short})"
