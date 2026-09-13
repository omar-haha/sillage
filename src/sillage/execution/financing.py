"""Interest earned on cash and paid on margin balances.

Execution costs happen at a fill; financing happens because time passes. Keeping those
separate prevents a margin rate from being hidden inside slippage and lets the same
historical rate curve price every strategy and benchmark.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from sillage.core.money import ZERO, dec, quantize_cash

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True, slots=True)
class RateCurve:
    """Annual decimal rates, effective from their observation date onward."""

    observations: tuple[tuple[date, Decimal], ...]

    def __post_init__(self) -> None:
        dates = [day for day, _ in self.observations]
        if not dates:
            raise ValueError("rate curve must not be empty")
        if dates != sorted(dates):
            raise ValueError("rate curve observations must be ordered")
        if len(dates) != len(set(dates)):
            raise ValueError("rate curve contains duplicate dates")
        if any(rate < ZERO for _, rate in self.observations):
            raise ValueError("rate curve cannot contain negative rates")

    @classmethod
    def from_series(cls, rates: pd.Series) -> RateCurve:
        """Build from the dated Series used by performance metrics."""
        import pandas as pd

        ordered = rates.sort_index()
        dates = pd.DatetimeIndex(ordered.index)
        observations = tuple(
            (stamp.date(), dec(value))
            for stamp, value in zip(dates, ordered.to_numpy(), strict=True)
        )
        return cls(observations)

    def rate_on(self, day: date) -> Decimal:
        dates = [stamp for stamp, _ in self.observations]
        index = bisect_right(dates, day) - 1
        if index < 0:
            raise ValueError(f"no financing rate known on or before {day}")
        return self.observations[index][1]


@dataclass(frozen=True, slots=True)
class FinancingModel:
    """Accrue positive cash and negative margin balances by calendar day."""

    cash_rates: RateCurve
    margin_rates: RateCurve
    day_count: Decimal = Decimal(365)

    def __post_init__(self) -> None:
        if self.day_count <= ZERO:
            raise ValueError("financing day count must be positive")

    def accrual(self, cash: Decimal, since: date, until: date) -> Decimal:
        """Interest between two session dates, including weekends between them."""
        if until < since:
            raise ValueError("financing interval cannot run backwards")
        if cash == ZERO or until == since:
            return ZERO

        balance = cash
        day = since
        while day < until:
            curve = self.cash_rates if balance > ZERO else self.margin_rates
            balance += balance * curve.rate_on(day) / self.day_count
            day += timedelta(days=1)
        return quantize_cash(balance - cash)
