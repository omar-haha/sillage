"""The loop. One of these exists, and it runs in every mode.

This file is the reason the project is shaped the way it is. There is no backtest
engine and no separate live engine; there is this, plus a clock and a broker chosen at
the edges. A bug in position sizing shows up identically in a 2008 replay and in
tomorrow's real trade, and a fix to one is a fix to both -- which is not true of any
system that keeps a vectorised research backtest alongside a hand-written live script.

The cycle, per session:

    open   ->  execute whatever was decided at the previous close
    close  ->  mark the book, then decide (if the schedule says so)

Note what that ordering forbids. A decision is made from data at a close and can only
be filled at the *next* open, so the price it trades at did not exist when it was made.
There is no configuration flag to disable this and no fast path that skips it, because
the moment there is one, someone will use it and the backtest will start lying.

The engine holds no numerics. It moves immutable value objects between five components
that each do one thing, which is what makes the whole cycle auditable by reading forty
lines.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

from sillage.core.money import ZERO, dec
from sillage.core.types import Instrument, Order, Portfolio
from sillage.engine.clock import Clock
from sillage.engine.events import Event
from sillage.engine.feed import DataSource
from sillage.engine.journal import InMemoryJournal, Journal, NavPoint
from sillage.execution.broker import Broker, Rejection
from sillage.portfolio.rebalance import Rebalancer
from sillage.risk.limits import RiskLimits, filter_orders
from sillage.strategy.base import Strategy

DEFAULT_INITIAL_CASH = dec("100000")


class Engine:
    """Drives one run from the first session to the last."""

    def __init__(
        self,
        *,
        clock: Clock,
        data: DataSource,
        broker: Broker,
        strategy: Strategy,
        instruments: Mapping[str, Instrument],
        rebalancer: Rebalancer | None = None,
        journal: Journal | None = None,
        initial_cash: Decimal = DEFAULT_INITIAL_CASH,
        limits: RiskLimits | None = None,
        high_water_mark: Decimal = ZERO,
    ) -> None:
        if initial_cash <= ZERO:
            raise ValueError("initial cash must be positive")
        self.clock = clock
        self.data = data
        self.broker = broker
        self.strategy = strategy
        self.instruments = dict(instruments)
        self.rebalancer = rebalancer or Rebalancer()
        self.journal: Journal = journal if journal is not None else InMemoryJournal()
        self.portfolio = Portfolio(cash=initial_cash)
        #: Optional. When set, every order is checked against the fund's own rules
        #: before it reaches the broker -- see `sillage.risk.limits`. Off by default
        #: in a backtest, because a limit that silently suppressed trades would make
        #: the result a study of the limits rather than of the strategy.
        self.limits = limits
        #: Highest NAV ever marked. Seeded from the journal on a live restart, so a
        #: drawdown kill-switch measures against the real peak and not this run's.
        self.high_water_mark = high_water_mark
        self.halted = ""
        self.first_rebalance: date | None = None
        #: Orders decided at the last close, waiting for the next open. Public so a
        #: live runner can persist it across process restarts -- an order decided on
        #: Friday evening has to survive until Monday morning.
        self.pending = self._Pending()

    def run(self) -> Portfolio:
        """Replay every event the clock produces. Returns the final book."""
        self.strategy.reset()
        for event in self.clock.events():
            self.step(event)
        return self.portfolio

    def step(self, event: Event) -> None:
        """React to one event. The entire cycle, and the only place it lives.

        Public, and called one event at a time by the live runner, because the claim
        this project is built on -- that backtesting and live trading run the same code
        -- is only true if they literally call the same method. A live loop that
        reimplemented "execute, then mark, then decide" would be a second engine wearing
        the first one's documentation, and the two would drift apart in exactly the ways
        nobody notices until money is involved.
        """
        if event.is_open:
            self._execute(self.pending.take(), event)
        else:
            self._mark(event)
            if self._should_decide(event.session):
                self.pending.put(self._decide(event))

    # ------------------------------------------------------------------ the two halves

    def _execute(self, orders: list[Order], event: Event) -> None:
        if not orders:
            return

        if self.limits is not None:
            prices = self._prices(event)
            decision = filter_orders(
                orders,
                portfolio=self.portfolio,
                prices=prices,
                limits=self.limits,
                high_water_mark=self.high_water_mark,
            )
            for rejection in decision.rejected:
                self.journal.record_rejection(rejection)
            self.halted = decision.halted
            if decision.halted:
                # Every order is dropped and recorded as refused, so the reason is in
                # the journal rather than only in a log nobody reads.
                for order in orders:
                    self.journal.record_rejection(Rejection(order, decision.halted, event.ts))
                return
            orders = list(decision.allowed)
            if not orders:
                return

        report = self.broker.execute(
            orders,
            portfolio=self.portfolio,
            session=event.session,
            ts=event.ts,
        )
        for fill in report.fills:
            self.portfolio = self.portfolio.apply_fill(fill)
            self.journal.record_fill(fill)
        for rejection in report.rejections:
            self.journal.record_rejection(rejection)

    def _decide(self, event: Event) -> list[Order]:
        targets = self.strategy.target_weights(as_of=event.ts, data=self.data)
        if targets is None:
            # No opinion. Distinct from an empty mapping, which would mean "sell
            # everything" -- see the note in `strategy.base`.
            #
            # The firing is handed back, because a schedule that only fires a fixed
            # number of times must not spend one on a session where nothing was
            # decided. See `Schedule.defer` for the bug that established this.
            self.strategy.schedule.defer()
            return []

        unknown = sorted(set(targets) - set(self.instruments))
        if unknown:
            raise KeyError(
                f"strategy {self.strategy.name!r} targeted symbols outside its universe: {unknown}"
            )

        if self.first_rebalance is None:
            self.first_rebalance = event.session

        orders = self.rebalancer.diff(
            targets,
            portfolio=self.portfolio,
            prices=self._prices(event),
            instruments=self.instruments,
            ts=event.ts,
        )
        for order in orders:
            self.journal.record_order(order)
        return orders

    # ------------------------------------------------------------------ bookkeeping

    def _mark(self, event: Event) -> None:
        """Value the fund at this close and journal it."""
        prices = self._prices(event)
        self.journal.record_nav(
            NavPoint(
                session=event.session,
                ts=event.ts,
                nav=self.portfolio.nav(prices),
                cash=self.portfolio.cash,
                gross_exposure=self.portfolio.gross_exposure(prices),
                holdings=sum(1 for p in self.portfolio.positions.values() if not p.is_flat),
                weights=self.portfolio.weights(prices),
            )
        )
        self.high_water_mark = max(self.high_water_mark, self.portfolio.nav(prices))

    def _prices(self, event: Event) -> dict[str, Decimal]:
        """Last known close for everything tradable, as of this instant.

        "Last known" rather than "today's", because an asset that did not trade today
        still has a value. Using its most recent close is what a fund administrator
        does; treating it as absent would make NAV jump on days a holding was quiet.
        """
        return self.data.closes(self.instruments, as_of=event.ts)

    def _should_decide(self, session: date) -> bool:
        return self.strategy.schedule.is_rebalance_session(session, self.clock.calendar)

    class _Pending:
        """Orders waiting for the next open.

        A one-slot mailbox rather than a queue. If a decision is somehow made twice
        before the next open, the newer one replaces the older rather than both being
        sent, because they express the same intent and submitting both would double the
        trade. Taking always empties it: an order sits for one session, and if the
        market could not fill it that morning it is not silently retried tomorrow with
        a stale rationale.
        """

        def __init__(self) -> None:
            self._orders: list[Order] = []

        def put(self, orders: list[Order]) -> None:
            self._orders = orders

        def take(self) -> list[Order]:
            orders, self._orders = self._orders, []
            return orders

        def peek(self) -> list[Order]:
            """What is still waiting, without consuming it.

            The live runner persists this after each step; taking it to write it down
            would mean a crash during the write lost the order entirely.
            """
            return list(self._orders)
