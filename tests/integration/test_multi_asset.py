"""Tests for holding things that trade on different calendars.

`ContinuousCalendar` was written in Phase 0 and never once ran through the engine until
Phase 7. The interesting question is not whether a crypto backtest produces a number --
it does -- but whether the number is honest, because a 24/7 asset in a portfolio whose
heartbeat is the NYSE creates exactly the conditions for a quiet lookahead.

The arithmetic that has to hold: a crypto bar for day D closes at 23:59:59 UTC, and the
NYSE closes at 20:00 or 21:00 UTC. So at the moment a decision is made, the strategy can
see crypto only through D-1. That is a one-day lag, it is conservative, and it is the
opposite of the mistake.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from sillage.backtest.metrics import analyse
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.core.calendar import ContinuousCalendar, TradingCalendar
from sillage.core.money import ZERO, dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.store import BarStore
from sillage.data.universe import CORE_ETFS, CRYPTO, Universe, get_universe
from sillage.engine.feed import HistoricalFeed, MarketFeed, load_bars
from sillage.strategy.momentum import build

DATA = "data"
START, END = date(2018, 1, 2), date(2026, 9, 11)


def has_crypto_data() -> bool:
    store = BarStore(DATA)
    return all(store.has(s) for s in ("BTC-USD", "ETH-USD", "SPY"))


needs_data = pytest.mark.skipif(not has_crypto_data(), reason="needs a synced core+crypto store")


# ------------------------------------------------------------------ eligibility


def test_an_instrument_knows_when_it_became_investable() -> None:
    """Not when its prices start -- when a reasonable person would have considered it."""
    btc = Instrument("BTC-USD", AssetClass.CRYPTO, available_from=date(2021, 1, 1))
    assert not btc.investable_on(date(2020, 12, 31))
    assert btc.investable_on(date(2021, 1, 1))


def test_an_ordinary_etf_was_always_investable() -> None:
    assert Instrument("SPY", AssetClass.ETF).investable_on(date(1995, 1, 1))


def test_the_universe_narrows_to_what_was_eligible() -> None:
    universe = get_universe("core+crypto")
    early = {i.symbol for i in universe.investable_on(date(2019, 1, 1))}
    late = {i.symbol for i in universe.investable_on(date(2024, 1, 1))}
    assert "BTC-USD" not in early
    assert {"BTC-USD", "ETH-USD"} <= late


def test_the_universe_itself_never_changes() -> None:
    """Membership is declared up front; only eligibility moves. Adding an instrument
    retroactively because it worked is the bias this exists to avoid."""
    universe = get_universe("core+crypto")
    assert len(universe.investable_on(date(2030, 1, 1))) == len(universe.instruments)


def test_crypto_is_marked_as_never_closing() -> None:
    assert all(i.trades_continuously for i in CRYPTO)
    assert not any(i.trades_continuously for i in CORE_ETFS)


# ------------------------------------------------------------------ the calendars


def test_a_continuous_session_closes_after_the_exchange_does() -> None:
    """The fact the whole no-lookahead argument rests on."""
    day = date(2024, 3, 1)
    nyse = TradingCalendar().sessions(day, day)[0]
    always = ContinuousCalendar().sessions(day, day)[0]
    assert always.close > nyse.close


@needs_data
def test_a_crypto_bar_is_not_visible_at_that_day_s_exchange_close() -> None:
    """If it were, the strategy would be deciding on a price that had not happened."""
    universe = get_universe("core+crypto")
    calendar = TradingCalendar()
    bars = load_bars(BarStore(DATA), universe.all_instruments, calendar=calendar, end=END)
    feed = HistoricalFeed(bars)

    for session in calendar.sessions(date(2024, 3, 1), date(2024, 3, 8)):
        btc = feed.latest("BTC-USD", as_of=session.close)
        spy = feed.latest("SPY", as_of=session.close)
        assert btc is not None and spy is not None
        assert spy.ts.date() == session.day, "equities are current at their own close"
        assert btc.ts.date() < session.day, "crypto lags by a session, which is the safe way"


@needs_data
def test_crypto_can_still_be_traded_on_an_exchange_session() -> None:
    """A one-day lag in what it *sees* must not become an inability to act."""
    universe = get_universe("core+crypto")
    calendar = TradingCalendar()
    bars = load_bars(BarStore(DATA), universe.all_instruments, calendar=calendar, end=END)
    market = MarketFeed(bars)
    session = calendar.sessions(date(2024, 3, 4), date(2024, 3, 4))[0]
    assert market.open_price("BTC-USD", session.day) is not None


# ------------------------------------------------------------------ end to end


@needs_data
def test_a_mixed_universe_backtest_runs_and_holds_crypto() -> None:
    universe = get_universe("core+crypto")
    strategy = build(universe)
    result = run_backtest(
        BacktestConfig(strategy=strategy, universe=universe, start=START, end=END)
    )
    assert result.rejections == []
    assert analyse(result).metrics.sharpe > 0
    assert any(
        symbol in {"BTC-USD", "ETH-USD"}
        for decision in strategy.decisions
        for symbol in decision.weights
    )


@needs_data
def test_crypto_never_dominates_the_book() -> None:
    """The roadmap expected it to. Inverse-volatility sizing gives a four-times-more-
    volatile asset a four-times-smaller weight, so it cannot."""
    universe = get_universe("core+crypto")
    strategy = build(universe)
    run_backtest(BacktestConfig(strategy=strategy, universe=universe, start=START, end=END))
    largest = max(
        (
            w
            for d in strategy.decisions
            for s, w in d.weights.items()
            if s in {"BTC-USD", "ETH-USD"}
        ),
        default=ZERO,
    )
    assert largest < dec("0.20")


@needs_data
def test_nothing_exceeds_the_position_cap() -> None:
    """Without it the strategy took 58.7% in high-yield credit -- inverse-vol concentrates
    into whatever looks quietest, and credit is quiet until it is not."""
    universe = get_universe("core+crypto")
    strategy = build(universe)
    run_backtest(BacktestConfig(strategy=strategy, universe=universe, start=START, end=END))
    largest = max((w for d in strategy.decisions for w in d.weights.values()), default=ZERO)
    assert largest <= strategy.config.max_weight


@needs_data
def test_admitting_crypto_later_changes_the_answer() -> None:
    """The finding this phase exists for: the crypto result is mostly hindsight.

    Admitted in January 2018 -- when ether had two months of price history and nobody was
    allocating to it -- the Sharpe crosses 1.0. Admitted on a date a real committee might
    have chosen, it adds almost nothing. No amount of bootstrapping detects this, because
    the bias is in the choice of what to test.
    """

    def sharpe(admitted: date) -> float:
        universe = Universe(
            "core+crypto",
            CORE_ETFS + tuple(replace(i, available_from=admitted) for i in CRYPTO),
        )
        result = run_backtest(
            BacktestConfig(strategy=build(universe), universe=universe, start=START, end=END)
        )
        return analyse(result).metrics.sharpe

    assert sharpe(date(2018, 1, 2)) > sharpe(date(2021, 1, 1)) + 0.1
