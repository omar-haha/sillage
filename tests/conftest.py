"""Fixtures available to every test.

The synthetic-data builders live in `tests.support` rather than here, because several
tests need them at module scope -- to parametrise over -- and a pytest fixture cannot
be called that way.
"""

from __future__ import annotations

import pytest

from sillage.core.calendar import TradingCalendar


@pytest.fixture(scope="session")
def calendar() -> TradingCalendar:
    """Shared because building a calendar materialises 36 years of sessions."""
    return TradingCalendar()
