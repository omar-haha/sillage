"""Data quality checks.

Bad data does not announce itself. A missing week looks like a flat price, and an
unadjusted stock split looks like a 50% crash -- which a momentum strategy will read as
a genuine signal and act on. Every serious backtest failure I would expect to hit in
this project traces back to something in this file going unnoticed.

The checks are deliberately advisory: they report, they do not silently repair. An
automatic fix would hide the fact that a data source is unreliable, which is the single
most useful thing to know about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

from sillage.core.calendar import ContinuousCalendar, TradingCalendar

if TYPE_CHECKING:
    import pandas as pd

# A single-session move beyond this is far more likely to be a data error -- usually an
# unadjusted split -- than a real price move in a diversified ETF. Individual stocks and
# crypto genuinely do this, so the threshold is a parameter, not a constant.
JUMP_THRESHOLD = 0.25

# Below this many sessions there is not enough history to compute a 12-month momentum
# signal with a 200-day trend filter, so the asset cannot be traded by the strategy.
MIN_SESSIONS_FOR_SIGNAL = 260


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Issue:
    symbol: str
    severity: Severity
    kind: str
    detail: str


def check_symbol(
    symbol: str,
    frame: pd.DataFrame,
    *,
    continuous: bool = False,
    jump_threshold: float = JUMP_THRESHOLD,
) -> list[Issue]:
    """Inspect one symbol's stored history and report anything suspicious."""
    issues: list[Issue] = []

    if frame.empty:
        return [Issue(symbol, Severity.ERROR, "empty", "no bars stored")]

    if len(frame) < MIN_SESSIONS_FOR_SIGNAL:
        issues.append(
            Issue(
                symbol,
                Severity.WARN,
                "short-history",
                f"{len(frame)} sessions; need {MIN_SESSIONS_FOR_SIGNAL} for a 12m signal",
            )
        )

    issues.extend(_check_missing_sessions(symbol, frame, continuous=continuous))
    issues.extend(_check_price_jumps(symbol, frame, jump_threshold))
    issues.extend(_check_flatlines(symbol, frame))
    issues.extend(_check_zero_volume(symbol, frame))
    return issues


def _check_missing_sessions(symbol: str, frame: pd.DataFrame, *, continuous: bool) -> list[Issue]:
    """Compare stored dates against the days the venue was actually open.

    Counting rows is not enough. A file can hold the right number of bars and still be
    missing a specific week, which would leave the momentum lookback quietly measuring
    a different span than intended.
    """
    start, end = frame.index[0].date(), frame.index[-1].date()
    calendar: TradingCalendar | ContinuousCalendar = (
        ContinuousCalendar() if continuous else TradingCalendar()
    )
    expected = {s.day for s in calendar.sessions(start, end)}
    present = {ts.date() for ts in frame.index}
    missing = sorted(expected - present)

    if not missing:
        return []

    severity = Severity.ERROR if len(missing) > 5 else Severity.WARN
    shown = ", ".join(d.isoformat() for d in missing[:5])
    suffix = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
    return [Issue(symbol, severity, "missing-sessions", f"{len(missing)} absent: {shown}{suffix}")]


def _check_price_jumps(symbol: str, frame: pd.DataFrame, threshold: float) -> list[Issue]:
    """Flag implausible one-session moves -- the signature of an unadjusted split."""
    returns = frame["close"].pct_change()
    extreme = returns[returns.abs() > threshold]
    if extreme.empty:
        return []
    # Positional lookup rather than `idxmax`, whose declared return type is a loose
    # union that does not carry the timestamp methods we need.
    worst_pos = int(extreme.abs().to_numpy().argmax())
    worst_ts = extreme.index[worst_pos]
    worst_move = float(extreme.iloc[worst_pos])
    return [
        Issue(
            symbol,
            Severity.WARN,
            "price-jump",
            f"{len(extreme)} move(s) over {threshold:.0%}; largest {worst_move:+.1%} "
            f"on {worst_ts.date()} -- check for an unadjusted split",
        )
    ]


def _check_flatlines(symbol: str, frame: pd.DataFrame) -> list[Issue]:
    """Find runs of identical closes, which usually mean a stale or forward-filled feed."""
    closes = frame["close"]
    unchanged = closes.diff() == 0
    run = unchanged.groupby((~unchanged).cumsum()).cumsum()
    longest = int(run.max()) if len(run) else 0
    if longest < 5:
        return []
    return [
        Issue(
            symbol,
            Severity.WARN,
            "flatline",
            f"{longest} consecutive sessions with an unchanged close",
        )
    ]


def _check_zero_volume(symbol: str, frame: pd.DataFrame) -> list[Issue]:
    """Zero-volume sessions cannot really be traded, whatever price is recorded."""
    zero = int((frame["volume"] == 0).sum())
    if zero == 0:
        return []
    severity = Severity.WARN if zero > len(frame) * 0.01 else Severity.INFO
    return [Issue(symbol, severity, "zero-volume", f"{zero} session(s) with no volume")]


def summarise(issues: list[Issue]) -> dict[Severity, int]:
    counts = dict.fromkeys(Severity, 0)
    for issue in issues:
        counts[issue.severity] += 1
    return counts


def first_and_last(frame: pd.DataFrame) -> tuple[date, date]:
    return frame.index[0].date(), frame.index[-1].date()
