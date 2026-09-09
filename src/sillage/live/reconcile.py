"""Checking that the broker and the journal agree, before doing anything else.

This is where real systems break. Not in the strategy, not in the sizing -- in the gap
between what a program believes it owns and what a broker actually holds. The gap opens
in ordinary ways: an order acknowledged but not filled when the process died, a manual
trade someone placed in the web terminal, a corporate action the journal never saw, a
partial fill reported after a timeout.

The rule here is deliberately blunt: **on any mismatch, refuse to trade.** Not "adjust
to match the broker", which would silently adopt a position nobody chose and then size
the next rebalance around it. Not "assume the journal is right", which would place
orders against a book that does not exist. A system that cannot account for what it owns
has no business deciding what to own next, and the correct response is to stop and page
a human.

The cost of that rule is downtime. The cost of the alternative is a position nobody can
explain, compounding.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sillage.core.money import ZERO, dec

#: Quantities below this are treated as equal. Fractional-share brokers report tiny
#: dust from dividend reinvestment that is not a real disagreement.
TOLERANCE = dec("0.00000001")


@dataclass(frozen=True, slots=True)
class Discrepancy:
    """One symbol the two sides disagree about."""

    symbol: str
    expected: Decimal
    actual: Decimal

    @property
    def difference(self) -> Decimal:
        return self.actual - self.expected

    def __str__(self) -> str:
        return f"{self.symbol}: journal says {self.expected}, broker says {self.actual}"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The result of comparing the two. Falsy when they disagree."""

    discrepancies: tuple[Discrepancy, ...]
    checked: int

    @property
    def agreed(self) -> bool:
        return not self.discrepancies

    def __bool__(self) -> bool:
        return self.agreed

    def __str__(self) -> str:
        if self.agreed:
            return f"reconciled: {self.checked} position(s) agree"
        listed = "; ".join(str(d) for d in self.discrepancies)
        return f"RECONCILIATION FAILED ({len(self.discrepancies)} of {self.checked}): {listed}"


class ReconciliationError(RuntimeError):
    """Raised when the live runner is asked to trade against a book it cannot verify."""


def reconcile(
    expected: Mapping[str, Decimal],
    actual: Mapping[str, Decimal],
    *,
    tolerance: Decimal = TOLERANCE,
) -> Reconciliation:
    """Compare the journal's positions against the broker's.

    Both directions matter. A symbol the broker holds and the journal does not is a
    position that appeared from nowhere; a symbol the journal holds and the broker does
    not is one that vanished. Neither is more alarming than the other, and checking only
    the journal's keys would miss the first entirely -- which is the one a manual trade
    produces.
    """
    discrepancies = [
        Discrepancy(symbol, expected.get(symbol, ZERO), actual.get(symbol, ZERO))
        for symbol in sorted(set(expected) | set(actual))
        if abs(actual.get(symbol, ZERO) - expected.get(symbol, ZERO)) > tolerance
    ]
    return Reconciliation(
        discrepancies=tuple(discrepancies),
        checked=len(set(expected) | set(actual)),
    )
