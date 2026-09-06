"""What trading costs, modelled in three separable pieces.

Most retail backtests are profitable only because they ignore this file. A strategy
rebalancing monthly across thirteen ETFs turns over enough that a few basis points per
trade compounds into a visible drag, and one rebalancing weekly can have its entire
edge consumed by costs it never accounted for.

The three components are kept apart rather than folded into one number because Phase 4
has to answer "at what cost level does this strategy stop working?", and that question
is unanswerable once the components are merged into an effective price:

- **Commission** -- what the broker charges. Known, small, and the least interesting.
- **Spread** -- you buy at the ask and sell at the bid, so crossing costs you half the
  quoted spread even on an infinitely small order. Fixed per asset, and the dominant
  cost at retail size.
- **Impact** -- large orders move the price against themselves. Scales with the square
  root of participation in the day's volume, which is the standard empirical form and
  is deliberately not a straight line: doubling an order does not double its impact.

Every figure here is a defensible estimate, not a measurement. The point of Phase 5b is
to replace them with numbers observed against a real broker, and `scaled()` exists so
Phase 4 can ask what happens if they are all two or five times worse.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Final, Self

from sillage.core.money import ZERO, dec, quantize_cash, safe_div

BPS: Final = dec("10000")

# IBKR's tiered US equity schedule, which is what this portfolio's order sizes would
# actually pay. Named constants rather than inline literals because they are defaults on
# a frozen dataclass, and because each one is an assumption someone may want to argue
# with.
COMMISSION_PER_SHARE: Final = dec("0.0035")
MIN_COMMISSION: Final = dec("0.35")
# Applied to anything not in the table below -- deliberately wider than every ETF in the
# core universe, so an unlisted asset is pessimistically priced rather than optimistically.
DEFAULT_HALF_SPREAD: Final = dec("2.0")
# Cost in basis points of an order equal to a full day's volume. The square root means
# 1% of ADV costs a tenth of this. A placeholder until Phase 5b measures the real thing.
IMPACT_AT_FULL_ADV: Final = dec("30")
ADV_WINDOW: Final = 20

# Half-spread estimates in basis points, from typical quoted spreads on these ETFs.
# SPY is among the tightest instruments in the world; DBC and HYG are an order of
# magnitude wider, and a strategy that rotates into them pays for the privilege.
DEFAULT_HALF_SPREAD_BPS: Mapping[str, str] = {
    "SPY": "0.3",
    "QQQ": "0.4",
    "IWM": "0.6",
    "EFA": "1.0",
    "EEM": "1.5",
    "TLT": "0.6",
    "IEF": "0.8",
    "LQD": "1.0",
    "HYG": "1.2",
    "GLD": "0.5",
    "DBC": "4.0",
    "VNQ": "1.2",
    "BIL": "1.0",
    "BTC-USD": "5.0",
    "ETH-USD": "8.0",
}


@dataclass(frozen=True, slots=True)
class CostModel:
    """Commission, spread and impact for one venue.

    Defaults describe a retail US equity broker: a per-share commission with a small
    minimum, which is what IBKR's tiered schedule looks like for the order sizes this
    portfolio generates.
    """

    commission_per_share: Decimal = COMMISSION_PER_SHARE
    min_commission: Decimal = MIN_COMMISSION
    # An alternative charging model, used by crypto venues and by percentage-based
    # brokers. Both are applied, so leaving one at zero disables it.
    commission_bps: Decimal = ZERO
    default_half_spread_bps: Decimal = DEFAULT_HALF_SPREAD
    half_spread_bps: Mapping[str, Decimal] = field(
        default_factory=lambda: {s: dec(v) for s, v in DEFAULT_HALF_SPREAD_BPS.items()}
    )
    impact_bps_at_full_adv: Decimal = IMPACT_AT_FULL_ADV
    adv_window: int = ADV_WINDOW

    def commission(self, quantity: Decimal, price: Decimal) -> Decimal:
        """Always positive: a cost, whichever way the trade goes."""
        shares = abs(quantity)
        charge = shares * self.commission_per_share
        charge += safe_div(shares * price * self.commission_bps, BPS)
        return quantize_cash(max(charge, self.min_commission) if shares > ZERO else ZERO)

    def half_spread_for(self, symbol: str) -> Decimal:
        return self.half_spread_bps.get(symbol, self.default_half_spread_bps)

    def impact_for(self, quantity: Decimal, average_volume: Decimal | None) -> Decimal:
        """Impact in basis points for an order of this size.

        With no volume history the impact is zero rather than a guess. Inventing a
        penalty from no information would make the backtest less honest, not more, and
        the missing-volume case is loud elsewhere: `data check` reports it.
        """
        if average_volume is None or average_volume <= ZERO:
            return ZERO
        participation = min(safe_div(abs(quantity), average_volume), dec(1))
        return self.impact_bps_at_full_adv * participation.sqrt()

    def slippage_bps(
        self, symbol: str, quantity: Decimal, average_volume: Decimal | None
    ) -> Decimal:
        """Total price concession in basis points: spread plus impact."""
        return self.half_spread_for(symbol) + self.impact_for(quantity, average_volume)

    def fill_price(
        self,
        symbol: str,
        reference_price: Decimal,
        quantity: Decimal,
        average_volume: Decimal | None = None,
    ) -> Decimal:
        """The reference price moved against the trader.

        Buys fill above the reference and sells below it, always. A cost model that
        could improve on the reference price would be modelling luck, and a backtest
        that gets lucky on every one of ten thousand fills is fiction.
        """
        concession = safe_div(self.slippage_bps(symbol, quantity, average_volume), BPS)
        direction = dec(1) if quantity > ZERO else dec(-1)
        return reference_price * (dec(1) + direction * concession)

    def scaled(self, factor: Decimal | int | str | float) -> Self:
        """A copy with every cost multiplied by `factor`.

        This is the Phase 4 cost-sensitivity sweep in one line. A strategy whose edge
        survives at 3x assumed costs is a different proposition from one that dies
        at 1.2x, and the difference is worth knowing before real money is involved.
        """
        multiplier = dec(factor)
        if multiplier < ZERO:
            raise ValueError("cost scaling factor must not be negative")
        return replace(
            self,
            commission_per_share=self.commission_per_share * multiplier,
            min_commission=self.min_commission * multiplier,
            commission_bps=self.commission_bps * multiplier,
            default_half_spread_bps=self.default_half_spread_bps * multiplier,
            half_spread_bps={s: v * multiplier for s, v in self.half_spread_bps.items()},
            impact_bps_at_full_adv=self.impact_bps_at_full_adv * multiplier,
        )


#: Costs switched off entirely. Used by the calibration test, where the question is
#: whether the accounting is right, and any cost at all would mask an error in it.
FREE = CostModel(
    commission_per_share=ZERO,
    min_commission=ZERO,
    commission_bps=ZERO,
    default_half_spread_bps=ZERO,
    half_spread_bps={},
    impact_bps_at_full_adv=ZERO,
)
