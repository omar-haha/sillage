"""Local price storage, and the enforcement point for point-in-time correctness.

Every read goes through `BarStore.read`, which takes an `as_of` timestamp and refuses
to return any bar that closed after it. This is the structural defence against
lookahead bias: rather than asking every strategy author to remember not to peek at the
future, the data layer makes peeking impossible. A strategy that wants tomorrow's price
cannot get it, because there is no code path that hands it over.

Storage is one Parquet file per symbol. Parquet because it is columnar (reading just
the close column of a 20-year history touches a fraction of the bytes), typed (a date
stays a date across a round trip, unlike CSV), compressed, and readable by anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sillage.core.money import dec, quantize_price
from sillage.core.types import Bar, Instrument
from sillage.data.providers.base import BAR_COLUMNS

if TYPE_CHECKING:
    import pandas as pd

DEFAULT_ROOT = Path("data")


@dataclass(frozen=True, slots=True)
class Coverage:
    """What the store holds for one symbol."""

    symbol: str
    start: date
    end: date
    rows: int

    @property
    def span_years(self) -> float:
        return (self.end - self.start).days / 365.25


class BarStore:
    """Parquet-backed daily bars, read-limited by `as_of`."""

    def __init__(self, root: Path | str = DEFAULT_ROOT, interval: str = "daily") -> None:
        self.root = Path(root)
        self.interval = interval

    @property
    def _dir(self) -> Path:
        return self.root / "bars" / self.interval

    def path_for(self, symbol: str) -> Path:
        # Symbols like BTC-USD are filesystem-safe, but a forward slash (BTC/USD, the
        # ccxt convention) would silently create a subdirectory.
        return self._dir / f"{symbol.replace('/', '_')}.parquet"

    def has(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def symbols(self) -> list[str]:
        if not self._dir.exists():
            return []
        return sorted(p.stem.replace("_", "/") for p in self._dir.glob("*.parquet"))

    # ------------------------------------------------------------------ writing

    def write(self, symbol: str, frame: pd.DataFrame) -> int:
        """Merge `frame` into stored history, returning the total row count.

        New rows win on conflict. Restating an existing bar is normal rather than
        suspicious: adjusted prices are recomputed whenever a dividend is paid, so
        re-syncing legitimately changes history. `data check` is what surveils that.
        """
        import pandas as pd

        if frame.empty:
            return self.rows(symbol)

        frame = _validate(symbol, frame)
        path = self.path_for(symbol)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = pd.read_parquet(path)
            combined = pd.concat([existing, frame])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = frame

        combined.to_parquet(path, compression="zstd", index=True)
        return len(combined)

    # ------------------------------------------------------------------ reading

    def read(
        self,
        symbol: str,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | date | None = None,
    ) -> pd.DataFrame:
        """Read stored bars, truncated at `as_of`.

        `as_of` is the whole point of this method. It represents "the moment the caller
        is pretending to be", and no bar that closed after it can be returned. During a
        backtest the engine passes the simulated clock's time; in live trading it
        passes now. The same code therefore cannot cheat in one mode and behave in the
        other.
        """
        import pandas as pd

        path = self.path_for(symbol)
        if not path.exists():
            raise FileNotFoundError(
                f"no stored bars for {symbol!r}; run `sillage data sync` to fetch them"
            )

        frame = pd.read_parquet(path)
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start, tz="UTC")]
        if end is not None:
            frame = frame[frame.index <= pd.Timestamp(end, tz="UTC").normalize()]
        if as_of is not None:
            frame = frame[frame.index <= _as_timestamp(as_of)]
        return frame

    def rows(self, symbol: str) -> int:
        path = self.path_for(symbol)
        if not path.exists():
            return 0
        import pandas as pd

        return len(pd.read_parquet(path, columns=["close"]))

    def coverage(self, symbol: str) -> Coverage | None:
        if not self.has(symbol):
            return None
        frame = self.read(symbol)
        if frame.empty:
            return None
        return Coverage(
            symbol=symbol,
            start=frame.index[0].date(),
            end=frame.index[-1].date(),
            rows=len(frame),
        )

    def bars(
        self,
        instrument: Instrument,
        *,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | date | None = None,
    ) -> list[Bar]:
        """Read bars as validated domain objects.

        This is the boundary where pandas stops and the rest of the system begins.
        Floats become Decimals here, and every `Bar` runs its own validation, so a
        malformed row fails loudly at the edge instead of silently poisoning a
        calculation twenty modules deep.
        """
        frame = self.read(instrument.symbol, start=start, end=end, as_of=as_of)
        return [
            Bar(
                instrument=instrument,
                ts=ts.to_pydatetime(),
                open=_price(row.open),
                high=_price(row.high),
                low=_price(row.low),
                close=_price(row.close),
                volume=_d(row.volume),
            )
            for ts, row in zip(frame.index, frame.itertuples(index=False), strict=True)
        ]

    def close_prices(
        self, symbols: list[str], *, as_of: datetime | date | None = None
    ) -> pd.DataFrame:
        """Aligned close prices for several symbols, one column each.

        Aligned on the union of dates, so a day the NYSE was shut but crypto traded
        appears with NaN in the equity columns. Callers decide how to handle that;
        forward-filling here would invent prices that never printed.
        """
        import pandas as pd

        series = {}
        for symbol in symbols:
            frame = self.read(symbol, as_of=as_of)
            if not frame.empty:
                series[symbol] = frame["close"]
        if not series:
            return pd.DataFrame()
        return pd.DataFrame(series).sort_index()


def _validate(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Reject anything that would corrupt the store."""
    import pandas as pd

    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"{symbol}: frame is missing columns {sorted(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise TypeError(f"{symbol}: frame must be indexed by DatetimeIndex")
    if frame.index.tz is None:
        raise ValueError(f"{symbol}: frame index must be timezone-aware")
    if frame.index.has_duplicates:
        raise ValueError(f"{symbol}: frame index contains duplicate timestamps")
    if not frame.index.is_monotonic_increasing:
        frame = frame.sort_index()
    return frame.loc[:, list(BAR_COLUMNS)].astype("float64")


def _as_timestamp(value: datetime | date) -> pd.Timestamp:
    import pandas as pd

    if isinstance(value, datetime):
        ts = pd.Timestamp(value)
        return ts.tz_localize(UTC) if ts.tz is None else ts.tz_convert(UTC)
    # A bare date means "the end of that day": bars closing on it are visible.
    return pd.Timestamp(value, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)


def _d(value: Any) -> Decimal:
    """Convert one numpy/pandas scalar to Decimal.

    Typed as `Any` deliberately: `itertuples` gives back numpy scalars whose static
    type is a large union, and narrowing it here would be noise. The `float()` call is
    the real contract, and a value that cannot become a float raises immediately.
    """
    return dec(float(value))


def _price(value: Any) -> Decimal:
    """Convert a stored price, rounded to a precision a price can actually have.

    Not cosmetic. Adjusted prices are computed by multiplying raw prices by a dividend
    factor in float64, and that arithmetic does not preserve the relationships between
    the four prices in a bar. On 2007-10-26 SPY closed at its high, and after
    adjustment the stored close came out one unit in the last place *above* the stored
    high -- a difference of 1e-14 dollars, which `Bar` correctly refused as impossible.

    Rounding to eight decimal places at the boundary keeps the domain model strict
    while declining to enforce a property of IEEE-754 rather than of the market. The
    bar validation stays exact on purpose: a discrepancy larger than a hundred-
    millionth of a cent is not floating-point noise and should still fail loudly.
    """
    return quantize_price(dec(float(value)))
