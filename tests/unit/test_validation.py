"""Tests for the validation battery.

Split in two. The statistics -- bootstrap, deflation -- are tested against series whose
answer is known or whose behaviour is forced, because a confidence interval that is
quietly wrong is worse than none. The orchestration is tested on a small synthetic
universe, where what matters is that the machinery does what it says: that a sweep
varies only the knob it names, and that the object counting trials counts all of them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import numpy as np
import pytest
from tests.support import etf, synthetic_store

from sillage.backtest.runner import BacktestConfig, BacktestResult
from sillage.backtest.validation import (
    Interval,
    Validator,
    Variant,
    block_bootstrap,
    deflated_sharpe,
    rolling_sharpe,
    run_battery,
    sharpe_spread,
)
from sillage.core.money import ZERO, dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.universe import Universe
from sillage.engine.journal import NavPoint
from sillage.strategy.momentum import build

CASH = Instrument("CASH", AssetClass.ETF)
UNIVERSE = Universe("synthetic", (etf("AAA"), etf("BBB"), etf("CCC"), etf("DDD")), cash_proxy=CASH)


def result_from(returns: list[float]) -> BacktestResult:
    """A BacktestResult carrying nothing but a NAV path, which is all the statistics need."""
    nav, points = 100_000.0, []
    for index, r in enumerate(returns):
        nav *= 1.0 + r
        day = date(2010, 1, 1) + __import__("datetime").timedelta(days=index)
        points.append(
            NavPoint(
                session=day,
                ts=datetime(day.year, day.month, day.day, 21, tzinfo=UTC),
                nav=Decimal(str(round(nav, 4))),
                cash=ZERO,
                gross_exposure=dec(1),
            )
        )
    return BacktestResult(
        config=BacktestConfig(
            strategy=build(UNIVERSE),
            universe=UNIVERSE,
            start=date(2010, 1, 1),
            end=date(2030, 1, 1),
        ),
        nav_points=points,
        fills=[],
        orders=[],
        rejections=[],
        first_rebalance=None,
    )


def noisy(n: int = 2000, mean: float = 0.0004, sd: float = 0.01, seed: int = 3) -> list[float]:
    return list(np.random.default_rng(seed).normal(mean, sd, n))


# ------------------------------------------------------------------ variants


def test_a_variant_names_every_knob_it_sets() -> None:
    """Trials have to be distinguishable in a log, or the count means nothing."""
    assert Variant().label != Variant(top_n=8).label


def test_changing_a_knob_leaves_the_rest_alone() -> None:
    changed = Variant().with_("top_n", 9)
    assert changed.top_n == 9
    assert changed.trend_window == Variant().trend_window


def test_a_misspelled_knob_is_an_error_not_a_silent_no_op() -> None:
    """Without this, a sweep runs the default forty times and reports a flat curve."""
    with pytest.raises(KeyError, match="unknown parameter"):
        Variant().with_("top-n", 9)


# ------------------------------------------------------------------ bootstrap


def test_the_interval_brackets_the_point_estimate() -> None:
    interval = block_bootstrap(result_from(noisy()), draws=400, seed=1)
    assert interval.low < interval.point < interval.high


def test_a_strong_series_gives_an_interval_clear_of_zero() -> None:
    interval = block_bootstrap(result_from(noisy(mean=0.0012)), draws=400, seed=1)
    assert interval.excludes_zero


def test_a_directionless_series_does_not() -> None:
    """The honest negative: no edge must not come back looking like one."""
    interval = block_bootstrap(result_from(noisy(mean=0.0)), draws=400, seed=1)
    assert not interval.excludes_zero


def test_it_is_reproducible() -> None:
    a = block_bootstrap(result_from(noisy()), draws=200, seed=7)
    b = block_bootstrap(result_from(noisy()), draws=200, seed=7)
    assert (a.low, a.high) == (b.low, b.high)


def test_a_different_seed_moves_the_interval_but_not_much() -> None:
    a = block_bootstrap(result_from(noisy()), draws=800, seed=1)
    b = block_bootstrap(result_from(noisy()), draws=800, seed=2)
    assert a.low != b.low
    assert abs(a.low - b.low) < 0.15


def test_it_refuses_a_series_too_short_to_block() -> None:
    with pytest.raises(ValueError, match="at least"):
        block_bootstrap(result_from(noisy(30)), block=21)


def test_the_interval_reads_as_a_sentence() -> None:
    assert "95% CI" in str(Interval(point=1.0, low=0.5, high=1.5, draws=100))


# ------------------------------------------------------------------ deflation


def test_more_trials_raise_the_bar() -> None:
    """Run enough strategies and one looks good by chance; that is the whole idea."""
    strong = result_from(noisy(mean=0.0008))
    few = deflated_sharpe(strong, trials=5, trial_sharpe_stdev=0.02)
    many = deflated_sharpe(strong, trials=5000, trial_sharpe_stdev=0.02)
    assert many.expected_maximum > few.expected_maximum
    assert many.probability < few.probability


def test_a_wider_spread_of_trials_raises_the_bar_too() -> None:
    strong = result_from(noisy(mean=0.0008))
    tight = deflated_sharpe(strong, trials=100, trial_sharpe_stdev=0.005)
    loose = deflated_sharpe(strong, trials=100, trial_sharpe_stdev=0.05)
    assert loose.expected_maximum > tight.expected_maximum


def test_a_genuinely_strong_result_survives_many_trials() -> None:
    deflation = deflated_sharpe(
        result_from(noisy(mean=0.0012)), trials=1000, trial_sharpe_stdev=0.01
    )
    assert deflation.survives


def test_a_result_indistinguishable_from_noise_does_not() -> None:
    deflation = deflated_sharpe(result_from(noisy(mean=0.0)), trials=1000, trial_sharpe_stdev=0.02)
    assert not deflation.survives


def test_the_observed_sharpe_is_reported_annualised() -> None:
    """Mixing per-session and annual units is how this formula usually goes wrong."""
    from sillage.backtest.metrics import from_nav, nav_series

    strong = result_from(noisy(mean=0.0008))
    deflation = deflated_sharpe(strong, trials=10, trial_sharpe_stdev=0.01)
    assert deflation.observed == pytest.approx(from_nav(nav_series(strong.nav_points)).sharpe)


def test_there_is_no_such_thing_as_zero_trials() -> None:
    with pytest.raises(ValueError, match="fewer than one"):
        deflated_sharpe(result_from(noisy()), trials=0)


def test_a_motionless_series_has_no_sharpe_to_deflate() -> None:
    with pytest.raises(ValueError, match="no variance"):
        deflated_sharpe(result_from([0.0] * 500), trials=10)


# ------------------------------------------------------------------ spread


def test_the_spread_needs_more_than_one_trial() -> None:
    assert sharpe_spread([]) == 0.0


# ------------------------------------------------------------------ rolling


def test_rolling_sharpe_is_shorter_than_the_run_by_its_window() -> None:
    """The aggregate cannot tell a steady strategy from one brilliant stretch."""
    series = rolling_sharpe(result_from(noisy(1500)), window_years=1)
    assert 0 < len(series) < 1500


# ------------------------------------------------------------------ orchestration


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> BacktestConfig:
    root = tmp_path_factory.mktemp("validation")
    synthetic_store(
        root,
        {"AAA": 0.0006, "BBB": 0.0003, "CCC": 0.0001, "DDD": -0.0002, "CASH": 0.00004},
    )
    return BacktestConfig(
        strategy=build(UNIVERSE),
        universe=UNIVERSE,
        start=date(2018, 1, 2),
        end=date(2020, 10, 12),
        data_root=root,
    )


def test_the_validator_counts_every_configuration_it_runs(base: BacktestConfig) -> None:
    """The count feeds the deflation, and a number that must be remembered will be wrong."""
    validator = Validator(base, tranched=False)
    validator.run()
    validator.sensitivity("top_n", [2, 3])
    assert validator.trial_count == 3


def test_a_sweep_varies_only_the_knob_it_names(base: BacktestConfig) -> None:
    validator = Validator(base, tranched=False)
    trials = validator.sensitivity("trend_window", [100, 150])
    assert [t.variant.trend_window for t in trials] == [100, 150]
    assert {t.variant.top_n for t in trials} == {Variant().top_n}


def test_the_split_runs_two_windows_that_do_not_overlap(base: BacktestConfig) -> None:
    validator = Validator(base, tranched=False)
    train, test = validator.split(date(2019, 7, 1))
    assert train.metrics.end <= test.metrics.start
    assert validator.trial_count == 2


def test_start_dates_produce_progressively_shorter_windows(base: BacktestConfig) -> None:
    validator = Validator(base, tranched=False)
    trials = validator.start_dates([date(2018, 1, 2), date(2019, 1, 2)])
    assert trials[0].metrics.years > trials[1].metrics.years


def test_a_quick_battery_runs_end_to_end(base: BacktestConfig) -> None:
    """Not a check on the numbers -- a check that every attack executes and reports."""
    report = run_battery(base, boundary=date(2019, 7, 1), tranched=False, quick=True, draws=100)
    assert report.trials > 5
    assert set(report.sensitivity) == {"top_n", "trend_window"}
    assert report.interval is not None
    assert len(report.deflation) == 3
    assert report.deflation[0].trials == report.trials


def test_the_grid_is_only_run_when_it_is_wanted(base: BacktestConfig) -> None:
    report = run_battery(base, boundary=date(2019, 7, 1), tranched=False, quick=True, draws=100)
    assert report.grid == []
    assert report.grid_axes is None
