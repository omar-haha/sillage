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
    """Rebalance once a month, `offset` sessions before the month's last one.

    The last *tradable* session, not the 31st -- see `TradingCalendar`. Monthly is the
    default for this system because it is slow enough that costs stay a rounding error
    and slow enough that a 12-month momentum signal is not being asked to say something
    new every day, which it cannot.

    **`offset` exists because the choice of date is arbitrary and it matters.** Nothing
    makes the last session of the month a better moment to trade than the third-to-last
    or the tenth-to-last. But a strategy rebalanced on one of those dates and the same
    strategy rebalanced on another will hold different things for weeks at a time, and
    over twenty years the two can differ by a percent a year or more purely by luck.
    This is documented -- Hoffstein's "rebalance timing luck" -- and a backtest on a
    single date is one draw from that distribution, presented as if it were the answer.

    `offset=0` is month end, `offset=5` is roughly a week earlier, and so on. Two uses:
    measuring the size of the effect (`sillage timing-luck`), and eventually removing
    most of it by running several offsets side by side and averaging them, which is the
    standard remedy and costs nothing but bookkeeping.
    """

    name = "monthly"

    def __init__(self, offset: int = 0) -> None:
        if offset < 0:
            raise ValueError("offset must not be negative")
        self.offset = offset
        if offset:
            self.name = f"monthly-{offset}"

    def is_rebalance_session(self, session: date, calendar: Calendar) -> bool:
        if not self.offset:
            return calendar.is_month_end_session(session)
        return session in _offset_rebalance_days(calendar, self.offset)

    def reset(self) -> None:
        return None


#: Keyed by calendar name and offset. A hand-rolled cache rather than `lru_cache`
#: because a calendar is not hashable, and keying on its name is what actually
#: identifies it.
_OFFSET_CACHE: dict[tuple[str, int], frozenset[date]] = {}


def _offset_rebalance_days(calendar: Calendar, offset: int) -> frozenset[date]:
    """Every session that sits `offset` trading days before a month end.

    Computed once per calendar and cached, because the alternative -- walking forward
    from each session to find its month end -- is a handful of calendar lookups on
    every one of five thousand days.

    The whole span of the calendar is materialised rather than just the backtest's
    range, so the answer does not depend on which window happened to be asked for.
    """
    key = (calendar.name, offset)
    cached = _OFFSET_CACHE.get(key)
    if cached is not None:
        return cached

    days = [s.day for s in calendar.sessions(*calendar.bounds)]
    ends = [
        index
        for index, day in enumerate(days[:-1])
        if (days[index + 1].year, days[index + 1].month) != (day.year, day.month)
    ]
    computed = frozenset(days[index - offset] for index in ends if index >= offset)
    _OFFSET_CACHE[key] = computed
    return computed


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
