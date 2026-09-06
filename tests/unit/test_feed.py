"""Tests for the in-memory feeds.

Two things are being checked. First, that `HistoricalFeed` cannot be made to return a
bar that had not closed -- this is the same guarantee `BarStore` makes, and it has to
survive being cached in memory or the backtest can quietly start cheating. Second, that
`MarketFeed` genuinely has no way to reach a closing price, which is what stops a
simulated fill being priced at the close of the day it happens on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.support import etf, make_bars, sessions

from sillage.core.money import dec
from sillage.engine.feed import DataSource, ExecutionFeed, HistoricalFeed, MarketFeed

AAA = etf("AAA")
CLOSES = [100, 101, 102, 103, 104, 105]


@pytest.fixture
def bars() -> dict[str, list]:  # type: ignore[type-arg]
    return {"AAA": make_bars(AAA, CLOSES)}


@pytest.fixture
def feed(bars: dict[str, list]) -> HistoricalFeed:  # type: ignore[type-arg]
    return HistoricalFeed(bars)


@pytest.fixture
def market(bars: dict[str, list]) -> MarketFeed:  # type: ignore[type-arg]
    return MarketFeed(bars)


# ------------------------------------------------------------------ the as_of gate


def test_never_returns_a_bar_that_had_not_closed(feed: HistoricalFeed, bars: dict) -> None:  # type: ignore[type-arg]
    """The property the whole design rests on, checked at every instant in the run."""
    for bar in bars["AAA"]:
        for offset in (timedelta(seconds=-1), timedelta(0), timedelta(hours=1)):
            as_of = bar.ts + offset
            assert all(b.ts <= as_of for b in feed.history("AAA", as_of=as_of))


def test_a_bar_becomes_visible_exactly_at_its_close(feed: HistoricalFeed, bars: dict) -> None:  # type: ignore[type-arg]
    third = bars["AAA"][2]
    assert len(feed.history("AAA", as_of=third.ts - timedelta(seconds=1))) == 2
    assert len(feed.history("AAA", as_of=third.ts)) == 3


def test_history_is_empty_before_the_first_bar(feed: HistoricalFeed) -> None:
    assert feed.history("AAA", as_of=datetime(2000, 1, 1, tzinfo=UTC)) == []
    assert feed.latest("AAA", as_of=datetime(2000, 1, 1, tzinfo=UTC)) is None


def test_count_keeps_the_most_recent_bars(feed: HistoricalFeed, bars: dict) -> None:  # type: ignore[type-arg]
    last = bars["AAA"][-1]
    window = feed.history("AAA", as_of=last.ts, count=2)
    assert [b.close for b in window] == [dec(104), dec(105)]


def test_count_larger_than_history_returns_what_exists(feed: HistoricalFeed, bars: dict) -> None:  # type: ignore[type-arg]
    assert len(feed.history("AAA", as_of=bars["AAA"][1].ts, count=50)) == 2


def test_latest_is_the_last_closed_bar(feed: HistoricalFeed, bars: dict) -> None:  # type: ignore[type-arg]
    bar = feed.latest("AAA", as_of=bars["AAA"][3].ts)
    assert bar is not None and bar.close == dec(103)


def test_closes_skips_symbols_with_no_history_yet(bars: dict) -> None:  # type: ignore[type-arg]
    later = make_bars(etf("BBB"), [50, 51], start=sessions(6)[4].day)
    feed = HistoricalFeed({**bars, "BBB": later})
    early = bars["AAA"][0].ts
    assert feed.closes(["AAA", "BBB"], as_of=early) == {"AAA": dec(100)}


def test_unknown_symbol_raises_rather_than_returning_nothing(feed: HistoricalFeed) -> None:
    with pytest.raises(KeyError, match="no bars loaded"):
        feed.history("NOPE", as_of=datetime.now(UTC))


def test_rejects_unsorted_bars() -> None:
    series = make_bars(AAA, CLOSES)
    with pytest.raises(ValueError, match="sorted by timestamp"):
        HistoricalFeed({"AAA": list(reversed(series))})


def test_satisfies_the_datasource_protocol(feed: HistoricalFeed) -> None:
    assert isinstance(feed, DataSource)


# ------------------------------------------------------------------ the venue's view


def test_market_feed_serves_opens_by_session(market: MarketFeed, bars: dict) -> None:  # type: ignore[type-arg]
    first = bars["AAA"][0]
    assert market.open_price("AAA", first.ts.date()) == first.open


def test_market_feed_cannot_reach_a_close(market: MarketFeed) -> None:
    """Not a style check. A fill priced at the close of its own session is lookahead."""
    assert not hasattr(market, "close_price")
    assert isinstance(market, ExecutionFeed)


def test_open_price_is_none_when_the_symbol_did_not_trade(market: MarketFeed) -> None:
    from datetime import date

    assert market.open_price("AAA", date(2024, 1, 1)) is None
    assert market.open_price("NOPE", date(2024, 1, 2)) is None


def test_average_volume_excludes_the_session_itself() -> None:
    volumes = [make_bars(AAA, CLOSES)[i] for i in range(len(CLOSES))]
    # Rebuild with a distinctive volume on the last session; if it leaked into the
    # average, the number below would move.
    from dataclasses import replace

    volumes[-1] = replace(volumes[-1], volume=dec(999_000_000))
    market = MarketFeed({"AAA": volumes})
    assert market.average_volume("AAA", volumes[-1].ts.date(), 5) == dec(1_000_000)


def test_average_volume_is_none_before_any_history(market: MarketFeed, bars: dict) -> None:  # type: ignore[type-arg]
    assert market.average_volume("AAA", bars["AAA"][0].ts.date(), 20) is None


def test_average_volume_rejects_a_meaningless_window(market: MarketFeed, bars: dict) -> None:  # type: ignore[type-arg]
    with pytest.raises(ValueError, match="must be positive"):
        market.average_volume("AAA", bars["AAA"][-1].ts.date(), 0)
