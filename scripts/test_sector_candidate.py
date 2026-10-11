"""Reproduce Candidate C's first acceptance gate without tuning or broker access."""

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from sillage.backtest.metrics import from_nav, load_risk_free_rates
from sillage.backtest.sector_neutral import run_sectors
from sillage.data.providers.yahoo import YahooProvider
from sillage.data.store import BarStore
from sillage.execution.costs import CostModel
from sillage.strategy.sector_neutral import SECTOR_ETFS, SectorNeutralConfig


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 16))
    args = parser.parse_args()
    root = Path("data/sector-research")
    store = BarStore(root)
    symbols = (*SECTOR_ETFS, "SPY")
    if args.download:
        provider = YahooProvider()
        for symbol in symbols:
            store.write(symbol, provider.fetch_daily(symbol, date(2005, 1, 3), args.end))
            print(f"Stored research history: {symbol}", flush=True)
    bars = {s: pd.read_parquet(store.path_for(s)) for s in symbols}
    end = pd.Timestamp(args.end, tz="UTC") + pd.Timedelta(days=1)
    bars = {s: frame.loc[frame.index < end] for s, frame in bars.items()}
    rates_path = Path("research/inputs/usd_3m_treasury.csv")
    rates = load_risk_free_rates(rates_path)
    variants = {
        "base": {},
        "free_execution_no_borrow": {"costs": CostModel().scaled(0), "borrow_rate": 0},
        "2x_costs": {"costs": CostModel().scaled(2)},
        "4pct_borrow": {"borrow_rate": 0.04},
        "delay_month": {"delay_months": 1},
        "one_per_side": {"signal": SectorNeutralConfig(selections_per_side=1)},
        "three_per_side": {"signal": SectorNeutralConfig(selections_per_side=3)},
    }
    rows = {}
    for name, kwargs in variants.items():
        # Conservative $25K account screen: cash earns zero, excess metrics still
        # use the dated Treasury benchmark. Do not credit short proceeds interest.
        result = run_sectors(bars, **kwargs)
        # Exclude signal warm-up; retain the prior close to include first return.
        nav = result.nav.iloc[252:]
        metric = from_nav(nav, label=name, risk_free_rate=rates)
        mid = len(nav) // 2
        halves = [
            from_nav(part, label=name, risk_free_rate=rates).sharpe
            for part in (nav.iloc[: mid + 1], nav.iloc[mid:])
        ]
        rows[name] = {
            "metrics": asdict(metric),
            "half_excess_sharpes": halves,
            "sector_pnl": result.sector_pnl,
            "fills": len(result.fills),
            "borrow": result.borrow,
            "commissions": result.commissions,
            "slippage": result.slippage,
            "cash_interest": result.cash_interest,
        }
        print(
            name,
            "excess Sharpe",
            round(metric.sharpe, 3),
            "CAGR",
            round(metric.cagr, 4),
            flush=True,
        )
    base = rows["base"]
    passes = (
        base["metrics"]["sharpe"] >= 0.60
        and base["metrics"]["max_drawdown"] > -0.20
        and min(base["half_excess_sharpes"]) > 0
    )
    report = {
        "candidate": "C",
        "initial_nav": 25000,
        "cash_interest_assumption": "zero; conservative retail-account screen",
        "signal": asdict(SectorNeutralConfig()),
        "execution": "month-end close decision, next observed session open, whole shares, trailing 20-session ADV",
        "gate_one_requirements": {
            "minimum_excess_sharpe": 0.60,
            "maximum_drawdown": 0.20,
            "both_half_excess_sharpes_positive": True,
        },
        "gate_one_pass": passes,
        "variants": rows,
        "data_sha256": {
            s: hashlib.sha256(store.path_for(s).read_bytes()).hexdigest() for s in symbols
        },
        "rates_sha256": hashlib.sha256(rates_path.read_bytes()).hexdigest(),
        "caveats": [
            "Adjusted Yahoo research prices are restated, not historical executable quotes.",
            "Borrow is a proxy, not a historical IBKR fee/availability archive.",
            "No interest on short proceeds; no live trading enabled.",
            "Correlation, drawdown overlap and venue gates remain conditional on gate one.",
        ],
    }
    Path("research/sector-candidate.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )


if __name__ == "__main__":
    main()
