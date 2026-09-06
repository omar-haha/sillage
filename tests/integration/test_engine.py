"""End-to-end tests of the loop, on prices chosen so every number is checkable by hand.

Closes are 100, 110, 120, 130, 140 and every open is exactly 10% below its close. That
gap is the instrument: an order decided at the close of day one must fill at 99, the
open of day two. A fill at 100 would mean it traded at the price it was decided on, and
a fill at 108 would mean it traded on a price from a day it could not have seen.

The arithmetic is small enough to state in full. Ten thousand dollars, a target of
100%, a price of 100 at the first close: one hundred shares. They fill at 99, costing
9,900 and leaving 100 in cash. At the last close those shares are worth 14,000, so the
fund is worth 14,100. Every assertion below is a piece of that sentence.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from tests.support import etf, make_bars, sessions

from sillage.core.money import dec
from sillage.core.types import Portfolio
from sillage.engine.clock import BacktestClock
from sillage.engine.feed import DataSource, HistoricalFeed, MarketFeed
from sillage.engine.journal import InMemoryJournal
from sillage.engine.loop import Engine
from sillage.execution.costs import FREE, CostModel
from sillage.execution.simulated import SimulatedBroker
from sillage.portfolio.rebalance import Rebalancer
from sillage.strategy.base import Daily, TargetWeights
from sillage.strategy.benchmarks import StaticWeights, buy_and_hold

AAA = etf("AAA")
CLOSES = [100, 110, 120, 130, 140]
BARS = {"AAA": make_bars(AAA, CLOSES)}
DAYS = [s.day for s in sessions(len(CLOSES))]


def build(
    strategy: object = None,
    *,
    costs: CostModel = FREE,
    cash: Decimal | int = 10_000,
    journal: InMemoryJournal | None = None,
) -> Engine:
    return Engine(
        clock=BacktestClock(DAYS[0], DAYS[-1]),
        data=HistoricalFeed(BARS),
        broker=SimulatedBroker(MarketFeed(BARS), costs),
        strategy=strategy or buy_and_hold("AAA"),  # type: ignore[arg-type]
        instruments={"AAA": AAA},
        rebalancer=Rebalancer(),
        journal=journal or InMemoryJournal(),
        initial_cash=dec(cash),
    )


# ------------------------------------------------------------------ the arithmetic


def test_the_whole_run_reconciles() -> None:
    journal = InMemoryJournal()
    final = build(journal=journal).run()

    assert len(journal.fills) == 1
    fill = journal.fills[0]
    assert fill.quantity == dec(100)
    assert fill.price == dec(90) * dec("1.1")  # the open of session two, i.e. 99
    assert final.cash == dec(100)
    assert final.position(AAA).quantity == dec(100)
    assert journal.nav_points[-1].nav == dec(14_100)


def test_nothing_is_decided_before_the_first_close() -> None:
    """The first session's open comes before any close, so there is nothing to fill."""
    journal = InMemoryJournal()
    build(journal=journal).run()
    assert journal.fills[0].ts.date() == DAYS[1]


def test_the_book_is_marked_at_every_close() -> None:
    journal = InMemoryJournal()
    build(journal=journal).run()
    assert [p.session for p in journal.nav_points] == DAYS


def test_nav_starts_at_the_initial_cash() -> None:
    journal = InMemoryJournal()
    build(journal=journal).run()
    assert journal.nav_points[0].nav == dec(10_000)
    assert journal.nav_points[0].gross_exposure == dec(0)


def test_costs_reduce_the_final_value() -> None:
    free = build().run()
    charged = build(costs=CostModel()).run()
    prices = {"AAA": dec(140)}
    assert charged.nav(prices) < free.nav(prices)


# ------------------------------------------------------------------ no lookahead


class Recorder:
    """A strategy that remembers what it was shown, so the test can inspect it."""

    name = "recorder"
    warmup_sessions = 1

    def __init__(self) -> None:
        self.schedule = Daily()
        self.seen: list[tuple[datetime, list[datetime]]] = []

    def reset(self) -> None:
        self.seen = []

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        self.seen.append((as_of, [b.ts for b in data.history("AAA", as_of=as_of)]))
        return None


def test_a_strategy_is_never_shown_a_bar_from_the_future() -> None:
    recorder = Recorder()
    build(recorder).run()
    assert recorder.seen
    for as_of, stamps in recorder.seen:
        assert all(ts <= as_of for ts in stamps)


def test_a_strategy_sees_exactly_the_history_up_to_its_decision() -> None:
    recorder = Recorder()
    build(recorder).run()
    assert [len(stamps) for _, stamps in recorder.seen] == [1, 2, 3, 4, 5]


def test_holding_no_opinion_leaves_the_book_alone() -> None:
    journal = InMemoryJournal()
    engine = build(Recorder(), journal=journal)
    assert engine.run() == Portfolio(cash=dec(10_000))
    assert journal.orders == []


# ------------------------------------------------------------------ loud failures


def test_targeting_a_symbol_outside_the_universe_is_an_error() -> None:
    stray = StaticWeights({"ZZZ": "1.0"}, schedule=Daily())
    engine = build(stray)
    engine.data = HistoricalFeed({**BARS, "ZZZ": make_bars(etf("ZZZ"), CLOSES)})
    with pytest.raises(KeyError, match="outside its universe"):
        engine.run()


def test_initial_cash_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        build(cash=0)


def test_a_rerun_starts_from_the_same_place() -> None:
    """Walk-forward depends on this: a strategy reused across windows must reset."""
    engine = build()
    first = engine.run()
    engine.portfolio = Portfolio(cash=dec(10_000))
    assert engine.run().positions.keys() == first.positions.keys()
