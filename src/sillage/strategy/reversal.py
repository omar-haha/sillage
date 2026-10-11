"""Candidate D's preregistered trailing-only residual reversal signal."""

from collections.abc import Mapping

import numpy as np

from sillage.strategy.sector_neutral import SECTOR_ETFS


def reversal_weights(
    prices: Mapping[str, np.ndarray], market: np.ndarray, vix: float, threshold: float = 25
) -> dict[str, float]:
    if len(market) < 253 or not np.isfinite(vix) or vix < threshold:
        return {}
    if not np.isfinite(market[-253:]).all() or np.any(market[-253:] <= 0):
        return {}
    market_returns = np.diff(np.log(market[-253:]))
    variance = np.var(market_returns, ddof=1)
    if variance <= 0:
        return {}
    betas, scores = {}, {}
    for symbol in SECTOR_ETFS:
        values = prices[symbol][-253:]
        if len(values) < 253 or not np.isfinite(values).all() or np.any(values <= 0):
            return {}
        returns = np.diff(np.log(values))
        beta = float(np.cov(returns, market_returns, ddof=1)[0, 1] / variance)
        residual = returns - beta * market_returns
        vol = float(np.std(residual[-20:], ddof=1))
        if not np.isfinite(beta) or beta <= 0 or not np.isfinite(vol) or vol <= 0:
            return {}
        betas[symbol] = beta
        scores[symbol] = float(residual[-5:].sum() / vol)
    ranked = sorted(SECTOR_ETFS, key=lambda s: (scores[s], s))
    longs, shorts = ranked[:2], ranked[-2:]
    long_beta = sum(betas[s] for s in longs) / 2
    short_beta = sum(betas[s] for s in shorts) / 2
    long_weight = short_beta / (long_beta + short_beta) / 2
    short_weight = long_beta / (long_beta + short_beta) / 2
    scale = min(1, 0.35 / max(long_weight, short_weight))
    return {
        **dict.fromkeys(longs, long_weight * scale),
        **dict.fromkeys(shorts, -short_weight * scale),
    }
