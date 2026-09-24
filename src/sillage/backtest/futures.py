"""Contract-level research simulator for the Phase 8 futures candidate.

This is deliberately separate from the equity event loop.  A futures position has no
cash purchase value: profit and loss is variation margin, exposure depends on a
contract multiplier, and changing delivery months is two trades rather than a renamed
holding.  Forcing those facts through the share ledger would make a plausible-looking
but incorrect backtest.

The simulator consumes only raw dated contracts plus the causal roll map from
``sillage.data.futures``.  A decision made from session D's close is applied at the next
available session's open.  No continuous-contract price is ever used for a fill.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from math import trunc
from typing import Any, cast

import pandas as pd

from sillage.data.futures import build_roll_map, stitched_returns, validate_contract_chain
from sillage.strategy.futures_trend import FuturesTrendConfig, decide


@dataclass(frozen=True, slots=True)
class FuturesCost:
    """Round-turn inputs stated in contract units, not equity basis points."""

    commission_per_contract: float
    half_spread_points: float

    def __post_init__(self) -> None:
        if self.commission_per_contract < 0 or self.half_spread_points < 0:
            raise ValueError("futures costs must not be negative")


@dataclass(frozen=True, slots=True)
class FuturesBacktestConfig:
    initial_nav: float = 25_000.0
    signal: FuturesTrendConfig = field(default_factory=FuturesTrendConfig)
    rebalance_weekday: int = 4
    economic_sign: Mapping[str, int] = field(default_factory=dict)
    costs: Mapping[str, FuturesCost] = field(default_factory=dict)
    initial_margin: Mapping[str, float] = field(default_factory=dict)
    max_margin_fraction: float = 0.35

    def __post_init__(self) -> None:
        if self.initial_nav <= 0:
            raise ValueError("initial NAV must be positive")
        if self.rebalance_weekday not in range(7):
            raise ValueError("rebalance weekday must be between 0 and 6")
        if not 0 < self.max_margin_fraction <= 1:
            raise ValueError("margin fraction must be in (0, 1]")
        if any(sign not in (-1, 1) for sign in self.economic_sign.values()):
            raise ValueError("economic signs must be -1 or 1")
        if any(value < 0 for value in self.initial_margin.values()):
            raise ValueError("initial margins must not be negative")


@dataclass(frozen=True, slots=True)
class FuturesTrade:
    session: date
    market: str
    contract: str
    quantity: int
    price: float
    commission: float
    spread_cost: float
    reason: str


@dataclass(frozen=True, slots=True)
class FuturesBacktestResult:
    nav: pd.Series
    positions: pd.DataFrame
    trades: tuple[FuturesTrade, ...]
    total_commission: float
    total_spread_cost: float
    maximum_margin_fraction: float
    skipped_margin_rebalances: int


def run_futures_trend(
    frame: pd.DataFrame,
    config: FuturesBacktestConfig | None = None,
    *,
    cash_rates: pd.Series | None = None,
) -> FuturesBacktestResult:
    """Replay the frozen trend candidate with whole contracts and daily settlement.

    ``cash_rates`` contains annual decimal rates effective from their dated index.  They
    are carried forward, never backward; omitting it explicitly models zero interest.
    """
    cfg = config or FuturesBacktestConfig()
    chain = validate_contract_chain(frame)
    rolls = build_roll_map(chain)
    returns = stitched_returns(chain, rolls, economic_sign=cfg.economic_sign)
    sessions = sorted(pd.Timestamp(value) for value in rolls["session"].unique())
    markets = sorted(str(value) for value in rolls["market"].unique())
    _require_inputs(markets, cfg)

    bars = chain.set_index(["market", "contract", "session"]).sort_index()
    active = rolls.set_index(["session", "market"]).sort_index()
    histories: dict[str, list[float]] = {market: [] for market in markets}
    return_rows = returns.set_index(["session", "market"]).sort_index()
    rates = _normalise_rates(cash_rates)

    nav = cfg.initial_nav
    quantities = dict.fromkeys(markets, 0)
    contracts: dict[str, str] = {}
    previous_closes: dict[str, float] = {}
    pending_weights: dict[str, float] | None = None
    nav_rows: list[tuple[pd.Timestamp, float]] = []
    position_rows: list[dict[str, object]] = []
    trades: list[FuturesTrade] = []
    commission_total = 0.0
    spread_total = 0.0
    maximum_margin_fraction = 0.0
    skipped_margin = 0
    previous_session: pd.Timestamp | None = None

    for session in sessions:
        if previous_session is not None and rates is not None:
            days = (session.date() - previous_session.date()).days
            nav += nav * _rate_on(rates, previous_session) * days / 365.0

        # Mark yesterday's actual contracts to today's open before changing delivery
        # month or target.  This includes the overnight move and keeps rolls causal.
        opens: dict[str, float] = {}
        selected: dict[str, str] = {}
        for market in markets:
            contract = str(active.loc[(session, market), "contract"])
            selected[market] = contract
            opens[market] = _float_at(bars, market, contract, session, "open")
            old_contract = contracts.get(market)
            if old_contract is not None and quantities[market]:
                old_open = _float_at(bars, market, old_contract, session, "open")
                multiplier = _float_at(bars, market, old_contract, session, "multiplier")
                nav += quantities[market] * (old_open - previous_closes[market]) * multiplier

        if pending_weights is not None:
            targets = _whole_contract_targets(
                pending_weights, nav, selected, opens, bars, session, cfg.economic_sign
            )
            margin = sum(abs(targets[market]) * cfg.initial_margin[market] for market in markets)
            margin_fraction = margin / nav if nav > 0 else float("inf")
            maximum_margin_fraction = max(maximum_margin_fraction, margin_fraction)
            if margin_fraction <= cfg.max_margin_fraction:
                for market in markets:
                    old_contract = contracts.get(market)
                    old_quantity = quantities[market]
                    new_contract = selected[market]
                    new_quantity = targets[market]
                    if old_contract is not None and old_contract != new_contract and old_quantity:
                        cost = _record_trade(
                            trades,
                            cfg,
                            bars,
                            session,
                            market,
                            old_contract,
                            -old_quantity,
                            "roll-close",
                        )
                        nav -= sum(cost)
                        commission_total += cost[0]
                        spread_total += cost[1]
                        old_quantity = 0
                    change = new_quantity - old_quantity
                    if change:
                        reason = "roll-open" if old_contract != new_contract else "rebalance"
                        cost = _record_trade(
                            trades, cfg, bars, session, market, new_contract, change, reason
                        )
                        nav -= sum(cost)
                        commission_total += cost[0]
                        spread_total += cost[1]
                    quantities[market] = new_quantity
                    contracts[market] = new_contract
            else:
                skipped_margin += 1
            pending_weights = None

        # A mandatory roll cannot wait for the next weekly signal. Preserve exposure in
        # the newly active delivery month, charging both legs.
        for market in markets:
            old_contract = contracts.get(market)
            new_contract = selected[market]
            quantity = quantities[market]
            if old_contract is not None and old_contract != new_contract and quantity:
                for contract, change, reason in (
                    (old_contract, -quantity, "roll-close"),
                    (new_contract, quantity, "roll-open"),
                ):
                    cost = _record_trade(
                        trades, cfg, bars, session, market, contract, change, reason
                    )
                    nav -= sum(cost)
                    commission_total += cost[0]
                    spread_total += cost[1]
            contracts[market] = new_contract

        # Mark the position held after the open from open to settlement close.
        for market in markets:
            contract = contracts[market]
            row = _row_at(bars, market, contract, session)
            multiplier = float(row["multiplier"])
            close = float(row["close"])
            nav += quantities[market] * (close - float(row["open"])) * multiplier
            previous_closes[market] = close
            position_rows.append(
                {
                    "session": session,
                    "market": market,
                    "contract": contract,
                    "quantity": quantities[market],
                }
            )
            key = (session, market)
            if key in return_rows.index:
                value = return_rows.loc[key, "return"]
                if pd.notna(value):
                    histories[market].append(float(cast(Any, value)))

        nav_rows.append((session, nav))
        if _is_weekly_decision(session, sessions, cfg.rebalance_weekday):
            decision = decide(histories, cfg.signal)
            pending_weights = {market: float(decision.weights.get(market, 0)) for market in markets}
        previous_session = session

    nav_series = pd.Series(
        [value for _, value in nav_rows],
        index=pd.DatetimeIndex([session for session, _ in nav_rows]),
        name="nav",
        dtype=float,
    )
    return FuturesBacktestResult(
        nav=nav_series,
        positions=pd.DataFrame(position_rows),
        trades=tuple(trades),
        total_commission=commission_total,
        total_spread_cost=spread_total,
        maximum_margin_fraction=maximum_margin_fraction,
        skipped_margin_rebalances=skipped_margin,
    )


def _whole_contract_targets(
    weights: Mapping[str, float],
    nav: float,
    contracts: Mapping[str, str],
    opens: Mapping[str, float],
    bars: pd.DataFrame,
    session: pd.Timestamp,
    economic_sign: Mapping[str, int],
) -> dict[str, int]:
    targets: dict[str, int] = {}
    for market, contract in contracts.items():
        multiplier = _float_at(bars, market, contract, session, "multiplier")
        unit_value = abs(opens[market] * multiplier)
        quantity = trunc(weights.get(market, 0.0) * nav / unit_value) if unit_value else 0
        targets[market] = quantity * economic_sign.get(market, 1)
    return targets


def _record_trade(
    trades: list[FuturesTrade],
    config: FuturesBacktestConfig,
    bars: pd.DataFrame,
    session: pd.Timestamp,
    market: str,
    contract: str,
    quantity: int,
    reason: str,
) -> tuple[float, float]:
    row = _row_at(bars, market, contract, session)
    cost = config.costs[market]
    commission = abs(quantity) * cost.commission_per_contract
    spread = abs(quantity) * cost.half_spread_points * float(row["multiplier"])
    trades.append(
        FuturesTrade(
            session=session.date(),
            market=market,
            contract=contract,
            quantity=quantity,
            price=float(row["open"]),
            commission=commission,
            spread_cost=spread,
            reason=reason,
        )
    )
    return commission, spread


def _row_at(frame: pd.DataFrame, market: str, contract: str, session: pd.Timestamp) -> pd.Series:
    indexed: Any = frame
    result: Any = indexed.loc[(market, contract, session)]
    return cast(pd.Series, result)


def _float_at(
    frame: pd.DataFrame, market: str, contract: str, session: pd.Timestamp, column: str
) -> float:
    row = _row_at(frame, market, contract, session)
    return float(cast(Any, row[column]))


def _require_inputs(markets: list[str], config: FuturesBacktestConfig) -> None:
    for name, values in (("cost", config.costs), ("initial margin", config.initial_margin)):
        missing = sorted(set(markets) - set(values))
        if missing:
            raise ValueError(f"missing {name} inputs for {missing}")


def _is_weekly_decision(session: pd.Timestamp, sessions: list[pd.Timestamp], weekday: int) -> bool:
    week = session.isocalendar()[:2]
    same_week = [candidate for candidate in sessions if candidate.isocalendar()[:2] == week]
    eligible = [candidate for candidate in same_week if candidate.weekday() <= weekday]
    return bool(eligible) and session == eligible[-1]


def _normalise_rates(rates: pd.Series | None) -> pd.Series | None:
    if rates is None:
        return None
    result = rates.copy().astype(float)
    result.index = pd.to_datetime(result.index, utc=True).normalize()
    return result.sort_index()


def _rate_on(rates: pd.Series, session: pd.Timestamp) -> float:
    known = rates.loc[rates.index <= session]
    if known.empty:
        raise ValueError(f"no cash rate known on {session.date()}")
    return float(known.iloc[-1])
