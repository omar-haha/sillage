"""Candidate D input/cost review only: no rankings, alpha curves or broker orders."""

import argparse
import hashlib
import json
from math import floor
from pathlib import Path

import exchange_calendars as xcals
import pandas as pd

from sillage.data.store import BarStore
from sillage.strategy.sector_neutral import SECTOR_ETFS

CBOE_VIX = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"


def cost_budget(
    capital: float,
    allocation: float,
    *,
    spread_bps: float = 4,
    borrow_rate: float = 0.01,
    days: int = 2,
) -> dict:
    """Four-leg round trip: eight $1 minimum tickets, before impact/extra fees.

    Flat $1 is an illustrative commission floor matching observed paper tickets,
    not a universal fee schedule. Spread is a declared assumption, not a quote.
    All basis points are relative to allocated sleeve capital (100% gross).
    """
    sleeve = capital * allocation
    if sleeve <= 0 or spread_bps < 0 or borrow_rate < 0 or days < 0:
        raise ValueError("invalid budget")
    commission = 8.0
    spread = sleeve * spread_bps / 10000
    borrow = sleeve * 0.5 * borrow_rate * days / 365
    return {
        "capital": capital,
        "allocation": allocation,
        "sleeve_capital": sleeve,
        "equal_leg_dollars": sleeve / 4,
        "commission_floor": commission,
        "spread_assumption_bps": spread_bps,
        "borrow_calendar_days": days,
        "estimated_round_trip_dollars": commission + spread + borrow,
        "break_even_bps_of_sleeve": (commission + spread + borrow) / sleeve * 10000,
        "illustrative_100_dollar_etf_shares_per_leg": floor(sleeve / 4 / 100),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-vix", action="store_true")
    args = parser.parse_args()
    root = Path("data/reversal-research")
    store = BarStore("data/sector-research")
    vix_path = root / "vix.parquet"
    if args.download_vix:
        vix = pd.read_csv(CBOE_VIX)
        vix.columns = vix.columns.str.strip().str.lower()
        vix.index = pd.to_datetime(vix.pop("date"), format="%m/%d/%Y", utc=True)
        root.mkdir(parents=True, exist_ok=True)
        vix.to_parquet(vix_path)
    frames = {s: pd.read_parquet(store.path_for(s)) for s in (*SECTOR_ETFS, "SPY")}
    frames["VIX"] = pd.read_parquet(vix_path)
    calendar = xcals.get_calendar("XNYS", start="2005-01-03", end="2026-09-16")
    expected = pd.DatetimeIndex(calendar.sessions_in_range("2005-01-03", "2026-09-16"))
    expected = expected.tz_localize("UTC") if expected.tz is None else expected.tz_convert("UTC")
    coverage = {}
    for s, frame in frames.items():
        frame.index = pd.to_datetime(frame.index, utc=True).normalize()
        sample = frame.reindex(expected)
        missing = sample.index[sample["close"].isna()]
        coverage[s] = {
            "expected_sessions": len(expected),
            "missing_close_sessions": len(missing),
            "missing_dates": [x.date().isoformat() for x in missing],
            "invalid_close_count": int((sample["close"] <= 0).sum()),
            "sha256": hashlib.sha256(
                (vix_path if s == "VIX" else store.path_for(s)).read_bytes()
            ).hexdigest(),
        }
    report = {
        "status": "daily-bar cost screen conditionally feasible; not execution validated",
        "period": ["2005-01-03", "2026-09-16"],
        "coverage": coverage,
        "vix_source": CBOE_VIX,
        "historical_quotes_available": False,
        "venue_shortability_and_margin_verified": False,
        "budgets": [cost_budget(n, a) for n in (25000, 100000) for a in (0.1, 0.2, 0.3)],
        "stress_budgets": [
            cost_budget(n, 0.2, spread_bps=20, borrow_rate=0.04, days=4) for n in (25000, 100000)
        ],
        "limitations": [
            "Not an observed strategy edge or turnover estimate.",
            "Equal-dollar budget is illustrative; final beta sizing may be asymmetric.",
            "Impact, additional fees, adverse opening gaps and recalls are not included in these floors.",
            "A $1 minimum is an assumption; per-share and venue fees may make larger tickets cost more.",
            "Paper account buying power exceeds the virtual $25K budget; cannot certify live $25K admission.",
            "VIX closes must be available at actual decision time, not ETF closing bell by assumption.",
        ],
    }
    if any(x["missing_close_sessions"] or x["invalid_close_count"] for x in coverage.values()):
        report["status"] = "input coverage needs review before freezing"
    Path("research/reversal-feasibility.json").write_text(json.dumps(report, indent=2) + "\n")
    print(report["status"])
    print("Missing sessions:", {s: x["missing_close_sessions"] for s, x in coverage.items()})
    for row in report["budgets"]:
        print(
            row["capital"],
            row["allocation"],
            "round-trip floor bps:",
            round(row["break_even_bps_of_sleeve"], 2),
        )


if __name__ == "__main__":
    main()
