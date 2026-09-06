"""Wiring a backtest together, and what comes out of one.

Everything here is assembly. The engine, the clock, the feeds, the broker and the
strategy are all built and tested independently; this file's only job is to connect
them in the one configuration that means "replay history", and to hand back the record.

One choice deserves stating. **History is loaded from the beginning of the store, not
from the backtest's start date.** A momentum signal on the first day of a 2010 run
needs 2009's prices to exist, and a feed that began at the start date would leave every
strategy blind for its first year -- producing a result that looks like the strategy
and is actually a year of doing nothing followed by the strategy. The `as_of` gate is
what stops that history being abused; loading it is not the same as being allowed to
read it.

Performance metrics deliberately do not live here. Phase 2 builds them against
`quantstats` as a cross-check, and a `total_return` computed casually in this file
would be the number people quote before that validation exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from sillage.core.calendar import Calendar, TradingCalendar
from sillage.core.money import ZERO, dec, safe_div
from sillage.core.types import Fill, Order
from sillage.data.store import BarStore
from sillage.data.universe import Universe
from sillage.engine.clock import BacktestClock
from sillage.engine.feed import HistoricalFeed, MarketFeed, load_bars
from sillage.engine.journal import InMemoryJournal, NavPoint
from sillage.engine.loop import DEFAULT_INITIAL_CASH, Engine
from sillage.execution.broker import Rejection
from sillage.execution.costs import CostModel
from sillage.execution.simulated import SimulatedBroker
from sillage.portfolio.rebalance import Rebalancer
from sillage.strategy.base import Strategy


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Everything needed to reproduce a run.

    Reproducibility is the point of gathering these into one object: a result is only
    meaningful alongside the exact costs, dates and rebalancing rules that produced it,
    and a config that lives in a function's arguments cannot be recorded next to the
    result it explains.
    """

    strategy: Strategy
    universe: Universe
    start: date
    end: date
    initial_cash: Decimal = DEFAULT_INITIAL_CASH
    costs: CostModel = field(default_factory=CostModel)
    rebalancer: Rebalancer = field(default_factory=Rebalancer)
    data_root: Path = Path("data")
    calendar: Calendar | None = None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """The complete record of one run."""

    config: BacktestConfig
    nav_points: list[NavPoint]
    fills: list[Fill]
    orders: list[Order]
    rejections: list[Rejection]
    first_rebalance: date | None

    @property
    def name(self) -> str:
        return self.config.strategy.name

    @property
    def initial_nav(self) -> Decimal:
        return self.config.initial_cash

    @property
    def final_nav(self) -> Decimal:
        return self.nav_points[-1].nav if self.nav_points else self.initial_nav

    @property
    def total_return(self) -> Decimal:
        """Cumulative return over the whole run.

        The one figure computed here, because the calibration test needs something to
        compare against a known total return. Everything else waits for Phase 2.
        """
        return safe_div(self.final_nav - self.initial_nav, self.initial_nav)

    @property
    def sessions(self) -> int:
        return len(self.nav_points)

    @property
    def total_commission(self) -> Decimal:
        return sum((f.commission for f in self.fills), start=ZERO)

    @property
    def total_slippage(self) -> Decimal:
        return sum((f.slippage for f in self.fills), start=ZERO)

    @property
    def total_costs(self) -> Decimal:
        return self.total_commission + self.total_slippage

    @property
    def traded_notional(self) -> Decimal:
        return sum((abs(f.quantity) * f.price for f in self.fills), start=ZERO)

    def summary(self) -> str:
        span = (
            f"{self.nav_points[0].session} to {self.nav_points[-1].session}"
            if self.nav_points
            else "no sessions"
        )
        return (
            f"{self.name}: {span}, "
            f"{float(self.initial_nav):,.0f} -> {float(self.final_nav):,.0f} "
            f"({float(self.total_return):+.1%}), "
            f"{len(self.fills)} fills, {float(self.total_costs):,.0f} in costs"
        )


def run_backtest(config: BacktestConfig) -> BacktestResult:
    """Replay `config` and return everything that happened."""
    store = BarStore(config.data_root)
    instruments = {i.symbol: i for i in config.universe.all_instruments}

    missing = sorted(s for s in instruments if not store.has(s))
    if missing:
        raise FileNotFoundError(
            f"no stored bars for {missing}; run `sillage data sync --universe "
            f"{config.universe.name}` first"
        )

    calendar = config.calendar or TradingCalendar()
    # No start bound: the strategy's lookback needs the history before the first
    # session it trades on. The `as_of` gate is what keeps that from being cheating.
    bars = load_bars(store, instruments.values(), calendar=calendar, end=config.end)
    journal = InMemoryJournal()

    engine = Engine(
        clock=BacktestClock(config.start, config.end, calendar),
        data=HistoricalFeed(bars),
        broker=SimulatedBroker(MarketFeed(bars), config.costs),
        strategy=config.strategy,
        instruments=instruments,
        rebalancer=config.rebalancer,
        journal=journal,
        initial_cash=config.initial_cash,
    )
    engine.run()

    return BacktestResult(
        config=config,
        nav_points=journal.nav_points,
        fills=journal.fills,
        orders=journal.orders,
        rejections=journal.rejections,
        first_rebalance=engine.first_rebalance,
    )


def annualised(total_return: Decimal, years: float) -> Decimal:
    """Compound a total return down to a yearly rate.

    Here rather than in Phase 2's metrics module only because the calibration test
    needs it to state a result in comparable terms. It moves when metrics arrive.
    """
    if years <= 0:
        return ZERO
    growth = float(dec(1) + total_return)
    if growth <= 0:
        return dec(-1)
    return dec(growth ** (1.0 / years) - 1.0)
