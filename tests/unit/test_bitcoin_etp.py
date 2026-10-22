import json
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from tests.support import make_bars

from sillage.backtest.bitcoin_etp import assessment, bitcoin_portfolio, feasibility
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.core.money import dec
from sillage.data.universe import get_universe
from sillage.strategy.base import Monthly
from sillage.strategy.benchmarks import StaticWeights
from sillage.strategy.blend import Blend
from sillage.strategy.registry import names


def test_bitcoin_is_not_added_to_live_universe_or_registry() -> None:
    strategy, universe = bitcoin_portfolio(0.03)
    assert isinstance(strategy, Blend)
    assert strategy.weights == (dec("0.97"), dec("0.03"))
    assert strategy.sleeves[0].name == "balanced"
    assert isinstance(strategy.sleeves[1], StaticWeights)
    assert strategy.sleeves[1].weights == {"IBIT": 1}
    assert isinstance(strategy.sleeves[1].schedule, Monthly)
    assert universe.get("IBIT").currency == "USD"
    assert universe.get("IBIT").lot_size == 1
    assert "IBIT" not in get_universe("core").symbols
    assert "bitcoin" not in names()
    assert bitcoin_portfolio(0)[0].name == "balanced"
    standalone, _ = bitcoin_portfolio(1)
    assert isinstance(standalone, StaticWeights)
    assert standalone.weights == {"IBIT": 1}
    spy, _ = bitcoin_portfolio(0.03, symbol="SPY")
    assert isinstance(spy, Blend) and isinstance(spy.sleeves[1], StaticWeights)
    assert spy.sleeves[1].weights == {"SPY": 1}


@pytest.mark.parametrize("weight", [-0.1, 1.1, float("nan"), float("inf")])
def test_invalid_weights_refused(weight: float) -> None:
    with pytest.raises(ValueError, match="invalid"):
        bitcoin_portfolio(weight)


def test_unknown_product_refused() -> None:
    with pytest.raises(ValueError, match="invalid"):
        bitcoin_portfolio(0.03, symbol="BTC-USD")


def test_netted_replay_uses_whole_shares_next_open_and_no_borrowing() -> None:
    strategy, universe = bitcoin_portfolio(0.03)
    bars = {
        i.symbol: make_bars(i, [100] * 45, start=date(2024, 1, 11), open_ratio=dec(1))
        for i in universe.all_instruments
    }
    result = run_backtest(
        BacktestConfig(
            strategy=strategy,
            universe=universe,
            start=date(2024, 1, 11),
            end=date(2024, 3, 1),
            initial_cash=dec(25000),
        ),
        bars=bars,
    )
    fills = [fill for fill in result.fills if fill.instrument.symbol == "IBIT"]
    assert fills and not result.rejections
    orders = {order.client_order_id: order for order in result.orders}
    for fill in fills:
        assert fill.quantity == fill.quantity.to_integral_value()
        created = orders[fill.order_id].created_at
        assert created is not None and created < fill.ts
    assert all(point.cash >= 0 and point.gross_exposure <= 1 for point in result.nav_points)


def test_feasibility_uses_actual_product_history_and_recent_volume() -> None:
    spec = json.loads(Path("research/bitcoin-etp-spec.json").read_text())
    frame = pd.DataFrame(
        {"volume": [200000] * 501}, index=pd.bdate_range("2024-01-11", periods=501)
    )
    result = feasibility(frame, spec["static_feasibility_gates"])
    assert result["pass"]
    assert not feasibility(frame.iloc[:100], spec["static_feasibility_gates"])["pass"]
    frame.iloc[-20:, 0] = 0
    assert not feasibility(frame, spec["static_feasibility_gates"])["pass"]
    frame.iloc[0, 0] = -1
    with pytest.raises(ValueError, match="volume"):
        feasibility(frame, spec["static_feasibility_gates"])
    frame.iloc[0, 0] = np.nan
    with pytest.raises(ValueError, match="volume"):
        feasibility(frame, spec["static_feasibility_gates"])


def test_uncertainty_and_neighbors_cannot_promote_failed_primary() -> None:
    spec = json.loads(Path("research/bitcoin-etp-spec.json").read_text())
    rows: dict[str, Any] = {
        key: {
            "metrics": {"sharpe": 0.6, "max_drawdown": -0.1, "longest_drawdown_days": 100},
            "rejections": 0,
            "minimum_cash": "0",
            "maximum_gross_exposure": "1",
        }
        for key in spec["variants"]
    }
    comparisons = {
        "21": {
            "half_excess_sharpe_improvements": [0.01, 0.02],
            "paired_block_sharpe_improvement_interval_95": [-0.1, 0.2],
        }
    }
    rows["bitcoin_5"]["metrics"]["sharpe"] = 2
    assert (
        assessment(rows, spec["criteria"], comparisons, True)["status"]
        == "reject_frozen_expression"
    )
    rows["primary"]["metrics"]["sharpe"] = 0.65
    outcome = assessment(rows, spec["criteria"], comparisons, True)
    assert outcome["status"] == "inconclusive_shadow_review_only"
    assert not outcome["paper_or_live_promotion"]
    rows["primary"]["minimum_cash"] = "-1"
    assert not assessment(rows, spec["criteria"], comparisons, True)["checks"][
        "unleveraged_cash_book"
    ]
    rows["primary"]["minimum_cash"] = "0"
    rows["core"]["rejections"] = 1
    assert (
        assessment(rows, spec["criteria"], comparisons, True)["status"]
        == "reject_frozen_expression"
    )
