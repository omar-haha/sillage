"""Prepare a separate IBIT vintage, then score the frozen offline experiment."""

import argparse
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from sillage.backtest.bitcoin_etp import assessment, bitcoin_portfolio, feasibility
from sillage.backtest.metrics import (
    _excess_returns,
    from_nav,
    load_risk_free_rates,
    nav_series,
    trading_stats,
)
from sillage.backtest.quality_etf import paired_diagnostics, validate_sessions
from sillage.backtest.runner import BacktestConfig, load_universe, run_backtest
from sillage.core.calendar import TradingCalendar
from sillage.core.money import dec
from sillage.data.providers.yahoo import YahooProvider
from sillage.data.store import BarStore
from sillage.data.universe import get_universe
from sillage.execution.costs import CostModel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", help="Download only, never score")
    parser.add_argument("--output", type=Path, default=Path("research/bitcoin-etp-candidate.json"))
    args = parser.parse_args()
    spec_path = Path("research/bitcoin-etp-spec.json")
    spec = json.loads(spec_path.read_text())
    root = Path("data/bitcoin-etp-research")
    store = BarStore(root)
    rates_path = Path("research/inputs/usd_3m_treasury.csv")
    manifest_path = Path("research/bitcoin-etp-inputs.json")
    symbols = [i.symbol for i in get_universe("core").all_instruments]
    if args.prepare:
        if store.has("IBIT") or manifest_path.exists():
            raise ValueError("Bitcoin vintage already exists; never overwrite it")
        source = BarStore(Path("data/quality-etf-research"))
        frozen = json.loads(Path("research/quality-etf-inputs.json").read_text())
        if sha(rates_path) != frozen["rates"] or any(
            sha(source.path_for(s)) != frozen[s] for s in symbols
        ):
            raise ValueError("core vintage changed")
        for symbol in symbols:
            target = store.path_for(symbol)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.path_for(symbol), target)
        frame = YahooProvider().fetch_daily(
            "IBIT", date.fromisoformat(spec["replay_start"]), date.fromisoformat(spec["end"])
        )
        if frame.empty:
            raise ValueError("IBIT source returned no observations")
        store.write("IBIT", frame)
        manifest = {s: sha(store.path_for(s)) for s in [*symbols, "IBIT"]}
        manifest["rates"] = sha(rates_path)
        manifest["spec"] = sha(spec_path)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print("Prepared", len(frame), "IBIT observations; no portfolios scored")
        return
    hashes = {s: sha(store.path_for(s)) for s in [*symbols, "IBIT"]}
    hashes.update(rates=sha(rates_path), spec=sha(spec_path))
    if hashes != json.loads(manifest_path.read_text()):
        raise ValueError("frozen Bitcoin input vintage changed")
    start, end = date.fromisoformat(spec["replay_start"]), date.fromisoformat(spec["end"])
    expected = pd.DatetimeIndex(
        [pd.Timestamp(s.day, tz="UTC") for s in TradingCalendar().sessions(start, end)]
    )
    for symbol in [*symbols, "IBIT"]:
        validate_sessions(pd.read_parquet(store.path_for(symbol)), expected)
    feasible = feasibility(
        pd.read_parquet(store.path_for("IBIT")), spec["static_feasibility_gates"]
    )
    if not feasible["pass"]:
        args.output.write_text(
            json.dumps(
                {"candidate": "G", "status": "reject_static_feasibility", "feasibility": feasible},
                indent=2,
            )
            + "\n"
        )
        print("Rejected at static feasibility; no portfolio curves scored")
        return
    rates = load_risk_free_rates(rates_path)
    variants = {
        "core": (0, "IBIT", 1),
        "primary": (spec["primary_weight"], "IBIT", 1),
        "spy_substitution": (spec["primary_weight"], "SPY", 1),
        "bitcoin_1": (spec["neighbor_weights"][0], "IBIT", 1),
        "bitcoin_5": (spec["neighbor_weights"][1], "IBIT", 1),
        "bitcoin_standalone": (1, "IBIT", 1),
        "core_2x": (0, "IBIT", 2),
        "primary_2x": (spec["primary_weight"], "IBIT", 2),
    }
    if list(variants) != spec["variants"]:
        raise ValueError("variant list changed after freeze")
    base_strategy, universe = bitcoin_portfolio(0)
    common = {
        "start": start,
        "end": end,
        "data_root": root,
        "universe": universe,
        "initial_cash": dec(spec["capital_usd"]),
    }
    bars = load_universe(BacktestConfig(strategy=base_strategy, **common))
    rows, navs = {}, {}
    for name, (weight, symbol, scale) in variants.items():
        strategy, _ = bitcoin_portfolio(weight, symbol=symbol)
        costs = CostModel(
            commission_per_share=dec("0.005"),
            min_commission=dec(1),
            half_spread_bps={**CostModel().half_spread_bps, "IBIT": dec(2)},
        ).scaled(scale)
        result = run_backtest(BacktestConfig(strategy=strategy, costs=costs, **common), bars=bars)
        nav = nav_series(result.nav_points)
        prior = nav.index[nav.index < pd.Timestamp(spec["score_start"])][-1]
        navs[name] = nav.loc[prior:]
        marked = [
            p for p in result.nav_points if p.session >= date.fromisoformat(spec["score_start"])
        ]
        rows[name] = {
            "metrics": asdict(from_nav(navs[name], label=name, risk_free_rate=rates)),
            "replay_trading": asdict(trading_stats(result, nav)),
            "rejections": len(result.rejections),
            "rejection_reasons": sorted({r.reason for r in result.rejections}),
            "bitcoin_fills": sum(f.instrument.symbol == "IBIT" for f in result.fills),
            "bitcoin_mean_weight": sum(float(p.weights.get("IBIT", 0)) for p in marked)
            / len(marked),
            "bitcoin_max_weight": max(float(p.weights.get("IBIT", 0)) for p in marked),
            "minimum_cash": str(min(p.cash for p in result.nav_points)),
            "maximum_gross_exposure": str(max(p.gross_exposure for p in result.nav_points)),
        }
        print(
            name,
            "excess Sharpe",
            round(rows[name]["metrics"]["sharpe"], 3),
            "rejections",
            rows[name]["rejections"],
            flush=True,
        )
    comparisons = {}
    for name in variants:
        if name == "core":
            continue
        base = navs["core_2x"] if name == "primary_2x" else navs["core"]
        comparisons[name] = {
            str(block): paired_diagnostics(
                base,
                navs[name],
                rates,
                blocks=block,
                replicates=spec["uncertainty"]["replicates"],
                seed=spec["uncertainty"]["seed"],
            )
            for block in spec["uncertainty"]["blocks"]
        }
    core_excess = _excess_returns(navs["core"].pct_change().dropna(), rates)
    bitcoin_excess = _excess_returns(navs["bitcoin_standalone"].pct_change().dropna(), rates)
    report = {
        "candidate": "G",
        "specification_commit": "7032207",
        "spec_sha256": sha(spec_path),
        "implementation_sha256": sha(__file__),
        "research_module_sha256": sha("src/sillage/backtest/bitcoin_etp.py"),
        "implementation_base_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "input_sha256": hashes,
        "feasibility": feasible,
        "variants": rows,
        "comparisons": comparisons,
        "bitcoin_core_daily_excess_correlation": float(bitcoin_excess.corr(core_excess)),
        **assessment(rows, spec["criteria"], comparisons["primary"], feasible["pass"]),
        "limitations": spec["limitations"],
        "production_changed": False,
    }
    args.output.write_text(json.dumps(report, indent=2, default=str, allow_nan=False) + "\n")
    print(report["status"])


if __name__ == "__main__":
    main()
