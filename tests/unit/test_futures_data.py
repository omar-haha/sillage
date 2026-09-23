"""Tests for raw futures chains, causal rolls and stitched signal returns."""

from __future__ import annotations

import pandas as pd
import pytest

from sillage.data.futures import build_roll_map, stitched_returns, validate_contract_chain


def chain(
    *,
    front_volume: list[int] | None = None,
    next_volume: list[int] | None = None,
    front_last_trade: str = "2026-02-20",
) -> pd.DataFrame:
    sessions = pd.date_range("2026-02-02", periods=8, freq="B", tz="UTC")
    front_volume = front_volume or [100] * len(sessions)
    next_volume = next_volume or [10] * len(sessions)
    rows = []
    for contract, last_trade, base, volumes in (
        ("ESH6", front_last_trade, 100, front_volume),
        ("ESM6", "2026-06-19", 200, next_volume),
    ):
        for index, session in enumerate(sessions):
            close = base + index
            rows.append(
                {
                    "market": "ES",
                    "contract": contract,
                    "session": session,
                    "open": close - 0.5,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": volumes[index],
                    "last_trade": last_trade,
                    "multiplier": 50,
                }
            )
    return pd.DataFrame(rows)


def test_volume_observed_at_close_rolls_the_next_session() -> None:
    data = chain(front_volume=[100] * 8, next_volume=[10, 150, 150, 150, 150, 150, 150, 150])
    rolls = build_roll_map(data)
    assert list(rolls["contract"][:2]) == ["ESH6", "ESH6"]
    assert rolls.iloc[2]["contract"] == "ESM6"
    assert rolls.iloc[2]["roll_reason"] == "volume"


def test_known_expiry_cutoff_rolls_at_that_sessions_open() -> None:
    # Five business days before Friday Feb 13 is Friday Feb 6.
    rolls = build_roll_map(chain(front_last_trade="2026-02-13"))
    rolled = rolls[rolls["roll_reason"] == "expiry"].iloc[0]
    assert rolled["session"] == pd.Timestamp("2026-02-06", tz="UTC")
    assert rolled["contract"] == "ESM6"


def test_roll_return_uses_the_new_contracts_own_previous_close() -> None:
    data = chain(front_volume=[100] * 8, next_volume=[10, 150, 150, 150, 150, 150, 150, 150])
    returns = stitched_returns(data, build_roll_map(data))
    rolled = returns[returns["roll_reason"] == "volume"].iloc[0]
    # ESM6 moved 201 -> 202. Comparing it with ESH6's 101 would invent a ~100% gain.
    assert rolled["return"] == pytest.approx(202 / 201 - 1)


def test_economic_direction_can_be_inverted_for_a_yield_contract() -> None:
    data = chain()
    ordinary = stitched_returns(data, build_roll_map(data))
    inverted = stitched_returns(data, build_roll_map(data), economic_sign={"ES": -1})
    assert inverted["return"].dropna().iloc[0] == pytest.approx(
        -ordinary["return"].dropna().iloc[0]
    )


def test_negative_prices_are_valid_futures_data() -> None:
    data = chain()
    row = data.index[0]
    data.loc[row, ["open", "high", "low", "close"]] = [-10.0, -9.0, -11.0, -10.0]
    assert validate_contract_chain(data).iloc[0]["close"] == -10.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.drop(columns="last_trade"), "missing columns"),
        (lambda data: pd.concat([data, data.iloc[[0]]]), "duplicate contract session"),
        (lambda data: data.assign(volume=-1), "negative volume"),
        (lambda data: data.assign(multiplier=0), "non-positive multiplier"),
    ],
)
def test_malformed_contract_chains_are_rejected(mutation: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_contract_chain(mutation(chain()))  # type: ignore[operator]


def test_a_roll_map_cannot_select_a_bar_that_does_not_exist() -> None:
    data = chain()
    rolls = build_roll_map(data)
    rolls.loc[0, "contract"] = "MISSING"
    with pytest.raises(ValueError, match="selects missing bar"):
        stitched_returns(data, rolls)
