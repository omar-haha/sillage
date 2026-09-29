"""Frozen signal for Candidate C, beta-neutral US sector momentum."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from sillage.core.money import dec

SECTOR_ETFS = ("XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY")


@dataclass(frozen=True, slots=True)
class SectorNeutralConfig:
    short_lookback: int = 126
    long_lookback: int = 252
    skip: int = 21
    beta_lookback: int = 252
    selections_per_side: int = 2
    gross: float = 1.0
    max_weight: float = 0.35

    def __post_init__(self) -> None:
        if not 0 < self.skip < self.short_lookback < self.long_lookback:
            raise ValueError("lookbacks must satisfy 0 < skip < short < long")
        if self.beta_lookback < 20:
            raise ValueError("beta lookback must be at least 20 sessions")
        if self.selections_per_side < 1:
            raise ValueError("selections per side must be positive")
        if not 0 < self.gross <= 1.5:
            raise ValueError("gross must be in (0, 1.5]")
        if not 0 < self.max_weight <= self.gross:
            raise ValueError("maximum weight must be positive and no larger than gross")


@dataclass(frozen=True, slots=True)
class SectorNeutralDecision:
    eligible: bool
    reason: str
    longs: tuple[str, ...]
    shorts: tuple[str, ...]
    scores: Mapping[str, float]
    betas: Mapping[str, float]
    weights: Mapping[str, Decimal]


def decide_sectors(
    prices: Mapping[str, Sequence[float]],
    spy_prices: Sequence[float],
    config: SectorNeutralConfig | None = None,
) -> SectorNeutralDecision:
    """Rank frozen 6-1/12-1 momentum and return whole-book beta-neutral weights."""
    cfg = config or SectorNeutralConfig()
    needed = max(cfg.long_lookback + cfg.skip + 1, cfg.beta_lookback + 1)
    if len(spy_prices) < needed or any(len(prices.get(s, ())) < needed for s in SECTOR_ETFS):
        return _empty("insufficient history")

    scores: dict[str, float] = {}
    betas: dict[str, float] = {}
    spy = np.log(np.asarray(spy_prices[-(cfg.beta_lookback + 1) :], dtype=float))
    market_returns = np.diff(spy)
    market_variance = float(np.var(market_returns, ddof=1))
    if not np.isfinite(market_returns).all() or market_variance <= 0:
        return _empty("invalid market history")

    for symbol in SECTOR_ETFS:
        values = np.asarray(prices[symbol], dtype=float)
        if not np.isfinite(values).all() or np.any(values <= 0):
            return _empty(f"invalid history for {symbol}")
        end = len(values) - 1 - cfg.skip
        six_one = values[end] / values[end - cfg.short_lookback] - 1
        twelve_one = values[end] / values[end - cfg.long_lookback] - 1
        scores[symbol] = float(six_one + twelve_one)
        asset_returns = np.diff(np.log(values[-(cfg.beta_lookback + 1) :]))
        beta = float(np.cov(asset_returns, market_returns, ddof=1)[0, 1] / market_variance)
        if not np.isfinite(beta) or beta <= 0:
            return _empty(f"invalid beta for {symbol}")
        betas[symbol] = beta

    ranked = sorted(SECTOR_ETFS, key=lambda s: (scores[s], s))
    count = cfg.selections_per_side
    shorts = tuple(ranked[:count])
    longs = tuple(reversed(ranked[-count:]))
    long_beta = sum(betas[s] for s in longs) / count
    short_beta = sum(betas[s] for s in shorts) / count
    long_gross = cfg.gross * short_beta / (long_beta + short_beta)
    short_gross = cfg.gross - long_gross
    long_weight = long_gross / count
    short_weight = short_gross / count
    if max(long_weight, short_weight) > cfg.max_weight:
        scale = cfg.max_weight / max(long_weight, short_weight)
        long_weight *= scale
        short_weight *= scale
    weights = {s: dec(long_weight).quantize(dec("0.000001")) for s in longs}
    weights.update({s: dec(-short_weight).quantize(dec("0.000001")) for s in shorts})
    return SectorNeutralDecision(True, "eligible", longs, shorts, scores, betas, weights)


def _empty(reason: str) -> SectorNeutralDecision:
    return SectorNeutralDecision(False, reason, (), (), {}, {}, {})
