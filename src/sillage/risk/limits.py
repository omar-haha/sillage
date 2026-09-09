"""The last thing between a strategy and the broker.

Everything upstream is a model of what should happen. This module assumes the model is
wrong and asks a narrower question: whatever the strategy believes, is this order one
the fund is *permitted* to place? The two are different, and keeping them separate is
what stops a bug in the sizing logic becoming a bug in the account.

Three checks, in increasing order of severity.

**Position caps.** No single holding above a fraction of the fund. A vol-targeting bug
that produced a 300% weight would be caught here rather than at the broker.

**Gross exposure.** The whole book, capped. In a long-only fund this should never bind;
the point is that if it ever does, something upstream has gone badly wrong and the
order should not go out while nobody knows what.

**A drawdown kill-switch.** Below a floor relative to the high-water mark, stop trading
entirely and require a human. This is the only check that is not about a single order,
and the only one whose job is to be wrong most of the time: a strategy in a legitimate
drawdown will trip it, and a human deciding to resume is the entire mechanism. An
automatic resume would make it decoration.

Limits reject rather than resize. A rejected order is visible in the journal and gets
explained; a silently shrunk one produces a book that quietly differs from the target
and nobody ever asks why.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sillage.core.money import ZERO, dec, safe_div
from sillage.core.types import Order, Portfolio
from sillage.execution.broker import Rejection

#: One hundred percent of the fund in one name. This is a *fault* threshold, not a
#: portfolio constraint: in a long-only fund a weight above 1.0 is always a bug, and a
#: cap below 1.0 would fight legitimate allocations -- a 60/40 wants 60% in equities and
#: a buy-and-hold wants 100%. A tighter cap is a deliberate choice for a specific fund,
#: made by whoever runs it. Set to 0.35 by default, this silently blocked every order a
#: 60/40 tried to place and the fund sat in cash looking healthy.
DEFAULT_MAX_WEIGHT = dec("1.00")
DEFAULT_MAX_GROSS = dec("1.05")
DEFAULT_DRAWDOWN_LIMIT = dec("0.25")


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """What the fund is permitted to do, regardless of what it wants to do."""

    #: Largest share of NAV any one holding may reach after an order.
    max_weight: Decimal = DEFAULT_MAX_WEIGHT
    #: Largest total exposure. Slightly above 1.0 so that ordinary intra-day drift in a
    #: fully invested long-only book does not trip a limit meant to catch real faults.
    max_gross: Decimal = DEFAULT_MAX_GROSS
    #: Stop trading once the fund is this far below its high-water mark.
    max_drawdown: Decimal = DEFAULT_DRAWDOWN_LIMIT

    def __post_init__(self) -> None:
        if self.max_weight <= ZERO:
            raise ValueError("maximum weight must be positive")
        if self.max_gross <= ZERO:
            raise ValueError("maximum gross exposure must be positive")
        if not ZERO < self.max_drawdown <= dec(1):
            raise ValueError("drawdown limit must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Which orders may go out, and why the rest may not."""

    allowed: tuple[Order, ...]
    rejected: tuple[Rejection, ...]
    #: Set when the kill-switch tripped: no order is permitted at all.
    halted: str = ""

    @property
    def trading(self) -> bool:
        return not self.halted


def drawdown(nav: Decimal, high_water_mark: Decimal) -> Decimal:
    """How far below the peak the fund is, as a positive fraction."""
    if high_water_mark <= ZERO:
        return ZERO
    return max(ZERO, safe_div(high_water_mark - nav, high_water_mark))


def filter_orders(
    orders: Sequence[Order],
    *,
    portfolio: Portfolio,
    prices: Mapping[str, Decimal],
    limits: RiskLimits,
    high_water_mark: Decimal = ZERO,
) -> RiskDecision:
    """Decide which of these orders the fund is allowed to place.

    Orders are checked against the book *as it would be after* each one, taken in the
    order given. Checking each against the current book independently would let a
    combination pass that no single order would -- three orders each taking a holding to
    30% of a 35% cap are individually fine and collectively impossible.
    """
    nav = portfolio.nav(prices)
    if nav <= ZERO:
        return RiskDecision((), (), halted="net asset value is not positive")

    current = drawdown(nav, high_water_mark)
    if current > limits.max_drawdown:
        # Everything stops. Deliberately not "trade smaller": the point of a kill-switch
        # is that it hands the decision to a person, and a person cannot intervene in a
        # system that quietly kept going.
        return RiskDecision(
            (),
            (),
            halted=(
                f"drawdown {float(current):.1%} exceeds the {float(limits.max_drawdown):.0%} "
                f"limit; trading halted pending review"
            ),
        )

    allowed: list[Order] = []
    rejected: list[Rejection] = []
    quantities = {s: p.quantity for s, p in portfolio.positions.items()}

    for order in orders:
        symbol = order.instrument.symbol
        price = prices.get(symbol)
        if price is None or price <= ZERO:
            rejected.append(Rejection(order, f"no price for {symbol}; cannot risk-check it"))
            continue

        after = quantities.get(symbol, ZERO) + order.quantity
        weight = safe_div(abs(after) * price, nav)
        if weight > limits.max_weight:
            rejected.append(
                Rejection(
                    order,
                    f"would take {symbol} to {float(weight):.1%} of the fund, "
                    f"above the {float(limits.max_weight):.0%} cap",
                )
            )
            continue

        projected = dict(quantities)
        projected[symbol] = after
        gross = sum(
            (abs(q) * prices[s] for s, q in projected.items() if s in prices and q != ZERO),
            start=ZERO,
        )
        if safe_div(gross, nav) > limits.max_gross:
            rejected.append(
                Rejection(
                    order,
                    f"would take gross exposure to {float(safe_div(gross, nav)):.1%}, "
                    f"above the {float(limits.max_gross):.0%} cap",
                )
            )
            continue

        quantities = projected
        allowed.append(order)

    return RiskDecision(tuple(allowed), tuple(rejected))
