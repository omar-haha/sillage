"""Frozen Candidate E mechanism screen: private source data, aggregate report only."""

import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sillage.backtest.factor_screen import diagnose, monthly_nav_returns, read_aqr
from sillage.backtest.metrics import nav_series
from sillage.backtest.runner import BacktestConfig, run_backtest
from sillage.core.money import dec
from sillage.data.store import BarStore
from sillage.data.universe import get_universe
from sillage.strategy.registry import build


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    spec_path = Path("research/value-quality-screen-spec.json")
    spec = json.loads(spec_path.read_text())
    root = Path("data/value-quality-research")
    universe = get_universe("core")
    store = BarStore(Path("data"))
    paths = {name: root / (name + ".xlsx") for name in ("vme", "qmj")}
    paths.update({item.symbol: store.path_for(item.symbol) for item in universe.all_instruments})
    actual_hashes = {name: sha(path) for name, path in paths.items()}
    frozen_hashes = json.loads(Path("research/value-quality-inputs.json").read_text())
    if actual_hashes != frozen_hashes:
        raise ValueError("research input vintage changed; do not silently restate the experiment")
    value = read_aqr(root / "vme.xlsx", "VME Factors", "VALLS_VME_US90", start=spec["start_month"])
    quality = read_aqr(root / "qmj.xlsx", "QMJ Factors", "USA", start=spec["start_month"])
    market = read_aqr(root / "qmj.xlsx", "MKT", "USA", start=spec["start_month"])
    rf = read_aqr(root / "qmj.xlsx", "RF", "Risk Free Rate", start=spec["start_month"])
    end = min(
        pd.Period(spec["last_possible_month"], freq="M"),
        value.index[-1],
        quality.index[-1],
        market.index[-1],
        rf.index[-1],
    )
    start = spec["start_month"]
    index = pd.period_range(start, end, freq="M")
    frame = pd.DataFrame({"value": value, "quality": quality, "market": market, "rf": rf}).reindex(
        index
    )
    if frame.isna().any().any():
        raise ValueError("missing factor months; never silently drop them")
    # Hash all core inputs, including cash proxy, before replay. No live refresh.
    hashes = {item.symbol: sha(store.path_for(item.symbol)) for item in universe.all_instruments}
    result = run_backtest(
        BacktestConfig(
            strategy=build("balanced", universe),
            universe=universe,
            start=date(2005, 1, 3),
            end=date(2026, 9, 16),
            initial_cash=dec(25000),
        )
    )
    frame["core_total"] = monthly_nav_returns(nav_series(result.nav_points), start, str(end))
    # QMJ RF uses provider monthly simple rates, aligned with its market excess series.
    frame["core_excess"] = frame.core_total - frame.rf
    frame["composite"] = 0.5 * frame.value + 0.5 * frame.quality
    report = diagnose(frame[["value", "quality", "market", "core_excess", "composite"]], spec)
    report.update(
        {
            "candidate": "E",
            "stage": "academic mechanism diagnostic, not an investable backtest",
            "spec_sha256": sha(spec_path),
            "factor_sha256": {name: sha(root / (name + ".xlsx")) for name in ("vme", "qmj")},
            "core_data_sha256": hashes,
            "common_months": len(frame),
            "start": str(index[0]),
            "end": str(end),
            "factor_units": "decimal monthly long/short excess returns; spreadsheet percentage formatting verified",
            "core": {
                "initial_nav_usd": 25000,
                "cash_interest": "zero; BIL fund returns still apply",
                "margin": False,
                "monthly_excess_rf": "AQR USA RF, subtracted once from core total only",
                "annualized_monthly_excess_sharpe": float(
                    frame.core_excess.mean() / frame.core_excess.std(ddof=1) * np.sqrt(12)
                ),
            },
            "caveats": [
                "Restated academic factors, native differing construction/gross exposures; not a joint long-only value/quality score.",
                "No factor trading costs, historical borrow or investable blend estimated.",
                "Bootstrap and in-sample market residualization are descriptive, not out-of-sample evidence.",
                "Repeated research history is not a fresh holdout; continuation only permits feasibility review.",
            ],
        }
    )
    # No raw provider series are redistributed.
    output = Path("research/value-quality-screen.json")
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(output)
    print(
        "Continue to implementation feasibility:", report["continue_to_implementation_feasibility"]
    )
    print("Composite:", json.dumps(report["diagnostics"]["composite"], indent=2))


if __name__ == "__main__":
    main()
