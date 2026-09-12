"""The tradable universe.

A universe is a fixed, explicit list of instruments. Fixed matters: if the list of
things a strategy may hold is derived at run time from "whatever is liquid today", the
backtest silently only ever considers assets that survived to today. That is
survivorship bias, and it flatters results badly.

The core sleeve is thirteen US-listed ETFs chosen for long history, tight spreads, and
coverage of genuinely different economic exposures. Momentum needs assets that can
diverge from one another; a universe of ten large-cap tech ETFs would rank things that
all move together and select noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sillage.core.types import AssetClass, Instrument


def _etf(symbol: str) -> Instrument:
    return Instrument(symbol, AssetClass.ETF, currency="USD", exchange="ARCA")


def _crypto(symbol: str, available_from: date) -> Instrument:
    return Instrument(
        symbol,
        AssetClass.CRYPTO,
        currency="USD",
        lot_size=Decimal("0.00000001"),
        trades_continuously=True,
        available_from=available_from,
    )


# The risk-free parking spot. When the trend filter rejects an asset, its weight comes
# here rather than sitting as idle cash, so the portfolio still earns the T-bill rate.
CASH_PROXY = _etf("BIL")

CORE_ETFS: tuple[Instrument, ...] = (
    _etf("SPY"),  # US large cap
    _etf("QQQ"),  # US tech / growth
    _etf("IWM"),  # US small cap
    _etf("EFA"),  # developed markets ex-US
    _etf("EEM"),  # emerging markets
    _etf("TLT"),  # long-dated US treasuries
    _etf("IEF"),  # intermediate treasuries
    _etf("LQD"),  # investment grade credit
    _etf("HYG"),  # high yield credit
    _etf("GLD"),  # gold
    _etf("DBC"),  # broad commodities
    _etf("VNQ"),  # US real estate
)

#: When crypto became something a diversified fund would plausibly allocate to, as
#: opposed to when a price series for it started existing. Both dates are judgements and
#: both are argued rather than assumed:
#:
#: - **2021-01-01 for bitcoin.** By the end of 2020 it had a regulated futures market,
#:   custody from mainstream providers, and corporate treasuries holding it. Before that
#:   a systematic multi-asset fund holding it is a story about hindsight.
#: - **2022-01-01 for ether.** Later, because its investment case rested on a network
#:   transition that had not happened yet and its history was shorter still.
#:
#: `sillage crypto-admission` sweeps these, because the right answer is unknowable and
#: the size of the disagreement is not.
CRYPTO_AVAILABLE_FROM = {"BTC-USD": date(2021, 1, 1), "ETH-USD": date(2022, 1, 1)}

CRYPTO: tuple[Instrument, ...] = tuple(
    _crypto(symbol, available) for symbol, available in CRYPTO_AVAILABLE_FROM.items()
)


@dataclass(frozen=True, slots=True)
class Universe:
    """A named set of instruments the strategy is permitted to hold."""

    name: str
    instruments: tuple[Instrument, ...]
    cash_proxy: Instrument = CASH_PROXY

    def __post_init__(self) -> None:
        symbols = [i.symbol for i in self.instruments]
        duplicates = {s for s in symbols if symbols.count(s) > 1}
        if duplicates:
            raise ValueError(f"universe {self.name!r} has duplicate symbols: {sorted(duplicates)}")
        if not self.instruments:
            raise ValueError(f"universe {self.name!r} is empty")

    @property
    def symbols(self) -> list[str]:
        return [i.symbol for i in self.instruments]

    @property
    def all_instruments(self) -> tuple[Instrument, ...]:
        """Everything needing price data, cash proxy included."""
        if self.cash_proxy in self.instruments:
            return self.instruments
        return (*self.instruments, self.cash_proxy)

    @property
    def has_continuous_assets(self) -> bool:
        return any(i.trades_continuously for i in self.instruments)

    def investable_on(self, day: date) -> tuple[Instrument, ...]:
        """The instruments a strategy was permitted to consider on `day`.

        The universe stays fixed -- every instrument is declared up front, so nothing is
        added retroactively because it worked. What varies is *when* each became
        eligible, which is the honest way to express "bitcoin was not a thing a
        diversified fund put money into in 2015" without pretending the price series
        did not exist.
        """
        return tuple(i for i in self.instruments if i.investable_on(day))

    def get(self, symbol: str) -> Instrument:
        for instrument in self.all_instruments:
            if instrument.symbol == symbol:
                return instrument
        raise KeyError(f"{symbol!r} is not in universe {self.name!r}")


CORE = Universe("core", CORE_ETFS)
CORE_PLUS_CRYPTO = Universe("core+crypto", CORE_ETFS + CRYPTO)

UNIVERSES: dict[str, Universe] = {u.name: u for u in (CORE, CORE_PLUS_CRYPTO)}


def get_universe(name: str) -> Universe:
    try:
        return UNIVERSES[name]
    except KeyError:
        raise KeyError(f"unknown universe {name!r}; known: {sorted(UNIVERSES)}") from None
