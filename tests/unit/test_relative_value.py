"""Tests for the frozen Candidate B relative-value signal."""

from __future__ import annotations

import numpy as np
import pytest

from sillage.strategy.relative_value import RelativeValueConfig, decide_pair


def cointegrated(*, shock: float = 0.0, beta: float = 1.1) -> tuple[list[float], list[float]]:
    index = np.arange(253)
    right_log = 4.5 + index * 0.0005
    stationary = np.asarray([0.008 if i % 2 else -0.008 for i in index])
    left_log = 0.2 + beta * right_log + stationary
    left_log[-1] += shock
    return np.exp(left_log).tolist(), np.exp(right_log).tolist()


def test_current_close_is_scored_without_refitting_the_model() -> None:
    ordinary = decide_pair("LEFT", "RIGHT", *cointegrated())
    shocked = decide_pair("LEFT", "RIGHT", *cointegrated(shock=0.08))
    assert shocked.hedge_ratio == pytest.approx(ordinary.hedge_ratio)
    assert shocked.stationarity_t == pytest.approx(ordinary.stationarity_t)
    assert shocked.zscore > ordinary.zscore


def test_positive_divergence_shorts_left_and_buys_right() -> None:
    result = decide_pair("LEFT", "RIGHT", *cointegrated(shock=0.08))
    assert result.eligible
    assert result.weights["LEFT"] < 0
    assert result.weights["RIGHT"] > 0
    assert float(sum(abs(weight) for weight in result.weights.values())) == pytest.approx(0.20)


def test_no_entry_inside_the_frozen_threshold() -> None:
    result = decide_pair("LEFT", "RIGHT", *cointegrated())
    assert result.eligible
    assert result.weights == {}


def test_an_unstable_hedge_ratio_is_rejected() -> None:
    left, right = cointegrated()
    right_log = np.log(right)
    left_log = np.log(left)
    left_log[126:-1] = (
        0.2
        + 1.8 * right_log[126:-1]
        + np.asarray([0.008 if i % 2 else -0.008 for i in range(126, 252)])
    )
    result = decide_pair("LEFT", "RIGHT", np.exp(left_log).tolist(), right)
    assert not result.eligible
    assert result.reason == "unstable hedge ratio"


def test_short_history_is_reported_not_backfilled() -> None:
    result = decide_pair("LEFT", "RIGHT", [100.0] * 100, [100.0] * 100)
    assert not result.eligible
    assert result.reason == "insufficient history"


@pytest.mark.parametrize(
    "change",
    [
        {"lookback": 10},
        {"entry_z": 0.4},
        {"exit_z": -0.1},
        {"stop_z": 2.0},
        {"max_holding_sessions": 0},
        {"max_pair_gross": 0.7},
        {"max_hedge_ratio_change": -0.1},
    ],
)
def test_invalid_configuration_is_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RelativeValueConfig(**change)  # type: ignore[arg-type]


def test_round_one_defaults_are_frozen() -> None:
    config = RelativeValueConfig()
    assert config.lookback == 252
    assert config.entry_z == 2.0
    assert config.exit_z == 0.5
    assert config.stop_z == 4.0
    assert config.max_holding_sessions == 42
    assert config.max_pair_gross == 0.20
    assert config.max_total_gross == 0.60
    assert config.stationarity_t == -3.34
    assert config.max_hedge_ratio_change == 0.25
