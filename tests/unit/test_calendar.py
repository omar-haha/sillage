"""Tests for trading calendars.

These assert against real, known NYSE history rather than a mocked calendar. The whole
point of the module is that market holidays are irregular and cannot be derived from
first principles, so a test using synthetic sessions would prove nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from sillage.core.calendar import ContinuousCalendar, TradingCalendar


@pytest.fixture(scope="module")
def nyse() -> TradingCalendar:
    return TradingCalendar()


def test_weekends_are_not_sessions(nyse: TradingCalendar) -> None:
    assert not nyse.is_session(date(2024, 6, 29))  # Saturday
    assert not nyse.is_session(date(2024, 6, 30))  # Sunday
    assert nyse.is_session(date(2024, 6, 28))  # Friday


@pytest.mark.parametrize(
    "holiday",
    [
        date(2024, 1, 1),  # New Year's Day
        date(2024, 3, 29),  # Good Friday -- moves every year, cannot be hardcoded
        date(2024, 7, 4),  # Independence Day
        date(2024, 11, 28),  # Thanksgiving
        date(2024, 12, 25),  # Christmas
        date(2018, 12, 5),  # national day of mourning, George H. W. Bush
    ],
)
def test_market_holidays_are_closed(nyse: TradingCalendar, holiday: date) -> None:
    assert not nyse.is_session(holiday)


def test_month_end_falls_on_the_last_tradable_day(nyse: TradingCalendar) -> None:
    # The three cases that break naive "is it the 31st?" logic.
    assert nyse.is_month_end_session(date(2024, 3, 28))  # 29th was Good Friday
    assert not nyse.is_month_end_session(date(2024, 3, 29))
    assert nyse.is_month_end_session(date(2024, 6, 28))  # 30th was a Sunday
    assert nyse.is_month_end_session(date(2024, 2, 29))  # leap year


def test_a_year_has_twelve_rebalance_dates(nyse: TradingCalendar) -> None:
    dates = nyse.month_end_sessions(date(2024, 1, 1), date(2024, 12, 31))
    assert len(dates) == 12
    assert [d.month for d in dates] == list(range(1, 13))


def test_session_open_precedes_close(nyse: TradingCalendar) -> None:
    session = nyse.sessions(date(2024, 6, 28), date(2024, 6, 28))[0]
    assert session.open < session.close
    assert session.open.tzinfo is not None


def test_half_day_closes_early(nyse: TradingCalendar) -> None:
    # The day after Thanksgiving closes at 13:00 ET. A backtest that assumes a
    # uniform session length will place fills at a time the market was shut.
    half = nyse.sessions(date(2024, 11, 29), date(2024, 11, 29))[0]
    full = nyse.sessions(date(2024, 11, 27), date(2024, 11, 27))[0]
    assert (half.close - half.open) < (full.close - full.open)


def test_navigation_skips_closures(nyse: TradingCalendar) -> None:
    assert nyse.next_session(date(2024, 12, 24)) == date(2024, 12, 26)  # skips Christmas
    assert nyse.previous_session(date(2024, 7, 5)) == date(2024, 7, 3)  # skips the 4th


class TestContinuousCalendar:
    """Crypto: every day is a session, including the ones the NYSE takes off."""

    def test_every_day_trades(self) -> None:
        cal = ContinuousCalendar()
        assert cal.is_session(date(2024, 12, 25))
        assert cal.is_session(date(2024, 6, 30))  # Sunday

    def test_month_end_is_the_actual_last_day(self) -> None:
        cal = ContinuousCalendar()
        assert cal.is_month_end_session(date(2024, 6, 30))  # Sunday, but still month end
        assert not cal.is_month_end_session(date(2024, 6, 28))
        assert cal.is_month_end_session(date(2024, 2, 29))

    def test_session_count_matches_calendar_days(self) -> None:
        cal = ContinuousCalendar()
        assert len(cal.sessions(date(2024, 1, 1), date(2024, 12, 31))) == 366
