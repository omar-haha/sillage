"""Synthetic evidence only; real broker exports must never enter Git."""

import csv
import sqlite3
from datetime import UTC, date, datetime

import pandas as pd
import pytest
from tests.support import etf

from sillage.core.money import dec
from sillage.core.types import Fill, Order
from sillage.data.store import BarStore
from sillage.engine.journal import NavPoint
from sillage.state.journal import SqliteJournal
from sillage.state.recovery import read_evidence, repair


@pytest.fixture
def evidence(tmp_path):
    statement = tmp_path / "activity.csv"
    rows = [
        ["Statement", "Header", "Field Name", "Field Value"],
        ["Statement", "Data", "Period", "September 18, 2024 - September 19, 2024"],
        ["Cash Report", "Header", "Currency Summary", "Currency", "Total"],
        ["Cash Report", "Data", "Starting Cash", "USD", "0"],
        ["Cash Report", "Data", "Ending Cash", "USD", "-782"],
        [
            "Open Positions",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Quantity",
        ],
        ["Open Positions", "Data", "Summary", "Stocks", "USD", "A", "8"],
        [
            "Trades",
            "Header",
            "DataDiscriminator",
            "Asset Category",
            "Currency",
            "Symbol",
            "Date/Time",
            "Quantity",
            "T. Price",
            "Proceeds",
            "Comm/Fee",
        ],
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "USD",
            "A",
            "2024-09-18, 09:30:01",
            "10",
            "100",
            "-1000",
            "-1",
        ],
        [
            "Trades",
            "Data",
            "Order",
            "Stocks",
            "USD",
            "A",
            "2024-09-19, 09:30:01",
            "-2",
            "110",
            "220",
            "-1",
        ],
    ]
    with statement.open("w", newline="") as stream:
        csv.writer(stream).writerows(rows)
    confirmations = tmp_path / "confirm.htm"
    confirmations.write_text(
        "<table>"
        + "".join(
            "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
            for row in [
                [
                    "PRIVATE",
                    "A",
                    "2024-09-18, 09:30:01",
                    "2024-09-19",
                    "ARCA",
                    "BUY",
                    "10",
                    "100",
                    "-1000",
                    "-1",
                    "0",
                    "MKT",
                    "O",
                ],
                [
                    "PRIVATE",
                    "A",
                    "2024-09-19, 09:30:01",
                    "2024-09-20",
                    "ARCA",
                    "SELL",
                    "-2",
                    "110",
                    "220",
                    "-1",
                    "0",
                    "MKT",
                    "C",
                ],
            ]
        )
        + "</table>"
    )
    instrument = etf("A")
    journal = SqliteJournal(tmp_path / "journal.db")
    buy = Order(
        instrument, dec(10), created_at=datetime(2024, 9, 17, 20, tzinfo=UTC), client_order_id="buy"
    )
    sell = Order(
        instrument,
        dec(-2),
        created_at=datetime(2024, 9, 18, 20, tzinfo=UTC),
        client_order_id="sell",
    )
    journal.record_order(buy)
    journal.record_fill(
        Fill(
            instrument, datetime(2024, 9, 18, 13, 30, tzinfo=UTC), dec(10), dec(101), order_id="buy"
        )
    )
    journal.save_pending([sell])
    journal.record_nav(NavPoint(date(2024, 9, 17), buy.created_at, dec(25000), dec(25000), dec(0)))
    journal.record_nav(
        NavPoint(date(2024, 9, 18), sell.created_at, dec(24990), dec(23990), dec(".04"))
    )
    store = BarStore(tmp_path / "data")
    frame = pd.DataFrame(
        {
            "open": [99, 109],
            "high": [101, 111],
            "low": [98, 108],
            "close": [100, 110],
            "volume": [1000, 1000],
        },
        index=pd.DatetimeIndex(["2024-09-18", "2024-09-19"], tz="UTC", name="ts"),
    )
    store.write("A", frame)
    return journal, statement, confirmations, store.root


def invoke(evidence, positions=None, through=date(2024, 9, 19)):
    journal, statement, confirmations, data = evidence
    return repair(
        journal.path,
        statement,
        confirmations,
        data,
        through,
        positions if positions is not None else {"A": dec(8)},
    )


def test_import_preserves_raw_fills_and_applies_audited_corrections(evidence):
    journal, _, _, _ = evidence
    result = invoke(evidence)
    assert result["added_fills"] == 1
    assert result["amendments"] == 1
    assert result["cash"] == "24218.00"
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute("SELECT price,commission FROM fills WHERE id=1").fetchone() == (
            "101",
            "0",
        )
        assert conn.execute("SELECT count(*) FROM fill_amendments").fetchone()[0] == 1
    fills = journal.fills({"A": etf("A")})
    assert fills[0].price == dec(100)
    assert fills[0].commission == dec(1)
    assert journal.portfolio({"A": etf("A")}, initial_cash=dec(25000)).cash == dec(24218)
    assert not journal.load_pending({"A": etf("A")})
    assert journal.last_session() == date(2024, 9, 19)
    assert journal.nav_history()[-1].nav == dec(25098)


def test_import_is_idempotent(evidence):
    invoke(evidence)
    assert invoke(evidence) == {"already_imported": True}
    assert evidence[0].counts()["fills"] == 2


def test_broker_mismatch_rolls_back_every_ledger_change(evidence):
    before = evidence[0].counts()
    with pytest.raises(ValueError, match="fresh broker positions"):
        invoke(evidence, {"A": dec(9)})
    assert evidence[0].counts() == before
    with sqlite3.connect(evidence[0].path) as conn:
        assert conn.execute("SELECT count(*) FROM fill_amendments").fetchone()[0] == 0
    assert evidence[0].load_pending({"A": etf("A")})


def test_missing_exact_valuation_price_rolls_back(evidence):
    (evidence[3] / "bars/daily/A.parquet").unlink()
    with pytest.raises(FileNotFoundError):
        invoke(evidence)
    assert evidence[0].counts()["fills"] == 1


def test_out_of_coverage_nav_is_refused(evidence):
    with pytest.raises(ValueError, match="coverage"):
        invoke(evidence, through=date(2024, 9, 20))
    assert evidence[0].counts()["fills"] == 1


def test_unexplained_cash_movement_is_refused(evidence):
    path = evidence[1]
    path.write_text(path.read_text().replace("USD,-782", "USD,-780"))
    with pytest.raises(ValueError, match="cash movements"):
        read_evidence(path, evidence[2])


def test_unconfirmed_trade_is_refused(evidence):
    evidence[2].write_text("<table></table>")
    with pytest.raises(ValueError, match="corroborating"):
        invoke(evidence)


def test_missing_order_is_refused(evidence):
    with sqlite3.connect(evidence[0].path) as conn:
        conn.execute("DELETE FROM orders WHERE client_order_id='sell'")
    with pytest.raises(ValueError, match="every journal order"):
        invoke(evidence)
