"""Decimal helpers.

Money is never represented as a float in this system.

Floats are binary fractions, so 0.1 is not exactly one tenth. Add a few thousand of
them together -- which a 20-year backtest does -- and the books stop balancing by
fractions of a cent. Those errors then show up as impossible NAV values that take
hours to trace. `Decimal` stores base-10 digits exactly, so a cent is a cent.

The one rule that matters: never build a Decimal from a float. `Decimal(0.1)` captures
the float's error and preserves it forever. `dec()` below routes through `str` so you
get the number you actually meant.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Final

# Cash is tracked to the cent; asset quantities need far more precision because a
# fractional share -- or 0.00042 BTC -- is a real position.
CASH_PLACES: Final = Decimal("0.01")
QTY_PLACES: Final = Decimal("0.00000001")
PRICE_PLACES: Final = Decimal("0.00000001")

ZERO: Final = Decimal("0")


def dec(value: Decimal | int | str | float) -> Decimal:
    """Build a Decimal safely from anything.

    Floats are routed through `str` deliberately: `dec(0.1)` gives exactly
    `Decimal("0.1")`, whereas `Decimal(0.1)` gives 0.1000000000000000055511151231...
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def quantize_cash(value: Decimal) -> Decimal:
    """Round a cash amount to the cent, using banker's rounding.

    ROUND_HALF_EVEN sends exact halves to the nearest even digit rather than always
    up. Over many roundings that keeps the error centred on zero instead of letting it
    accumulate in one direction, which is why it is the accounting default.
    """
    return value.quantize(CASH_PLACES, rounding=ROUND_HALF_EVEN)


def quantize_qty(value: Decimal) -> Decimal:
    return value.quantize(QTY_PLACES, rounding=ROUND_HALF_EVEN)


def quantize_price(value: Decimal) -> Decimal:
    return value.quantize(PRICE_PLACES, rounding=ROUND_HALF_EVEN)


def safe_div(numerator: Decimal, denominator: Decimal, default: Decimal = ZERO) -> Decimal:
    """Divide, returning `default` when the denominator is zero.

    Division by zero happens legitimately here -- an empty portfolio has zero NAV, and
    asking for a position's weight in it is a reasonable question with the answer
    "none" rather than an error.
    """
    if denominator == ZERO:
        return default
    # 28 significant digits is plenty for weights and returns, and bounds the memory a
    # long chain of divisions would otherwise consume.
    with localcontext() as ctx:
        ctx.prec = 28
        return numerator / denominator
