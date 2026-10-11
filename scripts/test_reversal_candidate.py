"""Run only preregistered D screens; no tuning or broker access."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from sillage.backtest.metrics import from_nav, load_risk_free_rates
from sillage.backtest.reversal import run_reversal
from sillage.core.money import dec
from sillage.data.store import BarStore
from sillage.execution.costs import CostModel
from sillage.strategy.sector_neutral import SECTOR_ETFS


def main():
    store = BarStore("data/sector-research")
    inputs = json.loads(Path("research/reversal-feasibility.json").read_text())
    for symbol, coverage in inputs["coverage"].items():
        path = (
            Path("data/reversal-research/vix.parquet")
            if symbol == "VIX"
            else store.path_for(symbol)
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != coverage["sha256"]:
            raise ValueError(f"{symbol}: research vintage changed; review before another trial")
    bars = {s: pd.read_parquet(store.path_for(s)) for s in (*SECTOR_ETFS, "SPY")}
    start = pd.Timestamp("2005-01-03", tz="UTC")
    end = pd.Timestamp("2026-09-17", tz="UTC")
    bars = {s: f.loc[(f.index >= start) & (f.index < end)] for s, f in bars.items()}
    vix = pd.read_parquet("data/reversal-research/vix.parquet")["close"]
    rates = load_risk_free_rates("research/inputs/usd_3m_treasury.csv")
    margin = load_risk_free_rates("research/inputs/usd_ibkr_pro_margin_proxy.csv")
    costs = CostModel(
        commission_per_share=dec("0.005"),
        min_commission=dec(1),
        half_spread_bps={},
        default_half_spread_bps=dec(2),
    )
    variants = {
        "primary_5k": {},
        "standalone_25k_diagnostic": {"initial_nav": 25000},
        "vix20": {"threshold": 20},
        "vix30": {"threshold": 30},
        "delay_session": {"delay": 1},
        "2x_costs": {"costs": costs.scaled(2)},
        "4pct_borrow": {"borrow_rate": 0.04},
        "crisis_costs": {
            "costs": CostModel(
                commission_per_share=dec("0.005"),
                min_commission=dec(1),
                half_spread_bps={},
                default_half_spread_bps=dec(10),
            ),
            "borrow_rate": 0.04,
        },
    }
    rows = {}
    for name, kwargs in variants.items():
        result = run_reversal(bars, vix, margin_rates=margin, **kwargs)
        nav = result.nav.iloc[252:]
        mid = len(nav) // 2
        halves = [float(nav.iloc[mid] - nav.iloc[0]), float(nav.iloc[-1] - nav.iloc[mid])]
        rows[name] = {
            "metrics": asdict(from_nav(nav, label=name, risk_free_rate=rates)),
            "half_net_trading_pnl": halves,
            "entries": result.entries,
            "fills": len(result.fills),
            "borrow": result.borrow,
            "commissions": result.commissions,
            "slippage": result.slippage,
            "financing": result.financing,
            "terminal_positions": result.terminal_positions,
        }
        print(
            name, "P&L", round(float(nav.iloc[-1] - nav.iloc[0]), 2), "halves", halves, flush=True
        )
    report = {
        "candidate": "D",
        "sanity_gate_pass": min(rows["primary_5k"]["half_net_trading_pnl"]) > 0,
        "variants": rows,
        "inputs": inputs,
        "rate_sha256": {
            name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in {
                "cash_benchmark": "research/inputs/usd_3m_treasury.csv",
                "margin_proxy": "research/inputs/usd_ibkr_pro_margin_proxy.csv",
            }.items()
        },
        "combined_portfolio_test": "conditional on sanity gate; not yet evaluated",
        "limitations": [
            "Declared quotes/borrow assumptions, not live execution validation.",
            "Reused historical period, not an untouched holdout.",
            "No cash interest; partial terminal basket is marked, not liquidated.",
        ],
    }
    Path("research/reversal-candidate.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )


if __name__ == "__main__":
    main()
