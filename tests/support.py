"""Shared builders for synthetic market data.

Every engine test runs on prices invented here rather than on real history, for the
usual reason: a test asserting that a fill happened at 99.00 has to know that 99.00 was
the open, and a test that reads real data can only assert vague things about plausible
numbers.

The one detail worth care is that opens and closes are deliberately far apart. If a bar
opened and closed at the same price, a test could not tell whether the engine filled an
order at the right price or merely at *a* right-looking price -- and the entire
decide-at-close/trade-at-next-open guarantee would be untestable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

from sillage.core.calendar import Session, TradingCalendar
from sillage.core.money import dec, quantize_price
from sillage.core.types import AssetClass, Bar, Instrument
from sillage.data.store import BarStore

#: Opens sit 10% below closes so the two are never confusable in an assertion.
OPEN_RATIO = dec("0.9")

FIRST_SESSION = date(2024, 1, 2)


def etf(symbol: str = "AAA", lot_size: Decimal | str | int = 1) -> Instrument:
    return Instrument(symbol, AssetClass.ETF, lot_size=dec(lot_size))


def sessions(count: int, *, start: date = FIRST_SESSION) -> list[Session]:
    """The next `count` NYSE sessions from `start`, real holidays included."""
    calendar = TradingCalendar()
    found: list[Session] = []
    day = start
    while len(found) < count:
        window = calendar.sessions(day, date(day.year + 1, day.month, day.day))
        found.extend(window[: count - len(found)])
        day = date(day.year + 1, day.month, day.day)
    return found[:count]


def make_bars(
    instrument: Instrument,
    closes: Sequence[float | str | Decimal],
    *,
    start: date = FIRST_SESSION,
    open_ratio: Decimal = OPEN_RATIO,
    volume: float = 1_000_000,
) -> list[Bar]:
    """A bar per session, timestamped at the session close as the engine expects."""
    bars: list[Bar] = []
    for session, close in zip(sessions(len(closes), start=start), closes, strict=True):
        c = quantize_price(dec(close))
        o = quantize_price(c * open_ratio)
        bars.append(
            Bar(
                instrument=instrument,
                ts=session.close,
                open=o,
                high=quantize_price(max(o, c) * dec("1.01")),
                low=quantize_price(min(o, c) * dec("0.99")),
                close=c,
                volume=dec(volume),
            )
        )
    return bars


def synthetic_store(root: Path, symbols: Mapping[str, float], sessions: int = 700) -> BarStore:
    """A store of invented history, for tests that need to run a whole backtest.

    Each symbol gets a compounding path at its own drift plus an alternating wobble, so
    the series have genuine volatility -- a perfectly smooth ramp has a constant daily
    return and therefore *zero* measured volatility, which silently disables any sizing
    logic under test.
    """
    import pandas as pd

    store = BarStore(root)
    days = [s.day for s in sessions_from(sessions)]
    for offset, (symbol, drift) in enumerate(symbols.items()):
        prices, price = [], 100.0
        for i in range(sessions):
            prices.append(price)
            price *= 1.0 + drift + (0.008 if (i + offset) % 2 == 0 else -0.008)
        frame = pd.DataFrame(
            {
                "open": [p * 0.999 for p in prices],
                "high": [p * 1.01 for p in prices],
                "low": [p * 0.99 for p in prices],
                "close": prices,
                "volume": [1_000_000.0] * sessions,
            },
            index=pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in days], name="ts"),
        )
        store.write(symbol, frame)
    return store


def sessions_from(count: int, *, start: date = FIRST_SESSION) -> list[Session]:
    """`count` real NYSE sessions, starting well before `FIRST_SESSION` if needed."""
    calendar = TradingCalendar()
    window = calendar.sessions(date(start.year - 6, 1, 1), date(start.year + 6, 1, 1))
    return window[:count]
