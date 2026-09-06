"""Naming strategies, so a run can be described by a string.

A backtest has to be reproducible from a record of what was asked for, and "the object
I constructed in a notebook" is not a record. Phase 3 replaces this with YAML config
files describing parameters as well as names; until then a strategy is one of the
benchmarks, chosen by name on the command line.
"""

from __future__ import annotations

from collections.abc import Callable

from sillage.data.universe import Universe
from sillage.strategy.base import Strategy
from sillage.strategy.benchmarks import buy_and_hold, equal_weight, sixty_forty
from sillage.strategy.momentum import build as build_momentum
from sillage.strategy.tranche import Tranched

Builder = Callable[[Universe], Strategy]

BUILDERS: dict[str, Builder] = {
    # The strategy. Tranched by default: a single rebalance date is one draw from a
    # distribution two percentage points wide, and reporting that draw as the answer
    # is the thing this project exists not to do.
    "momentum": lambda u: Tranched(build_momentum(u)),
    "momentum-single": build_momentum,
    # Benchmarks. Not decoration -- a result is meaningless without them.
    "buy-and-hold": lambda _: buy_and_hold("SPY"),
    "spy": lambda _: buy_and_hold("SPY"),
    "60-40": lambda _: sixty_forty(),
    "equal-weight": lambda u: equal_weight(u.symbols),
}


def build(name: str, universe: Universe) -> Strategy:
    try:
        builder = BUILDERS[name]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(BUILDERS)}") from None
    return builder(universe)


def names() -> list[str]:
    return sorted(BUILDERS)
