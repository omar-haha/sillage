"""Tests for the simulated venue.

Half of these are about what the broker *refuses* to do. A simulator that fills
everything asked of it produces a backtest holding positions no real account could have
acquired, and the resulting equity curve is unreachable rather than merely optimistic.
"""

from __future__ import annotations

from decimal import Decimal

from tests.support import etf, make_bars

from sillage.core.money import ZERO, dec
from sillage.core.types import Fill, Order, Portfolio, Position
from sillage.engine.feed import MarketFeed
from sillage.execution.broker import ExecutionReport
from sillage.execution.costs import FREE, CostModel
from sillage.execution.simulated import SimulatedBroker

AAA = etf("AAA")
BARS = make_bars(AAA, [100, 110, 120])
SESSION = BARS[1].ts.date()
TS = BARS[1].ts
OPEN = BARS[1].open  # 99: the closes are 10% above the opens by construction


def broker(
    costs: CostModel | None = None,
    *,
    allow_short: bool = False,
    enforce_buying_power: bool = True,
) -> SimulatedBroker:
    return SimulatedBroker(
        MarketFeed({"AAA": BARS}),
        costs or FREE,
        allow_short=allow_short,
        enforce_buying_power=enforce_buying_power,
    )


def portfolio(cash: Decimal | int = 1_000_000, held: Decimal | int = 0) -> Portfolio:
    positions = {"AAA": Position(AAA, quantity=dec(held), avg_cost=dec(100))} if held else {}
    return Portfolio(cash=dec(cash), positions=positions)


def run(
    orders: list[Order],
    book: Portfolio | None = None,
    *,
    costs: CostModel | None = None,
    allow_short: bool = False,
    enforce_buying_power: bool = True,
) -> ExecutionReport:
    return broker(
        costs, allow_short=allow_short, enforce_buying_power=enforce_buying_power
    ).execute(orders, portfolio=book or portfolio(), session=SESSION, ts=TS)


# ------------------------------------------------------------------ pricing


def test_fills_at_the_session_open_not_the_close() -> None:
    """The whole point. A fill at 110 would mean the engine traded on tomorrow's close."""
    report = run([Order(AAA, dec(10))])
    assert report.fills[0].price == OPEN


def test_costs_move_the_price_against_the_trade() -> None:
    bought = run([Order(AAA, dec(10))], costs=CostModel()).fills[0]
    sold = run([Order(AAA, dec(-10))], portfolio(held=100), costs=CostModel()).fills[0]
    assert bought.price > OPEN > sold.price


def test_slippage_is_recorded_separately_from_the_price() -> None:
    """Phase 4 has to be able to subtract costs back out and ask what was left."""
    fill = run([Order(AAA, dec(100))], costs=CostModel()).fills[0]
    assert fill.slippage > ZERO
    assert fill.slippage == abs(fill.price - OPEN) * dec(100)


# ------------------------------------------------------------------ refusals


def test_rejects_an_order_on_a_session_the_symbol_did_not_trade() -> None:
    from datetime import date

    report = broker().execute(
        [Order(AAA, dec(10))], portfolio=portfolio(), session=date(2030, 1, 2), ts=TS
    )
    assert not report.fills
    assert "no open price" in report.rejections[0].reason


def test_rejects_a_buy_it_cannot_pay_for() -> None:
    report = run([Order(AAA, dec(100))], portfolio(cash=1))
    assert report.rejections[0].reason == "insufficient cash"


def test_shrinks_a_buy_to_what_the_cash_covers() -> None:
    """A cash account fills what it can rather than refusing the whole order."""
    report = run([Order(AAA, dec(100))], portfolio(cash=500))
    assert report.fills[0].quantity == dec(5)  # 500 / 99, rounded down to whole shares


def test_buying_power_can_be_switched_off() -> None:
    report = run([Order(AAA, dec(100))], portfolio(cash=1), enforce_buying_power=False)
    assert report.fills[0].quantity == dec(100)


def test_trims_a_sale_to_what_is_held_in_a_long_only_account() -> None:
    report = run([Order(AAA, dec(-500))], portfolio(held=100))
    assert report.fills[0].quantity == dec(-100)


def test_rejects_a_sale_of_something_not_held() -> None:
    report = run([Order(AAA, dec(-10))])
    assert "nothing held" in report.rejections[0].reason


def test_shorting_is_allowed_when_configured() -> None:
    report = run([Order(AAA, dec(-10))], allow_short=True)
    assert report.fills[0].quantity == dec(-10)


def test_caps_an_order_that_would_dominate_the_day_s_volume() -> None:
    report = run([Order(AAA, dec(900_000))], portfolio(cash=10**9))
    assert report.fills[0].quantity == dec(100_000)  # capped at 10% of a 1m-share ADV


def test_rejects_a_quantity_that_rounds_away_at_the_lot_size() -> None:
    whole_shares = etf("AAA", lot_size=1)
    report = broker().execute(
        [Order(whole_shares, dec("0.4"))], portfolio=portfolio(), session=SESSION, ts=TS
    )
    assert "rounds to zero" in report.rejections[0].reason


# ------------------------------------------------------------------ ordering


def test_sells_execute_before_buys_so_they_can_fund_them() -> None:
    """Without this, a rebalance rejects the purchase its own sale was about to pay for."""
    book = Portfolio(cash=ZERO, positions={"AAA": Position(AAA, dec(100), dec(50))})
    report = broker(allow_short=True).execute(
        [Order(AAA, dec(50)), Order(AAA, dec(-100))],
        portfolio=book,
        session=SESSION,
        ts=TS,
    )
    assert [f.quantity for f in report.fills] == [dec(-100), dec(50)]


def test_fills_carry_the_originating_order_id() -> None:
    order = Order(AAA, dec(10))
    fill = run([order]).fills[0]
    assert isinstance(fill, Fill)
    assert fill.order_id == order.client_order_id
