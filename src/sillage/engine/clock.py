"""Where time comes from.

This is one half of what makes backtesting and live trading run the same code. The
engine never asks what day it is; it consumes events from a clock. Replaying history
and trading this afternoon differ only in which clock is plugged in, so there is no
"backtest mode" branch anywhere in the strategy or portfolio layers to get out of sync.

`BacktestClock` walks a calendar as fast as the CPU allows. The live clock arrives in
Phase 5 and blocks until each instant actually happens; the protocol below is written
so that it is a drop-in rather than a rewrite, which is why it yields events instead of
exposing a "next date" the caller has to loop over itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Protocol, runtime_checkable

from sillage.core.calendar import Calendar, TradingCalendar
from sillage.engine.events import Event, EventKind


@runtime_checkable
class Clock(Protocol):
    """A source of engine events, in chronological order."""

    #: Which market's sessions the clock is stepping through. The engine needs it to
    #: ask a schedule whether today is a rebalance day, and a clock always knows it.
    calendar: Calendar

    @property
    def now(self) -> datetime:
        """The instant of the event most recently yielded.

        Everything that reads data uses this, never `datetime.now()`. A module that
        reaches for the wall clock during a backtest is reading the future.
        """
        ...

    def events(self) -> Iterator[Event]: ...


class BacktestClock:
    """Replays an exchange calendar as open/close events.

    Yields the open before the close of the same day, so an order decided at one close
    is filled at the next open -- a full session later, at a price that had not printed
    when the decision was made.
    """

    def __init__(
        self,
        start: date,
        end: date,
        calendar: Calendar | None = None,
    ) -> None:
        if start > end:
            raise ValueError(f"backtest start {start} is after end {end}")
        self.calendar: Calendar = calendar or TradingCalendar()
        self.start = start
        self.end = end
        self._sessions = self.calendar.sessions(start, end)
        if not self._sessions:
            raise ValueError(f"no trading sessions between {start} and {end}")
        # Before the first event is yielded, "now" is the instant the backtest begins.
        # Not `None`: a clock that can report an unknown time forces every caller to
        # handle a state that only exists for a few microseconds.
        self._now = self._sessions[0].open

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def sessions(self) -> list[date]:
        return [s.day for s in self._sessions]

    @property
    def first_session(self) -> date:
        return self._sessions[0].day

    @property
    def last_session(self) -> date:
        return self._sessions[-1].day

    def events(self) -> Iterator[Event]:
        for session in self._sessions:
            self._now = session.open
            yield Event(EventKind.SESSION_OPEN, session.open, session.day)
            self._now = session.close
            yield Event(EventKind.SESSION_CLOSE, session.close, session.day)

    def __repr__(self) -> str:
        return (
            f"BacktestClock({self.first_session}..{self.last_session}, "
            f"{len(self._sessions)} sessions, {self.calendar.name})"
        )


class LiveClock:
    """Wall-clock sessions: every event that has already happened and not been processed.

    The other half of the claim this project is built on. `BacktestClock` replays a
    fixed range as fast as the CPU allows; this yields the same events from the real
    calendar, bounded by the actual time, and the engine cannot tell the difference.

    **It emits only events that are in the past.** A session open at 14:30 UTC is not an
    event until 14:30 UTC has happened. That is what makes a live run restart-safe and
    catch-up-safe in the same motion: a process that was down for three days comes back,
    asks for everything since the last session it recorded, and replays it in order
    through exactly the same `Engine.step` a backtest uses.

    **It does not sleep.** A blocking clock would put the scheduling policy inside the
    engine, where it cannot be tested and cannot be restarted. Waking up is cron's job,
    or a supervisor's; this only answers "what have I missed".
    """

    def __init__(
        self,
        *,
        since: date | None = None,
        until: datetime | None = None,
        calendar: Calendar | None = None,
        lookback_days: int = 30,
    ) -> None:
        self.calendar: Calendar = calendar or TradingCalendar()
        self._until = until or datetime.now(UTC)
        if self._until.tzinfo is None:
            raise ValueError("the live clock needs a timezone-aware instant")
        # Without a starting point, look back far enough to catch a long weekend plus a
        # holiday, but not so far that a fresh journal replays a decade.
        first = since or (self._until.date() - timedelta(days=lookback_days))
        self._sessions = [
            session
            for session in self.calendar.sessions(first, self._until.date())
            if session.day > (since or date.min)
        ]
        self._now = self._until

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def until(self) -> datetime:
        return self._until

    def events(self) -> Iterator[Event]:
        for session in self._sessions:
            for kind, moment in (
                (EventKind.SESSION_OPEN, session.open),
                (EventKind.SESSION_CLOSE, session.close),
            ):
                if moment > self._until:
                    return
                self._now = moment
                yield Event(kind, moment, session.day)

    def __repr__(self) -> str:
        return f"LiveClock(up to {self._until.isoformat()}, {len(self._sessions)} session(s))"
