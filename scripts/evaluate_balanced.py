"""Offline, frozen-vintage review of the deployed rule; never touches a broker."""

import argparse
import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sillage.backtest.metrics import (
    _excess_returns,
    analyse,
    from_nav,
    load_risk_free_rates,
    nav_series,
)
from sillage.backtest.quality_etf import paired_diagnostics, validate_sessions
from sillage.backtest.runner import BacktestConfig, load_universe, run_backtest
from sillage.core.calendar import TradingCalendar
from sillage.core.money import dec
from sillage.data.store import BarStore
from sillage.data.universe import get_universe
from sillage.execution.costs import CostModel
from sillage.strategy.benchmarks import StaticWeights, sixty_forty
from sillage.strategy.blend import Blend
from sillage.strategy.registry import build


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def variant(name):
    allowed = json.loads(Path("research/balanced-review-spec.json").read_text())["variants"]
    if name not in allowed:
        raise ValueError("undeclared research variant")
    universe = get_universe("core")
    costs = CostModel(commission_per_share=dec("0.005"), min_commission=dec(1))
    capital = dec(25000)
    if name == "static-50-50":
        strategy = StaticWeights({"SPY": "0.5", "IEF": "0.5"})
    elif name.startswith("blend-momentum-"):
        weight = dec(name.rsplit("-", 1)[1]) / dec(100)
        strategy = Blend([build("momentum", universe), sixty_forty()], [weight, dec(1) - weight])
    else:
        strategy = build(
            name if name in {"momentum", "60-40", "spy", "equal-weight"} else "balanced", universe
        )
    if name == "balanced-tiered":
        costs = CostModel()
    if name == "balanced-2x":
        costs = costs.scaled(2)
    if name == "balanced-100k":
        capital = dec(100000)
    return strategy, costs, capital


def window(nav, start, end):
    """Include preceding close once, never duplicate a scored daily return."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    prior = nav.index[nav.index < start]
    result = nav.loc[(prior[-1] if len(prior) else start) : end]
    if len(result) < 2:
        raise ValueError("insufficient observations in diagnostic window")
    return result


def factor_diagnostic(balanced, spy, ief, rates):
    series = pd.DataFrame({"balanced": balanced, "spy": spy, "ief": ief})
    if series.isna().any().any():
        raise ValueError("factor diagnostic requires identical observation dates")
    returns = series.pct_change().dropna()
    excess = pd.DataFrame({key: _excess_returns(returns[key], rates) for key in returns})
    x = np.column_stack([np.ones(len(excess)), excess["spy"], excess["ief"]])
    y = excess["balanced"].to_numpy()
    coefficients = np.linalg.lstsq(x, y, rcond=None)[0]
    residual = y - x @ coefficients
    return {
        "spy_beta": float(coefficients[1]),
        "ief_beta": float(coefficients[2]),
        "r_squared": float(1 - np.var(residual) / np.var(y)),
        "annualized_intercept_descriptive_only": float(coefficients[0] * 252),
        "note": "In-sample descriptive regression, not investable residual alpha or causal attribution.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("research/balanced-review.json"))
    args = parser.parse_args()
    spec_path = Path("research/balanced-review-spec.json")
    spec = json.loads(spec_path.read_text())
    store = BarStore(Path(spec["data_root"]))
    universe = get_universe("core")
    rate_path = Path("research/inputs/usd_3m_treasury.csv")
    expected_hashes = json.loads(Path(spec["input_manifest"]).read_text())
    hashes = {i.symbol: sha(store.path_for(i.symbol)) for i in universe.all_instruments}
    hashes["rates"] = sha(rate_path)
    if any(expected_hashes.get(key) != value for key, value in hashes.items()):
        raise ValueError("frozen input vintage changed; do not silently restate research")
    expected = pd.DatetimeIndex(
        [
            pd.Timestamp(s.day, tz="UTC")
            for s in TradingCalendar().sessions(
                date.fromisoformat(spec["start"]), date.fromisoformat(spec["end"])
            )
        ]
    )
    coverage = {}
    for instrument in universe.all_instruments:
        frame = pd.read_parquet(store.path_for(instrument.symbol))
        # Products absent at the start are not backfilled. Every session after the
        # first available observation must exist through the frozen end.
        available = frame.loc[frame.index <= expected[-1]]
        if available.empty:
            raise ValueError("missing instrument history")
        validate_sessions(frame, expected[expected >= available.index[0]])
        coverage[instrument.symbol] = str(available.index[0].date())
    rates = load_risk_free_rates(rate_path)
    common = {
        "universe": universe,
        "start": date.fromisoformat(spec["start"]),
        "end": date.fromisoformat(spec["end"]),
        "data_root": Path(spec["data_root"]),
    }
    bars = load_universe(BacktestConfig(strategy=build("balanced", universe), **common))
    navs, rows = {}, {}
    for name in spec["variants"]:
        strategy, costs, capital = variant(name)
        result = run_backtest(
            BacktestConfig(strategy=strategy, costs=costs, initial_cash=capital, **common),
            bars=bars,
        )
        nav = nav_series(result.nav_points)
        navs[name] = nav
        performance = analyse(result, risk_free_rate=rates)
        rows[name] = {
            "metrics": asdict(performance.metrics),
            "trading": asdict(performance.trading),
            "initial_cash": str(capital),
            "rejections": len(result.rejections),
            "rejection_reasons": sorted({r.reason for r in result.rejections}),
            "windows": {
                key: asdict(from_nav(window(nav, *dates), label=name, risk_free_rate=rates))
                for key, dates in spec["windows"].items()
            },
            "mean_marked_weights": {
                symbol: float(np.mean([float(p.weights.get(symbol, 0)) for p in result.nav_points]))
                for symbol in sorted({s for p in result.nav_points for s in p.weights})
            },
        }
        print(
            name,
            "Sharpe",
            round(performance.metrics.sharpe, 3),
            "rejections",
            len(result.rejections),
            flush=True,
        )
    comparisons = {}
    for name in spec["uncertainty"]["comparators"]:
        comparisons[name] = {}
        for block in spec["uncertainty"]["blocks"]:
            comparisons[name][str(block)] = paired_diagnostics(
                navs[name],
                navs["balanced"],
                rates,
                blocks=block,
                replicates=spec["uncertainty"]["replicates"],
                seed=spec["uncertainty"]["seed"],
            )
    returns = pd.DataFrame(
        {
            key: _excess_returns(navs[key].pct_change().dropna(), rates)
            for key in ("balanced", "momentum", "60-40", "spy")
        }
    )
    prices = {}
    for symbol in ("SPY", "IEF"):
        series = pd.read_parquet(store.path_for(symbol))["close"]
        series.index = series.index.tz_localize(None)
        prices[symbol] = series.reindex(navs["balanced"].index)
    report = {
        "study": spec["study"],
        "specification_commit": "f705c21",
        "spec_sha256": sha(spec_path),
        "implementation_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "implementation_sha256": sha(__file__),
        "input_sha256": hashes,
        "first_observations": coverage,
        "variants": rows,
        "balanced_minus_controls": comparisons,
        "excess_return_correlations": returns.corr().to_dict(),
        "market_bond_exposure": factor_diagnostic(
            navs["balanced"], prices["SPY"], prices["IEF"], rates
        ),
        "limitations": spec["limitations"],
        "untouched_holdout": False,
        "production_changed": False,
    }
    args.output.write_text(json.dumps(report, indent=2, default=str, allow_nan=False) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
