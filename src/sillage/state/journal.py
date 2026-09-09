"""The durable record: what was decided, what was sent, and what came back.

In a backtest the journal is a convenience -- the process holds everything in memory and
exits when it is done. Live it is the only thing standing between a restart and a
disaster, because the alternative to "read what actually happened from disk" is "assume
what probably happened", and a system that assumes will eventually assume it did not
already send an order it did send.

Three properties, in order of how much they matter.

**Append-only.** Rows are inserted and never updated. A journal whose entries can be
edited is not a journal; reconciliation is only meaningful if the record is what
happened rather than what a later run believed should have happened.

**Idempotent by construction.** Orders are keyed on the client order id the engine
generates, with `INSERT OR IGNORE`. Submitting the same order twice is not an error that
has to be caught somewhere, it is a no-op at the storage layer -- which is the only place
that can enforce it across a crash.

**Money is stored as text.** SQLite has no decimal type, and its REAL is a float. Storing
a Decimal through a float and reading it back would reintroduce, at the persistence
layer, precisely the error the whole domain model is built to avoid. Text round-trips
exactly.

Plain `sqlite3` rather than an ORM. This is four append-only tables and one derived view;
an ORM would add a dependency, a migration story and a layer of indirection in exchange
for nothing, and the SQL below is short enough to read in one sitting. If Phase 6 needs
Postgres, the statements are standard.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sillage.core.money import ZERO, dec, quantize_cash
from sillage.core.types import Fill, Instrument, Order, OrderType, Portfolio
from sillage.engine.journal import NavPoint
from sillage.execution.broker import Rejection

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    quantity        TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    limit_price     TEXT,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS fills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   TEXT NOT NULL,
    ts         TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    quantity   TEXT NOT NULL,
    price      TEXT NOT NULL,
    commission TEXT NOT NULL,
    slippage   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS fills_by_order ON fills (order_id);

CREATE TABLE IF NOT EXISTS rejections (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    ts       TEXT,
    symbol   TEXT NOT NULL,
    quantity TEXT NOT NULL,
    reason   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nav (
    session        TEXT PRIMARY KEY,
    ts             TEXT NOT NULL,
    nav            TEXT NOT NULL,
    cash           TEXT NOT NULL,
    gross_exposure TEXT NOT NULL,
    holdings       INTEGER NOT NULL,
    weights        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

#: Orders decided at a close and not yet executed. Held here rather than in memory so
#: that an order decided on Friday evening survives until Monday morning, including
#: across a restart in between.
PENDING = "pending_orders"


class SqliteJournal:
    """An append-only journal on disk. Satisfies `engine.journal.Journal`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, isolation_level=None)
        # Write-ahead logging so a reader (the dashboard, an operator) never blocks the
        # writer, and FULL synchronous so a fill is on disk before the process is told
        # it happened. Durability is the entire point of this file.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.row_factory = sqlite3.Row
        return connection

    # ------------------------------------------------------------------ writing

    def record_order(self, order: Order) -> None:
        """Record an order before it is sent. Recording the same one twice is a no-op.

        Written *before* submission on purpose. If the process dies between here and the
        broker, the journal says an order may be in flight, and reconciliation can find
        out. The opposite order -- submit, then record -- can lose an order entirely.
        """
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    order.client_order_id,
                    _stamp(order.created_at),
                    order.instrument.symbol,
                    str(order.quantity),
                    str(order.order_type),
                    str(order.limit_price) if order.limit_price is not None else None,
                    order.reason,
                ),
            )

    def record_fill(self, fill: Fill) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO fills (order_id, ts, symbol, quantity, price, commission, slippage)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    fill.order_id,
                    _stamp(fill.ts),
                    fill.instrument.symbol,
                    str(fill.quantity),
                    str(fill.price),
                    str(fill.commission),
                    str(fill.slippage),
                ),
            )

    def record_rejection(self, rejection: Rejection) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO rejections (order_id, ts, symbol, quantity, reason)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    rejection.order.client_order_id,
                    _stamp(rejection.ts),
                    rejection.order.instrument.symbol,
                    str(rejection.order.quantity),
                    rejection.reason,
                ),
            )

    def record_nav(self, point: NavPoint) -> None:
        """One row per session. Re-marking a session replaces it rather than duplicating.

        The only place anything is overwritten, and it is not history: a NAV mark is a
        statement about a session, and a session has one closing value. Re-running the
        same day must not produce two.
        """
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO nav VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    point.session.isoformat(),
                    _stamp(point.ts),
                    str(point.nav),
                    str(point.cash),
                    str(point.gross_exposure),
                    point.holdings,
                    json.dumps({s: str(w) for s, w in point.weights.items()}),
                ),
            )

    # ------------------------------------------------------------------ reading

    def has_order(self, client_order_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return row is not None

    def fills(self, instruments: Mapping[str, Instrument]) -> list[Fill]:
        """Every fill, oldest first, rebuilt as domain objects.

        A fill for a symbol absent from `instruments` is skipped rather than guessed at:
        it means the universe changed since the fill, and inventing an `Instrument` for
        it would put a position in the book with made-up lot sizes.
        """
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM fills ORDER BY id").fetchall()
        return [
            Fill(
                instrument=instruments[row["symbol"]],
                ts=datetime.fromisoformat(row["ts"]),
                quantity=dec(row["quantity"]),
                price=dec(row["price"]),
                commission=dec(row["commission"]),
                slippage=dec(row["slippage"]),
                order_id=row["order_id"],
            )
            for row in rows
            if row["symbol"] in instruments
        ]

    def portfolio(
        self, instruments: Mapping[str, Instrument], *, initial_cash: Decimal
    ) -> Portfolio:
        """Rebuild the book by replaying every fill.

        Derived, never stored. A stored position and a stored fill history can disagree,
        and when they do there is no way to tell which is right; a single source that has
        to be replayed cannot drift from itself.
        """
        portfolio = Portfolio(cash=quantize_cash(initial_cash))
        for fill in self.fills(instruments):
            portfolio = portfolio.apply_fill(fill)
        return portfolio

    def nav_history(self) -> list[NavPoint]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT * FROM nav ORDER BY session").fetchall()
        return [
            NavPoint(
                session=date.fromisoformat(row["session"]),
                ts=datetime.fromisoformat(row["ts"]),
                nav=dec(row["nav"]),
                cash=dec(row["cash"]),
                gross_exposure=dec(row["gross_exposure"]),
                holdings=row["holdings"],
                weights={s: dec(w) for s, w in json.loads(row["weights"]).items()},
            )
            for row in rows
        ]

    def last_session(self) -> date | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT MAX(session) AS last FROM nav").fetchone()
        return date.fromisoformat(row["last"]) if row and row["last"] else None

    def high_water_mark(self) -> Decimal:
        """The highest NAV ever marked. The drawdown kill-switch measures against this."""
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT nav FROM nav").fetchall()
        return max((dec(row["nav"]) for row in rows), default=ZERO)

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection:
            return {
                table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                for table in ("orders", "fills", "rejections", "nav")
            }

    # ------------------------------------------------------------------ pending orders

    def save_pending(self, orders: Sequence[Order]) -> None:
        """Persist the orders waiting for the next open, replacing any already held."""
        for order in orders:
            self.record_order(order)
        payload = json.dumps([_order_row(o) for o in orders])
        with self._connect() as connection:
            connection.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (PENDING, payload))

    def load_pending(self, instruments: Mapping[str, Instrument]) -> list[Order]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (PENDING,)).fetchone()
        if row is None:
            return []
        return [
            _order_from(entry, instruments)
            for entry in json.loads(row["value"])
            if entry["symbol"] in instruments
        ]

    def clear_pending(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM meta WHERE key = ?", (PENDING,))


def _order_row(order: Order) -> dict[str, str | None]:
    return {
        "client_order_id": order.client_order_id,
        "symbol": order.instrument.symbol,
        "quantity": str(order.quantity),
        "order_type": str(order.order_type),
        "limit_price": str(order.limit_price) if order.limit_price is not None else None,
        "created_at": _stamp(order.created_at),
        "reason": order.reason,
    }


def _order_from(entry: Mapping[str, str | None], instruments: Mapping[str, Instrument]) -> Order:
    """Rebuild an order from its stored row.

    Every field is nullable in the JSON's type because SQLite text columns are, so each
    is narrowed explicitly rather than trusted. A pending order read back wrong is an
    order sent wrong.
    """
    symbol, kind = entry["symbol"], entry["order_type"]
    if symbol is None or kind is None:
        raise ValueError(f"stored order is missing a symbol or type: {dict(entry)}")
    created = entry["created_at"]
    limit = entry["limit_price"]
    return Order(
        instrument=instruments[symbol],
        quantity=dec(entry["quantity"] or "0"),
        order_type=OrderType(kind),
        limit_price=dec(limit) if limit else None,
        client_order_id=entry["client_order_id"] or "",
        created_at=datetime.fromisoformat(created) if created else None,
        reason=entry["reason"] or "",
    )


def _stamp(moment: datetime | None) -> str:
    """ISO-8601, always UTC, never naive. A naive timestamp in a journal is unusable."""
    if moment is None:
        return datetime.now(UTC).isoformat()
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()


def positions_of(portfolio: Portfolio) -> dict[str, Decimal]:
    """Symbol to quantity, flat positions dropped. The shape reconciliation compares."""
    return {s: p.quantity for s, p in portfolio.positions.items() if not p.is_flat}
