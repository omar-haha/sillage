"""Research-only Bitcoin ETP allocation; never added to the live registry."""

from datetime import date
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from sillage.core.money import dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import Universe, get_universe
from sillage.strategy.base import Strategy
from sillage.strategy.benchmarks import StaticWeights, buy_and_hold
from sillage.strategy.blend import Blend
from sillage.strategy.registry import build


def bitcoin_portfolio(weight: float, *, symbol: str = "IBIT") -> tuple[Strategy, Universe]:
    if not isfinite(weight) or not 0 <= weight <= 1 or symbol not in {"IBIT", "SPY"}:
        raise ValueError("invalid Bitcoin research allocation")
    core = get_universe("core")
    instrument = Instrument(
        "IBIT", AssetClass.ETF, currency="USD", exchange="NASDAQ", available_from=date(2024, 1, 11)
    )
    universe = Universe("bitcoin-research", (*core.instruments, instrument), core.cash_proxy)
    if weight == 0:
        strategy: Strategy = build("balanced", core)
    elif weight == 1:
        strategy = buy_and_hold(symbol)
    else:
        strategy = Blend(
            [build("balanced", core), StaticWeights({symbol: 1})],
            [dec(1) - dec(weight), dec(weight)],
            name=f"balanced-{symbol}-{weight}",
        )
    return strategy, universe


def feasibility(frame: pd.DataFrame, gates: dict[str, float]) -> dict[str, Any]:
    volume = frame["volume"].to_numpy()
    if len(volume) < 20 or not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError("incomplete volume evidence")
    observations = len(frame)
    median_volume = float(np.median(volume[-20:]))
    zero_fraction = float(np.mean(volume == 0))
    checks = {
        "history": observations >= gates["minimum_product_sessions"],
        "volume": median_volume >= gates["minimum_20_session_median_volume"],
        "sparse_sessions": zero_fraction <= gates["maximum_zero_volume_fraction"],
    }
    return {
        "observations": observations,
        "first_session": str(frame.index[0].date()),
        "last_session": str(frame.index[-1].date()),
        "recent_median_volume": median_volume,
        "zero_volume_fraction": zero_fraction,
        "checks": checks,
        "pass": all(checks.values()),
    }


def assessment(
    rows: dict[str, Any], criteria: dict[str, float], comparisons: dict[str, Any], feasible: bool
) -> dict[str, Any]:
    core, primary, spy = (rows[key]["metrics"] for key in ("core", "primary", "spy_substitution"))
    diagnostics = next(iter(comparisons.values()))
    checks = {
        "primary_sharpe": primary["sharpe"] - core["sharpe"]
        >= criteria["minimum_full_excess_sharpe_improvement_vs_core"],
        "risky_asset_control": primary["sharpe"] - spy["sharpe"]
        >= criteria["minimum_full_excess_sharpe_improvement_vs_spy_substitution"],
        "both_halves": min(diagnostics["half_excess_sharpe_improvements"])
        >= criteria["minimum_half_excess_sharpe_improvements_vs_core"],
        "drawdown_depth": primary["max_drawdown"]
        >= core["max_drawdown"] - criteria["maximum_extra_drawdown_depth"],
        "drawdown_duration": primary["longest_drawdown_days"]
        <= core["longest_drawdown_days"] * criteria["maximum_drawdown_duration_ratio"],
        "neighbors": min(
            rows[key]["metrics"]["sharpe"] - core["sharpe"] for key in ("bitcoin_1", "bitcoin_5")
        )
        >= criteria["minimum_neighbor_excess_sharpe_improvements_vs_core"],
        "cost_stress": rows["primary_2x"]["metrics"]["sharpe"]
        - rows["core_2x"]["metrics"]["sharpe"]
        >= criteria["minimum_2x_cost_excess_sharpe_improvement_vs_2x_core"],
        "static_feasibility": feasible,
        "execution_evidence_clean": all(row["rejections"] == 0 for row in rows.values()),
        "unleveraged_cash_book": all(
            dec(row["minimum_cash"]) >= 0 and dec(row["maximum_gross_exposure"]) <= 1
            for row in rows.values()
        ),
    }
    positive_intervals = all(
        value["paired_block_sharpe_improvement_interval_95"][0] > 0
        for value in comparisons.values()
    )
    status = (
        "reject_frozen_expression"
        if not all(checks.values())
        else (
            "further_validation_only" if positive_intervals else "inconclusive_shadow_review_only"
        )
    )
    return {
        "checks": checks,
        "status": status,
        "positive_intervals": positive_intervals,
        "paper_or_live_promotion": False,
    }
