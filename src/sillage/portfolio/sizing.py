"""How much of each thing to own, once you have decided what to own.

Selection says *which* assets. This says *how much*, and it is where most of the risk
control in the system actually lives. Two ideas, applied in order.

**Inverse volatility for relative weights.** A quiet bond fund and a volatile commodity
fund held in equal dollar amounts are not held in equal *risk* amounts -- the commodity
sleeve will drive almost all of the portfolio's movement. Sizing inversely to each
asset's own recent volatility equalises the contribution instead, so no single holding
dominates by accident.

**Covariance for the overall scale.** Inverse-vol weights ignore correlations
completely, and that is a genuine hole rather than a simplification. A momentum screen
*systematically* concentrates into whatever has been working, which is usually one
theme, so five "different" assets can quietly be one trade. Assuming diversification you
do not have means a book aimed at 10% volatility delivers considerably more, precisely
when it matters. The fix is to compute the portfolio's expected volatility properly --
sqrt(wT S w) -- and scale the whole book by one number until it hits the target.

**Why the covariance matrix only sets the scale.** It could have set the weights too:
that is mean-variance optimisation, and it is famously an error-maximiser -- it
allocates hardest to whichever asset's estimate happens to be most wrong. Here a noisy
estimate governs one scalar rather than five weights, which is a much smaller thing to
be wrong about.

**On the estimate.** Sixty daily observations across five assets is workable but not
generous: sample correlations spread further apart than the truth. The correlation
matrix is therefore shrunk toward its own average -- every pair pulled part of the way
to the typical pair -- which damps the noisy extremes while preserving the average level
of correlation. Preserving the level is the point: shrinking toward zero correlation
would reintroduce exactly the optimism this module exists to remove.

Note this is a *fixed* shrinkage intensity, not Ledoit-Wolf's optimal one. Their delta
is itself estimated from the same short sample, and at this size the estimate of the
estimate is not obviously worth the machinery. Stated plainly rather than dressed up.

Plain Python floats throughout: five assets makes the linear algebra fifteen numbers,
and keeping numpy out of the domain layer is worth more than the microseconds.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import sqrt

from sillage.core.money import ZERO, dec

#: Trading days per year, for annualising a daily volatility.
SESSIONS_PER_YEAR = 252

#: Annualised volatility the whole portfolio aims at.
DEFAULT_TARGET_VOL = dec("0.10")

#: Sessions of daily returns used to estimate volatility and correlation.
DEFAULT_LOOKBACK = 60

#: How far each pairwise correlation is pulled toward the average pairwise correlation.
DEFAULT_SHRINKAGE = 0.25


@dataclass(frozen=True, slots=True)
class Sizing:
    """The outcome of sizing, with the reasoning attached.

    The diagnostics are not decoration. "The book is 62% invested" is uninterpretable on
    its own; "the book is 62% invested because its assets together were forecast at 16%
    volatility against a 10% target" is a claim that can be checked, and later compared
    against what volatility actually turned out to be.
    """

    weights: dict[str, Decimal]
    #: Forecast annualised volatility of the *unscaled* inverse-vol book.
    ex_ante_volatility: float
    #: What that book was multiplied by to reach the target.
    scale: float
    #: True when the scale was held back by the leverage cap rather than by the target,
    #: i.e. the strategy wanted more risk than it is allowed to take.
    capped: bool

    @property
    def gross(self) -> Decimal:
        return sum(self.weights.values(), start=ZERO)


@dataclass(frozen=True, slots=True)
class VolatilityTarget:
    """Scale a book of relative weights to a volatility budget."""

    target: Decimal = DEFAULT_TARGET_VOL
    #: The most the inverse-vol book may be scaled up. One means never lever.
    max_scale: float = 1.0
    lookback: int = DEFAULT_LOOKBACK
    shrinkage: float = DEFAULT_SHRINKAGE
    #: Below this many observations the covariance estimate is not worth trusting.
    min_observations: int = 40

    def __post_init__(self) -> None:
        if self.target <= ZERO:
            raise ValueError("volatility target must be positive")
        if self.max_scale <= 0:
            raise ValueError("max scale must be positive")
        if not 0.0 <= self.shrinkage <= 1.0:
            raise ValueError("shrinkage must be between 0 and 1")
        if self.min_observations < 2:
            raise ValueError("need at least two observations to estimate a variance")

    def size(self, returns: Mapping[str, Sequence[float]]) -> Sizing:
        """Inverse-vol weights for these assets, scaled to the volatility target.

        `returns` are daily simple returns, most recent last. An asset with too few
        observations is dropped rather than guessed at -- a made-up volatility would
        propagate silently into the position size.
        """
        usable = {
            symbol: list(series[-self.lookback :])
            for symbol, series in returns.items()
            if len(series) >= self.min_observations
        }
        if not usable:
            return Sizing(weights={}, ex_ante_volatility=0.0, scale=0.0, capped=False)

        deviations = {symbol: _stdev(series) for symbol, series in usable.items()}
        relative = inverse_volatility_weights(deviations)
        covariance = shrunk_covariance(usable, self.shrinkage)
        volatility = portfolio_volatility(relative, covariance)

        if volatility <= 0.0:
            # A book of assets that did not move. Cannot be scaled to a volatility
            # target because it has no volatility; take it at face value.
            scale, capped = self.max_scale, True
        else:
            wanted = float(self.target) / volatility
            scale = min(wanted, self.max_scale)
            capped = wanted > self.max_scale

        return Sizing(
            weights={s: _round_weight(w * dec(scale)) for s, w in relative.items()},
            ex_ante_volatility=volatility,
            scale=scale,
            capped=capped,
        )


def inverse_volatility_weights(deviations: Mapping[str, float]) -> dict[str, Decimal]:
    """Weights proportional to 1/volatility, summing to one.

    An asset with zero measured volatility would take an infinite weight, so those are
    dropped. In practice it means a completely flat price series -- a suspended fund, or
    the cash proxy during a zero-rate stretch -- and neither belongs in a risk book.
    """
    inverses = {s: 1.0 / d for s, d in deviations.items() if d > 0.0}
    total = sum(inverses.values())
    if total <= 0.0:
        return {}
    return {s: _round_weight(dec(v / total)) for s, v in inverses.items()}


def shrunk_covariance(
    returns: Mapping[str, Sequence[float]], shrinkage: float = DEFAULT_SHRINKAGE
) -> dict[tuple[str, str], float]:
    """Daily covariance, with correlations pulled toward the average correlation.

    Returned as a flat mapping keyed by symbol pair rather than a matrix, because at
    five assets a dict is clearer than an index scheme and the cost is irrelevant.
    """
    symbols = sorted(returns)
    if not symbols:
        # A legitimate input: every selected asset failed the trend filter, so the
        # risky book is empty and the whole portfolio is in cash.
        return {}
    series = {s: list(returns[s]) for s in symbols}
    length = min(len(v) for v in series.values())
    if length < 2:
        return {}
    # Aligned on the most recent observations: assets have different histories, and a
    # covariance computed across mismatched dates is not a covariance.
    series = {s: v[-length:] for s, v in series.items()}
    means = {s: sum(v) / length for s, v in series.items()}

    raw: dict[tuple[str, str], float] = {}
    for i, a in enumerate(symbols):
        for b in symbols[i:]:
            total = sum(
                (series[a][t] - means[a]) * (series[b][t] - means[b]) for t in range(length)
            )
            value = total / (length - 1)
            raw[(a, b)] = raw[(b, a)] = value

    deviations = {s: sqrt(max(raw[(s, s)], 0.0)) for s in symbols}
    off_diagonal = [
        raw[(a, b)] / (deviations[a] * deviations[b])
        for i, a in enumerate(symbols)
        for b in symbols[i + 1 :]
        if deviations[a] > 0.0 and deviations[b] > 0.0
    ]
    if not off_diagonal or shrinkage <= 0.0:
        return raw

    average = sum(off_diagonal) / len(off_diagonal)
    shrunk: dict[tuple[str, str], float] = {}
    for i, a in enumerate(symbols):
        shrunk[(a, a)] = raw[(a, a)]
        for b in symbols[i + 1 :]:
            if deviations[a] <= 0.0 or deviations[b] <= 0.0:
                shrunk[(a, b)] = shrunk[(b, a)] = 0.0
                continue
            correlation = raw[(a, b)] / (deviations[a] * deviations[b])
            blended = (1.0 - shrinkage) * correlation + shrinkage * average
            value = blended * deviations[a] * deviations[b]
            shrunk[(a, b)] = shrunk[(b, a)] = value
    return shrunk


def portfolio_volatility(
    weights: Mapping[str, Decimal], covariance: Mapping[tuple[str, str], float]
) -> float:
    """Annualised sqrt(wT S w) -- the number inverse-vol sizing cannot see.

    Missing pairs are treated as zero covariance, which only happens for an asset with
    no return history at all; such an asset also has no weight.
    """
    variance = 0.0
    for a, wa in weights.items():
        for b, wb in weights.items():
            variance += float(wa) * float(wb) * covariance.get((a, b), 0.0)
    if variance <= 0.0:
        return 0.0
    return sqrt(variance * SESSIONS_PER_YEAR)


def diversification_ratio(
    weights: Mapping[str, Decimal], covariance: Mapping[tuple[str, str], float]
) -> float:
    """Weighted average asset volatility divided by portfolio volatility.

    One means the assets move as a single trade and the book is not diversified at all;
    higher is better. This is the number that makes the module's central worry visible:
    it falls toward one exactly when a momentum screen has concentrated into one theme,
    which is when a naive inverse-vol book is most wrong about its own risk.
    """
    portfolio = portfolio_volatility(weights, covariance)
    if portfolio <= 0.0:
        return 0.0
    weighted = sum(
        float(w) * sqrt(max(covariance.get((s, s), 0.0), 0.0) * SESSIONS_PER_YEAR)
        for s, w in weights.items()
    )
    return weighted / portfolio


def daily_returns(prices: Sequence[Decimal]) -> list[float]:
    """Simple session-over-session returns, as floats.

    The one place money legitimately becomes a float: these feed square roots and
    products where Decimal buys no accuracy and costs a great deal of time.
    """
    return [
        float(prices[i] / prices[i - 1]) - 1.0
        for i in range(1, len(prices))
        if prices[i - 1] > ZERO
    ]


def _stdev(series: Sequence[float]) -> float:
    if len(series) < 2:
        return 0.0
    mean = sum(series) / len(series)
    variance = sum((x - mean) ** 2 for x in series) / (len(series) - 1)
    return sqrt(max(variance, 0.0))


def _round_weight(value: Decimal) -> Decimal:
    """Six places. Finer than any lot size can express, coarse enough to compare."""
    return value.quantize(dec("0.000001"))
