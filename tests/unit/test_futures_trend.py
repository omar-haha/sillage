"""Tests for the frozen Phase 8 futures-trend research component."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sillage.core.money import ZERO
from sillage.strategy.futures_trend import FuturesTrendConfig, decide

QUICK = FuturesTrendConfig(
    lookbacks=(5, 10, 20), skip_sessions=2, volatility_span=20, volatility_floor=0.01
)


def path(drift: float, *, sessions: int = 40, shock: float = 0.003) -> list[float]:
    return [drift + (shock if index % 2 else -shock) for index in range(sessions)]


def test_three_horizon_vote_goes_long_and_short() -> None:
    result = decide({"UP": path(0.002), "DOWN": path(-0.002)}, QUICK)
    assert result.directions == {"UP": 1, "DOWN": -1}
    assert result.weights["UP"] > ZERO
    assert result.weights["DOWN"] < ZERO


def test_the_most_recent_sessions_are_skipped() -> None:
    steady = path(0.002)
    shocked = decide({"A": [*steady[:-2], -0.50, -0.50]}, QUICK)
    baseline = decide({"A": steady}, QUICK)
    assert baseline.horizon_returns["A"] == shocked.horizon_returns["A"]
    assert shocked.directions["A"] == 1


def test_a_tied_vote_is_flat() -> None:
    config = FuturesTrendConfig(
        lookbacks=(2, 4), skip_sessions=0, volatility_span=4, volatility_floor=0.01
    )
    result = decide({"TIE": [-0.10, -0.10, 0.05, 0.05]}, config)
    assert result.directions["TIE"] == 0
    assert result.weights == {}


def test_risk_is_equal_before_the_portfolio_scale() -> None:
    result = decide(
        {"QUIET": path(0.001, shock=0.002), "LOUD": path(0.002, shock=0.008)}, QUICK
    )
    quiet_risk = abs(float(result.weights["QUIET"])) * result.annualized_volatility["QUIET"]
    loud_risk = abs(float(result.weights["LOUD"])) * result.annualized_volatility["LOUD"]
    assert quiet_risk == pytest.approx(loud_risk, rel=1e-4)


def test_gross_exposure_never_exceeds_the_frozen_cap() -> None:
    low_volatility = FuturesTrendConfig(
        lookbacks=(5, 10, 20),
        skip_sessions=2,
        volatility_span=20,
        volatility_floor=0.0001,
        target_volatility=1.0,
        max_gross=2.0,
    )
    result = decide(
        {"A": path(0.001, shock=0.00001), "B": path(0.001, shock=0.00001)}, low_volatility
    )
    assert result.capped
    assert result.gross <= Decimal("2.000001")


def test_volatility_is_floored_before_sizing() -> None:
    result = decide({"FLAT_UP": [0.001] * 40}, QUICK)
    assert result.annualized_volatility["FLAT_UP"] == QUICK.volatility_floor


def test_short_history_is_reported_not_invented() -> None:
    result = decide({"READY": path(0.001), "NEW": path(0.001, sessions=10)}, QUICK)
    assert result.unavailable == ("NEW",)
    assert "NEW" not in result.directions


@pytest.mark.parametrize(
    "bad",
    [
        {"lookbacks": ()},
        {"lookbacks": (0,)},
        {"skip_sessions": -1},
        {"volatility_span": 1},
        {"volatility_floor": 0.0},
        {"target_volatility": 0.0},
        {"max_gross": 0.0},
    ],
)
def test_invalid_configuration_is_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FuturesTrendConfig(**bad)  # type: ignore[arg-type]


def test_documented_defaults_are_frozen() -> None:
    config = FuturesTrendConfig()
    assert config.lookbacks == (63, 126, 252)
    assert config.skip_sessions == 5
    assert config.volatility_span == 63
    assert config.volatility_floor == 0.05
    assert config.target_volatility == 0.10
    assert config.max_gross == 2.0
