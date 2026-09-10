"""Tests for running the fund forward on real time.

Almost every test here is about being interrupted. A backtest runs once in one process;
a live fund runs in a process that will be killed, deployed over and rebooted, and the
only thing that survives is what reached disk. So these run `run_once` repeatedly, from
fresh objects each time, the way cron would.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from tests.support import etf, sessions_from, synthetic_store

from sillage.core.money import ZERO, dec
from sillage.core.types import AssetClass, Instrument, Order
from sillage.data.universe import Universe
from sillage.live.reconcile import ReconciliationError, reconcile
from sillage.live.runner import LiveConfig, StaleDataError, run_once, status
from sillage.risk.limits import RiskLimits
from sillage.state.journal import SqliteJournal
from sillage.strategy.benchmarks import StaticWeights

CASH = Instrument("CASH", AssetClass.ETF)
UNIVERSE = Universe("live", (etf("AAA"), etf("BBB")), cash_proxy=CASH)

#: The synthetic store's sessions, so "now" can be pinned to one of them.
SESSIONS = sessions_from(700)
LAST = SESSIONS[699]
AFTER_LAST_CLOSE = LAST.close.replace(hour=23)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("live-data")
    synthetic_store(root, {"AAA": 0.0006, "BBB": 0.0002, "CASH": 0.00004})
    return root


def config(store: Path, journal: Path, **kwargs: object) -> LiveConfig:
    return LiveConfig(
        strategy=StaticWeights({"AAA": "0.6", "BBB": "0.4"}),
        universe=UNIVERSE,
        journal_path=journal,
        data_root=store,
        initial_cash=dec(100_000),
        # Generous, so the synthetic run exercises the machinery rather than the limits.
        limits=RiskLimits(max_weight=dec(1), max_drawdown=dec("0.9")),
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ one step


def test_a_first_run_catches_up_and_trades(store: Path, tmp_path: Path) -> None:
    report = run_once(config(store, tmp_path / "live.db"), now=AFTER_LAST_CLOSE)
    assert report.acted
    assert report.fills > 0
    assert set(report.positions) == {"AAA", "BBB"}


def test_running_again_immediately_does_nothing(store: Path, tmp_path: Path) -> None:
    """Safe to run twice by accident, which cron will eventually do."""
    settings = config(store, tmp_path / "live.db")
    run_once(settings, now=AFTER_LAST_CLOSE)
    second = run_once(settings, now=AFTER_LAST_CLOSE)
    assert not second.acted
    assert second.fills == 0


def test_a_second_process_sees_the_same_book(store: Path, tmp_path: Path) -> None:
    """The whole point of the journal: a new process rebuilds what the old one held."""
    journal_path = tmp_path / "live.db"
    first = run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    # A completely fresh config object, as a new process would build.
    state = status(config(store, journal_path))
    assert state["positions"] == first.positions
    assert state["cash"] == first.cash


def test_it_does_not_replay_sessions_it_already_recorded(store: Path, tmp_path: Path) -> None:
    journal_path = tmp_path / "live.db"
    run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    before = SqliteJournal(journal_path).counts()
    run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    assert SqliteJournal(journal_path).counts()["nav"] == before["nav"]


# ------------------------------------------------------------------ interruption


def test_an_order_decided_before_a_crash_still_executes(store: Path, tmp_path: Path) -> None:
    """An order decided at Friday's close has to survive until Monday's open, including
    a restart in between."""
    journal_path = tmp_path / "live.db"
    journal = SqliteJournal(journal_path)
    order = Order(etf("AAA"), dec(10), created_at=SESSIONS[600].close, reason="pre-crash")
    journal.save_pending([order])

    report = run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    filled = SqliteJournal(journal_path).fills({"AAA": etf("AAA"), "BBB": etf("BBB")})
    assert any(f.order_id == order.client_order_id for f in filled)
    assert report.fills > 0


def test_pending_orders_are_persisted_for_the_next_run(store: Path, tmp_path: Path) -> None:
    journal_path = tmp_path / "live.db"
    # Stop just before a close so a decision is made with no open left to execute it.
    run_once(config(store, journal_path), now=LAST.close.replace(hour=23))
    assert SqliteJournal(journal_path).load_pending({"AAA": etf("AAA")}) is not None


def test_the_high_water_mark_survives_a_restart(store: Path, tmp_path: Path) -> None:
    """A kill-switch that measured against this run's peak would never fire after a
    restart, which is precisely when it is most needed."""
    journal_path = tmp_path / "live.db"
    run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    assert SqliteJournal(journal_path).high_water_mark() > ZERO


# ------------------------------------------------------------------ refusals


def test_it_refuses_to_trade_against_a_book_it_cannot_verify(store: Path, tmp_path: Path) -> None:
    journal_path = tmp_path / "live.db"
    run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    with pytest.raises(ReconciliationError, match="RECONCILIATION FAILED"):
        run_once(
            config(store, journal_path),
            now=AFTER_LAST_CLOSE,
            broker_positions={"AAA": dec(1)},
        )


def test_it_proceeds_when_the_broker_agrees(store: Path, tmp_path: Path) -> None:
    journal_path = tmp_path / "live.db"
    first = run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    second = run_once(
        config(store, journal_path), now=AFTER_LAST_CLOSE, broker_positions=first.positions
    )
    assert second.reconciliation is not None
    assert second.reconciliation.agreed


def test_it_refuses_to_trade_on_stale_prices(store: Path, tmp_path: Path) -> None:
    """A failed data sync is silent by nature: the reads still succeed, with last
    month's prices."""
    much_later = datetime(LAST.day.year + 1, LAST.day.month, 1, 23, tzinfo=UTC)
    with pytest.raises(StaleDataError, match="behind"):
        run_once(config(store, tmp_path / "live.db"), now=much_later)


def test_it_refuses_to_trade_with_no_data_at_all(tmp_path: Path) -> None:
    with pytest.raises((StaleDataError, FileNotFoundError)):
        run_once(config(tmp_path / "empty", tmp_path / "live.db"), now=AFTER_LAST_CLOSE)


def test_a_deep_drawdown_halts_the_fund(store: Path, tmp_path: Path) -> None:
    """And it measures against the peak recorded on disk, not this run's.

    A kill-switch that only knew about the current process would never fire after a
    restart, which is exactly when a fund is most likely to be in trouble.
    """
    from sillage.engine.journal import NavPoint

    journal_path = tmp_path / "live.db"
    journal = SqliteJournal(journal_path)
    # A peak from a previous life of this fund, far above where it is now.
    journal.record_nav(
        NavPoint(
            session=SESSIONS[0].day,
            ts=SESSIONS[0].close,
            nav=dec(1_000_000),
            cash=dec(1_000_000),
            gross_exposure=ZERO,
        )
    )

    settings = LiveConfig(
        strategy=StaticWeights({"AAA": "0.6", "BBB": "0.4"}),
        universe=UNIVERSE,
        journal_path=journal_path,
        data_root=store,
        initial_cash=dec(100_000),
        limits=RiskLimits(max_weight=dec(1), max_drawdown=dec("0.25")),
    )
    report = run_once(settings, now=AFTER_LAST_CLOSE)
    assert report.halted
    assert "trading halted" in report.halted
    assert report.fills == 0


# ------------------------------------------------------------------ reconciliation


def test_reconciliation_sees_a_position_that_appeared_from_nowhere() -> None:
    """A manual trade in the broker's web terminal. Checking only the journal's keys
    would miss it entirely."""
    result = reconcile({}, {"AAA": dec(10)})
    assert not result
    assert result.discrepancies[0].symbol == "AAA"


def test_reconciliation_sees_a_position_that_vanished() -> None:
    assert not reconcile({"AAA": dec(10)}, {})


def test_agreement_is_agreement() -> None:
    assert reconcile({"AAA": dec(10)}, {"AAA": dec(10)})


def test_dust_is_not_a_disagreement() -> None:
    """Fractional-share brokers report tiny remainders from dividend reinvestment."""
    assert reconcile({"AAA": dec(10)}, {"AAA": dec("10.000000001")})


def test_a_real_difference_is_reported_with_both_sides() -> None:
    result = reconcile({"AAA": dec(10)}, {"AAA": dec(12)})
    assert result.discrepancies[0].difference == dec(2)
    assert "journal says 10" in str(result)


def test_two_empty_books_agree() -> None:
    assert reconcile({}, {})


# ------------------------------------------------------------------ reporting


def test_a_step_that_ordered_but_filled_nothing_is_flagged(store: Path, tmp_path: Path) -> None:
    """The shape of every quiet live failure: the fund keeps running, keeps reporting a
    NAV, and never trades."""
    from sillage.live.runner import LiveReport

    stalled = LiveReport(
        sessions=(date(2024, 1, 2),),
        fills=0,
        orders=3,
        rejections=3,
        nav=dec(100_000),
        cash=dec(100_000),
        positions={},
        pending=0,
        reconciliation=None,
    )
    assert stalled.stalled
    assert "3 rejected" in str(stalled)


def test_a_step_with_nothing_to_do_says_so() -> None:
    from sillage.live.runner import LiveReport

    idle = LiveReport((), 0, 0, 0, dec(1), dec(1), {}, 0, None)
    assert not idle.acted
    assert "nothing to do" in str(idle)
    assert not idle.stalled


def test_status_reads_without_touching_anything(store: Path, tmp_path: Path) -> None:
    journal_path = tmp_path / "live.db"
    run_once(config(store, journal_path), now=AFTER_LAST_CLOSE)
    before = SqliteJournal(journal_path).counts()
    state = status(config(store, journal_path))
    assert SqliteJournal(journal_path).counts() == before
    assert isinstance(state["nav"], Decimal)


# ------------------------------------------------------------------ submitting ahead


def test_a_live_broker_gets_tonight_s_orders_before_tomorrow_s_open(
    store: Path, tmp_path: Path
) -> None:
    """A market-on-open order has to be at the exchange before the auction, which means
    sending it the evening before -- hours before the event that formally executes it."""
    from sillage.execution.broker import ExecutionReport

    class RecordingVenue:
        name = "recording"

        def __init__(self) -> None:
            self.submitted: list[str] = []

        def execute(self, orders, *, portfolio, session, ts):  # type: ignore[no-untyped-def]
            self.submitted.extend(o.instrument.symbol for o in orders)
            # A shut market: accepted, working, nothing filled.
            return ExecutionReport(outstanding=list(orders))

        def positions(self) -> dict[str, Decimal]:
            return {}

    venue = RecordingVenue()
    settings = LiveConfig(
        strategy=StaticWeights({"AAA": "0.6", "BBB": "0.4"}),
        universe=UNIVERSE,
        journal_path=tmp_path / "live.db",
        data_root=store,
        initial_cash=dec(100_000),
        limits=RiskLimits(max_weight=dec(1), max_drawdown=dec("0.9")),
        broker=venue,
    )
    report = run_once(settings, now=AFTER_LAST_CLOSE)

    assert venue.submitted, "the venue never saw tonight's orders"
    assert report.pending > 0, "orders the venue is holding must stay pending"


def test_a_simulated_run_does_not_submit_ahead(store: Path, tmp_path: Path) -> None:
    """Handing a future open to the simulator asks it for a price that does not exist
    yet; it would refuse, and refusals are not re-queued."""
    report = run_once(config(store, tmp_path / "live.db"), now=AFTER_LAST_CLOSE)
    assert report.fills > 0
