"""Tests for the cost model.

The important assertion in this file is the sign one: costs must always work against
the trader. A model that could return a better price than the reference would be
modelling luck, and a backtest that gets lucky on ten thousand consecutive fills is not
a backtest.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sillage.core.money import ZERO, dec
from sillage.execution.costs import FREE, CostModel

MODEL = CostModel()
PRICE = dec(100)


# ------------------------------------------------------------------ direction


@pytest.mark.parametrize("quantity", [dec(1), dec(100), dec("0.5")])
def test_buys_never_fill_below_the_reference(quantity: Decimal) -> None:
    assert MODEL.fill_price("SPY", PRICE, quantity) > PRICE


@pytest.mark.parametrize("quantity", [dec(-1), dec(-100), dec("-0.5")])
def test_sells_never_fill_above_the_reference(quantity: Decimal) -> None:
    assert MODEL.fill_price("SPY", PRICE, quantity) < PRICE


def test_a_buy_and_a_sell_of_the_same_size_cost_the_same() -> None:
    buy = MODEL.fill_price("SPY", PRICE, dec(100)) - PRICE
    sell = PRICE - MODEL.fill_price("SPY", PRICE, dec(-100))
    assert buy == sell


# ------------------------------------------------------------------ the components


def test_spread_is_per_symbol() -> None:
    """DBC is roughly ten times wider than SPY, and the model has to say so."""
    assert MODEL.half_spread_for("DBC") > MODEL.half_spread_for("SPY")


def test_an_unlisted_symbol_gets_the_pessimistic_default() -> None:
    assert MODEL.half_spread_for("WHATEVER") == MODEL.default_half_spread_bps
    assert MODEL.half_spread_for("WHATEVER") > MODEL.half_spread_for("EEM")


def test_impact_grows_with_participation_but_slower_than_linearly() -> None:
    adv = dec(1_000_000)
    small = MODEL.impact_for(dec(10_000), adv)
    large = MODEL.impact_for(dec(40_000), adv)
    # Four times the order, twice the impact: the square-root law.
    assert large == pytest.approx(float(small) * 2, rel=1e-9)


def test_impact_is_zero_without_volume_history() -> None:
    """A guess would make the backtest less honest, not more."""
    assert MODEL.impact_for(dec(1000), None) == ZERO
    assert MODEL.impact_for(dec(1000), ZERO) == ZERO


def test_impact_is_capped_at_full_participation() -> None:
    absurd = MODEL.impact_for(dec(10_000_000), dec(1000))
    assert absurd == MODEL.impact_bps_at_full_adv


def test_commission_has_a_floor() -> None:
    assert MODEL.commission(dec(1), PRICE) == MODEL.min_commission


def test_commission_scales_with_shares_above_the_floor() -> None:
    assert MODEL.commission(dec(10_000), PRICE) == dec("35.00")


def test_commission_is_positive_on_a_sale() -> None:
    assert MODEL.commission(dec(-500), PRICE) == MODEL.commission(dec(500), PRICE)


# ------------------------------------------------------------------ sensitivity


def test_scaling_multiplies_every_component() -> None:
    """The Phase 4 cost-sensitivity sweep, in one call."""
    doubled = MODEL.scaled(2)
    assert doubled.half_spread_for("SPY") == MODEL.half_spread_for("SPY") * 2
    assert doubled.commission_per_share == MODEL.commission_per_share * 2
    assert doubled.impact_bps_at_full_adv == MODEL.impact_bps_at_full_adv * 2


def test_scaling_to_zero_makes_trading_free() -> None:
    free = MODEL.scaled(0)
    assert free.fill_price("SPY", PRICE, dec(100)) == PRICE
    assert free.commission(dec(100), PRICE) == ZERO


def test_scaling_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        MODEL.scaled(-1)


def test_the_free_model_charges_nothing() -> None:
    assert FREE.fill_price("SPY", PRICE, dec(-100)) == PRICE
    assert FREE.commission(dec(100), PRICE) == ZERO
