"""Tests for per-holding attribution.

The load-bearing assertion is the reconciliation one: every dollar the fund made has to
be assigned to some holding, and the assigned dollars have to add up to the fund's
growth. An attribution that does not close is not an explanation, it is a guess.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.support import etf

from sillage.backtest.attribution import attribute, concentration
from sillage.core.money import ZERO, dec
from sillage.core.types import Portfolio, Position
from sillage.engine.journal import NavPoint

A, B = etf("A"), etf("B")


def point(day: int, **weights: str) -> NavPoint:
    return NavPoint(
        session=date(2024, 1, day),
        ts=datetime(2024, 1, day, 21, tzinfo=UTC),
        nav=dec(100_000),
        cash=ZERO,
        gross_exposure=dec(1),
        weights={s: dec(w) for s, w in weights.items()},
    )


def book(**positions: tuple[str, str, str, str]) -> Portfolio:
    """Positions as (quantity, avg_cost, realized, commission)."""
    return Portfolio(
        cash=ZERO,
        positions={
            symbol: Position(
                etf(symbol),
                quantity=dec(qty),
                avg_cost=dec(cost),
                realized_pnl=dec(realized),
                commission_paid=dec(commission),
            )
            for symbol, (qty, cost, realized, commission) in positions.items()
        },
    )


# ------------------------------------------------------------------ profit


def test_it_reports_realised_and_unrealised_separately() -> None:
    portfolio = book(A=("100", "50", "1000", "20"))
    result = attribute(portfolio, {"A": dec(60)}, [point(2, A="1")])[0]
    assert result.realized == dec(1000)
    assert result.unrealized == dec(1000)  # 100 shares up 10
    assert result.commission == dec(20)
    assert result.net == dec(1980)


def test_a_closed_position_still_appears() -> None:
    """A winning trade closed in 2009 must not be invisible in 2026."""
    portfolio = book(A=("0", "0", "5000", "30"))
    result = attribute(portfolio, {}, [point(2, A="1")])[0]
    assert result.net == dec(4970)


def test_holdings_are_ordered_by_what_they_earned() -> None:
    portfolio = book(A=("0", "0", "100", "0"), B=("0", "0", "900", "0"))
    assert [c.symbol for c in attribute(portfolio, {}, [point(2)])] == ["B", "A"]


def test_a_position_with_no_price_is_not_valued_at_zero() -> None:
    """Silently marking a holding to nothing would invent a loss."""
    portfolio = book(A=("100", "50", "0", "0"))
    assert attribute(portfolio, {}, [point(2, A="1")])[0].unrealized == ZERO


# ------------------------------------------------------------------ capital


def test_average_weight_spans_the_whole_run_not_just_the_days_held() -> None:
    """The question is what fraction of the fund a holding consumed, not what it was
    worth on the days it existed."""
    portfolio = book(A=("0", "0", "0", "0"))
    result = attribute(portfolio, {}, [point(2, A="1"), point(3), point(4), point(5)])[0]
    assert result.average_weight == pytest.approx(0.25)
    assert result.time_held == pytest.approx(0.25)
    assert result.sessions_held == 1


def test_a_zero_weight_session_does_not_count_as_held() -> None:
    portfolio = book(A=("0", "0", "0", "0"))
    result = attribute(portfolio, {}, [point(2, A="0"), point(3, A="1")])[0]
    assert result.sessions_held == 1


def test_a_run_with_no_sessions_does_not_divide_by_zero() -> None:
    assert attribute(book(A=("0", "0", "10", "0")), {}, [])[0].average_weight == 0.0


# ------------------------------------------------------------------ reconciliation


def test_the_parts_add_up_to_the_whole() -> None:
    """The identity that makes this an explanation rather than a guess."""
    portfolio = book(A=("100", "50", "1000", "20"), B=("50", "20", "-300", "10"))
    prices = {"A": dec(60), "B": dec(25)}
    contributions = attribute(portfolio, prices, [point(2, A="0.5", B="0.5")])

    total = sum((c.net for c in contributions), start=ZERO)
    expected = sum(
        (
            p.realized_pnl + p.unrealized_pnl(prices[s]) - p.commission_paid
            for s, p in portfolio.positions.items()
        ),
        start=ZERO,
    )
    assert total == expected


# ------------------------------------------------------------------ concentration


def test_concentration_finds_the_dominant_holding() -> None:
    portfolio = book(A=("0", "0", "9000", "0"), B=("0", "0", "1000", "0"))
    assert concentration(attribute(portfolio, {}, [point(2)])) == pytest.approx(0.9)


def test_losses_do_not_flatter_the_concentration_figure() -> None:
    """Netting a loss against the gains would understate how much rode on one asset."""
    portfolio = book(A=("0", "0", "1000", "0"), B=("0", "0", "-500", "0"))
    assert concentration(attribute(portfolio, {}, [point(2)])) == pytest.approx(1.0)


def test_a_fund_that_made_nothing_has_no_concentration() -> None:
    assert concentration([]) == 0.0


def test_contributions_read_as_a_line() -> None:
    portfolio = book(A=("0", "0", "1234", "0"))
    line = str(attribute(portfolio, {}, [point(2, A="1")])[0])
    assert "A" in line and "avg weight" in line


def test_net_is_a_decimal_not_a_float() -> None:
    portfolio = book(A=("100", "50", "1000", "20"))
    assert isinstance(attribute(portfolio, {"A": dec(60)}, [point(2)])[0].net, Decimal)
