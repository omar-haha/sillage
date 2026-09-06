"""Tests for the tearsheet.

A chart cannot be asserted on the way a number can, so these check the things that
actually go wrong in generated HTML: a figure that silently rendered with no data, a
NaN reaching the page as the string "NaN", an axis configured the wrong way, a legend
missing when there is more than one series. Whether it *looks* right still needs a
human with a browser.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from sillage.backtest.metrics import (
    Metrics,
    Performance,
    Trading,
    by_calendar_year,
    drawdown_series,
    from_nav,
    monthly_returns,
)
from sillage.backtest.report import SERIES, render, write
from sillage.core.money import ZERO, dec


def performance(label: str, seed: int = 1) -> Performance:
    import numpy as np

    rng = np.random.default_rng(seed)
    days = 1500
    values = 100_000 * np.cumprod(1 + rng.normal(0.0004, 0.011, days))
    nav = pd.Series(
        values, index=pd.date_range("2019-01-01", periods=days, freq="D", name="session")
    )
    exposure = pd.Series(0.98, index=nav.index)
    return Performance(
        metrics=from_nav(nav, label=label),
        trading=Trading(
            fills=42,
            rejections=0,
            traded_notional=dec(500_000),
            commission=dec(120),
            slippage=dec(80),
            annual_turnover=0.4,
            cost_drag=0.0005,
            average_exposure=0.98,
        ),
        by_year=by_calendar_year(nav, label=label),
        nav=nav,
        drawdown=drawdown_series(nav),
        monthly=monthly_returns(nav),
        exposure=exposure,
    )


@pytest.fixture(scope="module")
def page() -> str:
    return render([performance("strategy"), performance("benchmark", seed=2)])


def _payloads(html: str) -> list[str]:
    """The raw JSON handed to each `Plotly.newPlot` call.

    Extracted rather than searched for in the page, because the page also contains the
    whole of plotly.js -- whose own source mentions `NaN` and `cdn.plot.ly` hundreds of
    times. Asserting against the full text would be testing the library, not the report.
    """
    payloads = []
    for chunk in html.split("Plotly.newPlot(")[1:]:
        payload = chunk[chunk.index("[") :]
        depth, end = 0, 0
        for index, char in enumerate(payload):
            depth += (char == "[") - (char == "]")
            if depth == 0:
                end = index + 1
                break
        payloads.append(payload[:end])
    return payloads


def _figures(html: str) -> list[list[dict]]:  # type: ignore[type-arg]
    return [json.loads(payload) for payload in _payloads(html)]


# ------------------------------------------------------------------ structure


def test_the_page_is_one_self_contained_file(page: str) -> None:
    """No network at read time: the library is inlined, not linked."""
    assert page.startswith("<!doctype html>")
    assert '<script src="http' not in page
    assert "Plotly.newPlot(" in page


def test_every_expected_section_is_present(page: str) -> None:
    for heading in ("Growth of 100", "Underwater", "Rolling 12-month", "Monthly returns"):
        assert heading in page
    assert "Calendar years" in page


def test_all_five_figures_carry_data(page: str) -> None:
    figures = _figures(page)
    assert len(figures) == 5
    for traces in figures:
        assert traces, "a figure rendered with no traces at all"
        assert all(trace.get("y") or trace.get("z") for trace in traces)


def test_no_nan_reaches_the_charts(page: str) -> None:
    """A NaN in the data renders as a silent gap that nobody notices."""
    assert not any("NaN" in payload for payload in _payloads(page))


def test_the_growth_chart_is_logarithmic(page: str) -> None:
    """On a linear axis a twenty-year curve hides everything before the last few years."""
    assert '"type":"log"' in page


def test_series_take_palette_slots_in_order(page: str) -> None:
    """Colour follows identity, so adding a benchmark cannot repaint the strategy."""
    assert SERIES[0] in page
    assert SERIES[1] in page


def test_both_series_appear_in_the_statistics_table(page: str) -> None:
    assert page.count("<th>strategy</th>") >= 2  # stats table and calendar years
    assert "<th>benchmark</th>" in page


def test_calendar_years_are_listed_newest_first(page: str) -> None:
    years = [int(y) for y in ("2019", "2020", "2021", "2022") if f">{y}</th>" in page]
    positions = [page.index(f">{y}</th>") for y in years]
    assert positions == sorted(positions, reverse=True)


# ------------------------------------------------------------------ accessibility


def test_every_heatmap_cell_states_its_number(page: str) -> None:
    """Colour is a second reading of the value, never the only one."""
    assert "texttemplate" in page


def test_lines_are_labelled_at_their_ends(page: str) -> None:
    """Two palette slots sit below 3:1 on this surface; a visible label is the relief."""
    assert '"annotations"' in page


# ------------------------------------------------------------------ behaviour


def test_a_single_run_needs_no_legend() -> None:
    """One series is named by the title; a legend box would be noise."""
    solo = render([performance("only")])
    assert '"showlegend":true' not in solo


def test_two_runs_do_get_a_legend(page: str) -> None:
    """Identity is never carried by colour alone once there is more than one series."""
    assert '"showlegend":true' in page


def test_writing_creates_the_directory(tmp_path: object) -> None:
    from pathlib import Path

    target = Path(str(tmp_path)) / "nested" / "sheet.html"
    assert write([performance("x")], target) == target
    assert target.read_text().startswith("<!doctype html>")


def test_a_tearsheet_needs_something_to_report() -> None:
    with pytest.raises(ValueError, match="at least one run"):
        render([])


def test_a_run_too_short_to_have_months_still_renders() -> None:
    """Guards the heatmap, which is the one figure that can legitimately be absent."""
    nav = pd.Series([100.0, 101.0], index=pd.date_range("2024-01-01", periods=2, freq="D"))
    short = Performance(
        metrics=from_nav(nav, label="brief"),
        trading=Trading(0, 0, ZERO, ZERO, ZERO, 0.0, 0.0, 0.0),
        by_year=[],
        nav=nav,
        drawdown=drawdown_series(nav),
        monthly=monthly_returns(nav),
        exposure=pd.Series(0.0, index=nav.index),
    )
    assert "<!doctype html>" in render([short])


def test_metrics_stringify_for_a_terminal() -> None:
    assert "Sharpe" in str(performance("x").metrics)
    assert isinstance(performance("x").metrics, Metrics)
