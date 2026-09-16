"""Freeze the Phase 8 control runs and their dated financing inputs.

The output is deliberately plain CSV and JSON: reviewable in a diff, loadable without
this script, and stable enough for future strategy experiments to compare against.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path

from sillage.backtest.metrics import Performance, analyse, load_risk_free_rates
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.core.money import ZERO, dec
from sillage.data.universe import get_universe
from sillage.execution.financing import FinancingModel, RateCurve
from sillage.strategy.registry import build

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
STRATEGIES = ("momentum", "60-40", "balanced", "spy")


def _download(series: str, start: date, end: date) -> list[tuple[date, Decimal]]:
    url = f"{FRED}?id={series}&cosd={start.isoformat()}&coed={end.isoformat()}"
    with urllib.request.urlopen(url, timeout=30) as response:
        lines = response.read().decode("utf-8").splitlines()
    rows = csv.DictReader(lines)
    return [
        (date.fromisoformat(row["observation_date"]), dec(row[series]) / dec(100))
        for row in rows
        if row[series] not in ("", ".")
    ]


def _write_curve(path: Path, observations: list[tuple[date, Decimal]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("date", "annual_rate"))
        writer.writerows((day.isoformat(), str(rate)) for day, rate in observations)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric_row(performance: Performance) -> dict[str, object]:
    metrics = performance.metrics
    trading = performance.trading
    return {
        "label": metrics.label,
        "start": metrics.start.isoformat(),
        "end": metrics.end.isoformat(),
        "sessions": len(performance.nav),
        "final_nav": str(metrics.final_nav),
        "cagr": metrics.cagr,
        "volatility": metrics.volatility,
        "sharpe_excess": metrics.sharpe,
        "sharpe_zero_rate": metrics.raw_sharpe,
        "sortino": metrics.sortino,
        "max_drawdown": metrics.max_drawdown,
        "longest_drawdown_days": metrics.longest_drawdown_days,
        "time_underwater": metrics.time_underwater,
        "worst_month": metrics.worst_month,
        "annual_turnover": trading.annual_turnover,
        "cost_drag": trading.cost_drag,
        "financing_return": trading.financing_return,
        "average_exposure": trading.average_exposure,
        "fills": trading.fills,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2005, 1, 3))
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--output-root", type=Path, default=Path("research"))
    parser.add_argument("--refresh-rates", action="store_true")
    args = parser.parse_args()

    inputs = args.output_root / "inputs"
    cash_path = inputs / "usd_3m_treasury.csv"
    margin_path = inputs / "usd_ibkr_pro_margin_proxy.csv"
    if args.refresh_rates:
        cash = _download("DGS3MO", args.start, args.end)
        benchmark = _download("DFF", args.start, args.end)
        # IBKR Pro's first USD margin tier is benchmark + 1.5%. DFF is a transparent
        # historical proxy for that benchmark; this is not claimed as an IBKR archive.
        margin = [(day, max(ZERO, rate) + dec("0.015")) for day, rate in benchmark]
        _write_curve(cash_path, cash)
        _write_curve(margin_path, margin)

    cash_rates = load_risk_free_rates(cash_path)
    margin_rates = load_risk_free_rates(margin_path)
    financing = FinancingModel(
        RateCurve.from_series(cash_rates), RateCurve.from_series(margin_rates)
    )
    universe = get_universe("core")
    rows = []
    for name in STRATEGIES:
        result = run_backtest(
            BacktestConfig(
                strategy=build(name, universe),
                universe=universe,
                start=args.start,
                end=args.end,
                initial_cash=dec(100_000),
                data_root=args.data_root,
                financing=financing,
            )
        )
        rows.append({"strategy": name, **_metric_row(analyse(result, risk_free_rate=cash_rates))})

    payload = {
        "schema_version": 1,
        "period": {"start": args.start.isoformat(), "end": args.end.isoformat()},
        "universe": "core",
        "initial_cash": "100000",
        "cost_scale": 1.0,
        "no_trade_band": 0.2,
        "rates": {
            "cash_and_sharpe": {
                "series": "DGS3MO",
                "source": "Federal Reserve Board H.15 via FRED",
                "sha256": _hash(cash_path),
            },
            "margin": {
                "series": "DFF + 1.5 percentage points",
                "source": "Federal Reserve DFF via FRED; IBKR Pro first-tier spread",
                "sha256": _hash(margin_path),
                "note": "Historical proxy, not an archived IBKR customer-rate series.",
            },
        },
        "results": rows,
    }
    output = args.output_root / "baselines.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
