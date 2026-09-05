"""The domain model.

These are the nouns the whole system is built from. Everything here is deliberately
plain Python -- no pandas, no numpy. Those live at the edges (`sillage.data` and
`sillage.backtest.metrics`) so the logic that decides what to own and how much cash you
have stays readable and exactly testable.

**Precision rule.** Only numbers that correspond to real money movements are rounded
to the cent: `Portfolio.cash` and `Position.commission_paid`, because a broker confirms
those to the cent and our books must agree with theirs exactly. Everything derived --
cost basis, realised and unrealised P&L, market value, NAV -- keeps full Decimal
precision and is rounded once, at the point of display. Rounding derived values on
every fill throws away sub-cent remainders that accumulate into a visibly wrong equity
curve over a long backtest.

Value objects are frozen dataclasses: once created they cannot be mutated. That is not
ceremony. An `Order` that can be edited after being sent to a broker is a bug waiting
to happen, and immutability means a `Fill` can be safely stored in a journal and
replayed without anyone having quietly changed it in between.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self

from sillage.core.money import (
    ZERO,
    dec,
    quantize_cash,
    quantize_qty,
    safe_div,
)


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    CRYPTO = "crypto"
    CASH = "cash"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Something that can be held.

    `symbol` is the venue-neutral identifier used everywhere internally. Each broker
    adapter is responsible for translating it to whatever that venue calls the thing,
    so no venue's naming quirks leak into the strategy layer.
    """

    symbol: str
    asset_class: AssetClass
    currency: str = "USD"
    exchange: str | None = None
    # Smallest tradable increment. Whole shares for most equities, but fractional for
    # crypto and for brokers that support fractional share trading.
    lot_size: Decimal = Decimal("1")
    # Crypto never closes; equities respect an exchange calendar. The engine needs to
    # know this to decide whether a given timestamp is even tradable.
    trades_continuously: bool = False

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("instrument symbol must not be empty")
        if self.lot_size <= ZERO:
            raise ValueError(f"{self.symbol}: lot_size must be positive")

    @property
    def is_cash(self) -> bool:
        return self.asset_class is AssetClass.CASH

    def round_to_lot(self, quantity: Decimal) -> Decimal:
        """Round a desired quantity down toward zero to a tradable size.

        Always toward zero, never away: rounding a buy up would spend cash you may not
        have, and rounding a sell up would short you by accident.
        """
        if self.lot_size == ZERO:
            return quantity
        lots = (abs(quantity) / self.lot_size).to_integral_value(rounding="ROUND_FLOOR")
        magnitude = lots * self.lot_size
        return quantize_qty(magnitude if quantity >= ZERO else -magnitude)

    def __str__(self) -> str:
        return self.symbol


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV price bar.

    `ts` is the timestamp at which the bar **closed**, and it is always timezone-aware
    UTC. This convention is load-bearing: a daily bar stamped 2015-03-10 contains that
    day's closing price, which was not knowable until the close. Code that treats `ts`
    as the bar's *opening* time will happily trade on information from the future and
    produce a backtest that cannot be reproduced with real money.

    Prices are assumed adjusted for splits and dividends unless stated otherwise --
    see `sillage.data` for how adjustment is applied.
    """

    instrument: Instrument
    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = ZERO

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError(f"{self.instrument}: bar timestamp must be timezone-aware")
        if self.low > self.high:
            raise ValueError(f"{self.instrument} @ {self.ts}: low {self.low} > high {self.high}")
        for name in ("open", "close"):
            price: Decimal = getattr(self, name)
            if not (self.low <= price <= self.high):
                raise ValueError(
                    f"{self.instrument} @ {self.ts}: {name} {price} outside [{self.low}, {self.high}]"
                )
        if any(p <= ZERO for p in (self.open, self.high, self.low, self.close)):
            raise ValueError(f"{self.instrument} @ {self.ts}: prices must be positive")

    @property
    def typical_price(self) -> Decimal:
        """(H + L + C) / 3 -- a cheap stand-in for the volume-weighted average price.

        Used by the simulated broker as a fill reference when modelling an order that
        would realistically execute across the session rather than at a single print.
        """
        return (self.high + self.low + self.close) / dec(3)


@dataclass(frozen=True, slots=True)
class Order:
    """An instruction to change a position.

    `quantity` is signed: positive buys, negative sells. Carrying the direction in the
    number rather than in a separate side field means position arithmetic is ordinary
    addition, and it removes a whole category of bug where the sign and the side
    disagree. `side` is derived for the benefit of broker APIs that want it.
    """

    instrument: Instrument
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    # Set by the engine when the order is created; the broker adapter must pass it
    # through so a retry after a timeout cannot submit the same order twice.
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.quantity == ZERO:
            raise ValueError(f"{self.instrument}: order quantity must be non-zero")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError(f"{self.instrument}: limit order requires a limit price")
        if self.limit_price is not None and self.limit_price <= ZERO:
            raise ValueError(f"{self.instrument}: limit price must be positive")

    @property
    def side(self) -> Side:
        return Side.BUY if self.quantity > ZERO else Side.SELL

    @property
    def abs_quantity(self) -> Decimal:
        return abs(self.quantity)


@dataclass(frozen=True, slots=True)
class Fill:
    """An executed trade. Signed the same way as `Order.quantity`.

    `commission` and `slippage` are recorded separately rather than folded into the
    price, because Phase 4 needs to answer "at what cost level does this strategy stop
    working?" and that question is unanswerable once costs are baked into the fill.
    """

    instrument: Instrument
    ts: datetime
    quantity: Decimal
    price: Decimal
    commission: Decimal = ZERO
    slippage: Decimal = ZERO
    order_id: str = ""

    def __post_init__(self) -> None:
        if self.quantity == ZERO:
            raise ValueError(f"{self.instrument}: fill quantity must be non-zero")
        if self.price <= ZERO:
            raise ValueError(f"{self.instrument}: fill price must be positive")
        if self.commission < ZERO:
            raise ValueError(f"{self.instrument}: commission must not be negative")

    @property
    def side(self) -> Side:
        return Side.BUY if self.quantity > ZERO else Side.SELL

    @property
    def gross_value(self) -> Decimal:
        """Signed cash impact before costs: negative when buying, positive when selling."""
        return -(self.quantity * self.price)

    @property
    def cash_impact(self) -> Decimal:
        """Signed change to cash, costs included. This is the number the ledger uses."""
        return quantize_cash(self.gross_value - self.commission)


@dataclass(frozen=True, slots=True)
class Position:
    """A holding, with average-cost basis and realised P&L.

    Average-cost accounting is the fiddly part of any portfolio system, and it is where
    silent errors hide. The three cases handled in `apply_fill` are: adding to a
    position, reducing one, and flipping through zero from long to short or back. The
    third is the one that gets written wrong.
    """

    instrument: Instrument
    quantity: Decimal = ZERO
    avg_cost: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    commission_paid: Decimal = ZERO

    @property
    def is_flat(self) -> bool:
        return self.quantity == ZERO

    @property
    def is_long(self) -> bool:
        return self.quantity > ZERO

    @property
    def cost_basis(self) -> Decimal:
        return abs(self.quantity) * self.avg_cost

    def market_value(self, price: Decimal) -> Decimal:
        """Signed value of the holding. Negative for a short position.

        Deliberately not rounded to the cent -- see the note on precision in the module
        docstring. Round at the point of display, not here.
        """
        return self.quantity * price

    def unrealized_pnl(self, price: Decimal) -> Decimal:
        if self.is_flat:
            return ZERO
        return self.quantity * (price - self.avg_cost)

    @property
    def net_realized_pnl(self) -> Decimal:
        """Realised P&L after the commissions paid to achieve it."""
        return self.realized_pnl - self.commission_paid

    def apply_fill(self, fill: Fill) -> Self:
        """Return a new Position reflecting this fill. Never mutates."""
        if fill.instrument != self.instrument:
            raise ValueError(
                f"fill for {fill.instrument} cannot be applied to position in {self.instrument}"
            )

        new_qty = quantize_qty(self.quantity + fill.quantity)
        commission_paid = self.commission_paid + fill.commission
        realized = self.realized_pnl

        opening = self.is_flat or (self.quantity > ZERO) == (fill.quantity > ZERO)

        if opening:
            # Adding to (or starting) a position: the cost basis is the weighted
            # average of what we held and what we just bought.
            total_cost = self.cost_basis + abs(fill.quantity) * fill.price
            avg_cost = safe_div(total_cost, abs(new_qty), default=fill.price)
        elif abs(fill.quantity) <= abs(self.quantity):
            # Reducing, possibly to exactly flat. The basis of the remaining shares is
            # unchanged -- only the closed portion realises a gain or loss.
            closed = abs(fill.quantity)
            direction = dec(1) if self.is_long else dec(-1)
            realized += closed * (fill.price - self.avg_cost) * direction
            avg_cost = self.avg_cost if new_qty != ZERO else ZERO
        else:
            # Flipping through zero: close the whole existing position at the fill
            # price, then open a new one in the opposite direction with the remainder.
            closed = abs(self.quantity)
            direction = dec(1) if self.is_long else dec(-1)
            realized += closed * (fill.price - self.avg_cost) * direction
            avg_cost = fill.price

        return replace(
            self,
            quantity=new_qty,
            # avg_cost and realized_pnl stay at full precision on purpose. Rounding
            # them to the cent on every fill discards the sub-cent remainder, and over
            # thousands of trades that lost dust is what makes an equity curve drift
            # away from the truth.
            avg_cost=avg_cost if new_qty != ZERO else ZERO,
            realized_pnl=realized,
            commission_paid=quantize_cash(commission_paid),
        )


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Cash plus positions. The single source of truth for what is owned.

    Immutable like everything else: applying a fill returns a new Portfolio. That makes
    the backtest trivially able to snapshot state at any point, and means a bug can
    never corrupt history retroactively.
    """

    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)
    base_currency: str = "USD"

    def position(self, instrument: Instrument) -> Position:
        """The position in an instrument, or an empty one. Never raises."""
        return self.positions.get(instrument.symbol, Position(instrument=instrument))

    def apply_fill(self, fill: Fill) -> Self:
        updated = self.position(fill.instrument).apply_fill(fill)
        positions = dict(self.positions)
        if updated.is_flat and updated.realized_pnl == ZERO and updated.commission_paid == ZERO:
            positions.pop(fill.instrument.symbol, None)
        else:
            positions[fill.instrument.symbol] = updated
        return replace(
            self,
            cash=quantize_cash(self.cash + fill.cash_impact),
            positions=positions,
        )

    def market_value(self, prices: dict[str, Decimal]) -> Decimal:
        """Total value of holdings, excluding cash.

        Raises on a missing price rather than treating the position as worthless. A
        silently-zero NAV is far more dangerous than a loud failure.
        """
        total = ZERO
        for symbol, pos in self.positions.items():
            if pos.is_flat:
                continue
            if symbol not in prices:
                raise KeyError(f"no price for held instrument {symbol!r}; cannot value portfolio")
            total += pos.market_value(prices[symbol])
        return total

    def nav(self, prices: dict[str, Decimal]) -> Decimal:
        """Net asset value: what the whole fund is worth right now.

        Full precision. Use `quantize_cash` when presenting it to a human.
        """
        return self.cash + self.market_value(prices)

    def weights(self, prices: dict[str, Decimal]) -> dict[str, Decimal]:
        """Each position's share of NAV. Signed, and does not include cash.

        These are the numbers the strategy layer speaks in: a strategy returns target
        weights, and the rebalancer's job is to close the gap between these and those.
        """
        nav = self.nav(prices)
        if nav <= ZERO:
            return {}
        return {
            symbol: safe_div(pos.market_value(prices[symbol]), nav)
            for symbol, pos in self.positions.items()
            if not pos.is_flat
        }

    def gross_exposure(self, prices: dict[str, Decimal]) -> Decimal:
        """Sum of absolute position values over NAV -- 1.0 means fully invested, no leverage."""
        nav = self.nav(prices)
        gross = sum(
            (abs(p.market_value(prices[s])) for s, p in self.positions.items() if not p.is_flat),
            start=ZERO,
        )
        return safe_div(gross, nav)
