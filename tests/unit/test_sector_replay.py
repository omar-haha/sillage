"""Causality and accounting checks for the frozen sector research replay."""

from types import SimpleNamespace

import pandas as pd
import pytest

import sillage.backtest.sector_neutral as replay
from sillage.core.money import dec
from sillage.execution.costs import CostModel
from sillage.strategy.sector_neutral import SECTOR_ETFS, SectorNeutralConfig


def histories():
    days = pd.bdate_range("2020-01-01", periods=330, tz="UTC")
    return {
        s: pd.DataFrame({"open": 100.0, "close": 100.0, "volume": 1000000.0}, index=days)
        for s in (*SECTOR_ETFS, "SPY")
    }


def signal(monkeypatch):
    seen = []

    def decide(prices, spy, config):
        seen.append(len(spy))
        return SimpleNamespace(weights={"XLB": dec("0.5"), "XLE": dec("-0.5")})

    monkeypatch.setattr(replay, "decide_sectors", decide)
    return seen


def test_month_end_signal_executes_only_next_session(monkeypatch):
    seen = signal(monkeypatch)
    bars = histories()
    result = replay.run_sectors(bars, costs=CostModel().scaled(0))
    first = pd.Timestamp(result.fills[0]["session"], tz="UTC")
    assert first == bars["SPY"].index[seen[0]]
    prior = bars["SPY"].index[seen[0] - 1]
    assert prior.month != first.month
    assert all(isinstance(fill["quantity"], int) for fill in result.fills)


def test_flat_prices_only_lose_declared_costs_and_borrow(monkeypatch):
    signal(monkeypatch)
    result = replay.run_sectors(histories())
    assert result.cash_interest == 0
    assert result.borrow > 0
    assert result.commissions > 0
    assert result.nav.iloc[-1] == pytest.approx(
        25000 - result.borrow - result.commissions - result.slippage, abs=1e-7
    )
    assert sum(result.sector_pnl.values()) == pytest.approx(result.nav.iloc[-1] - 25000)


def test_delay_really_skips_one_month_decision(monkeypatch):
    signal(monkeypatch)
    base = replay.run_sectors(histories())
    delayed = replay.run_sectors(histories(), delay_months=1)
    assert delayed.fills[0]["session"] > base.fills[0]["session"]


def test_missing_session_is_not_silently_dropped():
    bars = histories()
    bars["XLB"] = bars["XLB"].iloc[1:]
    with pytest.raises(ValueError, match="align exactly"):
        replay.run_sectors(bars)


def test_future_changes_do_not_change_past_nav(monkeypatch):
    signal(monkeypatch)
    bars = histories()
    base = replay.run_sectors(bars)
    bars["XLB"].iloc[-10:, 0:2] = 150
    changed = replay.run_sectors(bars)
    pd.testing.assert_series_equal(base.nav.iloc[:-10], changed.nav.iloc[:-10])


def test_short_proceeds_do_not_earn_interest(monkeypatch):
    signal(monkeypatch)
    bars = histories()
    rates = pd.Series(0.1, index=bars["SPY"].index)
    result = replay.run_sectors(bars, cash_rates=rates)
    assert result.cash_interest > 0
    assert result.cash_interest < 25000 * 0.1 * 2


def test_execution_costs_never_use_future_same_day_volume(monkeypatch):
    signal(monkeypatch)
    bars = histories()
    base = replay.run_sectors(bars)
    first = pd.Timestamp(base.fills[0]["session"], tz="UTC")
    for frame in bars.values():
        frame.loc[first, "volume"] = 1
    changed = replay.run_sectors(bars)
    assert changed.fills[:2] == base.fills[:2]


def test_overlapping_long_short_selections_are_invalid():
    with pytest.raises(ValueError, match="disjoint"):
        SectorNeutralConfig(selections_per_side=5)
