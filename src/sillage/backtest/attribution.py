"""Which holdings made the money, and which ones got the capital.

A strategy's return is one number and it hides everything interesting. Twelve percent a
year could mean every sleeve contributed steadily, or that gold carried the whole thing
while five other positions lost money and the emerging-market sleeve was held for
eleven years to no effect. Those are different systems with the same equity curve, and
only one of them is worth keeping.

Two views, deliberately separate, because they answer different questions.

**Profit and loss, in currency.** Exact, taken from the closed positions' realised
results plus what the open ones are worth now, minus what it cost to trade them. This
is what each holding actually earned.

**Capital and time.** How much of the fund each symbol held on average, and how many
sessions it was held at all. This is what each holding actually cost in opportunity.

Reading them together is the point. A sleeve with a large profit and a large average
weight did its job. A sleeve with a small profit and a large average weight was an
expensive way to hold cash. A sleeve with a large profit and a tiny average weight was
lucky, and would not survive being sized properly.

This is not a time-weighted return decomposition -- it does not tell you what fraction
of the *return* each asset contributed, only what fraction of the *profit*. The two
differ when the fund's size changed a lot over the run, since a dollar earned early
compounds and a dollar earned late does not. Stated rather than glossed, and the
time-weighted version can come when something needs it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sillage.core.money import ZERO, safe_div
from sillage.core.types import Portfolio
from sillage.engine.journal import NavPoint


@dataclass(frozen=True, slots=True)
class Contribution:
    """One symbol's share of the outcome."""

    symbol: str
    realized: Decimal
    unrealized: Decimal
    commission: Decimal
    #: Mean share of NAV across every session of the run, including sessions when it
    #: was not held -- so this reads as "what fraction of the fund did this consume".
    average_weight: float
    #: Sessions on which any of it was held.
    sessions_held: int
    #: Fraction of the run those sessions represent.
    time_held: float

    @property
    def net(self) -> Decimal:
        """Profit after the commissions paid to achieve it."""
        return self.realized + self.unrealized - self.commission

    def __str__(self) -> str:
        return (
            f"{self.symbol:<6} {float(self.net):>12,.0f}  "
            f"{self.average_weight:>6.1%} avg weight  {self.time_held:>5.0%} of the run"
        )


def attribute(
    portfolio: Portfolio,
    prices: dict[str, Decimal],
    nav_points: Sequence[NavPoint],
) -> list[Contribution]:
    """Per-symbol profit and capital use, largest profit first.

    Closed positions still appear: `Portfolio` keeps a position that has gone flat as
    long as it realised something, precisely so that a winning trade closed in 2009 is
    not invisible in 2026.
    """
    sessions = len(nav_points)
    exposure: dict[str, Decimal] = {}
    held: dict[str, int] = {}
    for point in nav_points:
        for symbol, weight in point.weights.items():
            if weight == ZERO:
                continue
            exposure[symbol] = exposure.get(symbol, ZERO) + weight
            held[symbol] = held.get(symbol, 0) + 1

    contributions = []
    for symbol in sorted(set(portfolio.positions) | set(exposure)):
        position = portfolio.positions.get(symbol)
        unrealized = ZERO
        if position is not None and not position.is_flat and symbol in prices:
            unrealized = position.unrealized_pnl(prices[symbol])
        contributions.append(
            Contribution(
                symbol=symbol,
                realized=position.realized_pnl if position else ZERO,
                unrealized=unrealized,
                commission=position.commission_paid if position else ZERO,
                average_weight=float(safe_div(exposure.get(symbol, ZERO), Decimal(sessions)))
                if sessions
                else 0.0,
                sessions_held=held.get(symbol, 0),
                time_held=held.get(symbol, 0) / sessions if sessions else 0.0,
            )
        )

    return sorted(contributions, key=lambda c: c.net, reverse=True)


def concentration(contributions: Sequence[Contribution]) -> float:
    """Share of total profit that came from the single best holding.

    A blunt but honest overfitting check. If four fifths of a twenty-year result came
    from one asset, the strategy is a bet on that asset wearing a diversified costume,
    and the other eleven positions are decoration paid for in commission.
    """
    gains = [float(c.net) for c in contributions if c.net > ZERO]
    total = sum(gains)
    return max(gains) / total if total > 0 else 0.0
