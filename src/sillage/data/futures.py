"""Contract-level futures data and causal roll maps.

Futures research cannot use a smooth vendor ticker as though it were executable. This
module accepts raw dated contracts and records which real contract is active on every
session. A volume observation made at today's close can affect tomorrow's contract,
never today's; the known last-trade cutoff may act at the session open.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd


CHAIN_COLUMNS = (
    "market",
    "contract",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "last_trade",
    "multiplier",
)

ROLL_COLUMNS = ("market", "session", "contract", "previous_contract", "roll_reason")


@dataclass(frozen=True, slots=True)
class _ContractMetadata:
    contract: str
    last_trade: datetime


def validate_contract_chain(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a normalised copy or reject a chain that could not be traded faithfully."""
    import pandas as pd

    missing = set(CHAIN_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"contract chain is missing columns {sorted(missing)}")
    chain = frame.loc[:, list(CHAIN_COLUMNS)].copy()
    if chain.empty:
        raise ValueError("contract chain is empty")
    for column in ("market", "contract"):
        if chain[column].isna().any() or (chain[column].astype(str).str.strip() == "").any():
            raise ValueError(f"contract chain has an empty {column}")
        chain[column] = chain[column].astype(str)
    for column in ("session", "last_trade"):
        chain[column] = pd.to_datetime(chain[column], utc=True).dt.normalize()
    for column in ("open", "high", "low", "close", "volume", "multiplier"):
        chain[column] = pd.to_numeric(chain[column], errors="raise")

    duplicate = chain.duplicated(["market", "contract", "session"])
    if duplicate.any():
        row = chain.loc[duplicate, ["market", "contract", "session"]].iloc[0]
        raise ValueError(
            f"duplicate contract session: {row.market} {row.contract} {row.session.date()}"
        )
    if (chain["low"] > chain["high"]).any():
        raise ValueError("contract chain has low above high")
    for column in ("open", "close"):
        if ((chain[column] < chain["low"]) | (chain[column] > chain["high"])).any():
            raise ValueError(f"contract chain has {column} outside its high/low range")
    # Negative futures prices are possible (WTI did this in 2020), so positivity is not
    # a valid generic futures-data check. Volume and multipliers do have hard bounds.
    if (chain["volume"] < 0).any():
        raise ValueError("contract chain has negative volume")
    if (chain["multiplier"] <= 0).any():
        raise ValueError("contract chain has a non-positive multiplier")
    if (chain["session"] > chain["last_trade"]).any():
        raise ValueError("contract chain contains a bar after its last-trade date")

    for (market, contract), rows in chain.groupby(["market", "contract"], sort=False):
        if rows["last_trade"].nunique() != 1 or rows["multiplier"].nunique() != 1:
            raise ValueError(f"{market} {contract}: contract metadata changes through history")
    return chain.sort_values(["market", "session", "last_trade", "contract"]).reset_index(
        drop=True
    )


def build_roll_map(frame: pd.DataFrame, *, business_days_before_last_trade: int = 5) -> pd.DataFrame:
    """Select one dated contract per market/session using the frozen roll rule.

    The front contract rolls at the *earlier* of:

    - the session five business days before its last-trade date; or
    - the session after next-contract volume first exceeds front-contract volume.

    The one-session delay on the volume rule is load-bearing: settlement-day volume is
    unknown until the close and cannot choose a contract traded at that same open.
    """
    import pandas as pd
    from pandas.tseries.offsets import BDay

    if business_days_before_last_trade < 1:
        raise ValueError("roll cutoff must be at least one business day")
    chain = validate_contract_chain(frame)
    records: list[dict[str, object]] = []
    for market, market_rows in chain.groupby("market", sort=True):
        metadata = (
            market_rows[["contract", "last_trade"]]
            .drop_duplicates()
            .sort_values(["last_trade", "contract"])
        )
        contracts = [
            _ContractMetadata(str(row.contract), pd.Timestamp(str(row.last_trade)).to_pydatetime())
            for row in metadata.itertuples(index=False)
        ]
        sessions = sorted(pd.Timestamp(value) for value in market_rows["session"].unique())
        active_index = _initial_contract_index(contracts, sessions[0])
        pending_volume_roll = False
        previous: str | None = None

        for session in sessions:
            reason = "hold"
            if pending_volume_roll and active_index + 1 < len(contracts):
                active_index += 1
                reason = "volume"
                pending_volume_roll = False

            active = contracts[active_index]
            cutoff = active.last_trade - BDay(business_days_before_last_trade)
            if session >= cutoff and active_index + 1 < len(contracts):
                active_index += 1
                active = contracts[active_index]
                reason = "expiry"
                pending_volume_roll = False

            contract = str(active.contract)
            if previous is None:
                reason = "initial"
            records.append(
                {
                    "market": market,
                    "session": session,
                    "contract": contract,
                    "previous_contract": previous if contract != previous else contract,
                    "roll_reason": reason,
                }
            )
            previous = contract

            if active_index + 1 >= len(contracts):
                continue
            next_contract = str(contracts[active_index + 1].contract)
            volumes = market_rows[
                (market_rows["session"] == session)
                & market_rows["contract"].isin([contract, next_contract])
            ].set_index("contract")["volume"]
            if contract in volumes and next_contract in volumes:
                pending_volume_roll = float(volumes[next_contract]) > float(volumes[contract])

    return pd.DataFrame.from_records(records, columns=list(ROLL_COLUMNS))


def stitched_returns(
    frame: pd.DataFrame,
    roll_map: pd.DataFrame,
    *,
    economic_sign: Mapping[str, int] | None = None,
) -> pd.DataFrame:
    """Daily returns without treating a roll-price jump as investment performance.

    On a roll session the selected contract is compared with *its own* previous close,
    not the expiring contract's close. Actual closing/opening fills and both roll costs
    remain an execution-simulator responsibility.
    """
    chain = validate_contract_chain(frame)
    missing = set(ROLL_COLUMNS) - set(roll_map.columns)
    if missing:
        raise ValueError(f"roll map is missing columns {sorted(missing)}")
    signs = dict(economic_sign or {})
    if any(value not in (-1, 1) for value in signs.values()):
        raise ValueError("economic signs must be -1 or 1")

    closes = chain.loc[:, ["market", "contract", "session", "close"]].copy()
    closes["previous_close"] = closes.groupby(["market", "contract"])["close"].shift(1)
    selected = roll_map.loc[:, list(ROLL_COLUMNS)].merge(
        closes, on=["market", "contract", "session"], how="left", validate="one_to_one"
    )
    if selected["close"].isna().any():
        row = selected[selected["close"].isna()].iloc[0]
        raise ValueError(
            f"roll map selects missing bar: {row.market} {row.contract} {row.session}"
        )
    selected["return"] = selected["close"] / selected["previous_close"] - 1.0
    selected["return"] *= selected["market"].map(lambda market: signs.get(str(market), 1))
    result: pd.DataFrame = selected[
        ["market", "session", "contract", "roll_reason", "return"]
    ]
    return result


def _initial_contract_index(
    contracts: Sequence[_ContractMetadata], first_session: datetime
) -> int:
    for index, contract in enumerate(contracts):
        if contract.last_trade >= first_session:
            return index
    raise ValueError("no unexpired contract exists on the first session")
