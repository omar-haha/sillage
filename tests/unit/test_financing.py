"""Cash interest and margin financing are time costs, independent of fills."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from sillage.core.money import dec
from sillage.execution.financing import FinancingModel, RateCurve


def curve(rate: str) -> RateCurve:
    return RateCurve(((date(2024, 1, 1), dec(rate)),))


def test_positive_cash_earns_interest_for_calendar_days() -> None:
    financing = FinancingModel(curve("0.0365"), curve("0.08"))

    assert financing.accrual(dec("100000"), date(2024, 1, 5), date(2024, 1, 8)) == dec("30.00")


def test_negative_cash_pays_the_margin_rate() -> None:
    financing = FinancingModel(curve("0.03"), curve("0.073"))

    assert financing.accrual(dec("-50000"), date(2024, 1, 2), date(2024, 1, 3)) == dec("-10.00")


def test_rate_change_only_applies_from_its_date() -> None:
    rates = RateCurve(
        (
            (date(2024, 1, 1), dec("0.0365")),
            (date(2024, 1, 3), dec("0.073")),
        )
    )
    financing = FinancingModel(rates, rates)

    assert financing.accrual(dec("100000"), date(2024, 1, 2), date(2024, 1, 4)) == dec("30.00")


def test_rate_curve_never_backfills_from_the_future() -> None:
    with pytest.raises(ValueError, match="no financing rate known"):
        curve("0.05").rate_on(date(2023, 12, 31))


def test_rate_curve_accepts_the_metrics_series() -> None:
    rates = pd.Series([0.04, 0.05], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))

    converted = RateCurve.from_series(rates)

    assert converted.rate_on(date(2024, 1, 15)) == dec("0.04")
