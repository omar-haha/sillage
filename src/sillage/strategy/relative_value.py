"""Frozen causal signal for Candidate B, liquid ETF relative value.

The regression window ends at yesterday's close. Today's close is used only to score
the already-fitted spread, so a decision at today's close cannot rewrite its own model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

import numpy as np

from sillage.core.money import dec


@dataclass(frozen=True, slots=True)
class RelativeValueConfig:
    lookback: int = 252
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_holding_sessions: int = 42
    max_pair_gross: float = 0.20
    max_total_gross: float = 0.60
    stationarity_t: float = -3.34
    max_hedge_ratio_change: float = 0.25

    def __post_init__(self) -> None:
        if self.lookback < 20:
            raise ValueError("relative-value lookback must be at least 20 sessions")
        if not 0 <= self.exit_z < self.entry_z < self.stop_z:
            raise ValueError("z-score thresholds must satisfy 0 <= exit < entry < stop")
        if self.max_holding_sessions < 1:
            raise ValueError("maximum holding period must be positive")
        if not 0 < self.max_pair_gross <= self.max_total_gross <= 1:
            raise ValueError("gross limits must satisfy 0 < pair <= total <= 1")
        if self.max_hedge_ratio_change < 0:
            raise ValueError("hedge-ratio tolerance must not be negative")


@dataclass(frozen=True, slots=True)
class RelativeValueDecision:
    eligible: bool
    reason: str
    hedge_ratio: float
    first_half_ratio: float
    second_half_ratio: float
    stationarity_t: float
    zscore: float
    weights: dict[str, Decimal]


def decide_pair(
    left_symbol: str,
    right_symbol: str,
    left_prices: Sequence[float],
    right_prices: Sequence[float],
    config: RelativeValueConfig | None = None,
) -> RelativeValueDecision:
    """Fit through T-1, score T, and return beta-hedged entry weights if admitted."""
    cfg = config or RelativeValueConfig()
    required = cfg.lookback + 1
    if len(left_prices) < required or len(right_prices) < required:
        return _rejected("insufficient history")

    left = np.log(np.asarray(left_prices[-required:], dtype=float))
    right = np.log(np.asarray(right_prices[-required:], dtype=float))
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        return _rejected("non-finite price")
    training_left, current_left = left[:-1], float(left[-1])
    training_right, current_right = right[:-1], float(right[-1])
    intercept, beta = _ols(training_left, training_right)
    half = cfg.lookback // 2
    _, first_beta = _ols(training_left[:half], training_right[:half])
    _, second_beta = _ols(training_left[-half:], training_right[-half:])
    residuals = training_left - (intercept + beta * training_right)
    deviation = float(np.std(residuals, ddof=1))
    adf_t = _adf_t(residuals)
    current_residual = current_left - (intercept + beta * current_right)
    zscore = current_residual / deviation if deviation > 0 else 0.0

    if min(beta, first_beta, second_beta) <= 0:
        return _decision(
            False, "non-positive hedge ratio", beta, first_beta, second_beta, adf_t, zscore
        )
    change = abs(second_beta - first_beta) / max(abs(first_beta), abs(second_beta))
    if change > cfg.max_hedge_ratio_change:
        return _decision(
            False, "unstable hedge ratio", beta, first_beta, second_beta, adf_t, zscore
        )
    if not np.isfinite(adf_t) or adf_t > cfg.stationarity_t:
        return _decision(
            False, "residual is not stationary", beta, first_beta, second_beta, adf_t, zscore
        )
    weights = _entry_weights(left_symbol, right_symbol, zscore, beta, cfg)
    return _decision(True, "eligible", beta, first_beta, second_beta, adf_t, zscore, weights)


def _ols(left: np.ndarray, right: np.ndarray) -> tuple[float, float]:
    design = np.column_stack((np.ones(len(right)), right))
    intercept, beta = np.linalg.lstsq(design, left, rcond=None)[0]
    return float(intercept), float(beta)


def _adf_t(residuals: np.ndarray) -> float:
    """ADF(0) t-statistic for delta(e_t) = alpha + gamma*e_(t-1)."""
    lagged = residuals[:-1]
    changes = np.diff(residuals)
    if len(changes) < 3:
        return float("inf")
    design = np.column_stack((np.ones(len(lagged)), lagged))
    coefficients = np.linalg.lstsq(design, changes, rcond=None)[0]
    errors = changes - design @ coefficients
    degrees = len(changes) - design.shape[1]
    if degrees <= 0:
        return float("inf")
    variance = float(errors @ errors) / degrees
    covariance = variance * np.linalg.pinv(design.T @ design)
    standard_error = sqrt(max(float(covariance[1, 1]), 0.0))
    return float(coefficients[1] / standard_error) if standard_error else float("-inf")


def _entry_weights(
    left: str, right: str, zscore: float, beta: float, config: RelativeValueConfig
) -> dict[str, Decimal]:
    if abs(zscore) < config.entry_z:
        return {}
    direction = -1 if zscore > 0 else 1
    left_weight = config.max_pair_gross / (1.0 + beta)
    right_weight = config.max_pair_gross - left_weight
    return {
        left: dec(direction * left_weight).quantize(dec("0.000001")),
        right: dec(-direction * right_weight).quantize(dec("0.000001")),
    }


def _rejected(reason: str) -> RelativeValueDecision:
    return RelativeValueDecision(False, reason, 0.0, 0.0, 0.0, float("inf"), 0.0, {})


def _decision(
    eligible: bool,
    reason: str,
    beta: float,
    first_beta: float,
    second_beta: float,
    adf_t: float,
    zscore: float,
    weights: dict[str, Decimal] | None = None,
) -> RelativeValueDecision:
    return RelativeValueDecision(
        eligible, reason, beta, first_beta, second_beta, adf_t, zscore, weights or {}
    )
