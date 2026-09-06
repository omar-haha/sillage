"""Tests for the backtest clock.

The ordering assertions here are load-bearing. The engine's whole no-lookahead
guarantee reduces to "an open is always seen before the close of the same day, and a
close is always seen before the next day's open", so if this file passes, an order
decided at a close cannot be filled until a session later.
"""

from __future__ import annotations

from datetime import date
from itertools import pairwise

import pytest

from sillage.core.calendar import TradingCalendar
from sillage.engine.clock import BacktestClock
from sillage.engine.events import EventKind


def test_yields_open_then_close_for_each_session() -> None:
    clock = BacktestClock(date(2024, 1, 2), date(2024, 1, 4))
    kinds = [(e.session, e.kind) for e in clock.events()]
    assert kinds == [
        (date(2024, 1, 2), EventKind.SESSION_OPEN),
        (date(2024, 1, 2), EventKind.SESSION_CLOSE),
        (date(2024, 1, 3), EventKind.SESSION_OPEN),
        (date(2024, 1, 3), EventKind.SESSION_CLOSE),
        (date(2024, 1, 4), EventKind.SESSION_OPEN),
        (date(2024, 1, 4), EventKind.SESSION_CLOSE),
    ]


def test_events_are_strictly_increasing_in_time() -> None:
    clock = BacktestClock(date(2024, 1, 2), date(2024, 3, 29))
    stamps = [e.ts for e in clock.events()]
    assert all(a < b for a, b in pairwise(stamps))


def test_a_close_precedes_the_next_open() -> None:
    """The gap that makes decide-at-close/trade-at-next-open possible."""
    clock = BacktestClock(date(2024, 1, 2), date(2024, 1, 10))
    events = list(clock.events())
    closes = [e for e in events if e.is_close]
    opens = [e for e in events if e.is_open]
    assert all(c.ts < o.ts for c, o in zip(closes, opens[1:], strict=False))


def test_now_tracks_the_event_being_yielded() -> None:
    clock = BacktestClock(date(2024, 1, 2), date(2024, 1, 5))
    for event in clock.events():
        assert clock.now == event.ts


def test_skips_weekends_and_holidays() -> None:
    # 2024-01-01 was a Monday holiday; 6th and 7th were the weekend.
    clock = BacktestClock(date(2023, 12, 29), date(2024, 1, 8))
    assert clock.sessions == [
        date(2023, 12, 29),
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    ]


def test_refuses_a_backwards_range() -> None:
    with pytest.raises(ValueError, match="after end"):
        BacktestClock(date(2024, 6, 1), date(2024, 1, 1))


def test_refuses_a_range_with_no_sessions() -> None:
    with pytest.raises(ValueError, match="no trading sessions"):
        BacktestClock(date(2024, 1, 6), date(2024, 1, 7))


def test_carries_its_calendar(calendar: TradingCalendar) -> None:
    """The engine asks the clock which calendar it is on to evaluate schedules."""
    clock = BacktestClock(date(2024, 1, 2), date(2024, 1, 5), calendar)
    assert clock.calendar is calendar
