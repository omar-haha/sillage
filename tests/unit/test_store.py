"""Tests for the bar store.

The `as_of` tests are the important ones. `as_of` is the system's structural defence
against lookahead bias, and a strategy cannot cheat as long as the store refuses to
hand over the future -- so these tests are really testing that the backtest is capable
of telling the truth.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from sillage.core.types import AssetClass, Instrument
from sillage.data.store import BarStore

SPY = Instrument("SPY", AssetClass.ETF)


def frame(days: list[str], close: float = 100.0) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in days], name="ts")
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1_000_000.0,
        },
        index=index,
    )


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(tmp_path)


# ------------------------------------------------------------------ round trip


def test_write_then_read(store: BarStore) -> None:
    store.write("SPY", frame(["2024-01-02", "2024-01-03"]))
    assert len(store.read("SPY")) == 2
    assert store.has("SPY")


def test_reading_an_unknown_symbol_explains_the_fix(store: BarStore) -> None:
    with pytest.raises(FileNotFoundError, match="sillage data sync"):
        store.read("NOPE")


def test_writes_merge_rather_than_replace(store: BarStore) -> None:
    store.write("SPY", frame(["2024-01-02", "2024-01-03"]))
    store.write("SPY", frame(["2024-01-04"]))
    assert len(store.read("SPY")) == 3


def test_rewriting_a_bar_takes_the_newer_value(store: BarStore) -> None:
    # Adjusted prices get restated when a dividend is paid, so this is routine.
    store.write("SPY", frame(["2024-01-02"], close=100.0))
    store.write("SPY", frame(["2024-01-02"], close=101.0))
    result = store.read("SPY")
    assert len(result) == 1
    assert result["close"].iloc[0] == 101.0


def test_stored_history_stays_sorted(store: BarStore) -> None:
    store.write("SPY", frame(["2024-01-05"]))
    store.write("SPY", frame(["2024-01-02"]))
    assert store.read("SPY").index.is_monotonic_increasing


def test_symbols_with_slashes_do_not_create_directories(tmp_path: Path) -> None:
    store = BarStore(tmp_path)
    store.write("BTC/USD", frame(["2024-01-02"]))
    assert store.path_for("BTC/USD").is_file()
    assert store.symbols() == ["BTC/USD"]


# ---------------------------------------------------------------- as_of gating


@pytest.fixture
def week(store: BarStore) -> BarStore:
    store.write("SPY", frame(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]))
    return store


def test_as_of_hides_later_bars(week: BarStore) -> None:
    result = week.read("SPY", as_of=date(2024, 1, 3))
    assert [ts.date() for ts in result.index] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_as_of_includes_the_bar_that_closed_that_day(week: BarStore) -> None:
    # A bare date means "the end of that day", so the day's own close is knowable.
    assert len(week.read("SPY", as_of=date(2024, 1, 2))) == 1


def test_as_of_accepts_an_intraday_instant(week: BarStore) -> None:
    # Daily bars here are stamped midnight UTC, so an as_of before that excludes them.
    before = datetime(2024, 1, 2, 0, 0, tzinfo=UTC)
    assert len(week.read("SPY", as_of=before)) == 1
    assert len(week.read("SPY", as_of=datetime(2024, 1, 1, 23, 59, tzinfo=UTC))) == 0


def test_as_of_before_all_history_returns_nothing(week: BarStore) -> None:
    assert week.read("SPY", as_of=date(2020, 1, 1)).empty


def test_as_of_applies_to_domain_objects_too(week: BarStore) -> None:
    # The escape hatch would be reading Bars instead of the frame; there isn't one.
    bars = week.bars(SPY, as_of=date(2024, 1, 3))
    assert len(bars) == 2
    assert all(bar.ts.date() <= date(2024, 1, 3) for bar in bars)


def test_as_of_applies_to_multi_symbol_reads(store: BarStore) -> None:
    store.write("SPY", frame(["2024-01-02", "2024-01-03"]))
    store.write("QQQ", frame(["2024-01-02", "2024-01-03"]))
    prices = store.close_prices(["SPY", "QQQ"], as_of=date(2024, 1, 2))
    assert len(prices) == 1


def test_start_and_end_bound_the_window(week: BarStore) -> None:
    result = week.read("SPY", start=date(2024, 1, 3), end=date(2024, 1, 4))
    assert len(result) == 2


# ------------------------------------------------------------------- domain objects


def test_bars_come_back_as_validated_decimals(week: BarStore) -> None:
    from decimal import Decimal

    bar = week.bars(SPY)[0]
    assert isinstance(bar.close, Decimal)
    assert bar.instrument == SPY
    assert bar.ts.tzinfo is not None


def test_close_prices_aligns_symbols_without_inventing_data(store: BarStore) -> None:
    # QQQ is missing a day SPY has. Forward-filling here would fabricate a price that
    # never printed, so the gap must survive as NaN for the caller to deal with.
    store.write("SPY", frame(["2024-01-02", "2024-01-03"]))
    store.write("QQQ", frame(["2024-01-02"]))
    prices = store.close_prices(["SPY", "QQQ"])
    assert len(prices) == 2
    assert pd.isna(prices["QQQ"].iloc[1])


# ---------------------------------------------------------------------- validation


def test_naive_timestamps_are_rejected(store: BarStore) -> None:
    bad = frame(["2024-01-02"])
    bad.index = pd.DatetimeIndex(bad.index).tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        store.write("SPY", bad)


def test_duplicate_timestamps_are_rejected(store: BarStore) -> None:
    bad = pd.concat([frame(["2024-01-02"]), frame(["2024-01-02"])])
    with pytest.raises(ValueError, match="duplicate"):
        store.write("SPY", bad)


def test_missing_columns_are_rejected(store: BarStore) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        store.write("SPY", frame(["2024-01-02"]).drop(columns=["volume"]))


def test_coverage_reports_the_stored_span(week: BarStore) -> None:
    coverage = week.coverage("SPY")
    assert coverage is not None
    assert coverage.start == date(2024, 1, 2)
    assert coverage.end == date(2024, 1, 5)
    assert coverage.rows == 4
