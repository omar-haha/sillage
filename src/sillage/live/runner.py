"""Running the fund for real, one step at a time.

The whole file is about restarts. A backtest runs once, in one process, with everything
in memory; a live fund runs forever, in a process that will be killed, deployed over,
OOM-ed and rebooted, and the only state that survives is what was written to disk. So
this does not hold a loop open. It answers a single question -- *what has happened since
the last thing I recorded, and what should I do about it* -- and then exits.

That shape is what makes it safe. Called every evening by cron, it processes the day.
Called twice by accident, the second call finds nothing outstanding and does nothing.
Called after a week of downtime, it replays the week in order. Killed halfway through,
it resumes from the journal, because the journal was written before the orders went out
rather than after.

**It is the same engine.** `Engine.step` is the one place the decide-execute-mark cycle
exists, and this calls it with a `LiveClock` where a backtest passes a `BacktestClock`.
Nothing about the strategy, the sizing, the rebalancer or the accounting knows which
mode it is in, which is the only way the claim "the backtest and the live system run the
same code" can survive contact with a deadline.

**On the honesty of paper trading against our own simulator.** This validates
scheduling, restart safety, state persistence, reconciliation and risk limits. It cannot
validate fill prices, because it *is* the fill model -- there is no second opinion to
disagree with. Phase 5b puts a real broker on the other side for exactly that reason.

**The daily-bar constraint, stated rather than hidden.** With end-of-day data there is
no way to trade at this morning's open at 09:31, because this morning's bar does not
exist yet. So the natural cadence is once per evening: the day's open executes what was
decided at yesterday's close, and the day's close decides for tomorrow. The paper fund
is therefore a T+1 simulation on real prices. That is what 5a is for.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sillage.core.calendar import TradingCalendar
from sillage.core.money import dec
from sillage.core.types import Bar, Portfolio
from sillage.data.store import BarStore
from sillage.data.universe import Universe
from sillage.engine.clock import LiveClock
from sillage.engine.events import Event, EventKind
from sillage.engine.feed import HistoricalFeed, MarketFeed, load_bars
from sillage.engine.loop import Engine
from sillage.execution.broker import LiveBroker
from sillage.execution.costs import CostModel
from sillage.execution.simulated import SimulatedBroker
from sillage.live.reconcile import Reconciliation, ReconciliationError, reconcile
from sillage.portfolio.rebalance import Rebalancer
from sillage.risk.limits import RiskLimits
from sillage.state.journal import SqliteJournal, positions_of
from sillage.strategy.base import Strategy

DEFAULT_INITIAL_CASH = dec("100000")


@dataclass(frozen=True, slots=True)
class LiveConfig:
    """Everything a live fund needs to know about itself.

    Deliberately the same shape as `BacktestConfig`: if running live needed a materially
    different description of the fund, the two would not really be the same system.
    """

    strategy: Strategy
    universe: Universe
    journal_path: Path
    data_root: Path = Path("data")
    initial_cash: Decimal = DEFAULT_INITIAL_CASH
    costs: CostModel = field(default_factory=CostModel)
    rebalancer: Rebalancer = field(default_factory=Rebalancer)
    limits: RiskLimits = field(default_factory=RiskLimits)
    #: How far back a fresh journal looks for sessions to process.
    lookback_days: int = 30
    #: Refuse to act if the newest stored bar is older than this many sessions. A failed
    #: data sync is silent by nature: the store still answers, with last week's prices,
    #: and the fund trades on them without complaint.
    max_staleness_sessions: int = 3
    #: A real broker. Left unset, the fund trades against its own simulator, which
    #: validates the machinery and grades its own fills. Set, the fund also reconciles
    #: against what the venue says it holds -- which is the first check in the run that
    #: can actually disagree with the journal.
    broker: LiveBroker | None = None


@dataclass(frozen=True, slots=True)
class LiveReport:
    """What one step did. Everything an operator needs to decide whether to worry."""

    sessions: tuple[date, ...]
    fills: int
    orders: int
    rejections: int
    nav: Decimal
    cash: Decimal
    positions: dict[str, Decimal]
    pending: int
    reconciliation: Reconciliation | None
    halted: str = ""

    @property
    def acted(self) -> bool:
        return bool(self.sessions)

    def __str__(self) -> str:
        if not self.acted:
            return "nothing to do: no completed sessions since the last run"
        span = (
            f"{self.sessions[0]}"
            if len(self.sessions) == 1
            else (f"{self.sessions[0]}..{self.sessions[-1]}")
        )
        state = f" HALTED: {self.halted}" if self.halted else ""
        return (
            f"{span}: {self.fills} fill(s), {self.orders} order(s), "
            f"{self.rejections} rejected, NAV {float(self.nav):,.2f}, "
            f"{len(self.positions)} position(s), {self.pending} pending{state}"
        )

    @property
    def stalled(self) -> bool:
        """Orders were placed and none of them filled.

        Worth its own flag because it is the shape of every quiet live failure: the fund
        keeps running, keeps reporting a NAV, and never trades. A cap set too tight did
        exactly this and looked like a healthy fund sitting in cash.
        """
        return self.orders > 0 and self.fills == 0


def run_once(
    config: LiveConfig,
    *,
    now: datetime | None = None,
    broker_positions: dict[str, Decimal] | None = None,
) -> LiveReport:
    """Process everything that has happened since the last recorded session.

    `broker_positions` is what a real broker says it holds. With `config.broker` set it
    is fetched from the venue automatically and this argument is only for tests. Left
    unset with no broker, the fund is reconciled against itself, which always agrees --
    that is not a check, it is a tautology, and it is exactly what self-simulated paper
    trading is worth.
    """
    now = now or datetime.now(UTC)
    journal = SqliteJournal(config.journal_path)
    instruments = {i.symbol: i for i in config.universe.all_instruments}

    portfolio = journal.portfolio(instruments, initial_cash=config.initial_cash)

    if broker_positions is None and config.broker is not None:
        broker_positions = config.broker.positions()

    # Reconcile before anything else. A fund that cannot account for what it owns has no
    # business deciding what to own next.
    reconciliation = None
    if broker_positions is not None:
        reconciliation = reconcile(positions_of(portfolio), broker_positions)
        if not reconciliation:
            raise ReconciliationError(str(reconciliation))

    calendar = TradingCalendar()
    clock = LiveClock(
        since=journal.last_session(),
        until=now,
        calendar=calendar,
        lookback_days=config.lookback_days,
    )

    bars = load_bars(
        BarStore(config.data_root),
        instruments.values(),
        calendar=calendar,
        end=now.date(),
    )
    _require_fresh_data(bars, calendar, now, config.max_staleness_sessions)

    engine = Engine(
        clock=clock,
        data=HistoricalFeed(bars),
        broker=config.broker or SimulatedBroker(MarketFeed(bars), config.costs),
        strategy=config.strategy,
        instruments=instruments,
        rebalancer=config.rebalancer,
        journal=journal,
        initial_cash=config.initial_cash,
        limits=config.limits,
        high_water_mark=journal.high_water_mark(),
    )
    # Restored rather than started fresh: the book, and any order decided at a close the
    # process did not survive.
    engine.portfolio = portfolio
    engine.pending.put(journal.load_pending(instruments))

    before = journal.counts()
    sessions: list[date] = []
    engine.strategy.reset()
    for event in clock.events():
        engine.step(event)
        if event.is_close:
            sessions.append(event.session)

    if config.broker is not None:
        _submit_ahead(engine, calendar, clock.now)

    # Persisted last, so a crash mid-step leaves the previous pending set intact rather
    # than an empty one. Re-processing a session is safe; losing an order is not.
    outstanding = engine.pending.peek()
    journal.save_pending(outstanding)

    after = journal.counts()
    prices = HistoricalFeed(bars).closes(instruments, as_of=clock.now)
    return LiveReport(
        sessions=tuple(sessions),
        fills=after["fills"] - before["fills"],
        orders=after["orders"] - before["orders"],
        rejections=after["rejections"] - before["rejections"],
        nav=engine.portfolio.nav(prices),
        cash=engine.portfolio.cash,
        positions=positions_of(engine.portfolio),
        pending=len(outstanding),
        reconciliation=reconciliation,
        halted=engine.halted,
    )


def _submit_ahead(engine: Engine, calendar: TradingCalendar, now: datetime) -> None:
    """Get tonight's decisions to the venue before tomorrow's opening auction.

    The one place the live path genuinely cannot mirror the backtest, and it is worth
    being precise about why. In a replay, an order decided at Tuesday's close is
    executed when the engine later reaches Wednesday's open -- the engine gets to travel
    forward in time and act retroactively. A real broker does not: a market-on-open
    order has to be sitting at the exchange *before* the auction, which means it must be
    sent on Tuesday evening, hours before the event that will formally execute it.

    So the orders are submitted now, against tomorrow's open. Because the market is shut
    they will not fill, and the broker reports them outstanding, which puts them straight
    back into pending. When the next run reaches that open for real, the adapter
    recognises its own order references at the venue and collects the fills instead of
    sending a second set. That recognition is the whole reason every order carries the
    engine's id into the broker.

    Live brokers only. Handing a future open to the simulator would ask it for a price
    that does not exist yet; it would refuse, and refusals are not re-queued.
    """
    if not engine.pending.peek():
        return
    session = calendar.next_session(now.date())
    opens = calendar.sessions(session, session)
    if not opens:
        return
    engine.step(Event(EventKind.SESSION_OPEN, opens[0].open, session))


class StaleDataError(RuntimeError):
    """Raised when the fund is asked to trade on prices that are out of date."""


def _require_fresh_data(
    bars: Mapping[str, list[Bar]],
    calendar: TradingCalendar,
    now: datetime,
    tolerance: int,
) -> None:
    """Refuse to act on a store that has stopped being updated.

    The dangerous property of stale market data is that it is not an error. The files
    are there, the reads succeed, every price is a real price -- just the wrong week's.
    A fund on a cron job will trade on them every evening and report nothing unusual,
    which is why this is checked before anything else rather than left to a human
    noticing that a number looks old.
    """
    newest = max((series[-1].ts.date() for series in bars.values() if series), default=None)
    if newest is None:
        raise StaleDataError("no stored bars at all; run `sillage data sync`")

    behind = len(calendar.sessions(newest, now.date())) - 1
    if behind > tolerance:
        raise StaleDataError(
            f"newest bar is {newest}, {behind} session(s) behind {now.date()}; "
            f"run `sillage data sync` before trading"
        )


def status(config: LiveConfig) -> dict[str, object]:
    """What the fund looks like right now, without touching it."""
    journal = SqliteJournal(config.journal_path)
    instruments = {i.symbol: i for i in config.universe.all_instruments}
    portfolio: Portfolio = journal.portfolio(instruments, initial_cash=config.initial_cash)
    history = journal.nav_history()
    return {
        "journal": str(config.journal_path),
        "last_session": journal.last_session(),
        "sessions_recorded": len(history),
        "nav": history[-1].nav if history else config.initial_cash,
        "high_water_mark": journal.high_water_mark(),
        "cash": portfolio.cash,
        "positions": positions_of(portfolio),
        "pending": len(journal.load_pending(instruments)),
        "counts": journal.counts(),
    }
