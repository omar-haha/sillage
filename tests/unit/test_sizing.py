"""Tests for position sizing.

The central assertion in this file is the correlation one. Inverse-volatility sizing
believes five assets are five bets; if they all move together they are one bet, and a
book aimed at 10% volatility will deliver far more. Everything else here supports the
test that proves the covariance step actually catches that.
"""

from __future__ import annotations

import math

import pytest

from sillage.core.money import ZERO, dec
from sillage.portfolio.sizing import (
    SESSIONS_PER_YEAR,
    VolatilityTarget,
    daily_returns,
    diversification_ratio,
    inverse_volatility_weights,
    portfolio_volatility,
    shrunk_covariance,
)


def wave(n: int, amplitude: float, *, phase: int = 0) -> list[float]:
    """A deterministic oscillating return series with a known standard deviation."""
    return [amplitude * (1 if (i + phase) % 2 == 0 else -1) for i in range(n)]


def drift(n: int, amplitude: float, seed: int = 0) -> list[float]:
    """Pseudo-random returns that are reproducible without importing numpy."""
    values, state = [], seed * 7919 + 1
    for _ in range(n):
        state = (state * 1103515245 + 12345) % 2147483648
        values.append(amplitude * ((state / 2147483648) - 0.5))
    return values


# ------------------------------------------------------------------ inverse vol


def test_the_quieter_asset_gets_the_bigger_weight() -> None:
    weights = inverse_volatility_weights({"calm": 0.005, "wild": 0.020})
    assert weights["calm"] > weights["wild"]
    assert float(weights["calm"]) == pytest.approx(4 * float(weights["wild"]), rel=1e-5)


def test_weights_sum_to_one() -> None:
    weights = inverse_volatility_weights({"a": 0.01, "b": 0.02, "c": 0.03})
    assert float(sum(weights.values())) == pytest.approx(1.0, abs=1e-5)


def test_equal_volatility_gives_equal_weights() -> None:
    weights = inverse_volatility_weights({"a": 0.01, "b": 0.01})
    assert weights["a"] == weights["b"]


def test_a_motionless_asset_is_dropped_rather_than_given_infinite_weight() -> None:
    weights = inverse_volatility_weights({"a": 0.01, "frozen": 0.0})
    assert "frozen" not in weights
    assert float(weights["a"]) == pytest.approx(1.0, abs=1e-5)


# ------------------------------------------------------------------ covariance


def test_the_diagonal_is_the_variance() -> None:
    series = {"a": drift(120, 0.02, seed=1)}
    covariance = shrunk_covariance(series, shrinkage=0.0)
    mean = sum(series["a"]) / len(series["a"])
    expected = sum((x - mean) ** 2 for x in series["a"]) / (len(series["a"]) - 1)
    assert covariance[("a", "a")] == pytest.approx(expected)


def test_covariance_is_symmetric() -> None:
    covariance = shrunk_covariance({"a": drift(80, 0.02, 1), "b": drift(80, 0.02, 2)})
    assert covariance[("a", "b")] == covariance[("b", "a")]


def test_opposite_series_have_negative_covariance() -> None:
    covariance = shrunk_covariance(
        {"up": wave(60, 0.01), "down": wave(60, 0.01, phase=1)}, shrinkage=0.0
    )
    assert covariance[("up", "down")] < 0


def test_shrinkage_pulls_correlations_toward_the_average() -> None:
    """Damps the noisy extremes without moving the average level."""
    series = {"a": drift(80, 0.02, 1), "b": drift(80, 0.02, 2), "c": drift(80, 0.02, 3)}
    raw = shrunk_covariance(series, shrinkage=0.0)
    shrunk = shrunk_covariance(series, shrinkage=0.5)

    def correlations(cov: dict[tuple[str, str], float]) -> list[float]:
        pairs = [("a", "b"), ("a", "c"), ("b", "c")]
        return [cov[p] / math.sqrt(cov[(p[0], p[0])] * cov[(p[1], p[1])]) for p in pairs]

    before, after = correlations(raw), correlations(shrunk)
    assert max(after) - min(after) < max(before) - min(before)
    assert sum(after) / 3 == pytest.approx(sum(before) / 3, abs=1e-9)


def test_shrinkage_leaves_variances_alone() -> None:
    """Only the correlations are uncertain enough to be worth shrinking."""
    series = {"a": drift(80, 0.02, 1), "b": drift(80, 0.02, 2)}
    raw = shrunk_covariance(series, shrinkage=0.0)
    shrunk = shrunk_covariance(series, shrinkage=0.9)
    assert shrunk[("a", "a")] == pytest.approx(raw[("a", "a")])


def test_an_empty_book_has_an_empty_covariance() -> None:
    """Reached when every selected asset fails the trend filter."""
    assert shrunk_covariance({}) == {}


# ------------------------------------------------------------------ portfolio vol


def test_a_single_asset_portfolio_has_that_asset_s_volatility() -> None:
    series = {"a": drift(120, 0.02, seed=5)}
    covariance = shrunk_covariance(series, shrinkage=0.0)
    expected = math.sqrt(covariance[("a", "a")] * SESSIONS_PER_YEAR)
    assert portfolio_volatility({"a": dec(1)}, covariance) == pytest.approx(expected)


def test_perfectly_correlated_assets_are_one_bet() -> None:
    """The hole this module exists to close.

    Two identical series held half and half must have the same volatility as either one
    held alone -- there is no diversification whatsoever. Inverse-vol sizing cannot see
    that; the covariance can.
    """
    moves = drift(120, 0.02, seed=9)
    covariance = shrunk_covariance({"a": moves, "b": list(moves)}, shrinkage=0.0)
    half = {"a": dec("0.5"), "b": dec("0.5")}
    assert portfolio_volatility(half, covariance) == pytest.approx(
        portfolio_volatility({"a": dec(1)}, {("a", "a"): covariance[("a", "a")]})
    )
    assert diversification_ratio(half, covariance) == pytest.approx(1.0, abs=1e-9)


def test_uncorrelated_assets_actually_diversify() -> None:
    covariance = shrunk_covariance(
        {"a": drift(200, 0.02, 11), "b": drift(200, 0.02, 77)}, shrinkage=0.0
    )
    half = {"a": dec("0.5"), "b": dec("0.5")}
    assert diversification_ratio(half, covariance) > 1.2


def test_an_empty_book_has_no_volatility() -> None:
    assert portfolio_volatility({}, {}) == 0.0
    assert diversification_ratio({}, {}) == 0.0


# ------------------------------------------------------------------ targeting


def test_a_volatile_book_is_scaled_down_to_the_target() -> None:
    sizer = VolatilityTarget(target=dec("0.10"), lookback=200, min_observations=50)
    sizing = sizer.size({"a": drift(200, 0.06, 3), "b": drift(200, 0.06, 4)})
    assert sizing.ex_ante_volatility > 0.10
    assert sizing.scale < 1.0
    assert not sizing.capped
    assert float(sizing.gross) == pytest.approx(sizing.scale, abs=1e-4)


def test_a_calm_book_is_capped_rather_than_levered() -> None:
    """The documented asymmetry: the target is a ceiling, not a floor."""
    sizer = VolatilityTarget(target=dec("0.10"), lookback=200, min_observations=50)
    sizing = sizer.size({"a": drift(200, 0.001, 3), "b": drift(200, 0.001, 4)})
    assert sizing.ex_ante_volatility < 0.10
    assert sizing.scale == 1.0
    assert sizing.capped


def test_the_realised_target_is_actually_hit() -> None:
    sizer = VolatilityTarget(target=dec("0.08"), lookback=250, min_observations=50)
    returns = {"a": drift(250, 0.05, 21), "b": drift(250, 0.03, 22)}
    sizing = sizer.size(returns)
    covariance = shrunk_covariance(returns, sizer.shrinkage)
    assert portfolio_volatility(sizing.weights, covariance) == pytest.approx(0.08, abs=1e-6)


def test_assets_without_enough_history_are_dropped_not_guessed_at() -> None:
    sizer = VolatilityTarget(lookback=60, min_observations=40)
    sizing = sizer.size({"long": drift(60, 0.02, 1), "short": drift(5, 0.02, 2)})
    assert set(sizing.weights) == {"long"}


def test_nothing_usable_gives_nothing() -> None:
    sizing = VolatilityTarget().size({"short": [0.01, 0.02]})
    assert sizing.weights == {}
    assert sizing.gross == ZERO


@pytest.mark.parametrize(
    "bad", [{"target": dec(0)}, {"max_scale": 0.0}, {"shrinkage": 1.5}, {"min_observations": 1}]
)
def test_rejects_nonsensical_settings(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        VolatilityTarget(**bad)  # type: ignore[arg-type]


# ------------------------------------------------------------------ returns


def test_daily_returns_are_session_over_session() -> None:
    assert daily_returns([dec(100), dec(110), dec(99)]) == pytest.approx([0.10, -0.10])


def test_a_single_price_produces_no_returns() -> None:
    assert daily_returns([dec(100)]) == []
