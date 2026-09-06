"""The events the engine reacts to.

A session produces exactly two moments the system cares about. At the **open**, orders
decided earlier can be executed. At the **close**, the day's price becomes known, the
book can be marked, and a new decision can be made.

Separating them is the whole point. If deciding and trading happened at one instant the
backtest could use a price to decide and then trade at that same price, which is
impossible in reality and is the single most common way a backtest lies. Here a
decision made on a `SESSION_CLOSE` event can only ever be filled on the following
`SESSION_OPEN`, because that is the next event the engine sees.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class EventKind(StrEnum):
    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"


@dataclass(frozen=True, slots=True)
class Event:
    """One instant the engine wakes up at.

    `ts` is the moment itself and is what gates every data read. `session` is the
    trading day it belongs to, carried separately because the day is the natural key
    for looking up a bar while the instant is the natural key for enforcing what was
    knowable.
    """

    kind: EventKind
    ts: datetime
    session: date

    def __post_init__(self) -> None:
        if self.ts.tzinfo is None:
            raise ValueError("event timestamp must be timezone-aware")

    @property
    def is_open(self) -> bool:
        return self.kind is EventKind.SESSION_OPEN

    @property
    def is_close(self) -> bool:
        return self.kind is EventKind.SESSION_CLOSE
