"""Research-only quality allocation; never registered as a live strategy."""

from datetime import date

import numpy as np
import pandas as pd

from sillage.backtest.metrics import _excess_returns, from_nav
from sillage.core.money import dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import Universe, get_universe
from sillage.strategy.benchmarks import buy_and_hold
from sillage.strategy.blend import Blend
from sillage.strategy.registry import build


def validate_sessions(frame: pd.DataFrame, expected: pd.DatetimeIndex) -> None:
    """Require exact research calendar coverage, independent of timestamp precision."""
    actual = frame.loc[(frame.index >= expected[0]) & (frame.index <= expected[-1])]
    if (
        not actual.index.as_unit("ns").equals(expected.as_unit("ns"))
        or not np.isfinite(actual.to_numpy()).all()
    ):
        raise ValueError("incomplete/nonfinite session evidence")
    if (actual[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("nonpositive prices")


def quality_portfolio(weight: float, *, symbol: str = "QUAL"):
    """Keep QUAL outside the core signal universe; only execution gets the union."""
    if not np.isfinite(weight) or not 0 <= weight <= 1 or symbol not in ("QUAL", "SPY"):
        raise ValueError("invalid research allocation")
    core = get_universe("core")
    instrument = Instrument(
        "QUAL", AssetClass.ETF, currency="USD", exchange="BATS", available_from=date(2013, 7, 16)
    )
    execution = Universe("core-quality-research", (*core.instruments, instrument), core.cash_proxy)
    if weight == 0:
        strategy = build("balanced", core)
    elif weight == 1:
        strategy = buy_and_hold(symbol)
    else:
        strategy = Blend(
            [build("balanced", core), buy_and_hold(symbol)],
            [dec(1) - dec(weight), dec(weight)],
            name=f"core-{symbol}-{weight}",
        )
    return strategy, execution


def paired_diagnostics(
    core: pd.Series,
    candidate: pd.Series,
    rates: pd.Series,
    *,
    blocks=252,
    replicates=2000,
    seed=1102026,
) -> dict:
    """Report matched daily bootstrap uncertainty; no optimization or promotion."""
    if not core.index.equals(candidate.index) or len(core) < 4:
        raise ValueError("NAVs must have identical dates and enough observations")
    if (
        (core <= 0).any()
        or (candidate <= 0).any()
        or not np.isfinite(np.column_stack((core, candidate))).all()
    ):
        raise ValueError("NAVs must be finite and positive")
    core_returns = core.pct_change().dropna()
    candidate_returns = candidate.pct_change().dropna()
    ar, br = core_returns.to_numpy(), candidate_returns.to_numpy()
    a = _excess_returns(core_returns, rates).to_numpy()
    b = _excess_returns(candidate_returns, rates).to_numpy()

    def sharpe(x, total_returns):
        # Same convention as from_nav: excess numerator, total-return volatility.
        vol = total_returns.std(ddof=1)
        if vol <= 0:
            raise ValueError("constant excess returns cannot support uncertainty estimates")
        return float(x.mean() / vol * np.sqrt(252))

    if blocks < 1 or replicates < 1:
        raise ValueError("invalid bootstrap settings")
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(replicates):
        starts = rng.integers(0, len(a), size=int(np.ceil(len(a) / blocks)))
        indices = ((starts[:, None] + np.arange(blocks)) % len(a)).ravel()[: len(a)]
        diffs.append(sharpe(b[indices], br[indices]) - sharpe(a[indices], ar[indices]))
    halfway = (len(core) - 1) // 2
    halves = (
        (core.iloc[: halfway + 1], candidate.iloc[: halfway + 1]),
        (core.iloc[halfway:], candidate.iloc[halfway:]),
    )
    tail = a <= np.quantile(a, 0.1)
    return {
        "excess_sharpe_improvement": sharpe(b, br) - sharpe(a, ar),
        "paired_block_sharpe_improvement_interval_95": np.quantile(diffs, [0.025, 0.975]).tolist(),
        "half_excess_sharpe_improvements": [
            from_nav(y, risk_free_rate=rates).sharpe - from_nav(x, risk_free_rate=rates).sharpe
            for x, y in halves
        ],
        "half_periods": [
            {"start": str(x.index[0].date()), "end": str(x.index[-1].date())} for x, _ in halves
        ],
        "daily_excess_return_correlation": float(np.corrcoef(a, b)[0, 1]),
        "core_worst_daily_decile": {
            "sessions": int(tail.sum()),
            "core_mean_excess": float(a[tail].mean()),
            "candidate_mean_excess": float(b[tail].mean()),
        },
    }


def acceptance(rows: dict, limits: dict) -> dict:
    """Frozen criteria: primary cannot be replaced by a better neighbor."""
    core, primary, spy = (rows[k]["metrics"] for k in ("core", "primary", "spy_substitution"))
    comparison = rows["primary"]["comparison"]
    checks = {
        "primary_sharpe": primary["sharpe"] - core["sharpe"]
        >= limits["minimum_full_excess_sharpe_improvement_vs_core"],
        "quality_not_just_extra_equities": primary["sharpe"] - spy["sharpe"]
        >= limits["minimum_full_excess_sharpe_improvement_vs_spy_substitution"],
        "both_halves": min(comparison["half_excess_sharpe_improvements"])
        >= limits["minimum_half_excess_sharpe_improvements_vs_core"],
        "drawdown_depth": primary["max_drawdown"]
        >= core["max_drawdown"] - limits["maximum_extra_drawdown_depth"],
        "drawdown_duration": primary["longest_drawdown_days"]
        <= core["longest_drawdown_days"] * limits["maximum_drawdown_duration_ratio"],
        "neighbors": min(
            rows[k]["metrics"]["sharpe"] - core["sharpe"] for k in ("quality_10", "quality_30")
        )
        >= limits["minimum_neighbor_excess_sharpe_improvements_vs_core"],
        "cost_stress": rows["primary_2x"]["metrics"]["sharpe"]
        - rows["core_2x"]["metrics"]["sharpe"]
        >= limits["minimum_2x_cost_excess_sharpe_improvement_vs_2x_core"],
    }
    return {"checks": checks, "continue_to_raw_price_and_venue_validation": all(checks.values())}
