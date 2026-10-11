"""Prepare private QUAL data, or replay the frozen E2 netted portfolio screen."""

import argparse
import hashlib
import json
import shutil
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
from sillage.backtest.quality_etf import (
    acceptance,
    paired_diagnostics,
    quality_portfolio,
    validate_sessions,
)
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.core.calendar import TradingCalendar
from sillage.core.money import dec
from sillage.data.providers.yahoo import YahooProvider
from sillage.data.store import BarStore
from sillage.data.universe import get_universe
from sillage.execution.costs import CostModel


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", help="download only; never score")
    args = parser.parse_args()
    spec_path = Path("research/quality-etf-spec.json")
    spec = json.loads(spec_path.read_text())
    root = Path("data/quality-etf-research")
    store = BarStore(root)
    source = BarStore(Path("data"))
    symbols = [item.symbol for item in get_universe("core").all_instruments] + ["QUAL"]
    rate_path = Path("research/inputs/usd_3m_treasury.csv")
    if args.prepare:
        if store.has("QUAL"):
            raise ValueError("QUAL vintage already exists; do not overwrite it")
        for symbol in symbols[:-1]:
            target = store.path_for(symbol)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source.path_for(symbol), target)
        bars = YahooProvider().fetch_daily(
            "QUAL", date(2013, 7, 16), date.fromisoformat(spec["end"])
        )
        if bars.empty:
            raise ValueError("QUAL source returned no observations")
        store.write("QUAL", bars)
        print("Prepared QUAL observations:", len(bars), "no returns scored")
        return
    hashes = {s: sha(store.path_for(s)) for s in symbols}
    hashes["rates"] = sha(rate_path)
    frozen = json.loads(Path("research/quality-etf-inputs.json").read_text())
    if hashes != frozen:
        raise ValueError("input vintage changed; do not silently restate E2")
    expected = pd.DatetimeIndex(
        [
            pd.Timestamp(s.day, tz="UTC")
            for s in TradingCalendar().sessions(
                date.fromisoformat(spec["replay_start"]), date.fromisoformat(spec["end"])
            )
        ]
    )
    for symbol in symbols:
        frame = pd.read_parquet(store.path_for(symbol))
        validate_sessions(frame, expected)
    rates = load_risk_free_rates(rate_path)
    fixed = CostModel(commission_per_share=dec("0.005"), min_commission=dec(1))
    variants = {
        "core": (0, "QUAL", 1),
        "primary": (0.2, "QUAL", 1),
        "spy_substitution": (0.2, "SPY", 1),
        "qual_standalone": (1, "QUAL", 1),
        "quality_10": (0.1, "QUAL", 1),
        "quality_30": (0.3, "QUAL", 1),
        "core_2x": (0, "QUAL", 2),
        "primary_2x": (0.2, "QUAL", 2),
    }
    navs, rows = {}, {}
    for name, (weight, symbol, scale) in variants.items():
        strategy, universe = quality_portfolio(weight, symbol=symbol)
        result = run_backtest(
            BacktestConfig(
                strategy=strategy,
                universe=universe,
                start=date.fromisoformat(spec["replay_start"]),
                end=date.fromisoformat(spec["end"]),
                initial_cash=dec(spec["capital_usd"]),
                costs=fixed.scaled(scale),
                data_root=root,
            )
        )
        nav = nav_series(result.nav_points)
        # Include preceding close so the first scored session's return is retained.
        start = pd.Timestamp(spec["score_start"])
        prior = nav.index[nav.index < start][-1]
        navs[name] = nav.loc[prior:]
        perf = analyse(result, risk_free_rate=rates)
        qualifying_fills = [f for f in result.fills if f.instrument.symbol == "QUAL"]
        rows[name] = {
            "metrics": asdict(from_nav(navs[name], label=name, risk_free_rate=rates)),
            "replay_trading": asdict(perf.trading),
            "qual_fills": len(qualifying_fills),
            "rejections": len(result.rejections),
            "rejection_reasons": sorted({r.reason for r in result.rejections}),
            "qual_order_dates": sorted({f.ts.date().isoformat() for f in qualifying_fills}),
            "crisis_windows": {
                str(year): asdict(
                    from_nav(navs[name].loc[str(year)], label=name, risk_free_rate=rates)
                )
                for year in (2020, 2022)
            },
        }
        print(name, "excess Sharpe", round(rows[name]["metrics"]["sharpe"], 3), flush=True)
    for name in variants:
        if name != "core":
            baseline = navs["core_2x"] if name == "primary_2x" else navs["core"]
            rows[name]["comparison"] = paired_diagnostics(baseline, navs[name], rates)
    # QUAL-versus-SPY beta: descriptive, not a market-neutral replication or new gate.
    qr = _excess_returns(navs["qual_standalone"].pct_change().dropna(), rates)
    sp = pd.read_parquet(store.path_for("SPY"))["close"]
    sp.index = sp.index.tz_localize(None)
    mr = _excess_returns(sp.reindex(navs["core"].index).pct_change().dropna(), rates)
    beta = float(np.cov(qr, mr, ddof=1)[0, 1] / np.var(mr, ddof=1))
    report = {
        "candidate": "E2",
        "specification_commit": "ebc0342",
        "spec_sha256": sha(spec_path),
        "input_sha256": hashes,
        "variants": rows,
        "qual_standalone_market_beta": beta,
        "qual_core_daily_correlation": float(
            qr.corr(_excess_returns(navs["core"].pct_change().dropna(), rates))
        ),
        **acceptance(rows, spec["criteria"]),
        "limits": spec["limits"],
        "execution_evidence_clean": all(not row["rejections"] for row in rows.values()),
    }
    # Failed/rejected orders preclude promotion regardless of numerical metrics.
    report["continue_to_raw_price_and_venue_validation"] &= report["execution_evidence_clean"]
    output = Path("research/quality-etf-candidate.json")
    output.write_text(json.dumps(report, indent=2, default=str, allow_nan=False) + "\n")
    print("Frozen continuation:", report["continue_to_raw_price_and_venue_validation"])


if __name__ == "__main__":
    main()
