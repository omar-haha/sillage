"""Tests for schedules and the benchmark strategies.

`Once` gets the most attention despite being ten lines, because it is the only
stateful thing in the strategy layer and Phase 4's walk-forward analysis will run the
same strategy object over dozens of overlapping windows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.support import etf, make_bars

from sillage.core.calendar import TradingCalendar
from sillage.core.money import ZERO, dec
from sillage.engine.feed import HistoricalFeed
from sillage.strategy.base import Daily, Monthly, Once, Weekly
from sillage.strategy.benchmarks import (
    StaticWeights,
    buy_and_hold,
    equal_weight,
    sixty_forty,
)
from sillage.strategy.registry import build, names

AAA = etf("AAA")
BBB = etf("BBB")
LATER = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.fixture
def feed() -> HistoricalFeed:
    return HistoricalFeed({"AAA": make_bars(AAA, [100, 101]), "BBB": make_bars(BBB, [50, 51])})


# ------------------------------------------------------------------ schedules


def test_daily_always_fires(calendar: TradingCalendar) -> None:
    assert Daily().is_rebalance_session(date(2024, 3, 13), calendar)


def test_monthly_fires_on_the_last_tradable_day(calendar: TradingCalendar) -> None:
    # 2024-03-31 was a Sunday and the 29th was Good Friday, so March's last session
    # was Thursday the 28th. A schedule looking for "the 31st" would skip the month.
    assert Monthly().is_rebalance_session(date(2024, 3, 28), calendar)
    assert not Monthly().is_rebalance_session(date(2024, 3, 27), calendar)


def test_weekly_stands_in_when_the_chosen_day_was_a_holiday(calendar: TradingCalendar) -> None:
    """2024-03-29 was Good Friday, so the Thursday before it is that week's session."""
    friday = Weekly(weekday=4)
    assert friday.is_rebalance_session(date(2024, 3, 28), calendar)
    assert not friday.is_rebalance_session(date(2024, 3, 26), calendar)
    assert not friday.is_rebalance_session(date(2024, 4, 1), calendar)


def test_weekly_fires_on_an_ordinary_friday(calendar: TradingCalendar) -> None:
    assert Weekly(weekday=4).is_rebalance_session(date(2024, 3, 22), calendar)


def test_monthly_offsets_do_not_change_how_often_it_trades(calendar: TradingCalendar) -> None:
    """An offset moves the date, never the frequency -- otherwise it would not be a
    controlled comparison, it would be a different strategy."""
    sessions = [s.day for s in calendar.sessions(date(2022, 1, 1), date(2023, 12, 31))]
    counts = {
        offset: sum(Monthly(offset).is_rebalance_session(d, calendar) for d in sessions)
        for offset in (0, 5, 10, 15)
    }
    assert set(counts.values()) == {24}


def test_weekly_rejects_an_impossible_weekday() -> None:
    with pytest.raises(ValueError, match="weekday must be"):
        Weekly(weekday=9)


def test_once_fires_exactly_one_time(calendar: TradingCalendar) -> None:
    schedule = Once()
    fired = [schedule.is_rebalance_session(date(2024, 1, d), calendar) for d in (2, 3, 4)]
    assert fired == [True, False, False]


def test_resetting_once_lets_it_fire_again(calendar: TradingCalendar) -> None:
    """Walk-forward reuses strategy objects across windows; without this it would
    silently skip the first trade of every window after the first."""
    schedule = Once()
    schedule.is_rebalance_session(date(2024, 1, 2), calendar)
    schedule.reset()
    assert schedule.is_rebalance_session(date(2024, 1, 3), calendar)


# ------------------------------------------------------------------ static weights


def test_returns_its_weights_once_prices_exist(feed: HistoricalFeed) -> None:
    strategy = StaticWeights({"AAA": "0.5", "BBB": "0.5"})
    assert strategy.target_weights(as_of=LATER, data=feed) == {"AAA": dec("0.5"), "BBB": dec("0.5")}


def test_holds_no_opinion_until_every_asset_has_a_price(feed: HistoricalFeed) -> None:
    """`None`, not `{}`. An empty mapping would mean "sell everything"."""
    strategy = StaticWeights({"AAA": "0.5", "BBB": "0.5"})
    early = datetime(2000, 1, 1, tzinfo=UTC)
    assert strategy.target_weights(as_of=early, data=feed) is None


def test_waits_for_the_whole_set_not_just_the_available_part(feed: HistoricalFeed) -> None:
    """Otherwise every backtest silently begins as a different, smaller strategy."""
    late = HistoricalFeed(
        {"AAA": make_bars(AAA, [100, 101]), "BBB": make_bars(BBB, [50], start=date(2025, 1, 2))}
    )
    strategy = StaticWeights({"AAA": "0.5", "BBB": "0.5"})
    assert strategy.target_weights(as_of=make_bars(AAA, [100])[0].ts, data=late) is None


def test_rejects_weights_that_would_need_leverage() -> None:
    with pytest.raises(ValueError, match="leverage"):
        StaticWeights({"AAA": "0.7", "BBB": "0.7"})


def test_rejects_negative_weights() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        StaticWeights({"AAA": "-0.1"})


def test_rejects_an_empty_allocation() -> None:
    with pytest.raises(ValueError, match="at least one weight"):
        StaticWeights({})


# ------------------------------------------------------------------ the benchmarks


def test_buy_and_hold_trades_once_and_holds_everything() -> None:
    strategy = buy_and_hold("SPY")
    assert strategy.weights == {"SPY": dec(1)}
    assert strategy.schedule.name == "once"


def test_sixty_forty_is_rebalanced_monthly() -> None:
    strategy = sixty_forty()
    assert strategy.weights == {"SPY": dec("0.6"), "IEF": dec("0.4")}
    assert strategy.schedule.name == "monthly"


@pytest.mark.parametrize("count", [1, 2, 3, 6, 7, 12, 13])
def test_equal_weight_never_asks_for_leverage(count: int) -> None:
    """A third rounded to eight places and tripled comes to 1.00000001."""
    strategy = equal_weight(f"S{i}" for i in range(count))
    assert sum(strategy.weights.values(), start=ZERO) <= dec(1)


def test_equal_weight_needs_something_to_weight() -> None:
    with pytest.raises(ValueError, match="at least one symbol"):
        equal_weight([])


# ------------------------------------------------------------------ the registry


def test_every_registered_name_builds(calendar: TradingCalendar) -> None:
    from sillage.data.universe import CORE

    for name in names():
        assert build(name, CORE).name


def test_an_unknown_name_lists_the_known_ones() -> None:
    from sillage.data.universe import CORE

    with pytest.raises(KeyError, match="known:"):
        build("magic-beans", CORE)
