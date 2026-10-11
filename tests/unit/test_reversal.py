import numpy as np
import pandas as pd
import pytest

import sillage.backtest.reversal as replay
from sillage.strategy.reversal import reversal_weights
from sillage.strategy.sector_neutral import SECTOR_ETFS


def frames():
    days = pd.bdate_range("2020-01-01", periods=300, tz="UTC")
    bars = {
        s: pd.DataFrame({"open": 100.0, "close": 100.0, "volume": 1000000.0}, index=days)
        for s in (*SECTOR_ETFS, "SPY")
    }
    return bars, pd.Series(30.0, index=days)


def fixed_signal(monkeypatch):
    monkeypatch.setattr(
        replay,
        "reversal_weights",
        lambda *a: {"XLB": 0.25, "XLE": 0.25, "XLF": -0.25, "XLI": -0.25},
    )


def test_holds_two_complete_intervals_and_never_reenters_on_exit_day(monkeypatch):
    fixed_signal(monkeypatch)
    bars, vix = frames()
    result = replay.run_reversal(bars, vix)
    days = bars["SPY"].index
    first_entry = pd.Timestamp(result.fills[0]["session"], tz="UTC")
    first_exit = pd.Timestamp(
        next(f["session"] for f in result.fills if f["reason"] == "exit"), tz="UTC"
    )
    assert first_entry == days[253]
    assert first_exit == days[255]
    assert not any(
        f["reason"] == "entry" and f["session"] == first_exit.date().isoformat()
        for f in result.fills
    )


def test_accounting_flat_prices_and_real_borrow_days(monkeypatch):
    fixed_signal(monkeypatch)
    bars, vix = frames()
    r = replay.run_reversal(bars, vix)
    assert r.borrow > 0 and r.commissions > 0
    assert r.nav.iloc[-1] == pytest.approx(
        5000 - r.borrow - r.commissions - r.slippage - r.financing
    )


def test_delayed_entry_uses_original_decision(monkeypatch):
    fixed_signal(monkeypatch)
    bars, vix = frames()
    result = replay.run_reversal(bars, vix, delay=1)
    assert result.fills[0]["session"] == bars["SPY"].index[254].date().isoformat()


def test_missing_vix_is_not_filled_with_future_data():
    bars, vix = frames()
    with pytest.raises(ValueError, match="VIX"):
        replay.run_reversal(bars, vix.iloc[1:])


def test_invalid_parameters_fail():
    bars, vix = frames()
    with pytest.raises(ValueError):
        replay.run_reversal(bars, vix, initial_nav=0)


def test_future_prices_do_not_change_prior_nav(monkeypatch):
    fixed_signal(monkeypatch)
    bars, vix = frames()
    base = replay.run_reversal(bars, vix)
    bars["XLB"].iloc[-5:, :2] = 120
    altered = replay.run_reversal(bars, vix)
    pd.testing.assert_series_equal(base.nav.iloc[:-5], altered.nav.iloc[:-5])


def test_stress_gate_and_beta_neutral_sizing():
    rng = np.random.default_rng(42)
    market_returns = rng.normal(0.0003, 0.01, 260)
    market = 100 * np.exp(np.r_[0, np.cumsum(market_returns)])
    sectors = {
        s: 100 * np.exp(np.r_[0, np.cumsum(market_returns + rng.normal(0, 0.005, 260))])
        for s in SECTOR_ETFS
    }
    assert reversal_weights(sectors, market, 24.9) == {}
    weights = reversal_weights(sectors, market, 25)
    assert len(weights) == 4
    assert sum(abs(x) for x in weights.values()) <= 1.000001
    assert max(abs(x) for x in weights.values()) <= 0.350001
    m = np.diff(np.log(market[-253:]))
    beta = {
        s: np.cov(np.diff(np.log(sectors[s][-253:])), m, ddof=1)[0, 1] / np.var(m, ddof=1)
        for s in weights
    }
    assert sum(weights[s] * beta[s] for s in weights) == pytest.approx(0, abs=1e-8)
