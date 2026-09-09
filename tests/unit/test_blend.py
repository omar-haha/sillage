"""Tests for running several strategies as one fund.

The arithmetic that motivates blending is about correlation, which these cannot test --
that needs real return series. What they test is that the mechanism is honest: shares
are respected, a sleeve that has not started contributes nothing rather than distorting
the rest, and sleeves cannot interfere with each other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.support import etf, make_bars

from sillage.core.money import ZERO, dec
from sillage.engine.feed import DataSource, HistoricalFeed
from sillage.strategy.base import Daily, Monthly, Once, Schedule, TargetWeights
from sillage.strategy.blend import Blend

LATER = datetime(2040, 1, 1, tzinfo=UTC)


class Sleeve:
    """A strategy that returns what it is told, and can be silenced."""

    def __init__(self, name: str, weights: TargetWeights | None) -> None:
        self.name = name
        self.schedule: Schedule = Daily()
        self.weights = weights
        self.warmup_sessions = 1

    def reset(self) -> None:
        return None

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        return dict(self.weights) if self.weights is not None else None


@pytest.fixture
def data() -> HistoricalFeed:
    return HistoricalFeed({s: make_bars(etf(s), [100.0] * 40) for s in ("A", "B", "C")})


# ------------------------------------------------------------------ combining


def test_each_sleeve_gets_its_share(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("x", {"A": dec(1)}), Sleeve("y", {"B": dec(1)})])
    assert blend.target_weights(as_of=LATER, data=data) == {"A": dec("0.5"), "B": dec("0.5")}


def test_uneven_shares_are_respected(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("x", {"A": dec(1)}), Sleeve("y", {"B": dec(1)})], ["0.75", "0.25"])
    weights = blend.target_weights(as_of=LATER, data=data)
    assert weights == {"A": dec("0.75"), "B": dec("0.25")}


def test_sleeves_wanting_the_same_asset_add_up(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("x", {"A": dec(1)}), Sleeve("y", {"A": dec("0.4"), "B": dec("0.6")})])
    weights = blend.target_weights(as_of=LATER, data=data)
    assert weights == {"A": dec("0.7"), "B": dec("0.3")}


def test_a_partly_invested_sleeve_leaves_the_rest_in_cash(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("x", {"A": dec("0.5")}), Sleeve("y", {"B": dec(1)})])
    weights = blend.target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert sum(weights.values(), start=ZERO) == dec("0.75")


# ------------------------------------------------------------------ warm-up


def test_a_sleeve_with_no_opinion_contributes_nothing(data: HistoricalFeed) -> None:
    """Its share sits in cash, which is what staging money into a strategy looks like."""
    blend = Blend([Sleeve("x", {"A": dec(1)}), Sleeve("silent", None)])
    assert blend.target_weights(as_of=LATER, data=data) == {"A": dec("0.5")}


def test_no_opinion_anywhere_stays_no_opinion(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("a", None), Sleeve("b", None)])
    assert blend.target_weights(as_of=LATER, data=data) is None


def test_a_sleeve_holds_its_last_answer_between_its_own_rebalances(
    data: HistoricalFeed,
) -> None:
    """Otherwise half the book would drop to cash whenever one sleeve was not due."""
    sleeve = Sleeve("x", {"A": dec(1)})
    blend = Blend([sleeve, Sleeve("y", {"B": dec(1)})])
    blend.target_weights(as_of=LATER, data=data)
    sleeve.schedule = Monthly(5)  # never fires on this session
    assert blend.target_weights(as_of=LATER, data=data) == {"A": dec("0.5"), "B": dec("0.5")}


def test_a_declining_sleeve_gets_its_firing_back(data: HistoricalFeed) -> None:
    """A sleeve that fires once must not spend its single chance on a warm-up.

    This is the BIL bug in miniature: an asset that does not exist yet makes its
    strategy decline, and a schedule that counted that as its one firing would leave
    the sleeve permanently in cash.
    """
    silent = Sleeve("not ready yet", None)
    silent.schedule = Once()
    blend = Blend([Sleeve("x", {"A": dec(1)}), silent])
    blend.target_weights(as_of=LATER, data=data)
    assert silent.schedule.is_rebalance_session(LATER.date(), blend.calendar)


# ------------------------------------------------------------------ plumbing


def test_the_schedule_fires_when_any_sleeve_does(data: HistoricalFeed) -> None:
    from datetime import date

    from sillage.core.calendar import TradingCalendar

    calendar = TradingCalendar()
    blend = Blend([Sleeve("x", {"A": dec(1)})])
    blend.sleeves[0].schedule = Monthly()
    assert blend.schedule.is_rebalance_session(date(2024, 3, 28), calendar)
    assert not blend.schedule.is_rebalance_session(date(2024, 3, 27), calendar)


def test_warmup_is_the_slowest_sleeve_s(data: HistoricalFeed) -> None:
    slow = Sleeve("slow", {"A": dec(1)})
    slow.warmup_sessions = 300
    assert Blend([Sleeve("fast", {"B": dec(1)}), slow]).warmup_sessions == 300


def test_resetting_clears_every_sleeve(data: HistoricalFeed) -> None:
    blend = Blend([Sleeve("x", {"A": dec(1)})])
    blend.target_weights(as_of=LATER, data=data)
    blend.reset()
    blend.sleeves[0].schedule = Monthly(5)
    assert blend.target_weights(as_of=LATER, data=data) is None


def test_it_names_itself_after_its_sleeves() -> None:
    assert Blend([Sleeve("trend", None), Sleeve("static", None)]).name == "trend + static"


# ------------------------------------------------------------------ refusals


def test_a_blend_needs_something_to_blend() -> None:
    with pytest.raises(ValueError, match="at least one"):
        Blend([])


def test_weights_must_match_the_sleeves() -> None:
    with pytest.raises(ValueError, match="2 sleeves but 3 weights"):
        Blend([Sleeve("a", None), Sleeve("b", None)], ["0.3", "0.3", "0.4"])


def test_it_refuses_to_lever() -> None:
    with pytest.raises(ValueError, match="leverage"):
        Blend([Sleeve("a", None), Sleeve("b", None)], ["0.8", "0.8"])


def test_it_refuses_negative_shares() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        Blend([Sleeve("a", None), Sleeve("b", None)], ["1.2", "-0.2"])
