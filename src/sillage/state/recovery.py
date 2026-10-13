"""Strict statement-backed repair; never infer executions from broker positions.

Only whole, uniquely matched USD stock/ETF orders are supported. Raw fill records
are retained; corrections are append-only amendments. NAV is derived and rebuilt.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from sillage.core.calendar import TradingCalendar
from sillage.core.money import dec, quantize_cash, quantize_price
from sillage.core.types import AssetClass, Fill, Instrument, Portfolio
from sillage.data.store import BarStore
from sillage.state.journal import EFFECTIVE_FILLS, SqliteJournal, positions_of


@dataclass(frozen=True)
class StatementTrade:
    row: int
    symbol: str
    ts: datetime
    quantity: Decimal
    price: Decimal
    commission: Decimal


def number(value: str) -> Decimal:
    return dec(value.replace(",", ""))


def trade_time(value: str) -> datetime:
    return (
        datetime.strptime(value, "%Y-%m-%d, %H:%M:%S")
        .replace(tzinfo=ZoneInfo("America/New_York"))
        .astimezone(UTC)
    )


class ConfirmationTables(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def read_evidence(
    statement: Path,
    confirmations: Path,
    expected_account: str | None = None,
    *,
    starting_positions: dict[str, Decimal] | None = None,
) -> tuple[list[StatementTrade], Decimal, date]:
    headers: dict[str, list[str]] = {}
    trades = []
    cash: dict[str, Decimal] = {}
    positions: dict[str, Decimal] = {}
    coverage_end = None
    with statement.open(newline="", encoding="utf-8-sig") as stream:
        for index, row in enumerate(csv.reader(stream), 1):
            if len(row) < 2:
                continue
            if row[1] == "Header":
                headers[row[0]] = row[2:]
                continue
            if row[1] != "Data":
                continue
            values = dict(zip(headers.get(row[0], []), row[2:], strict=False))
            if row[0] == "Statement" and values.get("Field Name") == "Period":
                coverage_end = datetime.strptime(
                    values["Field Value"].split(" - ")[-1], "%B %d, %Y"
                ).date()
            if row[0] == "Cash Report" and values.get("Currency") == "USD":
                cash[values["Currency Summary"]] = number(values["Total"])
            if row[0] == "Open Positions" and values.get("DataDiscriminator") == "Summary":
                if values.get("Currency") != "USD" or values.get("Asset Category") != "Stocks":
                    raise ValueError("Unsupported statement position")
                positions[values["Symbol"]] = number(values["Quantity"])
            if row[0] != "Trades" or values.get("DataDiscriminator") != "Order":
                continue
            if values.get("Currency") != "USD" or values.get("Asset Category") != "Stocks":
                raise ValueError("Only USD stock/ETF executions are supported")
            if expected_account is not None and values.get("Account") != expected_account:
                raise ValueError("Statement account differs from the connected paper account")
            qty, price = number(values["Quantity"]), number(values["T. Price"])
            commission = -number(values["Comm/Fee"])
            if not qty or price <= 0 or commission < 0:
                raise ValueError("Unsupported execution economics")
            if abs(number(values["Proceeds"]) + qty * price) > dec("0.01"):
                raise ValueError("Execution proceeds disagree with quantity/price")
            trades.append(
                StatementTrade(
                    index, values["Symbol"], trade_time(values["Date/Time"]), qty, price, commission
                )
            )
    if (
        not trades
        or "Starting Cash" not in cash
        or (starting_positions is None and cash.get("Starting Cash") != 0)
        or "Ending Cash" not in cash
        or coverage_end is None
    ):
        raise ValueError(
            "Statement must cover the complete USD trade history from a zero USD cash start"
        )
    trades.sort(key=lambda trade: (trade.ts, trade.row))
    calculated = sum((-trade.quantity * trade.price - trade.commission for trade in trades), dec(0))
    movement = cash["Ending Cash"] - cash["Starting Cash"]
    if abs(calculated - movement) > dec("0.01"):
        raise ValueError("Statement USD cash includes unexplained cash movements")
    held: dict[str, Decimal] = dict(starting_positions or {})
    for trade in trades:
        held[trade.symbol] = held.get(trade.symbol, dec(0)) + trade.quantity
    if {symbol: qty for symbol, qty in held.items() if qty} != positions:
        raise ValueError("Statement trades do not explain closing positions")
    parser = ConfirmationTables()
    parser.feed(confirmations.read_text())
    confirmed = []
    for row in parser.rows:
        # The HTML export has both '-' order-summary and venue execution rows.
        if len(row) == 13 and row[5] in {"BUY", "SELL"} and row[4] not in {"", "-"}:
            if expected_account is not None and row[0] != expected_account:
                raise ValueError(
                    "Trade confirmation account differs from the connected paper account"
                )
            confirmed.append(
                (
                    row[1],
                    trade_time(row[2]),
                    number(row[6]),
                    number(row[7]),
                    -number(row[9]) - number(row[10]),
                )
            )
    for trade in trades:
        matches = [
            row
            for row in confirmed
            if row[:4] == (trade.symbol, trade.ts, trade.quantity, trade.price)
        ]
        if len(matches) != 1 or quantize_cash(matches[0][4]) != quantize_cash(trade.commission):
            raise ValueError("Execution lacks a unique corroborating trade confirmation")
    if len(confirmed) != len(trades):
        raise ValueError(
            "Confirmation report contains extra or split executions; manual review required"
        )
    return trades, movement, coverage_end


def fingerprint(connection: sqlite3.Connection) -> str:
    tables = ("orders", "fills", "fill_amendments", "nav", "meta", "statement_recoveries")
    state = {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in tables
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()


def repair(
    journal_path: Path,
    statement: Path,
    confirmations: Path,
    data_root: Path,
    through: date,
    expected_positions: dict[str, Decimal],
    initial_cash: Decimal = Decimal("25000"),
    expected_account: str | None = None,
    interval: bool = False,
) -> dict[str, object]:
    """Apply to an explicitly selected database (preview copy first).

    All changes commit atomically. An already-imported statement is a no-op.
    Old raw fills and NAV marks are retained in the evidence audit.
    """
    source_hash = hashlib.sha256(statement.read_bytes()).hexdigest()
    confirmation_hash = hashlib.sha256(confirmations.read_bytes()).hexdigest()
    SqliteJournal(journal_path)  # Install append-only audit schema.
    with sqlite3.connect(journal_path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        prior_recovery = connection.execute(
            "SELECT confirmation_sha256 FROM statement_recoveries WHERE source_sha256=?",
            (source_hash,),
        ).fetchone()
        if prior_recovery:
            if prior_recovery[0] != confirmation_hash:
                raise ValueError(
                    "Previously imported statement has different confirmation evidence"
                )
            return {"already_imported": True}
        before = fingerprint(connection)
        orders = [dict(row) for row in connection.execute("SELECT * FROM orders")]
        old_fills = [dict(row) for row in connection.execute(EFFECTIVE_FILLS)]
        old_nav = [dict(row) for row in connection.execute("SELECT * FROM nav ORDER BY session")]
        original_pending = connection.execute(
            "SELECT value FROM meta WHERE key='pending_orders'"
        ).fetchone()
        pending = json.loads(original_pending[0]) if original_pending else []
        opening = Portfolio(cash=initial_cash)
        instruments = {
            fill["symbol"]: Instrument(fill["symbol"], AssetClass.ETF) for fill in old_fills
        }
        if interval:
            if not old_nav or not pending:
                raise ValueError("Interval repair requires a prior mark and pending orders")
            for fill in old_fills:
                opening = opening.apply_fill(
                    Fill(
                        instruments[fill["symbol"]],
                        datetime.fromisoformat(fill["ts"]),
                        dec(fill["quantity"]),
                        dec(fill["price"]),
                        dec(fill["commission"]),
                        dec(fill["slippage"]),
                    )
                )
            ids = {row["client_order_id"] for row in pending}
            orders = [row for row in orders if row["client_order_id"] in ids]
        trades, ending_cash, coverage_end = read_evidence(
            statement,
            confirmations,
            expected_account,
            starting_positions=positions_of(opening) if interval else None,
        )
        instruments.update(
            {trade.symbol: Instrument(trade.symbol, AssetClass.ETF) for trade in trades}
        )
        if interval and any(
            trade.ts <= datetime.fromisoformat(old_nav[-1]["ts"]) for trade in trades
        ):
            raise ValueError("Interval execution overlaps already marked history")
        if len(orders) != len(trades):
            raise ValueError("Statement does not cover every journal order; manual review required")
        matched_ids: set[str] = set()
        additions = corrections = 0
        now = datetime.now(UTC).isoformat()
        for trade in trades:
            matches = [
                order
                for order in orders
                if order["symbol"] == trade.symbol
                and dec(order["quantity"]) == trade.quantity
                and datetime.fromisoformat(order["created_at"]) < trade.ts
                and trade.ts - datetime.fromisoformat(order["created_at"]) < timedelta(days=4)
            ]
            if len(matches) != 1 or matches[0]["client_order_id"] in matched_ids:
                raise ValueError("Trade cannot be uniquely matched to a persisted order")
            order_id = matches[0]["client_order_id"]
            matched_ids.add(order_id)
            recorded = [fill for fill in old_fills if fill["order_id"] == order_id]
            if len(recorded) > 1:
                raise ValueError("Split journal fills require manual review")
            if recorded:
                if interval:
                    raise ValueError(
                        "Interval order has prior partial fills; manual review required"
                    )
                fill = recorded[0]
                if dec(fill["quantity"]) != trade.quantity or fill["symbol"] != trade.symbol:
                    raise ValueError("Existing fill quantity differs from broker evidence")
                desired = (trade.ts.isoformat(), str(trade.price), str(trade.commission))
                if (
                    datetime.fromisoformat(fill["ts"]) != trade.ts
                    or dec(fill["price"]) != trade.price
                    or dec(fill["commission"]) != trade.commission
                ):
                    connection.execute(
                        "INSERT INTO fill_amendments (fill_id,ts,price,commission,slippage,source_sha256,source_row,created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (fill["id"], *desired, "0", source_hash, trade.row, now),
                    )
                    corrections += 1
            else:
                connection.execute(
                    "INSERT INTO fills (order_id,ts,symbol,quantity,price,commission,slippage) VALUES (?,?,?,?,?,?,?)",
                    (
                        order_id,
                        trade.ts.isoformat(),
                        trade.symbol,
                        str(trade.quantity),
                        str(trade.price),
                        str(trade.commission),
                        "0",
                    ),
                )
                additions += 1
        if not interval and any(fill["order_id"] not in matched_ids for fill in old_fills):
            raise ValueError("Journal has fills not covered by evidence")
        if any(order["client_order_id"] not in matched_ids for order in pending):
            raise ValueError("Pending order is not explained by statement")
        portfolio = opening
        for trade in trades:
            portfolio = portfolio.apply_fill(
                Fill(
                    instruments[trade.symbol],
                    trade.ts,
                    trade.quantity,
                    trade.price,
                    trade.commission,
                )
            )
        actual = positions_of(portfolio)
        if actual != {symbol: qty for symbol, qty in expected_positions.items() if qty}:
            raise ValueError("Repaired holdings do not match fresh broker positions")
        if abs(portfolio.cash - quantize_cash(opening.cash + ending_cash)) > dec("0.01"):
            raise ValueError("Repaired virtual cash does not match statement USD cash movement")
        if through < trades[-1].ts.date() or through > coverage_end:
            raise ValueError("Recovery valuation must remain within verified statement coverage")
        if old_nav and date.fromisoformat(old_nav[-1]["session"]) > through:
            raise ValueError("Recovery cannot move the latest journal session backwards")
        calendar = TradingCalendar()
        start = (
            date.fromisoformat(old_nav[-1]["session"]) + timedelta(days=1)
            if interval
            else date.fromisoformat(old_nav[0]["session"])
            if old_nav
            else trades[0].ts.date()
        )
        store = BarStore(data_root)
        prices = {
            symbol: store.read(symbol, start=start, end=through, as_of=through)
            for symbol in instruments
        }
        marked = opening
        cursor = 0
        rebuilt = []
        for session in calendar.sessions(start, through):
            while cursor < len(trades) and trades[cursor].ts <= session.close:
                trade = trades[cursor]
                marked = marked.apply_fill(
                    Fill(
                        instruments[trade.symbol],
                        trade.ts,
                        trade.quantity,
                        trade.price,
                        trade.commission,
                    )
                )
                cursor += 1
            close_prices = {}
            for symbol in positions_of(marked):
                frame = prices[symbol]
                rows = frame[pd.DatetimeIndex(frame.index).date == session.day]
                if len(rows) != 1:
                    raise ValueError("Missing exact-session close price; refusing valuation")
                close_prices[symbol] = quantize_price(dec(str(rows.iloc[0]["close"])))
            nav = marked.nav(close_prices)
            weights = marked.weights(close_prices)
            gross = sum((abs(value) for value in weights.values()), dec(0))
            point = (
                session.day.isoformat(),
                session.close.isoformat(),
                str(nav),
                str(marked.cash),
                str(gross),
                len(close_prices),
                json.dumps({s: str(w) for s, w in weights.items()}),
            )
            connection.execute("INSERT OR REPLACE INTO nav VALUES (?,?,?,?,?,?,?)", point)
            rebuilt.append(session.day.isoformat())
        connection.execute("INSERT OR REPLACE INTO meta VALUES ('pending_orders','[]')")
        audit = {
            "before_fingerprint": before,
            "interval": interval,
            "old_nav": old_nav,
            "old_pending": pending,
            "added_fills": additions,
            "amendments": corrections,
            "rebuilt_sessions": rebuilt,
            "cash": str(portfolio.cash),
            "positions": {s: str(q) for s, q in actual.items()},
            "match_policy": "unique symbol, signed quantity and subsequent timestamp within 4 days; independently confirmed",
        }
        connection.execute(
            "INSERT INTO statement_recoveries VALUES (?,?,?,?)",
            (source_hash, confirmation_hash, now, json.dumps(audit)),
        )
        return {key: value for key, value in audit.items() if key not in {"old_nav", "old_pending"}}
