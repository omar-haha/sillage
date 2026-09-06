"""Tests for the rebalance-timing-luck study.

The substantive assertion is the last one: a fixed-weight strategy must show almost no
timing luck. That is not a property of the code, it is a property of the world -- a
strategy that wants the same weights whichever day it looks at cannot care much which
day it looks. If this harness reported a large spread there, it would be measuring
itself rather than the strategy.
"""

from __future__ import annotations

from datetime import date

import pytest

from sillage.backtest.runner import BacktestConfig
from sillage.backtest.timing import DEFAULT_OFFSETS, TimingLuck, study
from sillage.core.calendar import TradingCalendar
from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import Universe
from sillage.strategy.base import Monthly, Once
from sillage.strategy.benchmarks import StaticWeights, buy_and_hold

SPY = Instrument("SPY", AssetClass.ETF, exchange="ARCA")
IEF = Instrument("IEF", AssetClass.ETF, exchange="ARCA")
UNIVERSE = Universe("spy-ief", (SPY, IEF), cash_proxy=IEF)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> object:
    """The committed fixtures: real prices, no network, same answer every run."""
    from pathlib import Path

    import pandas as pd

    from sillage.data.store import BarStore

    golden = Path(__file__).resolve().parents[1] / "golden"
    store = BarStore(tmp_path_factory.mktemp("timing"))
    for symbol in ("spy", "ief"):
        frame = pd.read_csv(golden / f"{symbol}_daily.csv.gz", index_col=0, parse_dates=True)
        store.write(symbol.upper(), frame)
    return store


def config(strategy: object, store: object) -> BacktestConfig:
    return BacktestConfig(
        strategy=strategy,  # type: ignore[arg-type]
        universe=UNIVERSE,
        start=date(2010, 1, 4),
        end=date(2020, 12, 31),
        data_root=store.root,  # type: ignore[attr-defined]
    )


# ------------------------------------------------------------------ the schedule


def test_offset_schedules_still_fire_twelve_times_a_year(calendar: TradingCalendar) -> None:
    sessions = [s.day for s in calendar.sessions(date(2024, 1, 1), date(2024, 12, 31))]
    for offset in DEFAULT_OFFSETS:
        schedule = Monthly(offset)
        fired = [d for d in sessions if schedule.is_rebalance_session(d, calendar)]
        assert len(fired) == 12, f"offset {offset} fired {len(fired)} times"


def test_offsets_land_on_different_days(calendar: TradingCalendar) -> None:
    sessions = [s.day for s in calendar.sessions(date(2024, 1, 1), date(2024, 12, 31))]
    landings = {
        offset: {d for d in sessions if Monthly(offset).is_rebalance_session(d, calendar)}
        for offset in DEFAULT_OFFSETS
    }
    for a in DEFAULT_OFFSETS:
        for b in DEFAULT_OFFSETS:
            if a != b:
                assert not landings[a] & landings[b]


def test_offset_zero_is_plain_month_end(calendar: TradingCalendar) -> None:
    assert Monthly(0).is_rebalance_session(date(2024, 3, 28), calendar)
    assert Monthly().name == "monthly"
    assert Monthly(5).name == "monthly-5"


def test_a_negative_offset_is_meaningless() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        Monthly(-1)


# ------------------------------------------------------------------ the study


def test_it_runs_every_offset(store: object) -> None:
    result = study(config(StaticWeights({"SPY": "1.0"}), store), offsets=(0, 10))
    assert result.offsets == (0, 10)
    assert len(result.metrics) == 2
    assert isinstance(result, TimingLuck)


def test_a_rebalanced_fixed_weight_book_has_almost_no_timing_luck(store: object) -> None:
    """The check that the harness measures the strategy rather than inventing variance.

    A 60/40 wants the same 60/40 whichever day it looks, so the date it rebalances on
    can only change things at the margin. Rebalance timing luck is a property of
    *selection*, and this strategy does not select.
    """
    result = study(config(StaticWeights({"SPY": "0.6", "IEF": "0.4"}), store))
    assert result.cagr_range < 0.005  # half a percentage point a year, across every date
    assert result.luckiest in DEFAULT_OFFSETS
    assert result.unluckiest in DEFAULT_OFFSETS


def test_a_single_asset_book_still_shows_entry_date_luck(store: object) -> None:
    """A different effect, and worth separating from the one above.

    A 100% SPY position never rebalances -- there is nothing to rebalance against -- so
    the only thing the offset changes is *which day the money went in*. Over eleven
    years, going in three weeks apart moves the annualised return by more than the
    rebalance date does for a two-asset book. Neither effect is skill, and a backtest
    reports both as though they were.
    """
    entry = study(config(StaticWeights({"SPY": "1.0"}), store))
    rebalanced = study(config(StaticWeights({"SPY": "0.6", "IEF": "0.4"}), store))
    assert entry.cagr_range > rebalanced.cagr_range


def test_the_summary_reads_as_a_sentence(store: object) -> None:
    result = study(config(StaticWeights({"SPY": "1.0"}), store), offsets=(0, 5))
    assert "rebalance dates" in result.summary()


def test_it_refuses_a_strategy_that_does_not_rebalance_monthly(store: object) -> None:
    """Silently rewriting a buy-and-hold's schedule would answer a question nobody asked."""
    with pytest.raises(TypeError, match="monthly rebalancing"):
        study(config(buy_and_hold("SPY"), store))


def test_it_needs_at_least_one_offset(store: object) -> None:
    with pytest.raises(ValueError, match="at least one"):
        study(config(StaticWeights({"SPY": "1.0"}), store), offsets=())


def test_each_run_gets_its_own_strategy_object(store: object) -> None:
    """Schedules carry state; sharing one would leak the first run into the second."""
    strategy = StaticWeights({"SPY": "1.0"}, schedule=Monthly())
    study(config(strategy, store), offsets=(0, 5))
    assert isinstance(strategy.schedule, Monthly)
    assert strategy.schedule.offset == 0


def test_once_schedules_are_not_monthly() -> None:
    assert not isinstance(Once(), Monthly)
