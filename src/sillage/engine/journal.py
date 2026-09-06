"""The record of what the system did.

Everything the engine decides or observes goes through here: orders raised, fills
received, orders refused, and the value of the fund at every close. Nothing else in the
engine keeps history, which means there is exactly one place to look when a result
needs explaining, and exactly one thing to reimplement when the same record has to
survive a process restart.

The in-memory implementation below is what a backtest uses -- five thousand sessions of
records is a few megabytes and there is nothing to persist. Phase 5 adds a database
implementation satisfying the same protocol, because a live system that forgets what it
submitted will resubmit it.

The interface is append-only on purpose. A journal whose entries can be edited is not a
journal; reconciliation depends on the record being what actually happened rather than
what a later run believed should have happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from sillage.core.money import ZERO
from sillage.core.types import Fill, Order
from sillage.execution.broker import Rejection


@dataclass(frozen=True, slots=True)
class NavPoint:
    """The fund's value at one session close.

    Kept at full precision rather than rounded to the cent. Rounding a NAV series
    before computing returns from it introduces an error of the order of a basis point
    in the daily figures, which is small in isolation and visible after compounding
    five thousand of them.
    """

    session: date
    ts: datetime
    nav: Decimal
    cash: Decimal
    gross_exposure: Decimal
    holdings: int = 0
    #: Each holding's share of NAV at this close. Carried because an equity curve
    #: cannot answer "what was it actually holding in March 2020", and that is usually
    #: the first question anyone asks of a result.
    weights: dict[str, Decimal] = field(default_factory=dict)

    @property
    def invested(self) -> Decimal:
        return self.nav - self.cash


@runtime_checkable
class Journal(Protocol):
    """Where the engine writes what happened."""

    def record_order(self, order: Order) -> None: ...

    def record_fill(self, fill: Fill) -> None: ...

    def record_rejection(self, rejection: Rejection) -> None: ...

    def record_nav(self, point: NavPoint) -> None: ...


@dataclass(slots=True)
class InMemoryJournal:
    """A journal that lives for as long as the run does."""

    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    nav_points: list[NavPoint] = field(default_factory=list)

    def record_order(self, order: Order) -> None:
        self.orders.append(order)

    def record_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def record_rejection(self, rejection: Rejection) -> None:
        self.rejections.append(rejection)

    def record_nav(self, point: NavPoint) -> None:
        self.nav_points.append(point)

    @property
    def total_commission(self) -> Decimal:
        return sum((f.commission for f in self.fills), start=ZERO)

    @property
    def total_slippage(self) -> Decimal:
        return sum((f.slippage for f in self.fills), start=ZERO)

    @property
    def traded_notional(self) -> Decimal:
        """Gross value traded, both directions. The raw input to turnover."""
        return sum((abs(f.quantity) * f.price for f in self.fills), start=ZERO)
