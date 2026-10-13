import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from tests.support import etf

from sillage.core.money import dec
from sillage.core.types import Fill, Order
from sillage.execution.broker import BrokerError
from sillage.execution.ibkr import Execution, IBGatewayClient, IBKRBroker, Placement
from sillage.state.executions import ExecutionArchive
from sillage.state.journal import SqliteJournal

TS = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)
A = etf("AAA")


def evidence(execution_id="exec1", quantity="1"):
    return Execution(
        order_ref="order1",
        execution_id=execution_id,
        symbol="AAA",
        quantity=dec(quantity),
        price=dec(100),
        commission=dec(1),
        ts=TS,
    )


def test_capture_survives_reconnect_and_midnight_and_is_not_a_ledger_fill(tmp_path):
    path = tmp_path / "journal.db"
    journal = SqliteJournal(path)
    archive = ExecutionArchive(path)
    archive.save("DU123", [evidence()])
    archive.save("DU123", [evidence()])
    assert ExecutionArchive(path).load("DU123", since=date(2026, 10, 9)) == [evidence()]
    assert archive.load("DU456") == []
    assert archive.load("DU123", since=date(2026, 10, 13)) == []
    assert journal.counts()["fills"] == 0
    client = IBGatewayClient(execution_archive=path)
    client._ib = SimpleNamespace(
        isConnected=lambda: True, reqExecutions=lambda _: [], managedAccounts=lambda: ["DU123"]
    )
    assert client.executions(since=date(2026, 10, 9)) == [evidence()]


def test_changed_evidence_and_correction_refused_without_overwrite(tmp_path):
    path = tmp_path / "journal.db"
    SqliteJournal(path)
    archive = ExecutionArchive(path)
    archive.save("DU123", [evidence("abc.01")])
    with pytest.raises(ValueError, match="changed"):
        archive.save("DU123", [replace(evidence("abc.01"), price=dec(101))])
    with pytest.raises(ValueError, match="correction"):
        archive.save("DU123", [evidence("abc.02")])
    assert archive.load("DU123") == [evidence("abc.01")]


def test_invalid_evidence_rolls_back_entire_batch(tmp_path):
    path = tmp_path / "journal.db"
    SqliteJournal(path)
    archive = ExecutionArchive(path)
    with pytest.raises(ValueError):
        archive.save("DU123", [evidence(), replace(evidence("exec2"), price=dec(-1))])
    assert archive.load("DU123") == []
    with pytest.raises(ValueError):
        ExecutionArchive(tmp_path / "absent.db").save("DU123", [evidence()])


def test_execution_id_journal_dedup_and_round_trip(tmp_path):
    journal = SqliteJournal(tmp_path / "journal.db")
    fill = Fill(A, TS, dec(1), dec(100), commission=dec(1), order_id="order1", execution_id="exec1")
    journal.record_fill(fill)
    journal.record_fill(fill)
    assert journal.counts()["fills"] == 1
    assert journal.fills({"AAA": A}) == [fill]
    with pytest.raises(ValueError, match="conflicting"):
        journal.record_fill(replace(fill, quantity=dec(2)))


def test_partial_fill_recovery_after_restart_does_not_double_import(tmp_path):
    path = tmp_path / "journal.db"
    journal = SqliteJournal(path)
    archive = ExecutionArchive(path)
    order = Order(A, dec(2), client_order_id="order1", created_at=TS)
    journal.record_order(order)
    archive.save("DU123", [evidence()])

    def client():
        value = IBGatewayClient(execution_archive=path)
        value._ib = SimpleNamespace(
            isConnected=lambda: True, reqExecutions=lambda _: [], managedAccounts=lambda: ["DU123"]
        )
        value.status = lambda _: Placement("order1", "Submitted")
        value.open_order_refs = lambda: set()
        value.place = lambda *a, **kw: pytest.fail("recovery cannot submit")
        return value

    first = IBKRBroker(client()).recover([order], session=date(2026, 10, 9), ts=TS)
    assert first.outstanding == [order]
    assert len(first.fills) == 1
    journal.record_fill(first.fills[0])
    archive.save("DU123", [evidence("exec2")])
    second = IBKRBroker(client()).recover([order], session=date(2026, 10, 9), ts=TS)
    assert not second.outstanding
    assert [f.execution_id for f in second.fills] == ["exec2"]
    journal.record_fill(second.fills[0])
    third = IBKRBroker(client()).recover([order], session=date(2026, 10, 9), ts=TS)
    assert not third.fills and not third.outstanding
    assert journal.counts()["fills"] == 2


def test_evidence_uses_existing_database_for_backup(tmp_path):
    path = tmp_path / "journal.db"
    SqliteJournal(path)
    ExecutionArchive(path).save("DU123", [evidence()])
    backup = tmp_path / "backup.db"
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    assert ExecutionArchive(backup).load("DU123") == [evidence()]


def test_legacy_partial_fill_is_not_guessed_as_a_duplicate(tmp_path):
    path = tmp_path / "journal.db"
    journal = SqliteJournal(path)
    journal.record_fill(Fill(A, TS, dec(1), dec(100), order_id="order1"))
    archive = ExecutionArchive(path)
    archive.save("DU123", [evidence()])
    client = IBGatewayClient(execution_archive=path)
    client._ib = SimpleNamespace(
        isConnected=lambda: True, reqExecutions=lambda _: [], managedAccounts=lambda: ["DU123"]
    )
    client.open_order_refs = lambda: set()
    with pytest.raises(BrokerError, match="Legacy partial"):
        IBKRBroker(client).recover(
            [Order(A, dec(2), client_order_id="order1", created_at=TS)],
            session=date(2026, 10, 9),
            ts=TS,
        )
    assert journal.counts()["fills"] == 1
