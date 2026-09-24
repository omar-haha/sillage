"""Tests for Candidate B's stateful pair-trading research backtest."""

from __future__ import annotations

import pandas as pd
import pytest

from sillage.backtest.relative_value import (
    RelativeValueBacktestConfig,
    _exit_reason,
    run_relative_value,
)
from sillage.execution.costs import FREE


def pair_frames(
    left: str = "LEFT",
    right: str = "RIGHT",
    *,
    sessions: int = 270,
    shock_session: int = 252,
) -> dict[str, pd.DataFrame]:
    index = pd.date_range("2025-01-02", periods=sessions, freq="B", tz="UTC")
    right_prices = [100.0 * (1.0005**day) for day in range(sessions)]
    left_prices = [
        1.2 * price * (1.008 if day % 2 else 0.992) for day, price in enumerate(right_prices)
    ]
    left_prices[shock_session] *= 1.10
    left_prices[shock_session + 1] = 1.2 * right_prices[shock_session + 1]
    return {
        left: _frame(index, left_prices),
        right: _frame(index, right_prices),
    }


def _frame(index: pd.DatetimeIndex, closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": closes,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
    )


def config(**changes: object) -> RelativeValueBacktestConfig:
    values: dict[str, object] = {
        "pairs": (("LEFT", "RIGHT"),),
        "costs": FREE,
        "annual_borrow_rate": 0.0,
    }
    values.update(changes)
    return RelativeValueBacktestConfig(**values)  # type: ignore[arg-type]


def test_close_signal_enters_both_legs_at_the_next_open() -> None:
    result = run_relative_value(pair_frames(), config())
    entries = [trade for trade in result.trades if trade.reason == "entry"]
    assert len(entries) == 2
    assert {trade.symbol for trade in entries} == {"LEFT", "RIGHT"}
    assert entries[0].session == pd.Timestamp("2025-12-23").date()
    assert entries[0].quantity < 0
    assert entries[1].quantity > 0


def test_convergence_or_invalidation_closes_both_legs() -> None:
    result = run_relative_value(pair_frames(), config())
    entries = [trade for trade in result.trades if trade.reason == "entry"]
    exits = [trade for trade in result.trades if trade.reason != "entry"]
    assert len(entries) == len(exits) == 2
    assert {trade.quantity for trade in exits} == {-trade.quantity for trade in entries}


def test_pair_gross_is_capped_and_whole_share_sized() -> None:
    result = run_relative_value(pair_frames(), config())
    assert result.gross_exposure.max() <= 0.201
    assert all(isinstance(trade.quantity, int) for trade in result.trades)


def test_short_leg_accrues_explicit_borrow_cost() -> None:
    result = run_relative_value(pair_frames(), config(annual_borrow_rate=0.10))
    assert result.borrow_cost > 0
    assert (
        result.pair_pnl["LEFT/RIGHT"]
        < run_relative_value(pair_frames(), config()).pair_pnl["LEFT/RIGHT"]
    )


def test_execution_costs_are_reported_separately() -> None:
    result = run_relative_value(pair_frames(), config(costs=RelativeValueBacktestConfig().costs))
    assert result.commission > 0
    assert result.slippage > 0
    assert result.nav.iloc[-1] < run_relative_value(pair_frames(), config()).nav.iloc[-1]


def test_total_gross_admits_only_three_simultaneous_pairs() -> None:
    pairs = tuple((f"L{i}", f"R{i}") for i in range(4))
    bars: dict[str, pd.DataFrame] = {}
    for left, right in pairs:
        bars.update(pair_frames(left, right))
    result = run_relative_value(bars, config(pairs=pairs))
    entries = [trade for trade in result.trades if trade.reason == "entry"]
    assert len({trade.pair for trade in entries}) == 3
    assert result.rejected_entries >= 1
    assert result.gross_exposure.max() <= 0.602


@pytest.mark.parametrize(
    ("eligible", "zscore", "held", "expected"),
    [
        (False, 2.5, 1, "invalidated"),
        (True, 0.4, 1, "converged"),
        (True, 4.1, 1, "stop"),
        (True, 2.5, 42, "timeout"),
        (True, 2.5, 41, None),
    ],
)
def test_exit_priority(eligible: bool, zscore: float, held: int, expected: str | None) -> None:
    assert _exit_reason(eligible, zscore, held, config()) == expected


def test_weekend_cash_and_borrow_use_calendar_days() -> None:
    rates = pd.Series([0.365], index=pd.to_datetime(["2025-01-01"], utc=True))
    result = run_relative_value(pair_frames(), config(), cash_rates=rates)
    no_interest = run_relative_value(pair_frames(), config())
    assert result.nav.iloc[-1] > no_interest.nav.iloc[-1]


def test_missing_or_malformed_pair_data_is_rejected() -> None:
    with pytest.raises(ValueError, match="missing pair bars"):
        run_relative_value({"LEFT": pair_frames()["LEFT"]}, config())
    bad = pair_frames()
    bad["RIGHT"] = bad["RIGHT"].drop(columns="open")
    with pytest.raises(ValueError, match="missing columns"):
        run_relative_value(bad, config())
