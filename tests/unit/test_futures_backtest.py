"""Accounting and timing tests for the contract-level futures research simulator."""

from __future__ import annotations

import pandas as pd
import pytest

from sillage.backtest.futures import (
    FuturesBacktestConfig,
    FuturesCost,
    run_futures_trend,
)
from sillage.strategy.futures_trend import FuturesTrendConfig

SIGNAL = FuturesTrendConfig(
    lookbacks=(2, 3, 4),
    skip_sessions=0,
    volatility_span=4,
    volatility_floor=0.01,
    target_volatility=0.20,
    max_gross=1.0,
)


def chain(*, second_contract: bool = False) -> pd.DataFrame:
    sessions = pd.date_range("2026-01-05", periods=15, freq="B", tz="UTC")
    rows: list[dict[str, object]] = []
    contracts = [("AH6", "2026-01-30", 100, [100] * 15)]
    if second_contract:
        contracts = [
            ("AF6", "2026-01-30", 100, [100] * 7 + [10] * 8),
            ("AG6", "2026-02-27", 110, [10] * 7 + [200] * 8),
        ]
    for contract, expiry, base, volumes in contracts:
        for index, session in enumerate(sessions):
            close = base + index
            rows.append(
                {
                    "market": "A",
                    "contract": contract,
                    "session": session,
                    "open": close - 0.5,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": volumes[index],
                    "last_trade": expiry,
                    "multiplier": 10,
                }
            )
    return pd.DataFrame(rows)


def config(**changes: object) -> FuturesBacktestConfig:
    values: dict[str, object] = {
        "signal": SIGNAL,
        "costs": {"A": FuturesCost(2.0, 0.25)},
        "initial_margin": {"A": 100.0},
    }
    values.update(changes)
    return FuturesBacktestConfig(**values)  # type: ignore[arg-type]


def test_close_signal_is_not_traded_until_the_next_session_open() -> None:
    result = run_futures_trend(chain(), config())
    first = result.trades[0]
    assert first.session == pd.Timestamp("2026-01-12").date()
    assert first.price == pytest.approx(104.5)


def test_contract_pnl_uses_multiplier_and_costs_are_separate() -> None:
    result = run_futures_trend(chain(), config())
    first = result.trades[0]
    assert first.commission == abs(first.quantity) * 2.0
    assert first.spread_cost == abs(first.quantity) * 0.25 * 10
    assert result.nav.iloc[-1] > 25_000  # persistent uptrend earns after explicit costs


def test_roll_is_two_real_trades_and_does_not_book_the_price_gap_as_pnl() -> None:
    result = run_futures_trend(chain(second_contract=True), config())
    reasons = [trade.reason for trade in result.trades]
    assert "roll-close" in reasons
    assert "roll-open" in reasons
    # The new contract is ten points dearer; a synthetic jump would add roughly 10%.
    # The actual roll-day change is only both half-day moves minus both-leg costs.
    assert result.nav.loc["2026-01-15"] - result.nav.loc["2026-01-14"] == pytest.approx(23.0)


def test_whole_contract_targets_round_toward_zero() -> None:
    result = run_futures_trend(chain(), config(initial_nav=1_000.0))
    assert all(isinstance(quantity, int) for quantity in result.positions["quantity"])


def test_margin_gate_leaves_the_existing_book_unchanged() -> None:
    result = run_futures_trend(
        chain(), config(initial_margin={"A": 50_000.0}, max_margin_fraction=0.35)
    )
    assert result.skipped_margin_rebalances > 0
    assert result.trades == ()
    assert (result.positions["quantity"] == 0).all()


def test_yield_economic_sign_flips_the_executable_contract_direction() -> None:
    ordinary = run_futures_trend(chain(), config())
    inverted = run_futures_trend(chain(), config(economic_sign={"A": -1}))
    # Rising quoted yields are a downtrend in conventional bond-return terms.  The
    # economic signal goes short, then maps that short back to a long yield contract,
    # which is the contract direction that actually profits from rising yields.
    assert ordinary.trades[0].quantity == inverted.trades[0].quantity


def test_cash_rate_accrues_over_the_weekend() -> None:
    rates = pd.Series([0.365], index=pd.to_datetime(["2026-01-01"], utc=True))
    result = run_futures_trend(chain(), config(initial_margin={"A": 50_000.0}), cash_rates=rates)
    assert result.nav.loc["2026-01-12"] - result.nav.loc["2026-01-09"] == pytest.approx(
        result.nav.loc["2026-01-09"] * 0.365 * 3 / 365
    )


def test_missing_market_assumptions_are_rejected() -> None:
    with pytest.raises(ValueError, match="missing cost"):
        run_futures_trend(chain(), FuturesBacktestConfig(signal=SIGNAL))
