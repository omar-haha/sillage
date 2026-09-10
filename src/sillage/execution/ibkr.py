"""A real broker on the other side.

Everything up to this point has been graded by a simulator that this project also
wrote, which means every fill so far has been marked by the same hand that produced it.
That is fine for testing whether the machinery works and worthless for testing whether
the *prices* are honest. This module puts Interactive Brokers on the other side of the
`Broker` protocol so the two can be compared.

**What a real venue does that a simulator does not.**

*Orders do not resolve when you ask.* A simulator fills or refuses before the call
returns. IBKR accepts an order and it works — for a second, for an hour, or until the
open if it is a market-on-open. So `execute` has a timeout, and anything still working
when it expires comes back as `outstanding`: already at the broker, not to be resent.
Everything about the design of this file follows from that one fact.

*Orders fill in pieces.* One instruction becomes several executions at several prices.
Each becomes its own domain `Fill`, because averaging them would throw away the
information the divergence report exists to measure.

*Prices improve.* A market order can fill better than the quote. The simulator can never
do this — it moves every price against the trader by construction — so a real fill
beating the model is expected, and measuring how often is part of the point.

*Orders are refused for reasons a simulator has no concept of.* Halts, margin, a symbol
that is not shortable, a contract that will not qualify. These arrive as a status and a
message, and both are journalled rather than collapsed into "rejected".

**Idempotency is the load-bearing property.** Every order carries the engine's own
`client_order_id` as its `orderRef`. Before submitting anything, the adapter asks the
venue what it already has — open orders and today's executions — and refuses to place
one it has seen before. A crash between "submitted" and "recorded" is otherwise
indistinguishable from "never submitted", and the difference is a duplicated trade.

**Nothing here has run against a real gateway.** The account did not exist when it was
written. The logic is tested against a fake that models the venue's behaviours, which
catches design errors and cannot catch protocol errors. Until `sillage broker check`
succeeds against a live IB Gateway, treat this as unverified — and see `docs/ibkr.md`.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sillage.core.money import ZERO, dec, quantize_price
from sillage.core.types import Fill, Order, Portfolio
from sillage.execution.broker import ExecutionReport, Rejection

#: IB's terminal order states. Anything else is still working.
DONE_STATES = frozenset({"Filled", "Cancelled", "Inactive", "ApiCancelled"})

#: How long `execute` waits for an order to reach a terminal state before giving up and
#: reporting it outstanding. Generous, because giving up early is free -- the order is
#: still at the broker and will be collected next run -- while giving up late blocks a
#: cron job.
DEFAULT_TIMEOUT = 45.0
DEFAULT_POLL = 1.0

#: Market-on-open. The faithful translation of this system's semantics: decisions are
#: made at a close and are supposed to execute at the next open, which is precisely what
#: this order type does. A plain market order sent at 09:30:00 would fill at whatever
#: the first print happened to be, which is a different and worse thing.
OPENING = "OPG"


@dataclass(frozen=True, slots=True)
class Execution:
    """One execution, normalised. Several of these can belong to one order."""

    order_ref: str
    execution_id: str
    symbol: str
    #: Signed the way the domain signs everything: positive bought, negative sold.
    quantity: Decimal
    price: Decimal
    commission: Decimal
    ts: datetime


@dataclass(frozen=True, slots=True)
class Placement:
    """What the venue currently says about one order."""

    order_ref: str
    status: str
    filled: Decimal = ZERO
    remaining: Decimal = ZERO
    average_price: Decimal = ZERO
    message: str = ""

    @property
    def done(self) -> bool:
        return self.status in DONE_STATES

    @property
    def refused(self) -> bool:
        """Terminal, and nothing was filled. A halt, a margin failure, a bad contract."""
        return self.done and self.filled == ZERO


@runtime_checkable
class IBClient(Protocol):
    """The slice of the broker API this adapter uses.

    Narrow on purpose, and normalised at this boundary so that `ib_async`'s types never
    reach the rest of the system. Two things fall out of that. The adapter's logic --
    idempotency, partial fills, timeouts -- is testable against a fake that models the
    venue's behaviour without a gateway or an account. And replacing the client library,
    which is a third-party wrapper around an undocumented socket protocol, means writing
    one file.
    """

    @property
    def connected(self) -> bool: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def place(self, symbol: str, quantity: Decimal, order_ref: str, *, tif: str) -> Placement:
        """Submit an order. Returns its status immediately, which is usually not final."""
        ...

    def status(self, order_ref: str) -> Placement | None:
        """The venue's current view of an order, or None if it has never heard of it."""
        ...

    def executions(self, *, since: date | None = None) -> list[Execution]: ...

    def open_order_refs(self) -> set[str]: ...

    def positions(self) -> dict[str, Decimal]: ...


class IBKRBroker:
    """Places orders at Interactive Brokers and reports what actually happened."""

    name = "ibkr"

    def __init__(
        self,
        client: IBClient,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        poll: float = DEFAULT_POLL,
        time_in_force: str = OPENING,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.timeout = timeout
        self.poll = poll
        self.time_in_force = time_in_force
        # Injected so tests do not spend forty-five seconds proving a timeout works.
        self._sleep = sleeper

    # ------------------------------------------------------------------ the protocol

    def execute(
        self,
        orders: Sequence[Order],
        *,
        portfolio: Portfolio,  # noqa: ARG002 - see below
        session: date,
        ts: datetime,
    ) -> ExecutionReport:
        """Submit what has not already been submitted, then wait for what can be waited for.

        `portfolio` is accepted and ignored. The simulator needs it to enforce buying
        power, because nothing else would; a real venue enforces its own, and checking
        first here would mean two answers to the same question with no way to know which
        was right.
        """
        if not self.client.connected:
            self.client.connect()

        already = self._already_submitted(session)
        submitted: dict[str, Order] = {}
        rejections: list[Rejection] = []

        # Sells before buys, as in the simulator: proceeds fund purchases, and a venue
        # enforces buying power whether or not the strategy remembered to.
        for order in sorted(orders, key=lambda o: o.quantity):
            reference = order.client_order_id
            if reference in already or self.client.status(reference) is not None:
                # Seen before. This is the crash-between-submit-and-record case, and it
                # is the reason every order carries the engine's own id into the venue.
                #
                # Asking for the status as well as checking the two lists is not
                # redundant. An order the venue accepted and then cancelled -- a halt, a
                # margin failure -- is in neither: it is not working, and it never
                # executed. Only its status remembers it happened, and without that it
                # would be placed again on the next attempt.
                submitted[reference] = order
                continue
            placement = self.client.place(
                order.instrument.symbol,
                order.quantity,
                reference,
                tif=self.time_in_force,
            )
            if placement.refused:
                rejections.append(Rejection(order, _reason(placement), ts))
                continue
            submitted[reference] = order

        self._await_resolution(set(submitted))

        fills, outstanding = self._collect(submitted, session, ts, rejections)
        return ExecutionReport(fills=fills, rejections=rejections, outstanding=outstanding)

    def positions(self) -> dict[str, Decimal]:
        """What the venue says is held. The other half of reconciliation."""
        if not self.client.connected:
            self.client.connect()
        return self.client.positions()

    # ------------------------------------------------------------------ the pieces

    def _already_submitted(self, session: date) -> set[str]:
        """Order references the venue has already seen, from either direction.

        Both are needed. An open order is one the venue accepted and is still working; an
        execution is one it accepted and filled. Checking only the first would resubmit
        anything that filled while the process was dead, which is the expensive mistake.
        """
        return self.client.open_order_refs() | {
            execution.order_ref for execution in self.client.executions(since=session)
        }

    def _await_resolution(self, references: set[str]) -> None:
        """Poll until every order is terminal, or the timeout expires.

        The timeout is not a failure. An order that is still working is in exactly the
        state a market-on-open order is supposed to be in the evening before, and the
        next run collects it.
        """
        deadline = self.timeout
        while deadline > 0 and references:
            references = {
                reference
                for reference in references
                if not (status := self.client.status(reference)) or not status.done
            }
            if not references:
                return
            self._sleep(self.poll)
            deadline -= self.poll

    def _collect(
        self,
        submitted: dict[str, Order],
        session: date,
        ts: datetime,
        rejections: list[Rejection],
    ) -> tuple[list[Fill], list[Order]]:
        """Turn the venue's executions into domain fills, and sort out what is left.

        Fills are built from executions rather than from an order's average price,
        because an order that filled in four pieces at four prices *did* fill in four
        pieces, and flattening that would discard exactly what the divergence report
        wants to look at.
        """
        executions = [e for e in self.client.executions(since=session) if e.order_ref in submitted]

        fills = [
            Fill(
                instrument=submitted[execution.order_ref].instrument,
                ts=execution.ts,
                quantity=execution.quantity,
                price=quantize_price(execution.price),
                commission=execution.commission,
                # Left at zero deliberately. The simulator computes slippage against a
                # reference price it chose; here the reference is whatever the market
                # was doing, and inventing one would fabricate the very number Phase 5b
                # exists to measure honestly.
                slippage=ZERO,
                order_id=execution.order_ref,
            )
            for execution in executions
            if execution.quantity != ZERO
        ]

        filled = {execution.order_ref for execution in executions}
        outstanding: list[Order] = []
        for reference, order in submitted.items():
            status = self.client.status(reference)
            if status is not None and status.done:
                if reference not in filled:
                    rejections.append(Rejection(order, _reason(status), ts))
                continue
            # Not terminal, so the venue still has it -- including the case where part
            # of it filled and the rest is working. The whole order stays outstanding
            # because that is what the broker is holding; the adapter will recognise it
            # as already submitted next time and collect the remainder rather than
            # sending a second one.
            outstanding.append(order)
        return fills, outstanding

    def __repr__(self) -> str:
        state = "connected" if self.client.connected else "disconnected"
        return f"IBKRBroker({state}, tif={self.time_in_force}, timeout={self.timeout}s)"


def _reason(placement: Placement) -> str:
    return placement.message or f"broker returned {placement.status!r}"


@dataclass
class IBGatewayClient:
    """A thin translation of `ib_async` into the shape above.

    Deliberately thin. Everything with a decision in it lives in `IBKRBroker`, where it
    can be tested; this only converts types and shapes, so the part that cannot be tested
    without a gateway is also the part with the least in it.
    """

    host: str = "127.0.0.1"
    #: 7497 is the paper-trading port for TWS, 4002 for the Gateway. The live ports --
    #: 7496 and 4001 -- are deliberately not the default.
    port: int = 7497
    client_id: int = 17
    account: str = ""
    #: Read-only refuses to place orders at the socket level. The right default for
    #: anything whose first job is to be inspected.
    readonly: bool = False
    exchange: str = "SMART"
    currency: str = "USD"
    _ib: object | None = field(default=None, init=False, repr=False)

    @property
    def connected(self) -> bool:
        return self._ib is not None and bool(self._ib.isConnected())  # type: ignore[attr-defined]

    def connect(self) -> None:
        from ib_async import IB

        if self._ib is None:
            self._ib = IB()
        self._ib.connect(  # type: ignore[attr-defined]
            self.host,
            self.port,
            clientId=self.client_id,
            account=self.account,
            readonly=self.readonly,
        )

    def disconnect(self) -> None:
        if self._ib is not None:
            self._ib.disconnect()  # type: ignore[attr-defined]

    def place(self, symbol: str, quantity: Decimal, order_ref: str, *, tif: str) -> Placement:
        from ib_async import MarketOrder

        ib = self._require()
        order = MarketOrder(
            "BUY" if quantity > ZERO else "SELL",
            float(abs(quantity)),
            orderRef=order_ref,
            tif=tif,
        )
        trade = ib.placeOrder(self._contract(symbol), order)
        return _placement(trade.orderStatus, order_ref, trade.log)

    def status(self, order_ref: str) -> Placement | None:
        ib = self._require()
        ib.waitOnUpdate(timeout=0.1)
        for trade in ib.trades():
            if trade.order.orderRef == order_ref:
                return _placement(trade.orderStatus, order_ref, trade.log)
        return None

    def executions(self, *, since: date | None = None) -> list[Execution]:
        from ib_async import ExecutionFilter

        ib = self._require()
        raw = ib.reqExecutions(
            ExecutionFilter(time=since.strftime("%Y%m%d 00:00:00") if since else "")
        )
        return [
            Execution(
                order_ref=item.execution.orderRef,
                execution_id=item.execution.execId,
                symbol=item.contract.symbol,
                quantity=dec(item.execution.shares)
                * (dec(-1) if item.execution.side.upper().startswith("S") else dec(1)),
                price=dec(item.execution.price),
                commission=dec(abs(item.commissionReport.commission or 0.0)),
                ts=item.time.astimezone(UTC) if item.time else datetime.now(UTC),
            )
            for item in raw
        ]

    def open_order_refs(self) -> set[str]:
        ib = self._require()
        return {trade.order.orderRef for trade in ib.openTrades() if trade.order.orderRef}

    def positions(self) -> dict[str, Decimal]:
        ib = self._require()
        held = {}
        for position in ib.positions(self.account):
            if position.position:
                held[position.contract.symbol] = dec(position.position)
        return held

    def _contract(self, symbol: str) -> object:
        from ib_async import Stock

        ib = self._require()
        contracts = ib.qualifyContracts(Stock(symbol, self.exchange, self.currency))
        if not contracts:
            raise ValueError(f"IBKR could not resolve a contract for {symbol!r}")
        return contracts[0]

    def _require(self) -> Any:
        if self._ib is None or not self.connected:
            raise RuntimeError("not connected to IB Gateway; call connect() first")
        return self._ib


def _placement(status: object, order_ref: str, log: Sequence[object] = ()) -> Placement:
    message = ""
    for entry in reversed(list(log)):
        text = getattr(entry, "message", "")
        if text:
            message = str(text)
            break
    return Placement(
        order_ref=order_ref,
        status=str(getattr(status, "status", "")),
        filled=dec(getattr(status, "filled", 0.0)),
        remaining=dec(getattr(status, "remaining", 0.0)),
        average_price=dec(getattr(status, "avgFillPrice", 0.0) or 0.0),
        message=message,
    )
