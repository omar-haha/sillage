"""In-memory price access, split into two views with deliberately different powers.

The store on disk is the archive; this is what the running engine reads. Bars are
loaded once into plain Python objects and then served from memory, because a backtest
asks about prices tens of thousands of times and reopening a Parquet file each time
would dominate the runtime.

**The two views are the interesting part.**

`HistoricalFeed` is what the *strategy* sees. Every read takes an `as_of` and cannot
return a bar that closed after it. This is the same guarantee `BarStore` makes, carried
into memory rather than dropped at the cache boundary.

`MarketFeed` is what the *broker* sees. A broker is not a participant guessing at the
future; it is the venue, and at the moment of the open it genuinely knows the opening
price. So it gets a view keyed by session -- but that view exposes only opens and
trailing volume. It has no method that returns a close, which means a simulated fill
cannot accidentally be priced at the close of the day it happens on. The restriction is
in the shape of the interface rather than in a rule someone has to remember.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Protocol, Self, runtime_checkable

from sillage.core.calendar import Calendar, ContinuousCalendar
from sillage.core.money import ZERO, safe_div
from sillage.core.types import Bar, Instrument
from sillage.data.store import BarStore


@runtime_checkable
class DataSource(Protocol):
    """What a strategy is permitted to know at a point in time."""

    def history(self, symbol: str, *, as_of: datetime, count: int | None = None) -> list[Bar]:
        """Bars that had closed at or before `as_of`, oldest first.

        `count` keeps only the most recent `count` of them, which is what every
        lookback-window calculation wants.
        """
        ...

    def latest(self, symbol: str, *, as_of: datetime) -> Bar | None: ...

    def closes(self, symbols: Iterable[str], *, as_of: datetime) -> dict[str, Decimal]: ...


@runtime_checkable
class ExecutionFeed(Protocol):
    """What a simulated venue is permitted to know when filling an order."""

    def open_price(self, symbol: str, session: date) -> Decimal | None: ...

    def average_volume(self, symbol: str, session: date, window: int) -> Decimal | None:
        """Mean volume over the `window` sessions strictly before `session`.

        Strictly before, because the day's own volume is not known at its open, and
        sizing slippage against a volume that includes your own trade is circular.
        """
        ...


def load_bars(
    store: BarStore,
    instruments: Iterable[Instrument],
    *,
    calendar: Calendar | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, list[Bar]]:
    """Read a universe out of the store into memory, once.

    Note there is no `as_of` here. Loading is not reading: the engine loads the whole
    span it intends to replay, and the `as_of` gate is applied on every access
    afterwards. Filtering at load time instead would mean reloading on every bar.

    **Timestamps are moved to the session close**, which is the subtle and important
    part. A daily bar arrives from the store dated to midnight UTC of its session,
    because that is how every daily data source identifies a day. But `Bar.ts` is
    defined as the instant the bar *closed*, and midnight is thirteen hours before the
    NYSE opens -- so a feed gated on the raw timestamp would consider the whole of
    today's bar knowable at today's open, closing price included.

    Nothing in the engine currently reads prices at an open, so this is latent rather
    than active. It is fixed here anyway: a guarantee that holds because of what the
    code happens not to do today is not a guarantee, and the live loop in Phase 5 will
    have decision points that the backtest does not.

    Pass no calendar and the timestamps are left exactly as stored, which is what the
    store's own tests want.
    """
    bars = {
        instrument.symbol: store.bars(instrument, start=start, end=end)
        for instrument in instruments
    }
    if calendar is None:
        return bars

    # The session-close map is built once per calendar and shared, rather than once per
    # symbol. Thirteen symbols spanning the same twenty years asked the same calendar
    # the same question thirteen times, which was most of the cost of loading a universe.
    continuous = {i.symbol for i in instruments if i.trades_continuously}
    spans = [(s[0].ts.date(), s[-1].ts.date()) for s in bars.values() if s]
    if not spans:
        return bars
    first, last = min(a for a, _ in spans), max(b for _, b in spans)

    closes = _session_closes(calendar, first, last)
    continuous_closes = _session_closes(ContinuousCalendar(), first, last) if continuous else {}
    return {
        symbol: _restamp(series, continuous_closes if symbol in continuous else closes)
        for symbol, series in bars.items()
    }


def _session_closes(calendar: Calendar, first: date, last: date) -> dict[date, datetime]:
    return {session.day: session.close for session in calendar.sessions(first, last)}


def _restamp(bars: Sequence[Bar], closes: Mapping[date, datetime]) -> list[Bar]:
    return [replace(bar, ts=closes[day]) for bar in bars if (day := bar.ts.date()) in closes]


def stamp_at_session_close(bars: Sequence[Bar], calendar: Calendar) -> list[Bar]:
    """Re-date each bar to the instant its session closed.

    A bar whose date is not a session on this calendar is dropped. That is not data
    loss to paper over: it means the price series and the calendar disagree about when
    the market was open, and `sillage data check` reports exactly that. Trading on a
    bar the calendar says could not exist is worse than not trading.
    """
    if not bars:
        return []
    return _restamp(bars, _session_closes(calendar, bars[0].ts.date(), bars[-1].ts.date()))


class HistoricalFeed:
    """The strategy's view of prices: everything up to `as_of`, and nothing past it."""

    def __init__(self, bars: Mapping[str, Sequence[Bar]]) -> None:
        self._bars: dict[str, list[Bar]] = {s: list(b) for s, b in bars.items()}
        for symbol, series in self._bars.items():
            if any(a.ts >= b.ts for a, b in pairwise(series)):
                raise ValueError(f"{symbol}: bars must be sorted by timestamp and unique")
        # Timestamps kept separately so a cutoff is a binary search over a flat list
        # rather than a scan over objects. With ~5,000 bars per symbol and a lookup on
        # every session this is the difference between a backtest that runs in a second
        # and one that runs in a minute.
        self._timestamps: dict[str, list[datetime]] = {
            symbol: [bar.ts for bar in series] for symbol, series in self._bars.items()
        }

    @classmethod
    def from_store(
        cls,
        store: BarStore,
        instruments: Iterable[Instrument],
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> Self:
        return cls(load_bars(store, instruments, start=start, end=end))

    @property
    def symbols(self) -> list[str]:
        return sorted(self._bars)

    def _cutoff(self, symbol: str, as_of: datetime) -> int:
        """How many bars for `symbol` had closed by `as_of`."""
        timestamps = self._timestamps.get(symbol)
        if timestamps is None:
            raise KeyError(f"no bars loaded for {symbol!r}")
        return bisect_right(timestamps, as_of)

    def history(self, symbol: str, *, as_of: datetime, count: int | None = None) -> list[Bar]:
        end = self._cutoff(symbol, as_of)
        if count is None:
            return self._bars[symbol][:end]
        return self._bars[symbol][max(0, end - count) : end]

    def latest(self, symbol: str, *, as_of: datetime) -> Bar | None:
        end = self._cutoff(symbol, as_of)
        return self._bars[symbol][end - 1] if end else None

    def closes(self, symbols: Iterable[str], *, as_of: datetime) -> dict[str, Decimal]:
        """Last known close per symbol, skipping any with no history yet.

        Symbols are skipped rather than defaulted to zero. A missing price means the
        asset did not exist yet, and pricing it at zero would put a fictitious hole in
        NAV; the caller decides what to do about it.
        """
        prices: dict[str, Decimal] = {}
        for symbol in symbols:
            bar = self.latest(symbol, as_of=as_of)
            if bar is not None:
                prices[symbol] = bar.close
        return prices

    def has_history(self, symbol: str, *, as_of: datetime, sessions: int) -> bool:
        """Whether at least `sessions` bars are available -- the warm-up question."""
        return symbol in self._bars and self._cutoff(symbol, as_of) >= sessions

    def first_session(self, symbol: str) -> date | None:
        series = self._bars.get(symbol)
        return series[0].ts.date() if series else None


class MarketFeed:
    """The venue's view: opening prices and past volume, indexed by session.

    Built from the same bars as `HistoricalFeed` but exposing a strict subset of them.
    Closes are reachable in the underlying objects and are simply never surfaced -- the
    protocol this satisfies has no method that would return one.
    """

    def __init__(self, bars: Mapping[str, Sequence[Bar]]) -> None:
        self._by_session: dict[str, dict[date, Bar]] = {}
        self._sessions: dict[str, list[date]] = {}
        for symbol, series in bars.items():
            self._by_session[symbol] = {bar.ts.date(): bar for bar in series}
            self._sessions[symbol] = [bar.ts.date() for bar in series]

    def open_price(self, symbol: str, session: date) -> Decimal | None:
        """The opening print, or None if the symbol did not trade that session.

        None is a real answer, not an error: an ETF that had not launched yet, or a
        crypto pair on a day the equity calendar is driving the loop, legitimately has
        no open. The broker turns it into a rejection, which is what a venue would do.
        """
        bar = self._by_session.get(symbol, {}).get(session)
        return bar.open if bar is not None else None

    def average_volume(self, symbol: str, session: date, window: int) -> Decimal | None:
        if window <= 0:
            raise ValueError("volume window must be positive")
        sessions = self._sessions.get(symbol)
        if not sessions:
            return None
        end = bisect_right(sessions, session)
        # `bisect_right` counts the session itself when it is present; step back so the
        # window is strictly prior.
        if end and sessions[end - 1] == session:
            end -= 1
        if end == 0:
            return None
        window_days = sessions[max(0, end - window) : end]
        by_session = self._by_session[symbol]
        total = sum((by_session[day].volume for day in window_days), start=ZERO)
        return safe_div(total, Decimal(len(window_days)))
