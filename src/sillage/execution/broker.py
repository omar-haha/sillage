"""The broker interface.

The other half of what lets one engine both backtest and trade. The loop hands orders
to something satisfying this protocol and gets back what happened; whether that
something is a simulator, an IBKR gateway, or a crypto exchange is not its concern.

Two decisions in the shape below are worth stating.

**Execution returns a report, not a list of fills.** An order that does not fill is
information -- it means the strategy asked for something the market would not give it,
and a system that silently drops those will show a backtest holding positions it could
never have acquired. Rejections are first-class and get journalled.

**Executing takes the portfolio.** A real broker knows your buying power and will not
let a cash account spend money it does not have. Modelling that is part of modelling a
venue, so the simulator is given the same information a real one already has, rather
than being allowed to fill orders that reality would have refused.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from sillage.core.types import Fill, Order, Portfolio


@dataclass(frozen=True, slots=True)
class Rejection:
    """An order the venue would not fill, and why."""

    order: Order
    reason: str
    ts: datetime | None = None

    def __str__(self) -> str:
        return f"{self.order.instrument} {self.order.quantity:+}: {self.reason}"


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Everything that came back from one round of submissions."""

    fills: list[Fill] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)

    @property
    def all_filled(self) -> bool:
        return not self.rejections


class BrokerError(RuntimeError):
    """The broker itself failed, as distinct from an order being rejected."""


@runtime_checkable
class Broker(Protocol):
    """Somewhere orders can be sent."""

    name: str

    def execute(
        self,
        orders: Sequence[Order],
        *,
        portfolio: Portfolio,
        session: date,
        ts: datetime,
    ) -> ExecutionReport:
        """Submit a batch of orders and report what happened.

        Batched rather than one at a time because a rebalance is inherently a set: the
        sells fund the buys, and a broker that handled each order in isolation could
        reject a buy for lack of cash that a sell in the same batch was about to
        provide.
        """
        ...
