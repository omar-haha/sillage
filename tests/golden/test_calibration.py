"""The calibration test: does the accounting actually work?

Every other test in this suite runs on invented prices, which proves the engine does
what it was told but not that what it was told corresponds to reality. This one runs
buy-and-hold SPY over twenty-one years of real bars and checks the result against an
arithmetic identity that has to hold if -- and only if -- the whole chain from order to
fill to position to cash to NAV is correct.

**The identity.** With costs switched off, a fund that buys `n` shares at `entry` and
holds them is worth `initial + n * (final - entry)` at the end. Rearranged: the fund's
total return is exactly the fraction of capital it managed to invest, multiplied by the
price return of the thing it bought. Nothing about that depends on the number of
sessions, so an error anywhere in five thousand iterations of the loop breaks it.

**Why not simply assert the return equals SPY's.** It cannot, and a test claiming
otherwise would be wrong in a way that hides a real effect. A hundred thousand dollars
does not divide evenly into whole SPY shares, so a little is left in cash and earns
nothing. That drag is real -- a live account has it too -- and the test measures it
rather than pretending it away.

The fixture is committed, so this runs in CI without touching Yahoo and gives the same
answer next year as it does today.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from sillage.backtest.runner import BacktestConfig, BacktestResult, annualised, run_backtest
from sillage.core.money import dec
from sillage.core.types import AssetClass, Instrument
from sillage.data.store import BarStore
from sillage.data.universe import Universe
from sillage.execution.costs import FREE, CostModel
from sillage.strategy.benchmarks import buy_and_hold

FIXTURE = Path(__file__).parent / "spy_daily.csv.gz"
SPY = Instrument("SPY", AssetClass.ETF, exchange="ARCA")
UNIVERSE = Universe("spy-only", (SPY,), cash_proxy=SPY)

START = date(2005, 1, 3)
END = date(2026, 9, 4)
CAPITAL = dec(100_000)


@pytest.fixture(scope="module")
def prices() -> pd.DataFrame:
    return pd.read_csv(FIXTURE, index_col=0, parse_dates=True)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory, prices: pd.DataFrame) -> BarStore:
    """The committed fixture, loaded into a real store so nothing is stubbed out."""
    store = BarStore(tmp_path_factory.mktemp("calibration"))
    store.write("SPY", prices)
    return store


def replay(store: BarStore, costs: CostModel = FREE) -> BacktestResult:
    return run_backtest(
        BacktestConfig(
            strategy=buy_and_hold("SPY"),
            universe=UNIVERSE,
            start=START,
            end=END,
            initial_cash=CAPITAL,
            costs=costs,
            data_root=store.root,
        )
    )


@pytest.fixture(scope="module")
def result(store: BarStore) -> BacktestResult:
    return replay(store)


# ------------------------------------------------------------------ the fixture


def test_the_fixture_covers_the_period_claimed(prices: pd.DataFrame) -> None:
    assert prices.index[0].date() == START
    assert prices.index[-1].date() == END
    assert len(prices) == 5453


# ------------------------------------------------------------------ the trade


def test_buy_and_hold_trades_exactly_once(result: BacktestResult) -> None:
    assert len(result.fills) == 1
    assert result.rejections == []


def test_it_buys_at_the_open_after_the_first_close(
    result: BacktestResult, prices: pd.DataFrame
) -> None:
    """The decision uses the close of session one; the fill is session two's open."""
    fill = result.fills[0]
    assert fill.ts.date() == prices.index[1].date()
    assert float(fill.price) == pytest.approx(prices["open"].iloc[1], rel=1e-9)


def test_it_invests_essentially_everything(result: BacktestResult) -> None:
    invested = abs(result.fills[0].quantity * result.fills[0].price)
    assert float(invested / CAPITAL) > 0.998


# ------------------------------------------------------------------ the identity


def test_final_value_is_exactly_what_the_shares_are_worth(
    result: BacktestResult, prices: pd.DataFrame
) -> None:
    """The whole accounting chain in one assertion.

    Cash is held to the cent, so the two sides agree to the cent rather than exactly.
    """
    fill = result.fills[0]
    final_close = dec(float(prices["close"].iloc[-1]))
    expected = CAPITAL + fill.quantity * (final_close - fill.price)
    assert abs(result.final_nav - expected) < dec("0.01")


def test_the_return_is_the_invested_fraction_of_the_market_s(
    result: BacktestResult, prices: pd.DataFrame
) -> None:
    """Cash drag, measured rather than assumed.

    A fund that put 99.8% of its capital to work should earn 99.8% of the return of
    what it bought -- no more, and no less by any amount that a bug could hide in.
    """
    fill = result.fills[0]
    invested_fraction = fill.quantity * fill.price / CAPITAL
    market_return = dec(float(prices["close"].iloc[-1])) / fill.price - dec(1)

    ratio = result.total_return / (invested_fraction * market_return)
    assert abs(ratio - dec(1)) < dec("0.0001")  # one basis point


def test_the_fund_trails_the_index_only_by_that_drag(
    result: BacktestResult, prices: pd.DataFrame
) -> None:
    index_return = float(prices["close"].iloc[-1] / prices["close"].iloc[0]) - 1
    shortfall = index_return - float(result.total_return)
    assert 0 < shortfall < 0.03 * (1 + index_return)


# ------------------------------------------------------------------ sanity


def test_the_annualised_figure_is_plausible_for_spy(result: BacktestResult) -> None:
    """A wide band on purpose. It is here to catch a catastrophe -- raw prices instead
    of adjusted ones, a decade of sessions dropped -- not to pin down a number."""
    years = (END - START).days / 365.25
    assert 0.08 < float(annualised(result.total_return, years)) < 0.14


def test_every_session_is_marked(result: BacktestResult, prices: pd.DataFrame) -> None:
    assert result.sessions == len(prices)


def test_nav_is_never_negative(result: BacktestResult) -> None:
    assert all(point.nav > 0 for point in result.nav_points)


# ------------------------------------------------------------------ with costs


def test_realistic_costs_are_a_rounding_error_on_one_trade(store: BarStore) -> None:
    charged = replay(store, CostModel())
    assert float(charged.total_costs / CAPITAL) < 0.0002
    assert charged.final_nav < replay(store).final_nav


def test_costs_scale_the_way_the_sensitivity_analysis_assumes(store: BarStore) -> None:
    """Phase 4 leans on this: multiplying the model must multiply what was paid."""
    single = replay(store, CostModel()).total_costs
    tripled = replay(store, CostModel().scaled(3)).total_costs
    assert float(tripled / single) == pytest.approx(3.0, rel=0.02)


def test_a_decimal_is_returned_not_a_float(result: BacktestResult) -> None:
    """Money never becomes a float on the way out; a float NAV is a silent precision bug."""
    assert isinstance(result.final_nav, Decimal)
