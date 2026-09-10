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
from decimal import Decimal
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
    """Everything that came back from one round of submissions.

    Three buckets, not two. A simulator resolves every order it is given -- it fills or
    it refuses, and the answer is known before the call returns. A real venue does not
    work that way: an order can be accepted and still be working when the timeout
    expires, and the fill may arrive minutes later or after the process has exited.
    Collapsing that third state into either of the others is how live systems either
    lose orders or send them twice.
    """

    fills: list[Fill] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)
    #: Accepted by the venue and neither filled nor refused yet. The caller must keep
    #: these and stop submitting them; they are already at the broker.
    outstanding: list[Order] = field(default_factory=list)

    @property
    def all_filled(self) -> bool:
        return not self.rejections and not self.outstanding

    @property
    def resolved(self) -> bool:
        """Whether every order reached a terminal state during this call."""
        return not self.outstanding


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


@runtime_checkable
class LiveBroker(Broker, Protocol):
    """A broker that holds real positions someone else is also keeping track of.

    The extra method is the whole difference. A simulator's positions are whatever the
    caller says they are, so asking is meaningless; a real venue has its own record, and
    the gap between the two is the thing reconciliation exists to find.
    """

    def positions(self) -> dict[str, Decimal]:
        """What the venue says is held, by symbol. Signed."""
        ...
