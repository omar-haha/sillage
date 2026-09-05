"""Tests for the domain model.

The example-based tests below pin down the tricky cases in average-cost accounting.
The property-based tests at the bottom are the ones that matter most: they assert
invariants that must hold for *any* sequence of trades, and they are what will catch
the accounting bug you did not think to write an example for.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from sillage.core.money import ZERO, dec
from sillage.core.types import (
    AssetClass,
    Bar,
    Fill,
    Instrument,
    Order,
    OrderType,
    Portfolio,
    Position,
    Side,
)

SPY = Instrument("SPY", AssetClass.ETF, exchange="ARCA")
BTC = Instrument(
    "BTC-USD", AssetClass.CRYPTO, lot_size=Decimal("0.00000001"), trades_continuously=True
)
TS = datetime(2020, 1, 2, 21, 0, tzinfo=UTC)


def fill(qty: str, price: str, commission: str = "0", instrument: Instrument = SPY) -> Fill:
    return Fill(instrument, TS, dec(qty), dec(price), dec(commission))


# --------------------------------------------------------------------------- instruments


def test_instrument_rejects_empty_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        Instrument("", AssetClass.ETF)


def test_round_to_lot_truncates_toward_zero() -> None:
    # Rounding a buy up would spend cash we may not have; rounding a sell up would
    # short us by accident. Both directions must move toward zero.
    assert SPY.round_to_lot(dec("10.9")) == dec("10")
    assert SPY.round_to_lot(dec("-10.9")) == dec("-10")


def test_crypto_keeps_fractional_precision() -> None:
    assert BTC.round_to_lot(dec("0.123456789")) == dec("0.12345678")


# --------------------------------------------------------------------------------- bars


def test_bar_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Bar(SPY, datetime(2020, 1, 2), dec("1"), dec("1"), dec("1"), dec("1"))


def test_bar_rejects_close_outside_range() -> None:
    with pytest.raises(ValueError, match="outside"):
        Bar(SPY, TS, dec("10"), dec("11"), dec("9"), dec("12"))


def test_bar_rejects_inverted_range() -> None:
    with pytest.raises(ValueError, match="low"):
        Bar(SPY, TS, dec("10"), dec("9"), dec("11"), dec("10"))


# ------------------------------------------------------------------------------- orders


def test_order_side_derives_from_sign() -> None:
    assert Order(SPY, dec("10")).side is Side.BUY
    assert Order(SPY, dec("-10")).side is Side.SELL


def test_order_rejects_zero_quantity() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        Order(SPY, ZERO)


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValueError, match="limit price"):
        Order(SPY, dec("10"), OrderType.LIMIT)


def test_client_order_ids_are_unique() -> None:
    # These become the broker idempotency key: two orders sharing one would let a
    # retry after a timeout submit the same trade twice.
    assert Order(SPY, dec("1")).client_order_id != Order(SPY, dec("1")).client_order_id


# ---------------------------------------------------------------------------- positions


def test_opening_a_long() -> None:
    pos = Position(SPY).apply_fill(fill("10", "100", "1"))
    assert pos.quantity == dec("10")
    assert pos.avg_cost == dec("100")
    assert pos.realized_pnl == ZERO
    assert pos.commission_paid == dec("1")


def test_adding_to_a_long_averages_the_cost() -> None:
    pos = Position(SPY).apply_fill(fill("10", "100")).apply_fill(fill("10", "120"))
    assert pos.quantity == dec("20")
    assert pos.avg_cost == dec("110")  # (10*100 + 10*120) / 20
    assert pos.realized_pnl == ZERO  # nothing closed, nothing realised


def test_reducing_a_long_realizes_only_the_closed_part() -> None:
    pos = Position(SPY).apply_fill(fill("10", "100")).apply_fill(fill("-4", "130"))
    assert pos.quantity == dec("6")
    assert pos.avg_cost == dec("100")  # basis of the survivors is untouched
    assert pos.realized_pnl == dec("120")  # 4 * (130 - 100)


def test_closing_a_long_completely() -> None:
    pos = Position(SPY).apply_fill(fill("10", "100")).apply_fill(fill("-10", "90"))
    assert pos.is_flat
    assert pos.avg_cost == ZERO
    assert pos.realized_pnl == dec("-100")


def test_short_positions_realize_with_the_correct_sign() -> None:
    # Sell high, buy back low: a short that made money.
    pos = Position(SPY).apply_fill(fill("-10", "100")).apply_fill(fill("10", "80"))
    assert pos.is_flat
    assert pos.realized_pnl == dec("200")


def test_flipping_long_to_short_closes_then_reopens() -> None:
    # The case that gets written wrong. Sell 15 against a long 10: the first 10 close
    # the long and realise, the remaining 5 open a short at the fill price.
    pos = Position(SPY).apply_fill(fill("10", "100")).apply_fill(fill("-15", "120"))
    assert pos.quantity == dec("-5")
    assert pos.avg_cost == dec("120")  # new basis is the flip price, not blended
    assert pos.realized_pnl == dec("200")  # 10 * (120 - 100), the closed long only


def test_fill_for_wrong_instrument_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot be applied"):
        Position(SPY).apply_fill(fill("1", "100", instrument=BTC))


def test_unrealized_pnl_tracks_the_mark() -> None:
    pos = Position(SPY).apply_fill(fill("10", "100"))
    assert pos.unrealized_pnl(dec("110")) == dec("100")
    assert pos.unrealized_pnl(dec("90")) == dec("-100")


# --------------------------------------------------------------------------- portfolios


def test_buying_moves_cash_and_creates_a_position() -> None:
    pf = Portfolio(cash=dec("10000")).apply_fill(fill("10", "100", "1"))
    assert pf.cash == dec("8999")  # 10000 - 1000 - 1 commission
    assert pf.position(SPY).quantity == dec("10")


def test_nav_is_cash_plus_marks() -> None:
    pf = Portfolio(cash=dec("10000")).apply_fill(fill("10", "100"))
    assert pf.nav({"SPY": dec("100")}) == dec("10000")
    assert pf.nav({"SPY": dec("110")}) == dec("10100")


def test_missing_price_raises_rather_than_valuing_at_zero() -> None:
    # A silently-zero NAV would look like a catastrophic loss and could trigger the
    # drawdown kill-switch on what is really a data outage.
    pf = Portfolio(cash=dec("10000")).apply_fill(fill("10", "100"))
    with pytest.raises(KeyError, match="no price"):
        pf.nav({})


def test_weights_are_fractions_of_nav() -> None:
    pf = Portfolio(cash=dec("10000")).apply_fill(fill("50", "100"))
    weights = pf.weights({"SPY": dec("100")})
    assert weights["SPY"] == dec("0.5")  # 5000 of 10000


def test_gross_exposure_counts_shorts_positively() -> None:
    pf = Portfolio(cash=dec("10000")).apply_fill(fill("-50", "100"))
    # Short 5000 against 10000 NAV: half the book is at risk even though it is a sale.
    assert pf.gross_exposure({"SPY": dec("100")}) == dec("0.5")


# ---------------------------------------------------------------------- property tests

# (signed quantity, price, commission) -- the shape the trade generators produce.
Trades = list[tuple[int, Decimal, Decimal]]

quantities = st.integers(min_value=-500, max_value=500).filter(lambda q: q != 0)
prices = st.decimals(min_value=Decimal("1"), max_value=Decimal("5000"), places=2)
commissions = st.decimals(min_value=Decimal("0"), max_value=Decimal("10"), places=2)
trades = st.lists(st.tuples(quantities, prices, commissions), min_size=1, max_size=25)


@given(trades=trades)
@settings(max_examples=300)
def test_trading_at_the_mark_only_costs_commission(trades: Trades) -> None:
    """Buying and selling at a price, then valuing at that same price, is value-neutral.

    Whatever you do, if every trade happens at price P and the book is marked at P,
    NAV must equal starting cash minus commissions. No sequence of buys, sells, or
    position flips can create or destroy value. This single invariant catches almost
    every possible sign error in the ledger.
    """
    price = dec("100")
    start = dec("10000000")
    pf = Portfolio(cash=start)
    total_commission = ZERO

    for qty, _unused_price, commission in trades:
        f = Fill(SPY, TS, dec(qty), price, dec(commission))
        pf = pf.apply_fill(f)
        total_commission += dec(commission)

    assert pf.nav({"SPY": price}) == start - total_commission


@given(trades=trades, mark=prices)
@settings(max_examples=300)
def test_pnl_identity_holds(trades: Trades, mark: Decimal) -> None:
    """NAV change must equal realised + unrealised P&L, net of commissions.

    This is the fundamental accounting identity. If it ever fails, the equity curve is
    lying, and so is every statistic computed from it.
    """
    mark = dec(mark)
    start = dec("100000000")
    pf = Portfolio(cash=start)

    for qty, price, commission in trades:
        pf = pf.apply_fill(Fill(SPY, TS, dec(qty), dec(price), dec(commission)))

    pos = pf.position(SPY)
    expected = pos.realized_pnl + pos.unrealized_pnl(mark) - pos.commission_paid
    actual = pf.nav({"SPY": mark}) - start
    # Not exact equality: average cost is a division, and a repeating decimal like
    # 1/3 is only representable to the Decimal context's 28 significant digits. The
    # residual is around 1e-20 relative -- a tolerance of one billionth of a dollar is
    # many orders of magnitude tighter than anything that could matter, while still
    # failing loudly on a genuine sign or rounding error.
    assert abs(actual - expected) < dec("1e-9")


@given(qty=quantities, price=prices)
def test_round_trip_at_one_price_realizes_nothing(qty: int, price: Decimal) -> None:
    """Opening and immediately closing at the same price is a no-op on P&L."""
    assume(price > ZERO)
    pos = (
        Position(SPY)
        .apply_fill(Fill(SPY, TS, dec(qty), dec(price)))
        .apply_fill(Fill(SPY, TS, dec(-qty), dec(price)))
    )
    assert pos.is_flat
    assert pos.realized_pnl == ZERO
