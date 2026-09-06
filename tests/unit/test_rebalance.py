"""Tests for turning weights into orders.

The funding test is the one that matters. It encodes a bug that a per-position
no-trade band produces and that is invisible in a summary: the band suppresses the sale
while permitting the purchase, and the portfolio orders something it cannot pay for.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.support import etf

from sillage.core.money import ZERO, dec
from sillage.core.types import Portfolio, Position
from sillage.portfolio.rebalance import Rebalancer

AAA = etf("AAA")
BBB = etf("BBB")
INSTRUMENTS = {"AAA": AAA, "BBB": BBB}
PRICES = {"AAA": dec(100), "BBB": dec(50)}


def book(cash: Decimal | int = 0, **holdings: int) -> Portfolio:
    return Portfolio(
        cash=dec(cash),
        positions={
            symbol.upper(): Position(INSTRUMENTS[symbol.upper()], dec(qty), dec(10))
            for symbol, qty in holdings.items()
        },
    )


def diff(targets: dict[str, str], portfolio: Portfolio, **kwargs: object) -> list:  # type: ignore[type-arg]
    rebalancer = Rebalancer(**kwargs)  # type: ignore[arg-type]
    return rebalancer.diff(
        {s: dec(w) for s, w in targets.items()},
        portfolio=portfolio,
        prices=PRICES,
        instruments=INSTRUMENTS,
    )


# ------------------------------------------------------------------ the basic diff


def test_buys_a_target_from_nothing() -> None:
    orders = diff({"AAA": "1.0"}, book(cash=10_000))
    assert [(o.instrument.symbol, o.quantity) for o in orders] == [("AAA", dec(100))]


def test_does_nothing_when_already_on_target() -> None:
    assert diff({"AAA": "1.0"}, book(aaa=100)) == []


def test_rounds_down_to_whole_shares() -> None:
    """Rounding up would order shares the cash does not stretch to."""
    orders = diff({"AAA": "1.0"}, book(cash=10_050))
    assert orders[0].quantity == dec(100)


def test_an_empty_portfolio_produces_nothing() -> None:
    assert diff({"AAA": "1.0"}, Portfolio(cash=ZERO)) == []


# ------------------------------------------------------------------ the band


def test_drift_inside_the_band_is_left_alone() -> None:
    # 45/55 against a 50/50 target: 10% relative drift, inside a 20% band.
    assert diff({"AAA": "0.5", "BBB": "0.5"}, book(aaa=45, bbb=110)) == []


def test_drift_outside_the_band_trades() -> None:
    # 70/30 against 50/50: 40% relative drift.
    orders = diff({"AAA": "0.5", "BBB": "0.5"}, book(aaa=70, bbb=60))
    assert {o.instrument.symbol for o in orders} == {"AAA", "BBB"}


def test_one_breach_restores_every_position() -> None:
    """The funding rule. A purchase must have a sale to pay for it.

    Against an 80/20 target the book sits at 85/15. In relative terms that is 6% of
    drift for AAA -- comfortably inside the band -- and 25% for BBB, which breaches it.
    Rebalancing BBB alone would order a purchase with no matching sale, which is what
    the simulated broker then refuses for lack of cash.
    """
    orders = diff({"AAA": "0.8", "BBB": "0.2"}, book(aaa=85, bbb=30))
    traded = {o.instrument.symbol: o.quantity for o in orders}
    assert set(traded) == {"AAA", "BBB"}
    assert traded["AAA"] < ZERO < traded["BBB"]


def test_a_wider_band_trades_less() -> None:
    drifted = book(aaa=70, bbb=60)
    assert diff({"AAA": "0.5", "BBB": "0.5"}, drifted, band=dec("0.5")) == []
    assert diff({"AAA": "0.5", "BBB": "0.5"}, drifted, band=dec("0.1")) != []


# ------------------------------------------------------------------ closing out


def test_a_dropped_asset_is_closed_completely() -> None:
    orders = diff({"BBB": "1.0"}, book(aaa=100, bbb=0))
    closing = next(o for o in orders if o.instrument.symbol == "AAA")
    assert closing.quantity == dec(-100)
    assert closing.reason == "close AAA"


def test_closing_ignores_the_minimum_notional() -> None:
    """A position the strategy has abandoned gets sold however small it is."""
    orders = diff({"AAA": "1.0"}, book(cash=100_000, bbb=1), min_notional=dec(10_000))
    assert any(o.instrument.symbol == "BBB" and o.quantity == dec(-1) for o in orders)


def test_closing_alone_is_enough_to_trigger_a_rebalance() -> None:
    assert diff({}, book(aaa=100)) != []


# ------------------------------------------------------------------ the small stuff


def test_trades_below_the_minimum_notional_are_dropped() -> None:
    """Band set to zero so the drop is unambiguously the notional filter's doing."""
    orders = diff({"AAA": "1.0"}, book(cash=150, aaa=99), band=ZERO, min_notional=dec(500))
    assert orders == []


def test_an_unpriced_symbol_is_skipped_rather_than_guessed_at() -> None:
    rebalancer = Rebalancer()
    orders = rebalancer.diff(
        {"AAA": dec(1)},
        portfolio=book(cash=10_000),
        prices={"AAA": dec(100), "BBB": dec(50)},
        instruments={"AAA": AAA},
    )
    assert [o.instrument.symbol for o in orders] == ["AAA"]


def test_orders_carry_a_readable_rationale() -> None:
    orders = diff({"AAA": "1.0"}, book(cash=10_000))
    assert orders[0].reason == "rebalance AAA 0.00% -> 100.00%"


@pytest.mark.parametrize("bad", [{"band": dec(-1)}, {"min_notional": dec(-1)}])
def test_rejects_nonsensical_settings(bad: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        Rebalancer(**bad)
