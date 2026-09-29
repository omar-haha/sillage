"""Tests for Candidate C's frozen sector-neutral signal."""

from __future__ import annotations

import math

import pytest

from sillage.strategy.sector_neutral import SECTOR_ETFS, SectorNeutralConfig, decide_sectors


def histories(sessions: int = 300) -> tuple[dict[str, list[float]], list[float]]:
    market = [100 * (1.0004**i) * (1 + 0.002 * math.sin(i / 7)) for i in range(sessions)]
    sectors = {
        symbol: [price * (1 + rank * 0.00008) ** i for i, price in enumerate(market)]
        for rank, symbol in enumerate(SECTOR_ETFS)
    }
    return sectors, market


def test_selects_two_strongest_and_two_weakest() -> None:
    sectors, market = histories()
    result = decide_sectors(sectors, market)
    assert result.eligible
    assert result.longs == ("XLY", "XLV")
    assert result.shorts == ("XLB", "XLE")
    assert set(result.weights) == {*result.longs, *result.shorts}


def test_weights_are_gross_and_beta_neutral() -> None:
    sectors, market = histories()
    result = decide_sectors(sectors, market)
    gross = sum(abs(float(weight)) for weight in result.weights.values())
    beta = sum(float(result.weights[s]) * result.betas[s] for s in result.weights)
    assert gross <= 1.0 + 1e-5
    assert beta == pytest.approx(0.0, abs=1e-5)
    assert max(abs(float(weight)) for weight in result.weights.values()) <= 0.35


def test_signal_uses_the_declared_skip_month() -> None:
    sectors, market = histories()
    baseline = decide_sectors(sectors, market)
    sectors["XLB"][-20:] = [
        value * (1 + 0.001 * (index + 1)) for index, value in enumerate(sectors["XLB"][-20:])
    ]
    assert decide_sectors(sectors, market).longs == baseline.longs


def test_insufficient_history_is_not_a_trade() -> None:
    sectors, market = histories(100)
    result = decide_sectors(sectors, market)
    assert not result.eligible
    assert not result.weights


def test_invalid_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="lookbacks"):
        SectorNeutralConfig(skip=126)
