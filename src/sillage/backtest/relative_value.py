"""Stateful after-cost research backtest for Candidate B ETF relative value."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from math import trunc
from typing import Any, cast

import pandas as pd

from sillage.core.money import dec
from sillage.execution.costs import CostModel
from sillage.strategy.relative_value import RelativeValueConfig, decide_pair

DEFAULT_PAIRS = (
    ("SPY", "IVV"),
    ("IWM", "VTWO"),
    ("EFA", "VEA"),
    ("LQD", "VCIT"),
    ("GLD", "IAU"),
)


@dataclass(frozen=True, slots=True)
class RelativeValueBacktestConfig:
    signal: RelativeValueConfig = field(default_factory=RelativeValueConfig)
    initial_nav: float = 25_000.0
    pairs: tuple[tuple[str, str], ...] = DEFAULT_PAIRS
    annual_borrow_rate: float = 0.01
    costs: CostModel = field(default_factory=CostModel)

    def __post_init__(self) -> None:
        if self.initial_nav <= 0:
            raise ValueError("initial NAV must be positive")
        if self.annual_borrow_rate < 0:
            raise ValueError("borrow rate must not be negative")
        symbols = [symbol for pair in self.pairs for symbol in pair]
        if len(symbols) != len(set(symbols)):
            raise ValueError("round-one pairs must not share instruments")


@dataclass(frozen=True, slots=True)
class PairTrade:
    session: date
    pair: str
    symbol: str
    quantity: int
    reference_price: float
    fill_price: float
    commission: float
    reason: str


@dataclass(frozen=True, slots=True)
class RelativeValueBacktestResult:
    nav: pd.Series
    gross_exposure: pd.Series
    trades: tuple[PairTrade, ...]
    pair_pnl: Mapping[str, float]
    borrow_cost: float
    commission: float
    slippage: float
    rejected_entries: int


@dataclass(slots=True)
class _Position:
    left_quantity: int = 0
    right_quantity: int = 0
    held_sessions: int = 0

    @property
    def active(self) -> bool:
        return self.left_quantity != 0 or self.right_quantity != 0


@dataclass(frozen=True, slots=True)
class _Instruction:
    pair: tuple[str, str]
    left_weight: float
    right_weight: float
    reason: str


def run_relative_value(
    bars: Mapping[str, pd.DataFrame],
    config: RelativeValueBacktestConfig | None = None,
    *,
    cash_rates: pd.Series | None = None,
) -> RelativeValueBacktestResult:
    """Replay pair decisions at the next open with borrow and execution costs."""
    cfg = config or RelativeValueBacktestConfig()
    required = {symbol for pair in cfg.pairs for symbol in pair}
    missing = sorted(required - set(bars))
    if missing:
        raise ValueError(f"missing pair bars for {missing}")
    frames = {symbol: _normalise(symbol, bars[symbol]) for symbol in required}
    sessions = sorted(set.intersection(*(set(frame.index) for frame in frames.values())))
    if not sessions:
        raise ValueError("pair histories have no common sessions")
    rates = _normalise_rates(cash_rates)

    nav = cfg.initial_nav
    positions = {pair: _Position() for pair in cfg.pairs}
    previous_close: dict[str, float] = {}
    pending: list[_Instruction] = []
    trades: list[PairTrade] = []
    pair_pnl = {_pair_name(pair): 0.0 for pair in cfg.pairs}
    nav_rows: list[tuple[pd.Timestamp, float]] = []
    gross_rows: list[tuple[pd.Timestamp, float]] = []
    borrow_total = commission_total = slippage_total = 0.0
    rejected_entries = 0
    previous_session: pd.Timestamp | None = None

    for session in sessions:
        gap = (session.date() - previous_session.date()).days if previous_session is not None else 0
        if gap and rates is not None:
            assert previous_session is not None
            nav += nav * _rate_on(rates, previous_session) * gap / 365.0

        # Existing positions earn the close-to-open move and pay borrow over calendar time.
        for pair, position in positions.items():
            if not position.active:
                continue
            name = _pair_name(pair)
            for symbol, quantity in zip(pair, _quantities(position), strict=True):
                opening = float(frames[symbol].loc[session, "open"])
                pnl = quantity * (opening - previous_close[symbol])
                nav += pnl
                pair_pnl[name] += pnl
                if gap and quantity < 0:
                    fee = (
                        abs(quantity) * previous_close[symbol] * cfg.annual_borrow_rate * gap / 365
                    )
                    nav -= fee
                    pair_pnl[name] -= fee
                    borrow_total += fee

        # Instructions were decided at the preceding close.
        for instruction in pending:
            pair = instruction.pair
            position = positions[pair]
            name = _pair_name(pair)
            changes: tuple[tuple[str, int], ...]
            if instruction.reason == "entry":
                left, right = pair
                left_open = float(frames[left].loc[session, "open"])
                right_open = float(frames[right].loc[session, "open"])
                left_target = trunc(instruction.left_weight * nav / left_open)
                right_target = trunc(instruction.right_weight * nav / right_open)
                if not left_target or not right_target:
                    rejected_entries += 1
                    continue
                changes = ((left, left_target), (right, right_target))
                position.left_quantity = left_target
                position.right_quantity = right_target
                position.held_sessions = 0
            else:
                changes = tuple(
                    (symbol, -quantity)
                    for symbol, quantity in zip(pair, _quantities(position), strict=True)
                    if quantity
                )
            for symbol, quantity in changes:
                cost = _execute(
                    trades,
                    cfg.costs,
                    frames[symbol],
                    session,
                    name,
                    symbol,
                    quantity,
                    instruction.reason,
                )
                nav -= cost[0] + cost[1]
                pair_pnl[name] -= cost[0] + cost[1]
                commission_total += cost[0]
                slippage_total += cost[1]
            if instruction.reason != "entry":
                position.left_quantity = position.right_quantity = 0
                position.held_sessions = 0
        pending = []

        # Positions held after the open earn the intraday move.
        for pair, position in positions.items():
            name = _pair_name(pair)
            if position.active:
                position.held_sessions += 1
            for symbol, quantity in zip(pair, _quantities(position), strict=True):
                row = frames[symbol].loc[session]
                pnl = quantity * (float(row["close"]) - float(row["open"]))
                nav += pnl
                pair_pnl[name] += pnl
                previous_close[symbol] = float(row["close"])
            if not position.active:
                for symbol in pair:
                    previous_close[symbol] = _value(frames[symbol], session, "close")

        histories = {
            symbol: frames[symbol].loc[:session, "close"].astype(float).tolist()
            for symbol in required
        }
        exits: list[_Instruction] = []
        entries: list[tuple[float, _Instruction]] = []
        for pair, position in positions.items():
            left, right = pair
            decision = decide_pair(left, right, histories[left], histories[right], cfg.signal)
            if position.active:
                reason = _exit_reason(
                    decision.eligible, decision.zscore, position.held_sessions, cfg
                )
                if reason is not None:
                    exits.append(_Instruction(pair, 0.0, 0.0, reason))
            elif decision.eligible and decision.weights:
                entries.append(
                    (
                        abs(decision.zscore),
                        _Instruction(
                            pair,
                            float(decision.weights[left]),
                            float(decision.weights[right]),
                            "entry",
                        ),
                    )
                )

        pending.extend(exits)
        active_gross = _gross(positions, frames, session, nav)
        reserved = active_gross
        exiting = {instruction.pair for instruction in exits}
        for _, instruction in sorted(entries, key=lambda item: item[0], reverse=True):
            if instruction.pair in exiting:
                continue
            pair_gross = abs(instruction.left_weight) + abs(instruction.right_weight)
            if reserved + pair_gross <= cfg.signal.max_total_gross + 1e-12:
                pending.append(instruction)
                reserved += pair_gross
            else:
                rejected_entries += 1

        nav_rows.append((session, nav))
        gross_rows.append((session, _gross(positions, frames, session, nav)))
        previous_session = session

    return RelativeValueBacktestResult(
        nav=_series(nav_rows, "nav"),
        gross_exposure=_series(gross_rows, "gross_exposure"),
        trades=tuple(trades),
        pair_pnl=pair_pnl,
        borrow_cost=borrow_total,
        commission=commission_total,
        slippage=slippage_total,
        rejected_entries=rejected_entries,
    )


def _exit_reason(
    eligible: bool, zscore: float, held_sessions: int, config: RelativeValueBacktestConfig
) -> str | None:
    if not eligible:
        return "invalidated"
    if abs(zscore) <= config.signal.exit_z:
        return "converged"
    if abs(zscore) >= config.signal.stop_z:
        return "stop"
    if held_sessions >= config.signal.max_holding_sessions:
        return "timeout"
    return None


def _execute(
    trades: list[PairTrade],
    costs: CostModel,
    frame: pd.DataFrame,
    session: pd.Timestamp,
    pair: str,
    symbol: str,
    quantity: int,
    reason: str,
) -> tuple[float, float]:
    reference = _value(frame, session, "open")
    volume = _value(frame, session, "volume")
    fill = float(costs.fill_price(symbol, dec(reference), dec(quantity), dec(volume)))
    commission = float(costs.commission(dec(quantity), dec(fill)))
    slippage = abs(fill - reference) * abs(quantity)
    trades.append(
        PairTrade(session.date(), pair, symbol, quantity, reference, fill, commission, reason)
    )
    return commission, slippage


def _value(frame: pd.DataFrame, session: pd.Timestamp, column: str) -> float:
    indexed: Any = frame
    value: Any = indexed.loc[session, column]
    return float(cast(Any, value))


def _normalise(symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{symbol}: missing columns {sorted(missing)}")
    result = frame.loc[:, ["open", "close", "volume"]].copy()
    result.index = pd.to_datetime(result.index, utc=True).normalize()
    if result.index.has_duplicates:
        raise ValueError(f"{symbol}: duplicate sessions")
    if result.isna().any().any() or (result[["open", "close"]] <= 0).any().any():
        raise ValueError(f"{symbol}: invalid pair bars")
    return result.sort_index()


def _gross(
    positions: Mapping[tuple[str, str], _Position],
    frames: Mapping[str, pd.DataFrame],
    session: pd.Timestamp,
    nav: float,
) -> float:
    if nav <= 0:
        return float("inf")
    notional = 0.0
    for pair, position in positions.items():
        for symbol, quantity in zip(pair, _quantities(position), strict=True):
            notional += abs(quantity) * _value(frames[symbol], session, "close")
    return notional / nav


def _quantities(position: _Position) -> tuple[int, int]:
    return position.left_quantity, position.right_quantity


def _pair_name(pair: tuple[str, str]) -> str:
    return f"{pair[0]}/{pair[1]}"


def _series(rows: list[tuple[pd.Timestamp, float]], name: str) -> pd.Series:
    return pd.Series(
        [value for _, value in rows],
        index=pd.DatetimeIndex([session for session, _ in rows]),
        name=name,
        dtype=float,
    )


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
