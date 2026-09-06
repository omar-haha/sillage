"""Yahoo Finance provider, used for research.

Yahoo is free, needs no key, and has decades of history for every ETF in the universe,
which makes it the right tool for building and validating a strategy. It is also an
undocumented endpoint that can change without notice, so it is deliberately confined to
research: nothing that places a real order will ever read from it.

**A caveat worth understanding.** These are *adjusted* prices -- each historical bar is
scaled to account for every split and dividend that happened after it. That is what
makes `close` a total-return series, which is what a momentum signal should measure.
But it also means the past is restated: the bar for 2015-03-10 downloaded today is a
slightly different number from the same bar downloaded a year ago, because dividends
have been paid in between. Strictly, a backtest reading today's adjusted history sees
prices no one could have observed at the time.

For monthly-rebalanced ETF momentum the distortion is immaterial -- adjustment scales
the whole series, so relative rankings and percentage returns are almost untouched.
It would matter for a strategy keying off absolute price levels or short horizons.
This is documented rather than fixed because the honest fix is a provider that stores
prices as they were first reported, which is a paid data product.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from sillage.data.providers.base import BAR_COLUMNS, ProviderError

if TYPE_CHECKING:
    import pandas as pd


class YahooProvider:
    """Daily bars from Yahoo Finance via `yfinance`."""

    name = "yahoo"

    def __init__(self, *, timeout: int = 30) -> None:
        self.timeout = timeout

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        import pandas as pd
        import yfinance as yf

        if start > end:
            raise ValueError(f"{symbol}: start {start} is after end {end}")

        try:
            raw = yf.download(
                symbol,
                start=start.isoformat(),
                # yfinance treats `end` as exclusive; add a day so the caller's
                # inclusive range means what they wrote.
                end=(end + timedelta(days=1)).isoformat(),
                interval="1d",
                # Adjust OHLC for splits and dividends, giving a total-return series.
                auto_adjust=True,
                progress=False,
                threads=False,
                timeout=self.timeout,
            )
        # Deliberately broad: yfinance raises requests errors, JSON decode errors and
        # its own types depending on how the undocumented endpoint fails that day.
        except Exception as exc:
            raise ProviderError(f"yahoo: failed to fetch {symbol}: {exc}") from exc

        if raw is None or raw.empty:
            return _empty_frame()

        # A single-symbol download still comes back with a two-level column index
        # ('Close', 'SPY'). Flatten it so the schema does not depend on how many
        # symbols were requested.
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        frame = raw.rename(columns=str.lower)
        missing = set(BAR_COLUMNS) - set(frame.columns)
        if missing:
            raise ProviderError(f"yahoo: {symbol} response is missing {sorted(missing)}")

        frame = frame.loc[:, list(BAR_COLUMNS)].copy()
        frame.index = _to_utc_index(frame.index)
        frame.index.name = "ts"

        # Yahoo occasionally returns a duplicated final row, and rows of all-NaN for
        # days the symbol did not trade. Neither is fatal, both must not reach the
        # store, where they would become phantom bars in the backtest.
        frame = frame[~frame.index.duplicated(keep="last")]
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        frame = frame.sort_index()

        if (frame[["open", "high", "low", "close"]] <= 0).to_numpy().any():
            raise ProviderError(f"yahoo: {symbol} returned non-positive prices")

        result: pd.DataFrame = frame
        return result


def _to_utc_index(index: pd.Index) -> pd.DatetimeIndex:
    """Force any index Yahoo returns into timezone-aware UTC.

    Daily bars come back tz-naive and dated to the exchange's local day. Localising to
    UTC keeps one convention everywhere and makes crypto (genuinely UTC) and equities
    directly comparable.
    """
    import pandas as pd

    idx = pd.DatetimeIndex(index)
    localised = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return pd.DatetimeIndex(localised)


def _empty_frame() -> pd.DataFrame:
    import pandas as pd

    return pd.DataFrame(
        columns=list(BAR_COLUMNS),
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
        dtype="float64",
    )
