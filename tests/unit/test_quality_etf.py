import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sillage.backtest.quality_etf import acceptance, paired_diagnostics, quality_portfolio
from sillage.core.money import dec
from sillage.strategy.blend import Blend
from sillage.strategy.registry import names


def test_research_union_does_not_change_live_registry_or_core_universe():
    from sillage.data.universe import get_universe

    strategy, execution = quality_portfolio(0.2)
    assert isinstance(strategy, Blend)
    assert strategy.weights == (dec("0.8"), dec("0.2"))
    assert execution.symbols.count("QUAL") == 1
    assert "QUAL" not in get_universe("core").symbols
    assert "quality" not in names()
    assert strategy.sleeves[0].name == "balanced"
    assert strategy.sleeves[1].weights == {"QUAL": 1}


@pytest.mark.parametrize("weight", [-0.1, 1.1, float("nan")])
def test_invalid_allocation(weight):
    with pytest.raises(ValueError):
        quality_portfolio(weight)


def test_shared_spy_control_and_endpoints():
    strategy, _ = quality_portfolio(0.2, symbol="SPY")
    assert strategy.sleeves[1].weights == {"SPY": 1}
    assert quality_portfolio(0)[0].name == "balanced"
    assert quality_portfolio(1)[0].weights == {"QUAL": 1}
    with pytest.raises(ValueError):
        quality_portfolio(0.2, symbol="OTHER")


def test_paired_bootstrap_identical_curves_zero_difference_and_no_mutation():
    rng = np.random.default_rng(1)
    index = pd.bdate_range("2016-01-01", periods=600)
    nav = pd.Series(25000 * np.cumprod(1 + rng.normal(0.0002, 0.005, len(index))), index=index)
    copy = nav.copy()
    rates = pd.Series(0.02, index=index)
    a = paired_diagnostics(nav, nav.copy(), rates, replicates=30)
    assert a == paired_diagnostics(nav, nav, rates, replicates=30)
    assert a["excess_sharpe_improvement"] == 0
    assert a["paired_block_sharpe_improvement_interval_95"] == [0, 0]
    pd.testing.assert_series_equal(nav, copy)
    with pytest.raises(ValueError):
        paired_diagnostics(nav, nav.iloc[1:], rates)
    with pytest.raises(ValueError):
        paired_diagnostics(nav, nav, rates, blocks=0)


def test_better_neighbor_cannot_rescue_failed_primary_gate():
    spec = json.loads(Path("research/quality-etf-spec.json").read_text())
    metric = {"sharpe": 0.8, "max_drawdown": -0.2, "longest_drawdown_days": 500}
    rows = {
        k: {"metrics": metric.copy()}
        for k in (
            "core",
            "primary",
            "spy_substitution",
            "quality_10",
            "quality_30",
            "primary_2x",
            "core_2x",
        )
    }
    rows["primary"]["comparison"] = {"half_excess_sharpe_improvements": [0.05, 0.05]}
    for k in ("quality_10", "quality_30"):
        rows[k]["metrics"]["sharpe"] = 1.5
    result = acceptance(rows, spec["criteria"])
    assert result["checks"]["neighbors"]
    assert not result["continue_to_raw_price_and_venue_validation"]
    rows["primary"]["metrics"]["sharpe"] = 1.0
    assert acceptance(rows, spec["criteria"])["continue_to_raw_price_and_venue_validation"]
