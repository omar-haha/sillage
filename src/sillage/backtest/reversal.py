"""Frozen research-only stress reversal replay. No broker or journal writes."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import trunc

import pandas as pd

from sillage.backtest.relative_value import _normalise, _normalise_rates, _rate_on
from sillage.core.money import dec
from sillage.execution.costs import CostModel
from sillage.strategy.reversal import reversal_weights
from sillage.strategy.sector_neutral import SECTOR_ETFS


@dataclass(frozen=True)
class ReversalReplay:
    nav: pd.Series
    fills: tuple[dict, ...]
    borrow: float
    commissions: float
    slippage: float
    financing: float
    entries: int
    terminal_positions: Mapping[str, int]


def run_reversal(
    bars: Mapping[str, pd.DataFrame],
    vix: pd.Series,
    *,
    initial_nav: float = 5000,
    threshold: float = 25,
    delay: int = 0,
    borrow_rate: float = 0.01,
    costs: CostModel | None = None,
    margin_rates: pd.Series | None = None,
) -> ReversalReplay:
    if initial_nav <= 0 or delay < 0 or borrow_rate < 0:
        raise ValueError("invalid replay parameters")
    costs = costs or CostModel(
        commission_per_share=dec("0.005"),
        min_commission=dec(1),
        half_spread_bps={},
        default_half_spread_bps=dec(2),
    )
    frames = {s: _normalise(s, bars[s]) for s in (*SECTOR_ETFS, "SPY")}
    sessions = frames["SPY"].index
    if any(not frame.index.equals(sessions) for frame in frames.values()):
        raise ValueError("ETF sessions must align exactly")
    vix = vix.copy()
    vix.index = pd.to_datetime(vix.index, utc=True).normalize()
    vix = vix.reindex(sessions)
    if vix.isna().any() or (vix <= 0).any():
        raise ValueError("VIX has missing or invalid sessions")
    rates = _normalise_rates(margin_rates)
    close = {s: f["close"].to_numpy(float) for s, f in frames.items()}
    opening = {s: f["open"].to_numpy(float) for s, f in frames.items()}
    nav = initial_nav
    quantities = dict.fromkeys(SECTOR_ETFS, 0)
    fills, rows = [], []
    pending = None
    exit_at = None
    borrow = commission = slippage = financing = 0.0
    entries = 0
    for i, day in enumerate(sessions):
        if i:
            gap = (day - sessions[i - 1]).days
            long_value = sum(max(q, 0) * close[s][i - 1] for s, q in quantities.items())
            debit = max(0, long_value - nav)  # short proceeds are collateral, not free financing
            if debit:
                if rates is None:
                    raise ValueError("debit financing requires a dated margin curve")
                fee = debit * _rate_on(rates, sessions[i - 1]) * gap / 365
                nav -= fee
                financing += fee
            for s, q in quantities.items():
                fee = max(-q, 0) * close[s][i - 1] * borrow_rate * gap / 365
                nav += q * (opening[s][i] - close[s][i - 1]) - fee
                borrow += fee
        changes = {}
        reason = ""
        if exit_at == i:
            changes = {s: -q for s, q in quantities.items()}
            exit_at = None
            reason = "exit"
        elif pending is not None and pending[0] == i:
            changes = {s: trunc(pending[1].get(s, 0) * nav / opening[s][i]) for s in SECTOR_ETFS}
            # No partial admission if whole-share sizing loses any of the four legs.
            selected = set(pending[1])
            if not all(changes[s] for s in selected):
                changes = {}
            else:
                exit_at = i + 2
                entries += 1
            pending = None
            reason = "entry"
        for s, change in changes.items():
            if not change:
                continue
            adv = float(frames[s]["volume"].iloc[max(0, i - 20) : i].mean())
            price = float(costs.fill_price(s, dec(opening[s][i]), dec(change), dec(adv)))
            fee = float(costs.commission(dec(change), dec(price)))
            slip = abs(price - opening[s][i]) * abs(change)
            nav -= fee + slip
            commission += fee
            slippage += slip
            quantities[s] += change
            fills.append(
                {
                    "session": day.date().isoformat(),
                    "symbol": s,
                    "quantity": change,
                    "price": price,
                    "reason": reason,
                }
            )
        for s, q in quantities.items():
            nav += q * (close[s][i] - opening[s][i])
        if nav <= 0:
            raise ValueError("reversal sleeve insolvent")
        if i >= 252 and exit_at is None and pending is None:
            weights = reversal_weights(
                {s: close[s][: i + 1] for s in SECTOR_ETFS},
                close["SPY"][: i + 1],
                float(vix.iloc[i]),
                threshold,
            )
            if weights:
                pending = (i + 1 + delay, weights)
        rows.append(nav)
    return ReversalReplay(
        pd.Series(rows, index=sessions, name="reversal"),
        tuple(fills),
        borrow,
        commission,
        slippage,
        financing,
        entries,
        {s: q for s, q in quantities.items() if q},
    )
