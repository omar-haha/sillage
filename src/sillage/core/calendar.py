"""Trading calendars: deciding when the system is allowed to act.

This module exists because the portfolio holds two kinds of thing at once. NYSE-listed
ETFs trade roughly 6.5 hours a day, on weekdays, minus a shifting list of holidays and
the occasional half-day. Crypto never closes. Any code that assumes one of those
worlds is wrong about the other.

The resolution used here: the *portfolio* rebalances on a schedule driven by the
exchange calendar of its non-continuous assets, because you cannot rebalance a book
containing SPY on a day the NYSE is shut. Crypto positions are simply carried across
the closure and rebalanced along with everything else on the next session. Trading
crypto on days the equity sleeve is frozen would mean the two halves of the portfolio
drift out of sync with each other, which is worse than waiting.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from functools import lru_cache
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

import exchange_calendars as xcals

if TYPE_CHECKING:
    import pandas as pd

# The exchange whose sessions define the portfolio's heartbeat. XNYS is the NYSE.
DEFAULT_CALENDAR = "XNYS"

# `exchange_calendars` builds only a ~20-year window around today unless told
# otherwise, so a calendar left to its defaults does not know that 2005 existed. Since
# the whole point of the backtest is to include 2008, the bounds are set explicitly.
CALENDAR_START: Final = "1990-01-01"


def _calendar_end() -> str:
    """Two years ahead, rounded to a year boundary.

    Rounding keeps the cache key stable: computing an exact date from `today()` would
    give a different key each day and rebuild the calendar every midnight.
    """
    return f"{date.today().year + 2}-12-31"


@lru_cache(maxsize=8)
def _calendar(name: str, start: str, end: str) -> xcals.ExchangeCalendar:
    """Load and cache an exchange calendar.

    Building one is expensive -- it materialises every session over the whole range --
    and the backtest asks about sessions thousands of times, so caching is not
    optional.
    """
    return xcals.get_calendar(name, start=start, end=end)


@dataclass(frozen=True, slots=True)
class Session:
    """One tradable day, with the instants that matter for execution.

    `close` is when the day's closing price becomes known, so it is the earliest moment
    a signal computed from that close may exist. `next_open` is the earliest moment
    that signal may be acted upon. Keeping both on the same object is what makes the
    decide-at-close/trade-at-next-open rule easy to honour and hard to violate.
    """

    day: date
    open: datetime
    close: datetime

    def __post_init__(self) -> None:
        if self.close <= self.open:
            raise ValueError(f"{self.day}: session close must be after open")


@runtime_checkable
class Calendar(Protocol):
    """What the engine needs from a calendar, whichever market it describes.

    Stated as a protocol rather than a base class so the two implementations below stay
    independent: one wraps a third-party library and the other is six lines of date
    arithmetic, and forcing them to share machinery would only make both worse.
    """

    name: str

    def is_session(self, day: date) -> bool: ...

    def sessions(self, start: date, end: date) -> list[Session]: ...

    def next_session(self, after: date) -> date: ...

    def previous_session(self, before: date) -> date: ...

    def is_month_end_session(self, day: date) -> bool: ...

    def month_end_sessions(self, start: date, end: date) -> list[date]: ...


class TradingCalendar:
    """Sessions for one exchange, plus the month-boundary queries the strategy needs."""

    def __init__(self, name: str = DEFAULT_CALENDAR) -> None:
        self.name = name
        self._cal = _calendar(name, CALENDAR_START, _calendar_end())

    def is_session(self, day: date) -> bool:
        return bool(self._cal.is_session(_to_ts(day)))

    def sessions(self, start: date, end: date) -> list[Session]:
        """Every tradable session in [start, end], inclusive."""
        days = self._cal.sessions_in_range(_to_ts(start), _to_ts(end))
        return [self._session_for(d.date()) for d in days]

    def _session_for(self, day: date) -> Session:
        ts = _to_ts(day)
        return Session(
            day=day,
            open=self._cal.session_open(ts).to_pydatetime().astimezone(UTC),
            close=self._cal.session_close(ts).to_pydatetime().astimezone(UTC),
        )

    def next_session(self, after: date) -> date:
        """The first tradable day strictly after `after`."""
        result: date = self._cal.next_session(_to_ts(after)).date()
        return result

    def previous_session(self, before: date) -> date:
        result: date = self._cal.previous_session(_to_ts(before)).date()
        return result

    def is_month_end_session(self, day: date) -> bool:
        """True if `day` is the last tradable day of its calendar month.

        Note this is the last *tradable* day, not the 30th or 31st. If the month ends
        on a Sunday, the month-end session is the preceding Friday. A strategy that
        rebalances on "the last day of the month" and looks for day 31 will silently
        skip months, so the question has to be asked this way.
        """
        if not self.is_session(day):
            return False
        nxt = self.next_session(day)
        return (nxt.year, nxt.month) != (day.year, day.month)

    def month_end_sessions(self, start: date, end: date) -> list[date]:
        """Every month-end rebalance date in the range. The strategy's schedule."""
        return [s.day for s in self.sessions(start, end) if self.is_month_end_session(s.day)]


class ContinuousCalendar:
    """A calendar for markets that never close, i.e. crypto.

    Deliberately mirrors `TradingCalendar`'s interface rather than sharing a base
    class. Every day is a session running midnight to midnight UTC.
    """

    name = "24/7"

    def is_session(self, day: date) -> bool:  # noqa: ARG002
        return True

    def sessions(self, start: date, end: date) -> list[Session]:
        return list(_daily_sessions(start, end))

    def next_session(self, after: date) -> date:
        return after.fromordinal(after.toordinal() + 1)

    def previous_session(self, before: date) -> date:
        return before.fromordinal(before.toordinal() - 1)

    def is_month_end_session(self, day: date) -> bool:
        return self.next_session(day).month != day.month

    def month_end_sessions(self, start: date, end: date) -> list[date]:
        return [s.day for s in self.sessions(start, end) if self.is_month_end_session(s.day)]


def _daily_sessions(start: date, end: date) -> Iterator[Session]:
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        yield Session(
            day=day,
            open=datetime.combine(day, time.min, tzinfo=UTC),
            close=datetime.combine(day, time.max, tzinfo=UTC),
        )


def _to_ts(day: date) -> pd.Timestamp:
    import pandas as pd

    return pd.Timestamp(day)
