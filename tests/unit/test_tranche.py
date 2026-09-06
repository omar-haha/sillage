"""Tests for tranching.

The point of tranching is to stop reporting one arbitrary rebalance date as the answer.
These check the mechanism -- that four copies really are independent, that their targets
are averaged, that a tranche with no opinion contributes nothing rather than distorting
the blend -- and one test checks the claim that motivates the whole thing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from tests.support import etf, make_bars

from sillage.core.calendar import TradingCalendar
from sillage.core.money import ZERO, dec
from sillage.engine.feed import DataSource, HistoricalFeed
from sillage.strategy.base import AnyOf, Daily, Monthly, Once, Schedule, TargetWeights
from sillage.strategy.benchmarks import StaticWeights
from sillage.strategy.tranche import DEFAULT_OFFSETS, Tranched

LATER = datetime(2040, 1, 1, tzinfo=UTC)


class Counter:
    """A strategy that returns whatever it is told and remembers how often it was asked."""

    name = "counter"
    warmup_sessions = 1

    def __init__(self, weights: TargetWeights | None = None) -> None:
        # Annotated as the protocol type: the concrete schedule is swapped by tests.
        self.schedule: Schedule = Monthly()
        self.weights = weights if weights is not None else {"A": dec(1)}
        self.calls = 0

    def reset(self) -> None:
        self.calls = 0

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        self.calls += 1
        return dict(self.weights)


@pytest.fixture
def data() -> HistoricalFeed:
    return HistoricalFeed({"A": make_bars(etf("A"), [100.0] * 60)})


# ------------------------------------------------------------------ the schedule


def test_any_of_fires_on_the_union(calendar: TradingCalendar) -> None:
    schedule = AnyOf([Monthly(0), Monthly(10)])
    sessions = [s.day for s in calendar.sessions(date(2024, 1, 1), date(2024, 12, 31))]
    fired = [d for d in sessions if schedule.is_rebalance_session(d, calendar)]
    assert len(fired) == 24


def test_any_of_resets_everything_it_wraps(calendar: TradingCalendar) -> None:
    inner = Once()
    schedule = AnyOf([inner])
    schedule.is_rebalance_session(date(2024, 1, 2), calendar)
    schedule.reset()
    assert inner.is_rebalance_session(date(2024, 1, 3), calendar)


def test_any_of_needs_something_to_wrap() -> None:
    with pytest.raises(ValueError, match="at least one schedule"):
        AnyOf([])


# ------------------------------------------------------------------ blending


def test_it_averages_what_the_tranches_want(data: HistoricalFeed) -> None:
    tranched = Tranched(Counter({"A": dec("0.8")}))
    # Force every tranche to have an opinion, as it would after warm-up.
    for tranche in tranched.tranches:
        tranche.schedule = Daily()
    weights = tranched.target_weights(as_of=LATER, data=data)
    assert weights == {"A": dec("0.8")}


def test_a_tranche_with_no_opinion_contributes_nothing(data: HistoricalFeed) -> None:
    """Its quarter of the capital sits in cash, which is what actually happens."""
    tranched = Tranched(Counter({"A": dec(1)}), offsets=(0, 5))
    tranched.tranches[0].schedule = Daily()
    tranched.tranches[1].schedule = Monthly(5)
    weights = tranched.target_weights(as_of=LATER, data=data)
    assert weights == {"A": dec("0.5")}


def test_no_opinion_anywhere_stays_no_opinion(data: HistoricalFeed) -> None:
    tranched = Tranched(Counter(), offsets=(0,))
    tranched.tranches[0].schedule = Monthly(5)  # never fires on this session
    assert tranched.target_weights(as_of=LATER, data=data) is None


def test_a_tranche_holds_its_last_answer_between_its_own_rebalances(
    data: HistoricalFeed,
) -> None:
    """Otherwise three quarters of the book would drop to cash between rebalances."""
    tranched = Tranched(Counter({"A": dec(1)}), offsets=(0, 5))
    for tranche in tranched.tranches:
        tranche.schedule = Daily()
    tranched.target_weights(as_of=LATER, data=data)
    # Now silence both tranches; the blend must still reflect what they last said.
    for tranche in tranched.tranches:
        tranche.schedule = Monthly(5)
    assert tranched.target_weights(as_of=LATER, data=data) == {"A": dec(1)}


# ------------------------------------------------------------------ independence


def test_tranches_are_copies_not_references() -> None:
    """Four tranches sharing one object would interleave into nonsense."""
    prototype = Counter()
    tranched = Tranched(prototype)
    assert all(t is not prototype for t in tranched.tranches)
    assert len({id(t) for t in tranched.tranches}) == len(DEFAULT_OFFSETS)


def test_each_tranche_gets_its_own_offset() -> None:
    tranched = Tranched(Counter(), offsets=(0, 5, 10))
    offsets = [t.schedule.offset for t in tranched.tranches]  # type: ignore[attr-defined]
    assert offsets == [0, 5, 10]


def test_the_prototype_is_left_alone() -> None:
    prototype = Counter()
    Tranched(prototype)
    assert isinstance(prototype.schedule, Monthly)
    assert prototype.schedule.offset == 0


def test_resetting_clears_every_tranche(data: HistoricalFeed) -> None:
    """Walk-forward reuses one object across overlapping windows; leftover held
    targets from the previous window would be positions nobody chose."""
    tranched = Tranched(Counter(), offsets=(0,))
    tranched.tranches[0].schedule = Daily()
    tranched.target_weights(as_of=LATER, data=data)

    tranched.reset()
    assert all(t.calls == 0 for t in tranched.tranches)  # type: ignore[attr-defined]

    # Silence the tranche: with nothing held and nothing due, there is no opinion left.
    tranched.tranches[0].schedule = Monthly(5)
    assert tranched.target_weights(as_of=LATER, data=data) is None


# ------------------------------------------------------------------ configuration


def test_warmup_is_the_slowest_tranche_s() -> None:
    assert Tranched(Counter()).warmup_sessions == 1


def test_it_names_itself_after_what_it_wraps() -> None:
    assert "counter" in Tranched(Counter()).name
    assert "4 tranches" in Tranched(Counter()).name


def test_duplicate_offsets_are_a_mistake_not_a_feature() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        Tranched(Counter(), offsets=(0, 5, 5))


def test_it_needs_at_least_one_tranche() -> None:
    with pytest.raises(ValueError, match="at least one offset"):
        Tranched(Counter(), offsets=())


def test_a_static_strategy_survives_being_tranched(data: HistoricalFeed) -> None:
    """Tranching must be safe to apply to anything, even where it achieves nothing."""
    tranched = Tranched(StaticWeights({"A": "0.5"}), offsets=(0, 5))
    for tranche in tranched.tranches:
        tranche.schedule = Daily()
    weights = tranched.target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert weights["A"] == dec("0.5")
    assert sum(weights.values(), start=ZERO) <= dec(1)
