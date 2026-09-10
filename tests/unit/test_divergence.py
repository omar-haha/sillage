"""Tests for the simulated-versus-real fill comparison.

The number this produces is the only one in the project a backtest cannot generate on
its own, so the tests are mostly about sign and about honesty: divergence has to point
against the trader in both directions, price improvement has to survive being reported,
and a figure built from a handful of trades has to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.support import etf

from sillage.core.money import ZERO, dec
from sillage.core.types import Fill
from sillage.execution.costs import CostModel
from sillage.live.divergence import MEANINGFUL_SAMPLE, collapse, compare

A = etf("SPY")
B = etf("IEF")


def fill(
    symbol: str = "SPY",
    quantity: str = "10",
    price: str = "100",
    day: int = 12,
    commission: str = "0.35",
) -> Fill:
    return Fill(
        instrument=etf(symbol),
        ts=datetime(2026, 3, day, 14, 30, tzinfo=UTC),
        quantity=dec(quantity),
        price=dec(price),
        commission=dec(commission),
    )


# ------------------------------------------------------------------ direction


def test_a_buy_that_filled_higher_is_divergence_against_the_trader() -> None:
    result = compare([fill(price="100")], [fill(price="101")])
    assert result.pairs[0].divergence_bps == pytest.approx(100.0)


def test_a_sell_that_filled_lower_is_also_against_the_trader() -> None:
    """The sign has to flip with the side, or every sale would look like a bargain."""
    result = compare([fill(quantity="-10", price="100")], [fill(quantity="-10", price="99")])
    assert result.pairs[0].divergence_bps == pytest.approx(100.0)


def test_price_improvement_comes_back_negative() -> None:
    """The simulator moves every price against the trader by construction, so a real
    fill beating it is a sign the model is honest rather than a sign it is wrong."""
    result = compare([fill(price="100")], [fill(price="99.5")])
    assert result.pairs[0].divergence_bps < 0
    assert result.improved == 1


def test_an_identical_fill_diverges_by_nothing() -> None:
    assert compare([fill()], [fill()]).pairs[0].divergence_bps == pytest.approx(0.0)


# ------------------------------------------------------------------ pairing


def test_trades_are_matched_by_session_symbol_and_side() -> None:
    simulated = [fill("SPY", day=12), fill("IEF", day=12), fill("SPY", day=13)]
    actual = [fill("SPY", day=12, price="101"), fill("IEF", day=12, price="99")]
    result = compare(simulated, actual)
    assert result.count == 2
    assert result.unmatched_simulated == 1
    assert result.unmatched_actual == 0


def test_a_buy_and_a_sell_of_the_same_thing_are_different_trades() -> None:
    result = compare(
        [fill(quantity="10"), fill(quantity="-10")],
        [fill(quantity="10", price="101"), fill(quantity="-10", price="99")],
    )
    assert result.count == 2


def test_several_real_fills_collapse_to_one_weighted_price() -> None:
    """One live order that filled in pieces is still one decision; comparing each piece
    against the single simulated fill would count the same intention repeatedly."""
    pieces = [fill(quantity="10", price="100"), fill(quantity="30", price="110")]
    collapsed = collapse(pieces)
    intention = next(iter(collapsed.values()))
    assert intention.quantity == dec(40)
    assert intention.price == dec("107.5")  # volume weighted, not the mean of 100 and 110


def test_collapsing_adds_up_the_commissions() -> None:
    pieces = [fill(commission="0.35"), fill(commission="0.40")]
    assert next(iter(collapse(pieces).values())).commission == dec("0.75")


def test_nothing_in_common_is_reported_rather_than_hidden() -> None:
    """A systematic gap means the two funds were not doing the same thing."""
    result = compare([fill("SPY")], [fill("IEF")])
    assert result.count == 0
    assert result.unmatched_simulated == 1
    assert result.unmatched_actual == 1
    assert "no paired fills" in result.summary(CostModel())


# ------------------------------------------------------------------ aggregates


def test_the_median_resists_one_terrible_fill() -> None:
    simulated = [fill(day=d) for d in (10, 11, 12, 13)]
    actual = [
        fill(day=10, price="100.1"),
        fill(day=11, price="100.1"),
        fill(day=12, price="100.1"),
        fill(day=13, price="150"),
    ]
    result = compare(simulated, actual)
    assert result.median_bps == pytest.approx(10.0)
    assert result.mean_bps > 100
    assert result.worst_bps == pytest.approx(5000.0)


def test_a_small_sample_says_it_is_a_small_sample() -> None:
    """A dozen fills of a liquid ETF will produce a number, and it will be noise."""
    result = compare([fill()], [fill(price="101")])
    assert not result.trustworthy
    assert "indicative" in result.summary(CostModel())


def test_a_large_enough_sample_does_not() -> None:
    days = range(1, MEANINGFUL_SAMPLE + 2)
    result = compare([fill(day=d) for d in days], [fill(day=d, price="101") for d in days])
    assert result.trustworthy
    assert "indicative" not in result.summary(CostModel())


# ------------------------------------------------------------------ correcting the model


def test_a_worse_than_modelled_reality_scales_the_costs_up() -> None:
    costs = CostModel()
    modelled = float(costs.half_spread_for("SPY"))
    result = compare([fill(price="100")], [fill(price=str(100 * (1 + modelled * 2 / 10_000)))])
    assert result.optimism(costs) == pytest.approx(2.0, rel=0.05)
    assert result.corrected(costs).half_spread_for("SPY") > costs.half_spread_for("SPY")


def test_a_better_than_modelled_reality_does_not_scale_costs_negative() -> None:
    """Price improvement should not produce a cost model that pays the trader."""
    result = compare([fill(price="100")], [fill(price="98")])
    corrected = result.corrected(CostModel())
    assert corrected.half_spread_for("SPY") >= ZERO
    assert corrected.commission_per_share >= ZERO


def test_correcting_with_nothing_to_go_on_changes_nothing() -> None:
    assert compare([], []).optimism(CostModel()) == 1.0


def test_the_summary_names_which_way_it_went() -> None:
    costs = CostModel()
    optimistic = compare([fill(price="100")], [fill(price="110")])
    assert "was optimistic" in optimistic.summary(costs)
    pessimistic = compare([fill(price="100")], [fill(price="99.9")])
    assert "pessimistic" in pessimistic.summary(costs)


def test_commission_divergence_is_measured_too() -> None:
    result = compare([fill(commission="0.35")], [fill(commission="1.35")])
    assert result.commission_bps > 0


def test_a_pair_reads_as_a_line() -> None:
    line = str(compare([fill()], [fill(price="101")]).pairs[0])
    assert "SPY" in line and "buy" in line and "bp" in line
