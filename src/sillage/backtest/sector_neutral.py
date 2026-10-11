"""Research-only monthly sector sleeve; never connects to a broker."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import trunc

import pandas as pd

from sillage.backtest.relative_value import _normalise, _normalise_rates, _rate_on
from sillage.core.money import dec
from sillage.execution.costs import CostModel
from sillage.strategy.sector_neutral import SECTOR_ETFS, SectorNeutralConfig, decide_sectors


@dataclass(frozen=True)
class SectorReplay:
    nav: pd.Series
    sector_pnl: Mapping[str, float]
    fills: tuple[dict, ...]
    borrow: float
    commissions: float
    slippage: float
    cash_interest: float


def run_sectors(
    bars: Mapping[str, pd.DataFrame],
    *,
    signal: SectorNeutralConfig | None = None,
    initial_nav: float = 25_000,
    borrow_rate: float = 0.01,
    costs: CostModel | None = None,
    cash_rates: pd.Series | None = None,
    delay_months: int = 0,
) -> SectorReplay:
    if initial_nav <= 0 or borrow_rate < 0 or delay_months < 0:
        raise ValueError("invalid replay parameters")
    cfg = signal or SectorNeutralConfig()
    costs = costs or CostModel()
    symbols = (*SECTOR_ETFS, "SPY")
    frames = {s: _normalise(s, bars[s]) for s in symbols}
    sessions = frames["SPY"].index
    if any(not frame.index.equals(sessions) for frame in frames.values()):
        raise ValueError("Sector and SPY sessions must align exactly; no silent dropped days")
    rates = _normalise_rates(cash_rates)
    close = {s: frames[s]["close"].to_numpy(float) for s in symbols}
    opening = {s: frames[s]["open"].to_numpy(float) for s in symbols}
    quantity = dict.fromkeys(SECTOR_ETFS, 0)
    pnl = dict.fromkeys(SECTOR_ETFS, 0.0)
    nav = initial_nav
    nav_rows = []
    fills = []
    pending = None
    queue = []
    borrow = commissions = slippage = interest = 0.0
    needed = max(cfg.long_lookback, cfg.beta_lookback)
    for i, day in enumerate(sessions):
        if i:
            gap = (day - sessions[i - 1]).days
            # Exclude short-sale proceeds: only NAV minus owned long securities earns cash interest.
            owned = sum(max(q, 0) * close[s][i - 1] for s, q in quantity.items())
            if rates is not None:
                earned = max(0.0, nav - owned) * _rate_on(rates, sessions[i - 1]) * gap / 365
                nav += earned
                interest += earned
            for s, q in quantity.items():
                fee = max(-q, 0) * close[s][i - 1] * borrow_rate * gap / 365
                movement = q * (opening[s][i] - close[s][i - 1]) - fee
                nav += movement
                pnl[s] += movement
                borrow += fee
        if pending is not None:
            targets = {s: trunc(pending.get(s, 0) * nav / opening[s][i]) for s in SECTOR_ETFS}
            for s in SECTOR_ETFS:
                change = targets[s] - quantity[s]
                if not change:
                    continue
                # ADV is known at the decision time, never today's future volume.
                adv = float(frames[s]["volume"].iloc[max(0, i - 20) : i].mean())
                price = float(costs.fill_price(s, dec(opening[s][i]), dec(change), dec(adv)))
                fee = float(costs.commission(dec(change), dec(price)))
                slip = abs(price - opening[s][i]) * abs(change)
                nav -= fee + slip
                pnl[s] -= fee + slip
                commissions += fee
                slippage += slip
                quantity[s] = targets[s]
                fills.append(
                    {
                        "session": day.date().isoformat(),
                        "symbol": s,
                        "quantity": change,
                        "price": price,
                    }
                )
            pending = None
        for s, q in quantity.items():
            movement = q * (close[s][i] - opening[s][i])
            nav += movement
            pnl[s] += movement
        if nav <= 0:
            raise ValueError("sleeve insolvent")
        # Next observed session determines only the calendar boundary, not the signal.
        month_end = i + 1 < len(sessions) and day.month != sessions[i + 1].month
        if i >= needed and month_end:
            decision = decide_sectors(
                {s: close[s][: i + 1] for s in SECTOR_ETFS}, close["SPY"][: i + 1], cfg
            )
            queue.append({s: float(w) for s, w in decision.weights.items()})
            if len(queue) > delay_months:
                pending = queue.pop(0)
        nav_rows.append(nav)
    return SectorReplay(
        pd.Series(nav_rows, index=sessions, name="sector-neutral"),
        pnl,
        tuple(fills),
        borrow,
        commissions,
        slippage,
        interest,
    )
