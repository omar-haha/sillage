"""Tests for the limits between a strategy and the broker.

These check refusals. Everything upstream is a model of what should happen; this layer
assumes the model is wrong, so the interesting cases are all the ones where an order the
strategy wanted does not go out.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.support import etf

from sillage.core.money import ZERO, dec
from sillage.core.types import Order, Portfolio, Position
from sillage.risk.limits import RiskLimits, drawdown, filter_orders

A, B = etf("A"), etf("B")
PRICES = {"A": dec(100), "B": dec(100)}


def book(cash: str = "100000", **holdings: str) -> Portfolio:
    return Portfolio(
        cash=dec(cash),
        positions={
            symbol.upper(): Position(etf(symbol.upper()), quantity=dec(qty), avg_cost=dec(100))
            for symbol, qty in holdings.items()
        },
    )


# ------------------------------------------------------------------ position cap


def test_an_ordinary_order_passes() -> None:
    decision = filter_orders(
        [Order(A, dec(100))],
        portfolio=book(),
        prices=PRICES,
        limits=RiskLimits(),
    )
    assert len(decision.allowed) == 1
    assert not decision.rejected


def test_an_order_past_the_position_cap_is_refused() -> None:
    decision = filter_orders(
        [Order(A, dec(600))],
        portfolio=book(),
        prices=PRICES,
        limits=RiskLimits(max_weight=dec("0.35")),
    )
    assert not decision.allowed
    assert "above the 35% cap" in decision.rejected[0].reason


def test_the_default_cap_does_not_fight_a_legitimate_allocation() -> None:
    """A 60/40 wants 60% in equities. A cap that blocked that put a live fund in cash
    and left it looking healthy, which is how this default came to be 1.0."""
    decision = filter_orders(
        [Order(A, dec(600))],
        portfolio=book(),
        prices=PRICES,
        limits=RiskLimits(),
    )
    assert len(decision.allowed) == 1


def test_the_cap_counts_what_is_already_held() -> None:
    decision = filter_orders(
        [Order(A, dec(100))],
        portfolio=book(cash="50000", a="500"),
        prices=PRICES,
        limits=RiskLimits(max_weight=dec("0.55")),
    )
    assert not decision.allowed


def test_orders_are_checked_cumulatively_not_independently() -> None:
    """Three orders each taking a holding to 30% of a 35% cap are individually fine and
    collectively impossible."""
    orders = [Order(A, dec(300)), Order(A, dec(300)), Order(A, dec(300))]
    decision = filter_orders(
        orders, portfolio=book(), prices=PRICES, limits=RiskLimits(max_weight=dec("0.65"))
    )
    assert len(decision.allowed) < len(orders)


def test_a_refused_order_is_refused_not_resized() -> None:
    """A silently shrunk order produces a book that differs from the target and nobody
    ever asks why."""
    decision = filter_orders(
        [Order(A, dec(600))],
        portfolio=book(),
        prices=PRICES,
        limits=RiskLimits(max_weight=dec("0.35")),
    )
    assert decision.rejected[0].order.quantity == dec(600)


def test_an_unpriced_order_cannot_be_checked_so_is_refused() -> None:
    decision = filter_orders(
        [Order(etf("ZZZ"), dec(10))],
        portfolio=book(),
        prices=PRICES,
        limits=RiskLimits(),
    )
    assert "cannot risk-check" in decision.rejected[0].reason


# ------------------------------------------------------------------ gross exposure


def test_gross_exposure_is_capped_across_the_whole_book() -> None:
    """Negative cash is how a long-only book ends up levered -- an overspend, or a fill
    worse than the price it was sized at. The cap should never bind in normal running,
    which is exactly why it binding is worth stopping for."""
    decision = filter_orders(
        [Order(B, dec(10))],
        portfolio=book(cash="-20000", a="1200"),
        prices=PRICES,
        limits=RiskLimits(max_gross=dec("1.05")),
    )
    assert not decision.allowed
    assert "gross exposure" in decision.rejected[0].reason


# ------------------------------------------------------------------ kill-switch


def test_drawdown_is_measured_from_the_peak() -> None:
    assert drawdown(dec(75), dec(100)) == dec("0.25")
    assert drawdown(dec(120), dec(100)) == ZERO
    assert drawdown(dec(100), ZERO) == ZERO


def test_a_deep_drawdown_halts_everything() -> None:
    decision = filter_orders(
        [Order(A, dec(10))],
        portfolio=book(cash="70000"),
        prices=PRICES,
        limits=RiskLimits(max_drawdown=dec("0.20")),
        high_water_mark=dec(100_000),
    )
    assert not decision.trading
    assert "trading halted" in decision.halted
    assert not decision.allowed


def test_it_halts_rather_than_trading_smaller() -> None:
    """The point of a kill-switch is that it hands the decision to a person, and a
    person cannot intervene in a system that quietly kept going."""
    decision = filter_orders(
        [Order(A, dec(1))],
        portfolio=book(cash="50000"),
        prices=PRICES,
        limits=RiskLimits(max_drawdown=dec("0.20")),
        high_water_mark=dec(100_000),
    )
    assert decision.allowed == ()


def test_a_shallow_drawdown_does_not_halt() -> None:
    decision = filter_orders(
        [Order(A, dec(10))],
        portfolio=book(cash="95000"),
        prices=PRICES,
        limits=RiskLimits(max_drawdown=dec("0.20")),
        high_water_mark=dec(100_000),
    )
    assert decision.trading


def test_a_worthless_fund_cannot_trade() -> None:
    decision = filter_orders(
        [Order(A, dec(10))], portfolio=Portfolio(cash=ZERO), prices=PRICES, limits=RiskLimits()
    )
    assert not decision.trading


# ------------------------------------------------------------------ configuration


@pytest.mark.parametrize(
    "bad",
    [
        {"max_weight": dec(0)},
        {"max_gross": dec(0)},
        {"max_drawdown": dec(0)},
        {"max_drawdown": dec("1.5")},
    ],
)
def test_rejects_nonsensical_limits(bad: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        RiskLimits(**bad)
