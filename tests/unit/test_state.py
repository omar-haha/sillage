"""Tests for the durable journal.

Live, this file is the only thing between a restart and a disaster. The tests that
matter are the ones about surviving being run twice: recording the same order twice must
be a no-op, and rebuilding the book from disk must give exactly what was in memory.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.support import etf

from sillage.core.money import ZERO, dec
from sillage.core.types import Fill, Order, Portfolio
from sillage.engine.journal import NavPoint
from sillage.execution.broker import Rejection
from sillage.state.journal import SqliteJournal, positions_of

A, B = etf("A"), etf("B")
INSTRUMENTS = {"A": A, "B": B}
TS = datetime(2024, 3, 14, 21, tzinfo=UTC)


@pytest.fixture
def journal(tmp_path: Path) -> SqliteJournal:
    return SqliteJournal(tmp_path / "nested" / "live.db")


def fill(symbol: str = "A", quantity: str = "10", price: str = "100", order: str = "o1") -> Fill:
    return Fill(
        instrument=INSTRUMENTS[symbol],
        ts=TS,
        quantity=dec(quantity),
        price=dec(price),
        commission=dec("0.35"),
        order_id=order,
    )


def nav_point(day: int = 14, nav: str = "100000") -> NavPoint:
    return NavPoint(
        session=date(2024, 3, day),
        ts=datetime(2024, 3, day, 21, tzinfo=UTC),
        nav=dec(nav),
        cash=dec("500.25"),
        gross_exposure=dec("0.995"),
        holdings=2,
        weights={"A": dec("0.6"), "B": dec("0.4")},
    )


# ------------------------------------------------------------------ durability


def test_it_creates_its_own_directory(tmp_path: Path) -> None:
    """A live fund started by cron must not fail because a folder was missing."""
    assert SqliteJournal(tmp_path / "a" / "b" / "live.db").counts()["fills"] == 0


def test_the_same_order_recorded_twice_is_recorded_once(journal: SqliteJournal) -> None:
    """Idempotency is enforced at the storage layer, because that is the only place
    that survives a crash between the check and the write."""
    order = Order(A, dec(10), created_at=TS)
    journal.record_order(order)
    journal.record_order(order)
    assert journal.counts()["orders"] == 1
    assert journal.has_order(order.client_order_id)


def test_an_unknown_order_is_not_claimed(journal: SqliteJournal) -> None:
    assert not journal.has_order("never-seen")


def test_money_survives_the_round_trip_exactly(journal: SqliteJournal) -> None:
    """SQLite has no decimal type; storing through its float would reintroduce, at the
    persistence layer, the error the whole domain model exists to avoid."""
    journal.record_fill(fill(price="123.45678901", quantity="0.1"))
    stored = journal.fills(INSTRUMENTS)[0]
    assert stored.price == dec("123.45678901")
    assert stored.quantity == dec("0.1")
    assert isinstance(stored.price, Decimal)


def test_timestamps_come_back_timezone_aware(journal: SqliteJournal) -> None:
    journal.record_fill(fill())
    assert journal.fills(INSTRUMENTS)[0].ts.tzinfo is not None


# ------------------------------------------------------------------ rebuilding


def test_the_book_is_rebuilt_by_replaying_fills(journal: SqliteJournal) -> None:
    """Positions are derived, never stored. A stored position and a stored fill history
    can disagree, and when they do there is no way to tell which is right."""
    journal.record_fill(fill("A", "10", "100"))
    journal.record_fill(fill("B", "5", "50"))
    journal.record_fill(fill("A", "-4", "110"))

    portfolio = journal.portfolio(INSTRUMENTS, initial_cash=dec(100_000))
    assert positions_of(portfolio) == {"A": dec(6), "B": dec(5)}

    expected = Portfolio(cash=dec(100_000))
    for f in (fill("A", "10", "100"), fill("B", "5", "50"), fill("A", "-4", "110")):
        expected = expected.apply_fill(f)
    assert portfolio.cash == expected.cash


def test_replaying_an_empty_journal_gives_the_opening_cash(journal: SqliteJournal) -> None:
    assert journal.portfolio(INSTRUMENTS, initial_cash=dec(50_000)).cash == dec(50_000)


def test_a_fill_for_an_unknown_symbol_is_skipped_not_invented(journal: SqliteJournal) -> None:
    """The universe changed since the fill; conjuring an Instrument would put a position
    in the book with a made-up lot size."""
    journal.record_fill(fill("A"))
    assert journal.fills({"B": B}) == []


def test_flat_positions_are_not_reported_as_holdings(journal: SqliteJournal) -> None:
    journal.record_fill(fill("A", "10"))
    journal.record_fill(fill("A", "-10", "105"))
    assert positions_of(journal.portfolio(INSTRUMENTS, initial_cash=dec(100_000))) == {}


# ------------------------------------------------------------------ nav


def test_re_marking_a_session_replaces_rather_than_duplicates(journal: SqliteJournal) -> None:
    """A session has one closing value. Re-running a day must not produce two."""
    journal.record_nav(nav_point(nav="100000"))
    journal.record_nav(nav_point(nav="101000"))
    history = journal.nav_history()
    assert len(history) == 1
    assert history[0].nav == dec("101000")


def test_nav_history_comes_back_in_order(journal: SqliteJournal) -> None:
    for day in (15, 13, 14):
        journal.record_nav(nav_point(day=day))
    assert [p.session.day for p in journal.nav_history()] == [13, 14, 15]


def test_weights_survive_the_round_trip(journal: SqliteJournal) -> None:
    journal.record_nav(nav_point())
    assert journal.nav_history()[0].weights == {"A": dec("0.6"), "B": dec("0.4")}


def test_the_last_session_is_where_a_restart_resumes(journal: SqliteJournal) -> None:
    assert journal.last_session() is None
    journal.record_nav(nav_point(day=13))
    journal.record_nav(nav_point(day=15))
    assert journal.last_session() == date(2024, 3, 15)


def test_the_high_water_mark_is_the_peak_not_the_last(journal: SqliteJournal) -> None:
    """The drawdown kill-switch measures against this; taking the latest instead would
    mean a falling fund never registered a drawdown at all."""
    journal.record_nav(nav_point(day=13, nav="120000"))
    journal.record_nav(nav_point(day=14, nav="90000"))
    assert journal.high_water_mark() == dec("120000")


def test_an_empty_journal_has_no_high_water_mark(journal: SqliteJournal) -> None:
    assert journal.high_water_mark() == ZERO


# ------------------------------------------------------------------ pending orders


def test_pending_orders_survive_the_process(journal: SqliteJournal) -> None:
    """An order decided on Friday evening has to be there on Monday morning."""
    order = Order(A, dec(10), created_at=TS, reason="rebalance")
    journal.save_pending([order])

    reopened = SqliteJournal(journal.path)
    restored = reopened.load_pending(INSTRUMENTS)
    assert len(restored) == 1
    assert restored[0].client_order_id == order.client_order_id
    assert restored[0].quantity == dec(10)
    assert restored[0].reason == "rebalance"
    assert restored[0].created_at == TS


def test_saving_pending_also_records_the_orders(journal: SqliteJournal) -> None:
    journal.save_pending([Order(A, dec(10), created_at=TS)])
    assert journal.counts()["orders"] == 1


def test_saving_pending_replaces_the_previous_set(journal: SqliteJournal) -> None:
    journal.save_pending([Order(A, dec(10), created_at=TS)])
    journal.save_pending([Order(B, dec(5), created_at=TS)])
    assert [o.instrument.symbol for o in journal.load_pending(INSTRUMENTS)] == ["B"]


def test_clearing_pending_leaves_nothing_outstanding(journal: SqliteJournal) -> None:
    journal.save_pending([Order(A, dec(10), created_at=TS)])
    journal.clear_pending()
    assert journal.load_pending(INSTRUMENTS) == []


def test_no_pending_orders_is_not_an_error(journal: SqliteJournal) -> None:
    assert journal.load_pending(INSTRUMENTS) == []


# ------------------------------------------------------------------ rejections


def test_rejections_are_kept_with_their_reason(journal: SqliteJournal) -> None:
    """A refused order that leaves no trace is a live failure nobody can diagnose."""
    journal.record_rejection(Rejection(Order(A, dec(10), created_at=TS), "insufficient cash", TS))
    assert journal.counts()["rejections"] == 1
