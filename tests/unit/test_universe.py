"""Tests for universe definitions."""

from __future__ import annotations

import pytest

from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import CORE, CORE_PLUS_CRYPTO, Universe, get_universe


def test_core_universe_size() -> None:
    assert len(CORE.instruments) == 12
    # The cash proxy is needed for prices but is not something momentum ranks.
    assert len(CORE.all_instruments) == 13
    assert CORE.cash_proxy.symbol == "BIL"


def test_universe_covers_uncorrelated_exposures() -> None:
    # Momentum ranks assets against each other, so a universe whose members all move
    # together would be ranking noise. This is a smoke test on that intent.
    assert {"SPY", "TLT", "GLD", "DBC"} <= set(CORE.symbols)


def test_duplicate_symbols_are_rejected() -> None:
    spy = Instrument("SPY", AssetClass.ETF)
    with pytest.raises(ValueError, match="duplicate"):
        Universe("bad", (spy, spy))


def test_empty_universe_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        Universe("bad", ())


def test_crypto_universe_marks_continuous_assets() -> None:
    assert CORE_PLUS_CRYPTO.has_continuous_assets
    assert not CORE.has_continuous_assets
    assert CORE_PLUS_CRYPTO.get("BTC-USD").trades_continuously


def test_lookup_of_unknown_symbol_names_the_universe() -> None:
    with pytest.raises(KeyError, match="core"):
        CORE.get("TSLA")


def test_unknown_universe_lists_the_known_ones() -> None:
    with pytest.raises(KeyError, match="known:"):
        get_universe("nope")
