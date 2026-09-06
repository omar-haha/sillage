"""Fetching a universe's history into the local store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sillage.data.providers.base import PriceProvider, ProviderError
from sillage.data.store import BarStore
from sillage.data.universe import Universe

# Far enough back to include 2008, which is the single most informative period for a
# strategy whose entire purpose is limiting drawdowns.
DEFAULT_START = date(2005, 1, 1)


@dataclass(frozen=True, slots=True)
class SyncResult:
    symbol: str
    fetched: int
    stored: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def sync_universe(
    universe: Universe,
    store: BarStore,
    provider: PriceProvider,
    *,
    start: date = DEFAULT_START,
    end: date | None = None,
    incremental: bool = True,
) -> list[SyncResult]:
    """Fetch each instrument in `universe` and merge it into `store`.

    With `incremental`, refetching starts a few days before the last stored bar rather
    than at `start`. The overlap is intentional: adjusted prices get restated when a
    dividend is paid, so the most recent bars are the ones most likely to have changed
    since the last sync, and re-fetching them keeps the store honest.

    One symbol failing never aborts the run. A universe is usually mostly fetchable,
    and a partial sync plus a clear error beats an all-or-nothing failure.
    """
    end = end or datetime.now(UTC).date()
    results: list[SyncResult] = []

    for instrument in universe.all_instruments:
        symbol = instrument.symbol
        fetch_start = start
        if incremental and (coverage := store.coverage(symbol)) is not None:
            fetch_start = max(start, _back_off(coverage.end))

        try:
            frame = provider.fetch_daily(symbol, fetch_start, end)
        except (ProviderError, ValueError) as exc:
            results.append(SyncResult(symbol, 0, store.rows(symbol), str(exc)))
            continue

        stored = store.write(symbol, frame)
        results.append(SyncResult(symbol, len(frame), stored))

    return results


def _back_off(last: date, days: int = 7) -> date:
    return date.fromordinal(max(1, last.toordinal() - days))
