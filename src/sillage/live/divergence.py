"""Comparing what the simulator said a trade would cost against what it actually cost.

This is the point of running a paper account at a real broker, and the answer it gives
is the one number in this project that nothing else can produce. Every cost figure so
far -- the spread estimates, the impact model, the commission schedule -- is a defensible
guess, and a backtest graded by its own guesses will always agree with itself. Putting
IBKR on the other side of the same orders is the only way to find out whether the guesses
were optimistic, and by how much.

**How the pairing works.** Both funds run the same strategy over the same dates, so an
intended trade appears once on each side: same session, same symbol, same direction. The
live side may have filled in several pieces at several prices, so those are collapsed to
a volume-weighted average before comparison -- one intention, one price, on each side.

**What the number means.** Divergence is reported in basis points *against the trader*: a
buy that filled higher than the simulator said, or a sell that filled lower, is positive.
Negative divergence is price improvement, which the simulator can never produce, because
it moves every price against the trader by construction. Seeing some is a sign the model
is honest rather than a sign it is wrong.

**What to do with it.** `Divergence.corrected` scales the cost model by the ratio of
observed to modelled cost, so the Phase 3 backtest can be re-run on measured assumptions
instead of assumed ones. That before-and-after is the whole justification for splitting
Phase 5 in two.

**A caution about small samples.** A dozen fills of a liquid ETF will produce a number,
and it will be noise. This reports the count and the spread alongside the average
precisely so that a confident-looking figure built from eleven trades is visibly built
from eleven trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import median

from sillage.core.money import ZERO, dec, safe_div
from sillage.core.types import Fill
from sillage.execution.costs import BPS, CostModel

#: Below this many paired fills, any average is a story about a handful of trades.
MEANINGFUL_SAMPLE = 30


@dataclass(frozen=True, slots=True)
class Intention:
    """One trade as it was meant to happen, on one side of the comparison."""

    session: date
    symbol: str
    quantity: Decimal
    price: Decimal
    commission: Decimal

    @property
    def buying(self) -> bool:
        return self.quantity > ZERO


@dataclass(frozen=True, slots=True)
class Pair:
    """The same intended trade, as the simulator had it and as it happened."""

    simulated: Intention
    actual: Intention

    @property
    def divergence_bps(self) -> float:
        """Basis points against the trader. Negative means the real fill was better."""
        if self.simulated.price <= ZERO:
            return 0.0
        direction = 1 if self.simulated.buying else -1
        move = (self.actual.price - self.simulated.price) / self.simulated.price
        return float(move) * float(BPS) * direction

    @property
    def commission_bps(self) -> float:
        """How much the real commission differed, relative to the notional traded."""
        notional = abs(self.actual.quantity) * self.actual.price
        if notional <= ZERO:
            return 0.0
        return float(
            safe_div(self.actual.commission - self.simulated.commission, notional)
        ) * float(BPS)

    def __str__(self) -> str:
        side = "buy " if self.simulated.buying else "sell"
        return (
            f"{self.simulated.session} {side} {self.simulated.symbol:<5} "
            f"modelled {float(self.simulated.price):>10,.4f}  "
            f"actual {float(self.actual.price):>10,.4f}  "
            f"{self.divergence_bps:+.1f}bp"
        )


@dataclass(frozen=True, slots=True)
class Divergence:
    """How optimistic the simulator turned out to be."""

    pairs: tuple[Pair, ...]
    #: Fills on one side with no counterpart on the other. Worth reporting rather than
    #: dropping: a systematic gap means the two funds were not doing the same thing.
    unmatched_simulated: int = 0
    unmatched_actual: int = 0

    @property
    def count(self) -> int:
        return len(self.pairs)

    @property
    def median_bps(self) -> float:
        return median(p.divergence_bps for p in self.pairs) if self.pairs else 0.0

    @property
    def mean_bps(self) -> float:
        return sum(p.divergence_bps for p in self.pairs) / self.count if self.pairs else 0.0

    @property
    def worst_bps(self) -> float:
        return max((p.divergence_bps for p in self.pairs), default=0.0)

    @property
    def improved(self) -> int:
        """Fills that beat the simulator. The model cannot produce these at all."""
        return sum(1 for p in self.pairs if p.divergence_bps < 0)

    @property
    def commission_bps(self) -> float:
        return sum(p.commission_bps for p in self.pairs) / self.count if self.pairs else 0.0

    @property
    def trustworthy(self) -> bool:
        return self.count >= MEANINGFUL_SAMPLE

    def optimism(self, costs: CostModel) -> float:
        """Observed cost divided by modelled cost. Above one means the model was kind.

        Measured against what the model charges for a *typical* trade in this sample
        rather than against each order's own estimate, because the point is to produce
        one number the cost model can be scaled by.
        """
        modelled = self.modelled_bps(costs)
        if modelled <= 0:
            return 1.0
        return max(0.0, self.median_bps) / modelled

    def modelled_bps(self, costs: CostModel) -> float:
        """The half-spread the model assumed, averaged over the symbols actually traded."""
        if not self.pairs:
            return 0.0
        return (
            sum(float(costs.half_spread_for(p.simulated.symbol)) for p in self.pairs) / self.count
        )

    def corrected(self, costs: CostModel) -> CostModel:
        """The cost model, scaled so its assumptions match what was observed.

        Deliberately a uniform scaling rather than a per-symbol refit. A dozen fills per
        symbol cannot support a per-symbol estimate, and pretending otherwise would
        replace one guess with a more elaborate guess.
        """
        return costs.scaled(dec(str(round(self.optimism(costs), 4))))

    def summary(self, costs: CostModel) -> str:
        if not self.pairs:
            return "no paired fills: the two funds have no trades in common"
        verdict = (
            "the simulator was optimistic"
            if self.median_bps > self.modelled_bps(costs)
            else "the simulator was, if anything, pessimistic"
        )
        caveat = "" if self.trustworthy else f" (only {self.count} fills -- treat as indicative)"
        return (
            f"{self.count} paired fills: median divergence {self.median_bps:+.1f}bp "
            f"against a modelled {self.modelled_bps(costs):.1f}bp, "
            f"{self.improved} improved. {verdict}{caveat}."
        )


def collapse(fills: Sequence[Fill]) -> dict[tuple[date, str, bool], Intention]:
    """One intention per session, symbol and direction, at a volume-weighted price.

    A live order that filled in four pieces is still one decision, and comparing four
    live prices against one simulated one would count the same intention four times.
    """
    grouped: dict[tuple[date, str, bool], list[Fill]] = {}
    for fill in fills:
        key = (fill.ts.date(), fill.instrument.symbol, fill.quantity > ZERO)
        grouped.setdefault(key, []).append(fill)

    collapsed = {}
    for key, group in grouped.items():
        quantity = sum((f.quantity for f in group), start=ZERO)
        notional = sum((abs(f.quantity) * f.price for f in group), start=ZERO)
        volume = sum((abs(f.quantity) for f in group), start=ZERO)
        collapsed[key] = Intention(
            session=key[0],
            symbol=key[1],
            quantity=quantity,
            price=safe_div(notional, volume),
            commission=sum((f.commission for f in group), start=ZERO),
        )
    return collapsed


def compare(simulated: Sequence[Fill], actual: Sequence[Fill]) -> Divergence:
    """Pair up the two funds' fills and measure the gap."""
    left, right = collapse(simulated), collapse(actual)
    shared = sorted(set(left) & set(right))
    return Divergence(
        pairs=tuple(Pair(simulated=left[key], actual=right[key]) for key in shared),
        unmatched_simulated=len(set(left) - set(right)),
        unmatched_actual=len(set(right) - set(left)),
    )
