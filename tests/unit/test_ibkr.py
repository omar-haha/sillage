"""Tests for the IBKR adapter, against a fake that behaves like a venue.

**None of this has run against a real gateway.** These test the adapter's reasoning --
idempotency, partial fills, timeouts, refusals -- which is where the design errors are.
They cannot test the protocol translation in `IBGatewayClient`, and nothing can until
there is an account to point it at.

The fake exists because the behaviours worth testing are the ones a simulator never
produces: an order that is accepted and still working, one that fills in three pieces, a
fill that arrives after the process gave up waiting.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.support import etf

from sillage.core.money import ZERO, dec
from sillage.core.types import Order, Portfolio
from sillage.execution.broker import LiveBroker
from sillage.execution.ibkr import Execution, IBClient, IBKRBroker, Placement

A, B = etf("AAA"), etf("BBB")
SESSION = date(2026, 3, 12)
TS = datetime(2026, 3, 12, 14, 30, tzinfo=UTC)


class FakeVenue:
    """A broker that can do the things a simulator cannot."""

    def __init__(self) -> None:
        self.connected = False
        self.connects = 0
        self.placed: list[tuple[str, Decimal, str]] = []
        self._status: dict[str, Placement] = {}
        self._executions: list[Execution] = []
        self._open: set[str] = set()
        self.held: dict[str, Decimal] = {}
        #: What to do with the next order placed, by symbol.
        self.behaviour: dict[str, str] = {}

    # -- the client protocol

    def connect(self) -> None:
        self.connected = True
        self.connects += 1

    def disconnect(self) -> None:
        self.connected = False

    def place(self, symbol: str, quantity: Decimal, order_ref: str, *, tif: str) -> Placement:
        self.placed.append((symbol, quantity, order_ref))
        behaviour = self.behaviour.get(symbol, "fill")
        if behaviour == "refuse":
            self._status[order_ref] = Placement(order_ref, "Inactive", message="contract is halted")
        elif behaviour == "working":
            self._status[order_ref] = Placement(order_ref, "Submitted", remaining=abs(quantity))
            self._open.add(order_ref)
        elif behaviour == "partial":
            half = quantity / dec(2)
            self.fill(order_ref, symbol, half, dec(100))
            self._status[order_ref] = Placement(
                order_ref, "Submitted", filled=abs(half), remaining=abs(half)
            )
            self._open.add(order_ref)
        else:
            self.fill(order_ref, symbol, quantity, dec(100))
            self._status[order_ref] = Placement(order_ref, "Filled", filled=abs(quantity))
        return self._status[order_ref]

    def status(self, order_ref: str) -> Placement | None:
        return self._status.get(order_ref)

    def executions(self, *, since: date | None = None) -> list[Execution]:
        return list(self._executions)

    def open_order_refs(self) -> set[str]:
        return set(self._open)

    def positions(self) -> dict[str, Decimal]:
        return dict(self.held)

    # -- helpers for arranging a scenario

    def fill(self, order_ref: str, symbol: str, quantity: Decimal, price: Decimal) -> None:
        self._executions.append(
            Execution(
                order_ref=order_ref,
                execution_id=f"e{len(self._executions)}",
                symbol=symbol,
                quantity=quantity,
                price=price,
                commission=dec("0.35"),
                ts=TS,
            )
        )
        self.held[symbol] = self.held.get(symbol, ZERO) + quantity

    def settle(self, order_ref: str, status: str = "Filled") -> None:
        previous = self._status[order_ref]
        self._status[order_ref] = Placement(order_ref, status, filled=previous.filled)
        self._open.discard(order_ref)


def broker(venue: FakeVenue, **kwargs: object) -> IBKRBroker:
    return IBKRBroker(venue, timeout=3.0, poll=1.0, sleeper=lambda _: None, **kwargs)  # type: ignore[arg-type]


def run(venue: FakeVenue, orders: list[Order], **kwargs: object):  # type: ignore[no-untyped-def]
    return broker(venue, **kwargs).execute(
        orders, portfolio=Portfolio(cash=dec(100_000)), session=SESSION, ts=TS
    )


# ------------------------------------------------------------------ the happy path


def test_a_filled_order_comes_back_as_a_fill() -> None:
    venue = FakeVenue()
    report = run(venue, [Order(A, dec(10))])
    assert len(report.fills) == 1
    assert report.fills[0].quantity == dec(10)
    assert report.fills[0].price == dec(100)
    assert report.resolved


def test_it_connects_if_it_is_not_already() -> None:
    venue = FakeVenue()
    run(venue, [Order(A, dec(10))])
    assert venue.connects == 1


def test_sells_are_submitted_before_buys() -> None:
    """Proceeds fund purchases, and a venue enforces buying power whether or not the
    strategy remembered to."""
    venue = FakeVenue()
    run(venue, [Order(A, dec(10)), Order(B, dec(-5))])
    assert [symbol for symbol, _, _ in venue.placed] == ["BBB", "AAA"]


def test_the_fill_carries_the_engine_s_own_order_id() -> None:
    venue = FakeVenue()
    order = Order(A, dec(10))
    report = run(venue, [order])
    assert report.fills[0].order_id == order.client_order_id


def test_slippage_is_left_at_zero_rather_than_invented() -> None:
    """The simulator measures against a reference price it chose. Here the reference is
    whatever the market was doing, and fabricating one would forge the very number the
    divergence report exists to measure."""
    venue = FakeVenue()
    assert run(venue, [Order(A, dec(10))]).fills[0].slippage == ZERO


# ------------------------------------------------------------------ idempotency


def test_an_order_the_venue_already_filled_is_not_resubmitted() -> None:
    """The crash-between-submit-and-record case, and the reason every order carries the
    engine's id into the venue."""
    venue = FakeVenue()
    order = Order(A, dec(10))
    venue.fill(order.client_order_id, "AAA", dec(10), dec(100))
    venue._status[order.client_order_id] = Placement(
        order.client_order_id, "Filled", filled=dec(10)
    )

    report = run(venue, [order])
    assert venue.placed == []
    assert len(report.fills) == 1


def test_an_order_the_venue_is_still_working_is_not_resubmitted() -> None:
    venue = FakeVenue()
    order = Order(A, dec(10))
    venue._open.add(order.client_order_id)
    venue._status[order.client_order_id] = Placement(order.client_order_id, "Submitted")

    report = run(venue, [order])
    assert venue.placed == []
    assert report.outstanding == [order]


def test_submitting_the_same_batch_twice_places_it_once() -> None:
    venue = FakeVenue()
    orders = [Order(A, dec(10))]
    run(venue, orders)
    run(venue, orders)
    assert len(venue.placed) == 1


# ------------------------------------------------------------------ partial fills


def test_a_partial_fill_is_a_fill_and_the_rest_stays_outstanding() -> None:
    venue = FakeVenue()
    venue.behaviour["AAA"] = "partial"
    order = Order(A, dec(10))
    report = run(venue, [order])
    assert report.fills[0].quantity == dec(5)
    assert report.outstanding == [order]
    assert not report.resolved


def test_several_executions_become_several_fills() -> None:
    """Averaging them would discard exactly what the divergence report looks at."""
    venue = FakeVenue()
    order = Order(A, dec(30))
    venue.behaviour["AAA"] = "working"
    venue.place("AAA", dec(30), order.client_order_id, tif="OPG")
    venue._open.discard(order.client_order_id)
    for price in (dec(100), dec("100.5"), dec(101)):
        venue.fill(order.client_order_id, "AAA", dec(10), price)
    venue.settle(order.client_order_id)

    report = run(venue, [order])
    assert len(report.fills) == 3
    assert {f.price for f in report.fills} == {dec(100), dec("100.5"), dec(101)}


# ------------------------------------------------------------------ refusals


def test_a_refused_order_is_a_rejection_with_the_venue_s_reason() -> None:
    venue = FakeVenue()
    venue.behaviour["AAA"] = "refuse"
    report = run(venue, [Order(A, dec(10))])
    assert not report.fills
    assert "halted" in report.rejections[0].reason


def test_an_order_the_venue_cancelled_is_rejected_not_resubmitted() -> None:
    """Cancelled orders appear in neither the open list nor the executions. Only the
    status remembers them, which is why the adapter asks."""
    venue = FakeVenue()
    venue.behaviour["AAA"] = "working"
    order = Order(A, dec(10))
    venue.place("AAA", dec(10), order.client_order_id, tif="OPG")
    venue.settle(order.client_order_id, status="Cancelled")
    venue.placed.clear()

    report = run(venue, [order])
    assert venue.placed == []
    assert not report.fills
    assert report.rejections


# ------------------------------------------------------------------ timeouts


def test_an_order_still_working_at_the_timeout_is_outstanding_not_lost() -> None:
    """A market-on-open order placed the evening before is *supposed* to be working."""
    venue = FakeVenue()
    venue.behaviour["AAA"] = "working"
    order = Order(A, dec(10))
    report = run(venue, [order])
    assert not report.fills
    assert not report.rejections
    assert report.outstanding == [order]


def test_waiting_gives_up_rather_than_blocking_forever() -> None:
    venue = FakeVenue()
    venue.behaviour["AAA"] = "working"
    slept: list[float] = []
    engine = IBKRBroker(venue, timeout=3.0, poll=1.0, sleeper=slept.append)
    engine.execute(
        [Order(A, dec(10))], portfolio=Portfolio(cash=dec(100_000)), session=SESSION, ts=TS
    )
    assert 0 < len(slept) <= 3


# ------------------------------------------------------------------ positions


def test_it_reports_what_the_venue_says_it_holds() -> None:
    """The other half of reconciliation, and the half a simulator cannot supply."""
    venue = FakeVenue()
    venue.held = {"AAA": dec(100)}
    assert broker(venue).positions() == {"AAA": dec(100)}


def test_it_satisfies_the_live_broker_protocol() -> None:
    assert isinstance(broker(FakeVenue()), LiveBroker)


def test_the_fake_satisfies_the_client_protocol() -> None:
    """If the fake drifts from the protocol, these tests stop meaning anything."""
    assert isinstance(FakeVenue(), IBClient)


# ------------------------------------------------------------------ configuration


def test_it_defaults_to_market_on_open() -> None:
    """The faithful translation of decide-at-close, fill-at-next-open."""
    assert broker(FakeVenue()).time_in_force == "OPG"


def test_it_says_whether_it_is_connected() -> None:
    venue = FakeVenue()
    assert "disconnected" in repr(broker(venue))
    venue.connect()
    assert "connected" in repr(broker(venue))


def test_the_gateway_client_refuses_to_act_unconnected() -> None:
    from sillage.execution.ibkr import IBGatewayClient

    client = IBGatewayClient()
    assert not client.connected
    with pytest.raises(RuntimeError, match="not connected"):
        client.positions()


def test_the_gateway_client_defaults_to_a_paper_port() -> None:
    """7496 and 4001 are the live ports. Neither is the default here."""
    from sillage.execution.ibkr import IBGatewayClient

    assert IBGatewayClient().port == 7497
