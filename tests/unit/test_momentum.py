"""Tests for the dual momentum strategy.

Built on invented prices where the right answer is known in advance. A momentum test on
real data can only assert that something plausible happened; here a rising asset must be
selected and a falling one must not, and if that stops being true the test says so.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from tests.support import etf, make_bars

from sillage.core.money import ZERO, dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import Universe
from sillage.engine.feed import HistoricalFeed
from sillage.portfolio.sizing import VolatilityTarget
from sillage.strategy.base import Monthly
from sillage.strategy.momentum import (
    SESSIONS_PER_MONTH,
    DualMomentum,
    MomentumConfig,
    _average_ranks,
    build,
)

LATER = datetime(2040, 1, 1, tzinfo=UTC)
BARS = 120

#: Small windows so a test fixture needs a hundred bars rather than three hundred.
QUICK = MomentumConfig(lookbacks=(1, 2), skip_months=1, trend_window=10, top_n=2)
QUICK_SIZER = VolatilityTarget(lookback=20, min_observations=10)

CASH = Instrument("CASH", AssetClass.ETF)


def universe(*symbols: str) -> Universe:
    return Universe("test", (*[etf(s) for s in symbols], CASH), cash_proxy=CASH)


def ramp(start: float, per_session: float, n: int = BARS, wobble: float = 0.006) -> list[float]:
    """A compounding path with a trend and a real amount of daily movement.

    The wobble is not decoration. A perfectly smooth ramp has a daily return that never
    changes, so its measured volatility is zero, and inverse-volatility sizing would
    then be dividing by rounding error. A first version of this fixture did exactly
    that and the sizing tests were meaningless without failing.
    """
    prices, price = [], start
    for i in range(n):
        prices.append(price)
        price *= 1.0 + per_session + (wobble if i % 2 == 0 else -wobble)
    return prices


def feed_of(**paths: list[float]) -> HistoricalFeed:
    return HistoricalFeed({s: make_bars(etf(s), p) for s, p in paths.items()})


def strategy(*symbols: str, config: MomentumConfig | None = None) -> DualMomentum:
    return DualMomentum(universe(*symbols), config or QUICK, QUICK_SIZER)


# ------------------------------------------------------------------ ranking


def test_ranks_share_the_average_when_scores_tie() -> None:
    """Otherwise selection would silently depend on the alphabet."""
    ranks = _average_ranks({"a": 1.0, "b": 2.0, "c": 2.0, "d": 5.0})
    assert ranks == {"a": 1.0, "b": 2.5, "c": 2.5, "d": 4.0}


def test_ranks_run_from_one_upward_with_the_best_highest() -> None:
    ranks = _average_ranks({"worst": -0.5, "middle": 0.0, "best": 0.9})
    assert ranks["best"] > ranks["middle"] > ranks["worst"] == 1.0


def test_the_most_recent_month_is_skipped() -> None:
    """Short-horizon momentum reverses, so including it points the wrong way."""
    prices = [dec(100)] * BARS
    # A spike confined to the final month must not register at all.
    spiked = prices[:-SESSIONS_PER_MONTH] + [dec(500)] * SESSIONS_PER_MONTH
    assert strategy("A")._momentum(spiked, months=2) == pytest.approx(0.0)


def test_momentum_measures_the_window_it_claims_to() -> None:
    prices = [dec(100)] * BARS
    # Doubling one month before the skipped month must show up as a 100% gain.
    doubled = prices[: -2 * SESSIONS_PER_MONTH] + [dec(200)] * SESSIONS_PER_MONTH * 2
    assert strategy("A")._momentum(doubled, months=1) == pytest.approx(1.0)


def test_an_unmeasurable_lookback_ranks_last() -> None:
    assert strategy("A")._momentum([dec(100)] * 5, months=12) == float("-inf")


# ------------------------------------------------------------------ selection


def test_it_holds_the_risers_and_not_the_fallers() -> None:
    data = feed_of(
        UP=ramp(100, 0.003), ALSO=ramp(100, 0.002), FLAT=ramp(100, 0.0), DOWN=ramp(100, -0.002)
    )
    weights = strategy("UP", "ALSO", "FLAT", "DOWN").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert set(weights) - {"CASH"} == {"UP", "ALSO"}


def test_it_selects_no_more_than_the_configured_count() -> None:
    data = feed_of(A=ramp(100, 0.004), B=ramp(100, 0.003), C=ramp(100, 0.002), D=ramp(100, 0.001))
    weights = strategy("A", "B", "C", "D").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert len(set(weights) - {"CASH"}) == QUICK.top_n


def test_a_falling_asset_sends_its_money_to_cash() -> None:
    """The trend filter's whole job: retreat, do not reallocate to the survivors."""
    data = feed_of(UP=ramp(100, 0.003), DOWN=ramp(100, -0.004))
    weights = strategy("UP", "DOWN").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert "DOWN" not in weights
    assert weights["CASH"] > ZERO


def test_everything_falling_means_everything_in_cash() -> None:
    data = feed_of(A=ramp(100, -0.002), B=ramp(100, -0.003))
    weights = strategy("A", "B").target_weights(as_of=LATER, data=data)
    assert weights == {"CASH": dec(1)}


def test_the_rejected_weight_is_not_shared_out_among_survivors() -> None:
    """Concentrating the book when the filter says take less risk would invert it."""
    both_up = feed_of(A=ramp(100, 0.003), B=ramp(100, 0.002))
    one_down = feed_of(A=ramp(100, 0.003), B=ramp(100, -0.002))
    a_when_both = strategy("A", "B").target_weights(as_of=LATER, data=both_up)
    a_when_one = strategy("A", "B").target_weights(as_of=LATER, data=one_down)
    assert a_when_both is not None and a_when_one is not None
    assert a_when_one["A"] <= a_when_both["A"]
    assert a_when_one["CASH"] > a_when_both.get("CASH", ZERO)


# ------------------------------------------------------------------ weights


def test_weights_including_cash_sum_to_one() -> None:
    data = feed_of(A=ramp(100, 0.003), B=ramp(100, 0.002), C=ramp(100, -0.001))
    weights = strategy("A", "B", "C").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert sum(weights.values(), start=ZERO) == pytest.approx(Decimal(1), abs=Decimal("0.00001"))


def test_it_never_asks_for_leverage() -> None:
    data = feed_of(A=ramp(100, 0.0001), B=ramp(100, 0.00005))
    weights = strategy("A", "B").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert sum(weights.values(), start=ZERO) <= dec("1.000001")


def test_every_decision_is_recorded_with_its_reasoning() -> None:
    data = feed_of(A=ramp(100, 0.003), B=ramp(100, -0.003))
    strat = strategy("A", "B")
    strat.target_weights(as_of=LATER, data=data)
    decision = strat.decisions[-1]
    assert decision.selected == ("A", "B")
    assert decision.rejected_by_trend == ("B",)
    assert decision.ex_ante_volatility > 0


# ------------------------------------------------------------------ warm-up


def test_it_holds_no_opinion_before_it_has_history() -> None:
    """`None`, never `{}`. An empty mapping would liquidate the book on day one."""
    data = feed_of(A=ramp(100, 0.003), B=ramp(100, 0.002))
    early = make_bars(etf("A"), ramp(100, 0.003))[30].ts
    assert strategy("A", "B").target_weights(as_of=early, data=data) is None


def test_an_asset_with_too_little_history_is_simply_not_eligible() -> None:
    data = HistoricalFeed(
        {
            "A": make_bars(etf("A"), ramp(100, 0.003)),
            "B": make_bars(etf("B"), ramp(100, 0.002)),
            "NEW": make_bars(etf("NEW"), ramp(100, 0.010, n=20)),
        }
    )
    weights = strategy("A", "B", "NEW").target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert "NEW" not in weights


def test_a_universe_of_one_cannot_be_ranked() -> None:
    data = feed_of(A=ramp(100, 0.003))
    assert strategy("A").target_weights(as_of=LATER, data=data) is None


# ------------------------------------------------------------------ configuration


def test_resetting_clears_the_decision_log() -> None:
    """Walk-forward reuses one strategy object across many overlapping windows."""
    data = feed_of(A=ramp(100, 0.003), B=ramp(100, 0.002))
    strat = strategy("A", "B")
    strat.target_weights(as_of=LATER, data=data)
    strat.reset()
    assert strat.decisions == []


def test_warmup_covers_the_longest_thing_it_needs() -> None:
    strat = strategy("A", "B", config=MomentumConfig(lookbacks=(3, 6, 12)))
    assert strat.warmup_sessions == 12 * SESSIONS_PER_MONTH + SESSIONS_PER_MONTH + 1


def test_the_cash_proxy_is_never_itself_a_candidate() -> None:
    strat = strategy("A", "B")
    assert CASH not in strat.candidates(date(2030, 1, 1))


def test_an_asset_is_not_a_candidate_before_it_was_investable() -> None:
    """Whether something belonged in a diversified fund is a judgement that changed over
    time; a backtest that ignores that is a story about hindsight."""
    late = Instrument("LATE", AssetClass.ETF, available_from=date(2025, 1, 1))
    strat = DualMomentum(Universe("t", (etf("A"), late, CASH), cash_proxy=CASH), QUICK, QUICK_SIZER)
    assert late not in strat.candidates(date(2024, 6, 1))
    assert late in strat.candidates(date(2025, 6, 1))


def test_a_position_is_capped_and_the_excess_goes_to_cash() -> None:
    """Inverse-vol handles concentration; the cap is for gap risk it cannot see."""
    data = feed_of(A=ramp(100, 0.003), B=ramp(100, 0.002))
    capped = DualMomentum(
        universe("A", "B"),
        MomentumConfig(
            lookbacks=QUICK.lookbacks,
            trend_window=QUICK.trend_window,
            top_n=QUICK.top_n,
            max_weight=dec("0.10"),
        ),
        QUICK_SIZER,
    )
    weights = capped.target_weights(as_of=LATER, data=data)
    assert weights is not None
    assert all(w <= dec("0.10") for s, w in weights.items() if s != "CASH")
    assert weights["CASH"] >= dec("0.80")


@pytest.mark.parametrize(
    "bad",
    [
        {"lookbacks": ()},
        {"lookbacks": (0,)},
        {"top_n": 0},
        {"trend_window": 0},
        {"skip_months": -1},
    ],
)
def test_rejects_nonsensical_settings(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MomentumConfig(**bad)  # type: ignore[arg-type]


def test_the_documented_defaults_are_what_the_roadmap_says() -> None:
    from sillage.data.universe import CORE

    strat = build(CORE)
    assert strat.config.lookbacks == (3, 6, 12)
    assert strat.config.top_n == 5
    assert strat.config.trend_window == 200
    assert strat.sizer.target == dec("0.10")
    assert isinstance(strat.schedule, Monthly)
