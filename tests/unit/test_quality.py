"""Tests for the data quality checks.

Each test constructs data with one specific defect, because a check that cannot be
shown to fire on a known-bad input is indistinguishable from a check that never fires.
"""

from __future__ import annotations

import pandas as pd
import pytest

from sillage.data.quality import Issue, Severity, check_symbol, summarise


def series(days: list[str], closes: list[float], volume: float = 1e6) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in days], name="ts")
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": volume,
        },
        index=index,
    )


def kinds(issues: list[Issue]) -> set[str]:
    return {i.kind for i in issues}


def test_empty_history_is_an_error() -> None:
    issues = check_symbol("SPY", series([], []))
    assert kinds(issues) == {"empty"}
    assert issues[0].severity is Severity.ERROR


def test_short_history_warns_because_no_signal_can_be_computed() -> None:
    issues = check_symbol("SPY", series(["2024-01-02", "2024-01-03"], [100.0, 101.0]))
    assert "short-history" in kinds(issues)


def test_missing_sessions_are_detected() -> None:
    # 2024-01-03 and 01-04 were both open; skipping them is a real gap.
    issues = check_symbol("SPY", series(["2024-01-02", "2024-01-05"], [100.0, 101.0]))
    assert "missing-sessions" in kinds(issues)


def test_weekends_are_not_reported_as_missing() -> None:
    # Friday to Monday. A naive calendar-day gap check would flag the weekend.
    issues = check_symbol("SPY", series(["2024-01-05", "2024-01-08"], [100.0, 101.0]))
    assert "missing-sessions" not in kinds(issues)


def test_market_holidays_are_not_reported_as_missing() -> None:
    # 2024-01-01 was New Year's Day; the market was shut, so nothing is missing.
    issues = check_symbol("SPY", series(["2023-12-29", "2024-01-02"], [100.0, 101.0]))
    assert "missing-sessions" not in kinds(issues)


def test_unadjusted_split_is_flagged_as_a_price_jump() -> None:
    # A 2:1 split that was never adjusted for looks exactly like a 50% crash.
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    issues = check_symbol("SPY", series(days, [100.0, 100.0, 50.0]))
    assert "price-jump" in kinds(issues)


def test_ordinary_volatility_is_not_flagged() -> None:
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    issues = check_symbol("SPY", series(days, [100.0, 103.0, 99.0]))
    assert "price-jump" not in kinds(issues)


def test_stale_feed_is_flagged_as_a_flatline() -> None:
    days = pd.bdate_range("2024-01-02", periods=10).strftime("%Y-%m-%d").tolist()
    issues = check_symbol("SPY", series(days, [100.0] * 10))
    assert "flatline" in kinds(issues)


def test_zero_volume_sessions_are_reported() -> None:
    days = ["2024-01-02", "2024-01-03"]
    issues = check_symbol("SPY", series(days, [100.0, 101.0], volume=0.0))
    assert "zero-volume" in kinds(issues)


def test_continuous_assets_are_checked_against_a_247_calendar() -> None:
    """The same rows are complete for the NYSE and incomplete for a 24/7 market.

    Friday to Monday with nothing in between. For an ETF that is a full week and there
    is no gap. For crypto, which trades every day, Saturday and Sunday are genuinely
    missing -- and a momentum lookback that quietly spans fewer bars than it thinks is
    exactly the kind of error this check exists to surface.
    """
    days = ["2024-01-05", "2024-01-08"]
    frame = series(days, [100.0, 101.0])
    assert "missing-sessions" not in kinds(check_symbol("SPY", frame, continuous=False))
    assert "missing-sessions" in kinds(check_symbol("BTC-USD", frame, continuous=True))


def test_summarise_counts_by_severity() -> None:
    issues = check_symbol("SPY", series([], []))
    counts = summarise(issues)
    assert counts[Severity.ERROR] == 1
    assert counts[Severity.WARN] == 0


@pytest.mark.parametrize("threshold", [0.10, 0.50])
def test_jump_threshold_is_configurable(threshold: float) -> None:
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frame = series(days, [100.0, 100.0, 70.0])  # a 30% drop
    fired = "price-jump" in kinds(check_symbol("SPY", frame, jump_threshold=threshold))
    assert fired is (threshold < 0.30)
