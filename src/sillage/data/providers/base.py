"""The price provider interface.

Every source of market data -- Yahoo for research, IBKR for live equities, a crypto
exchange via ccxt -- is reduced to this one shape, so that nothing downstream knows or
cares where a price came from. Swapping the research provider for the live one must not
require touching a single line of strategy code.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import pandas as pd

# The normalised schema every provider must return. Prices are adjusted for splits and
# dividends, so `close` is a total-return price rather than the headline quote.
BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class ProviderError(RuntimeError):
    """A provider could not supply data. Never swallowed silently."""


@runtime_checkable
class PriceProvider(Protocol):
    """Fetches historical daily bars for one symbol."""

    name: str

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return adjusted daily bars for [start, end].

        The result is indexed by a timezone-aware UTC `DatetimeIndex` named `ts`, whose
        values are the instant each bar *closed*, sorted ascending and free of
        duplicates. Columns are exactly `BAR_COLUMNS`.

        Raises `ProviderError` if the symbol is unknown or the source is unreachable.
        Returning an empty frame is reserved for "this symbol legitimately has no data
        in this window", which is a different situation from a failure.
        """
        ...
