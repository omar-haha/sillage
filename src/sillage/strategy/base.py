"""What a strategy is, and when it is allowed to speak.

A strategy answers exactly one question: *given everything knowable at this instant,
what fraction of the fund should be in each asset?* It does not see the portfolio, does
not know what it currently holds, does not place orders, and cannot observe its own
performance. That narrowness is deliberate and is what makes the rest of the system
reusable -- swapping dual momentum for risk parity means writing one class, because
nothing downstream knows which strategy produced the weights it is acting on.

It also makes strategies testable in the only way that matters: feed one a fixed slice
of history and assert on the weights. No portfolio, no broker, no clock.

**Weights, not orders.** A strategy that emitted orders would have to know current
positions, and would then be able to react to its own trading -- which is how a
backtest quietly becomes path-dependent in ways nobody intended. Weights are a pure
function of market history, and turning a weight into an order is the rebalancer's job.

**`None` is not the same as `{}`.** Returning an empty mapping means "hold nothing,
move to cash", which is a real and sometimes correct decision. Returning `None` means
"no opinion" -- still warming up, or not a scheduled decision point -- and leaves the
book untouched. Collapsing the two would have every strategy liquidate the portfolio on
its first day while it waited for enough history.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from sillage.core.calendar import Calendar

if TYPE_CHECKING:
    from sillage.engine.feed import DataSource

TargetWeights = dict[str, Decimal]


@runtime_checkable
class Schedule(Protocol):
    """When a strategy gets to make a decision."""

    name: str

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool: ...

    def reset(self) -> None:
        """Return to the state before any session was seen.

        Only schedules that count occurrences need this, but every schedule has it so
        the engine can reset unconditionally at the start of a run. Walk-forward
        analysis runs the same objects over many overlapping windows, and a schedule
        that remembered the previous window would silently skip the first trade of the
        next one.
        """
        ...


class Daily:
    """Rebalance every session. Maximal responsiveness, maximal turnover."""

    name = "daily"

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool:  # noqa: ARG002
        return True

    def reset(self) -> None:
        return None


class Monthly:
    """Rebalance on the last tradable session of each month.

    The last *tradable* session, not the 31st -- see `TradingCalendar`. Monthly is the
    default for this system because it is slow enough that costs stay a rounding error
    and slow enough that a 12-month momentum signal is not being asked to say something
    new every day, which it cannot.
    """

    name = "monthly"

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool:
        return calendar.is_month_end_session(session)

    def reset(self) -> None:
        return None


class Weekly:
    """Rebalance on a chosen weekday, defaulting to Friday.

    When the chosen day is a holiday the *preceding* session stands in, which mirrors
    how `Monthly` treats a month ending on a weekend. Both answer the same question --
    what is the last session of this period? -- and a schedule where the weekly rule
    looked forward while the monthly one looked back would be a trap for anyone reading
    a backtest's trade dates.
    """

    name = "weekly"

    def __init__(self, weekday: int = 4) -> None:
        if not 0 <= weekday <= 6:
            raise ValueError("weekday must be 0 (Monday) through 6 (Sunday)")
        self.weekday = weekday

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool:
        if session.weekday() == self.weekday:
            return True
        # Otherwise this is the last session before the chosen day only if the chosen
        # day falls in the gap before the next one.
        day = session + timedelta(days=1)
        following = calendar.next_session(session)
        while day < following:
            if day.weekday() == self.weekday:
                return True
            day += timedelta(days=1)
        return False

    def reset(self) -> None:
        return None


class Once:
    """Fire on the first session asked about, then never again.

    This is buy-and-hold: acquire the position at inception and hold it through
    everything. The only stateful schedule, hence the emphasis on `reset`.
    """

    name = "once"

    def __init__(self) -> None:
        self._fired = False

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool:  # noqa: ARG002
        if self._fired:
            return False
        self._fired = True
        return True

    def reset(self) -> None:
        self._fired = False


@runtime_checkable
class Strategy(Protocol):
    """Market history in, target weights out."""

    name: str
    schedule: Schedule

    @property
    def warmup_sessions(self) -> int:
        """Sessions of history needed before the strategy can say anything.

        The engine uses this only for reporting the point at which the backtest becomes
        meaningful. Enforcement is the strategy's own job -- it returns `None` while it
        is not ready -- because only the strategy knows whether its warm-up is per
        asset, and whether a partially warm universe is still tradable.
        """
        ...

    def reset(self) -> None: ...

    def target_weights(self, *, as_of: datetime, data: DataSource) -> TargetWeights | None:
        """The desired fraction of NAV in each asset, or `None` for no opinion.

        Weights are fractions of NAV: 0.25 means a quarter of the fund. They need not
        sum to one -- the shortfall is simply cash -- and should not exceed one in
        total unless the portfolio layer has been configured to permit leverage.
        """
        ...
