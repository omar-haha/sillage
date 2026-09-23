"""Frozen Phase 8 diversified time-series trend signal and risk allocation.

This module deliberately stops at *continuous-contract notional targets*. A continuous
series is suitable for measuring a trend, but it is not an executable instrument: the
futures implementation still needs a dated contract chain, multipliers, rolls and margin.
Keeping that boundary explicit prevents a research convenience from leaking into orders.

Inputs are daily excess returns for a long position in each contract family. That
contract-return convention matters for products such as ``10Y``, whose quoted yield rises
when a conventional bond price falls. Direction correction belongs in the contract data,
not in a symbol-specific exception hidden inside the signal.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import prod, sqrt

from sillage.core.money import ZERO, dec
from sillage.portfolio.sizing import SESSIONS_PER_YEAR


@dataclass(frozen=True, slots=True)
class FuturesTrendConfig:
    """Every frozen round-one parameter, stated before candidate performance exists."""

    lookbacks: tuple[int, ...] = (63, 126, 252)
    skip_sessions: int = 5
    volatility_span: int = 63
    volatility_floor: float = 0.05
    target_volatility: float = 0.10
    max_gross: float = 2.0

    def __post_init__(self) -> None:
        if not self.lookbacks or any(window <= 0 for window in self.lookbacks):
            raise ValueError("trend lookbacks must be positive")
        if self.skip_sessions < 0:
            raise ValueError("skip sessions must not be negative")
        if self.volatility_span < 2:
            raise ValueError("volatility span must be at least two sessions")
        if self.volatility_floor <= 0.0:
            raise ValueError("volatility floor must be positive")
        if self.target_volatility <= 0.0:
            raise ValueError("target volatility must be positive")
        if self.max_gross <= 0.0:
            raise ValueError("gross cap must be positive")

    @property
    def required_sessions(self) -> int:
        return max(max(self.lookbacks) + self.skip_sessions, self.volatility_span)


@dataclass(frozen=True, slots=True)
class FuturesTrendDecision:
    """A reproducible signal decision, including why each exposure has its size."""

    directions: dict[str, int]
    horizon_returns: dict[str, tuple[float, ...]]
    annualized_volatility: dict[str, float]
    weights: dict[str, Decimal]
    forecast_volatility: float
    gross: Decimal
    scale: float
    capped: bool
    unavailable: tuple[str, ...]


def decide(
    excess_returns: Mapping[str, Sequence[float]],
    config: FuturesTrendConfig | None = None,
) -> FuturesTrendDecision:
    """Vote on direction, equalise standalone risk, then target portfolio volatility."""
    cfg = config or FuturesTrendConfig()
    usable = {
        symbol: list(series)
        for symbol, series in excess_returns.items()
        if len(series) >= cfg.required_sessions
    }
    unavailable = tuple(sorted(set(excess_returns) - set(usable)))
    horizon_returns = {
        symbol: tuple(
            _horizon_return(series, window, cfg.skip_sessions) for window in cfg.lookbacks
        )
        for symbol, series in usable.items()
    }
    directions = {symbol: _vote(values) for symbol, values in horizon_returns.items()}
    volatilities = {
        symbol: max(
            _ewma_annualized_volatility(series[-cfg.volatility_span :], cfg.volatility_span),
            cfg.volatility_floor,
        )
        for symbol, series in usable.items()
    }
    active = {symbol: direction for symbol, direction in directions.items() if direction}
    if not active:
        return FuturesTrendDecision(
            directions=directions,
            horizon_returns=horizon_returns,
            annualized_volatility=volatilities,
            weights={},
            forecast_volatility=0.0,
            gross=ZERO,
            scale=0.0,
            capped=False,
            unavailable=unavailable,
        )

    # A unit of preliminary weight contributes the same standalone risk in every
    # active market. Covariance governs one portfolio-wide scalar, never the relative
    # allocation, avoiding a noisy mean-variance optimisation.
    preliminary = {
        symbol: direction / (len(active) * volatilities[symbol])
        for symbol, direction in active.items()
    }
    covariance = _ewma_covariance(
        {symbol: usable[symbol][-cfg.volatility_span :] for symbol in active},
        cfg.volatility_span,
    )
    unscaled_volatility = _portfolio_volatility(preliminary, covariance)
    # A perfectly offset synthetic book can estimate to zero variance. It must not be
    # levered without bound, nor silently turned off: in that edge case the explicit
    # gross limit is the only honest constraint available.
    wanted_scale = (
        cfg.target_volatility / unscaled_volatility
        if unscaled_volatility > 0.0
        else float("inf")
    )
    preliminary_gross = sum(abs(weight) for weight in preliminary.values())
    gross_scale = cfg.max_gross / preliminary_gross if preliminary_gross > 0.0 else 0.0
    scale = min(wanted_scale, gross_scale)
    capped = gross_scale < wanted_scale
    weights = {
        symbol: dec(weight * scale).quantize(dec("0.000001"))
        for symbol, weight in preliminary.items()
    }
    gross = sum((abs(weight) for weight in weights.values()), start=ZERO)
    return FuturesTrendDecision(
        directions=directions,
        horizon_returns=horizon_returns,
        annualized_volatility=volatilities,
        weights=weights,
        forecast_volatility=unscaled_volatility * scale,
        gross=gross,
        scale=scale,
        capped=capped,
        unavailable=unavailable,
    )


def _horizon_return(returns: Sequence[float], window: int, skip: int) -> float:
    end = len(returns) - skip if skip else len(returns)
    start = end - window
    if start < 0:
        raise ValueError("insufficient history for trend horizon")
    return prod(1.0 + value for value in returns[start:end]) - 1.0


def _vote(horizon_returns: Sequence[float]) -> int:
    votes = sum((value > 0.0) - (value < 0.0) for value in horizon_returns)
    return (votes > 0) - (votes < 0)


def _decay_weights(length: int, span: int) -> list[float]:
    """Oldest-first finite EWMA weights using the conventional span definition."""
    alpha = 2.0 / (span + 1.0)
    raw = [(1.0 - alpha) ** (length - index - 1) for index in range(length)]
    total = sum(raw)
    return [weight / total for weight in raw]


def _ewma_annualized_volatility(returns: Sequence[float], span: int) -> float:
    if len(returns) < 2:
        return 0.0
    weights = _decay_weights(len(returns), span)
    mean = sum(weight * value for weight, value in zip(weights, returns, strict=True))
    variance = sum(
        weight * (value - mean) ** 2
        for weight, value in zip(weights, returns, strict=True)
    )
    return sqrt(max(variance, 0.0) * SESSIONS_PER_YEAR)


def _ewma_covariance(
    returns: Mapping[str, Sequence[float]], span: int
) -> dict[tuple[str, str], float]:
    symbols = sorted(returns)
    if not symbols:
        return {}
    length = min(len(returns[symbol]) for symbol in symbols)
    weights = _decay_weights(length, span)
    aligned = {symbol: list(returns[symbol][-length:]) for symbol in symbols}
    means = {
        symbol: sum(
            weight * value for weight, value in zip(weights, aligned[symbol], strict=True)
        )
        for symbol in symbols
    }
    covariance: dict[tuple[str, str], float] = {}
    for index, left in enumerate(symbols):
        for right in symbols[index:]:
            value = sum(
                weight * (a - means[left]) * (b - means[right])
                for weight, a, b in zip(weights, aligned[left], aligned[right], strict=True)
            )
            covariance[(left, right)] = covariance[(right, left)] = value
    return covariance


def _portfolio_volatility(
    weights: Mapping[str, float], covariance: Mapping[tuple[str, str], float]
) -> float:
    variance = sum(
        left_weight * right_weight * covariance[(left, right)]
        for left, left_weight in weights.items()
        for right, right_weight in weights.items()
    )
    return sqrt(max(variance, 0.0) * SESSIONS_PER_YEAR)
