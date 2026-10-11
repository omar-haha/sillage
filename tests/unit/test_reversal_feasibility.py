import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "feasibility", Path(__file__).parents[2] / "scripts/reversal_feasibility.py"
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_minimum_commission_is_diluted_not_spread_or_borrow():
    small = module.cost_budget(25000, 0.2)
    large = module.cost_budget(100000, 0.2)
    assert small["commission_floor"] == large["commission_floor"] == 8
    assert small["sleeve_capital"] == 5000
    assert large["sleeve_capital"] == 20000
    assert small["break_even_bps_of_sleeve"] - large["break_even_bps_of_sleeve"] == pytest.approx(
        12
    )


def test_cost_floor_includes_all_eight_tickets():
    row = module.cost_budget(25000, 0.2, spread_bps=0, borrow_rate=0)
    assert row["break_even_bps_of_sleeve"] == pytest.approx(16)


def test_small_sleeve_rounds_to_whole_shares():
    assert module.cost_budget(25000, 0.1)["illustrative_100_dollar_etf_shares_per_leg"] == 6


def test_invalid_budget_rejected():
    with pytest.raises(ValueError):
        module.cost_budget(0, 0.2)
