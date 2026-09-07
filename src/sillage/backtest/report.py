"""The tearsheet: one self-contained HTML page describing a run.

A table of statistics tells you what happened; a tearsheet tells you what it would
have felt like. Those are different questions, and the second one decides whether a
strategy is actually holdable. The underwater chart in particular is the honest one --
it is the only view where a five-year recovery looks like five years rather than like
a dip on the way to a bigger number.

**Design notes**, because charts made carelessly mislead in ways tables do not:

*The equity curve is logarithmic and indexed to 100.* On a linear axis a twenty-year
curve compresses everything before the last few years into a flat line, so 2008 --
the most informative event in the sample -- becomes invisible. Indexing to a common
base means one shared axis rather than two, which is the one chart rule worth never
breaking.

*The monthly heatmap runs blue-to-red, not green-to-red.* Finance convention is green
for gains, and it is the wrong convention: red-green is exactly the pair that eight
percent of men cannot distinguish. Blue for gains and red for losses carries the same
polarity to everyone. Every cell also shows its number, so the colour is a second
encoding rather than the only one.

*Light mode only, deliberately.* A half-implemented dark mode that simply inverts the
background leaves series colours chosen for a light surface sitting on a dark one,
which is worse than not offering it. If it is wanted later it needs its own validated
steps, not a flip.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sillage.backtest.attribution import Contribution, concentration
from sillage.backtest.metrics import Performance, monthly_table

if TYPE_CHECKING:
    import pandas as pd
    import plotly.graph_objects as go

# Categorical slots, in fixed order, from a palette validated for colour-vision
# deficiency. Series take slots by identity and never by rank, so adding a benchmark
# cannot repaint the strategy.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7")

SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# Diverging ramp for the monthly heatmap: red (loss) through a neutral grey to blue
# (gain). Two hues with a colourless midpoint, so "no change" reads as nothing at all.
DIVERGING = (
    (0.00, "#8f2222"),
    (0.15, "#e34948"),
    (0.32, "#ef8b8a"),
    (0.44, "#f7c9c8"),
    (0.50, "#f0efec"),
    (0.56, "#cde2fb"),
    (0.68, "#6da7ec"),
    (0.85, "#2a78d6"),
    (1.00, "#104281"),
)

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'
SESSIONS_PER_YEAR = 252


def _layout(title: str, subtitle: str = "", height: int = 380) -> dict[str, Any]:
    """Chrome shared by every figure: recessive grid, one y-axis, unified hover."""
    return {
        "title": {
            "text": f"<b>{title}</b>"
            + (
                f"<br><span style='font-size:12px;color:{INK_MUTED}'>{subtitle}</span>"
                if subtitle
                else ""
            ),
            "font": {"size": 15, "color": INK},
            "x": 0,
            "xanchor": "left",
        },
        "height": height,
        "margin": {"l": 60, "r": 90, "t": 60 if subtitle else 48, "b": 40},
        "paper_bgcolor": SURFACE,
        "plot_bgcolor": SURFACE,
        "font": {"family": FONT, "size": 12, "color": INK_SECONDARY},
        "hovermode": "x unified",
        "hoverlabel": {"font": {"family": FONT, "size": 12}, "bgcolor": SURFACE},
        "xaxis": {
            "showgrid": False,
            "linecolor": AXIS,
            "tickcolor": AXIS,
            "tickfont": {"color": INK_MUTED},
        },
        "yaxis": {
            "gridcolor": GRID,
            "zeroline": False,
            "linecolor": AXIS,
            "tickfont": {"color": INK_MUTED},
        },
        "legend": {
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.0,
            "xanchor": "right",
            "x": 1.0,
            "font": {"color": INK_SECONDARY},
        },
        "showlegend": True,
    }


def _end_label(fig: go.Figure, series: pd.Series, colour: str, text: str) -> None:
    """Name the line at its right-hand end.

    Direct labelling is what lets a reader identify a series without tracing it back
    to a legend, and it is required rather than optional here: two of the palette
    slots sit below 3:1 contrast on this surface, and a visible label is the relief.
    """
    fig.add_annotation(
        x=series.index[-1],
        y=float(series.iloc[-1]),
        text=f" {text}",
        showarrow=False,
        xanchor="left",
        font={"size": 11, "color": colour, "family": FONT},
    )


def _growth(runs: Sequence[Performance]) -> go.Figure:
    import plotly.graph_objects as go

    fig = go.Figure()
    for index, run in enumerate(runs):
        indexed = run.nav / run.nav.iloc[0] * 100
        colour = SERIES[index % len(SERIES)]
        fig.add_trace(
            go.Scatter(
                x=indexed.index,
                y=indexed,
                name=run.label,
                line={"color": colour, "width": 2},
                hovertemplate="%{y:,.0f}<extra></extra>",
            )
        )
        _end_label(fig, indexed, colour, run.label)

    layout = _layout(
        "Growth of 100",
        "Logarithmic. Equal vertical distances are equal percentage moves, so 2008 is "
        "as visible as 2024.",
        height=430,
    )
    layout["yaxis"]["type"] = "log"
    layout["showlegend"] = len(runs) > 1
    fig.update_layout(**layout)
    return fig


def _underwater(runs: Sequence[Performance]) -> go.Figure:
    import plotly.graph_objects as go

    fig = go.Figure()
    for index, run in enumerate(runs):
        colour = SERIES[index % len(SERIES)]
        fig.add_trace(
            go.Scatter(
                x=run.drawdown.index,
                y=run.drawdown * 100,
                name=run.label,
                line={"color": colour, "width": 2},
                fill="tozeroy",
                fillcolor=_translucent(colour, 0.12),
                hovertemplate="%{y:.1f}%<extra></extra>",
            )
        )
    layout = _layout(
        "Underwater",
        "How far below the previous high-water mark, every day. The chart that decides "
        "whether a strategy is holdable.",
    )
    layout["yaxis"]["ticksuffix"] = "%"
    layout["showlegend"] = len(runs) > 1
    fig.update_layout(**layout)
    return fig


def _rolling(runs: Sequence[Performance]) -> go.Figure:
    import plotly.graph_objects as go

    fig = go.Figure()
    for index, run in enumerate(runs):
        rolling = (run.nav / run.nav.shift(SESSIONS_PER_YEAR) - 1).dropna() * 100
        if rolling.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=rolling.index,
                y=rolling,
                name=run.label,
                line={"color": SERIES[index % len(SERIES)], "width": 2},
                hovertemplate="%{y:+.1f}%<extra></extra>",
            )
        )
    layout = _layout(
        "Rolling 12-month return",
        "What someone who started on any given day would have seen a year later.",
    )
    layout["yaxis"]["ticksuffix"] = "%"
    layout["showlegend"] = len(runs) > 1
    fig.update_layout(**layout)
    fig.add_hline(y=0, line={"color": AXIS, "width": 1})
    return fig


def _heatmap(run: Performance) -> go.Figure | None:
    import plotly.graph_objects as go

    table = monthly_table(run.nav)
    if table.empty:
        return None

    values = table.to_numpy() * 100
    extreme = float(max(abs(values[~_isnan(values)].min()), abs(values[~_isnan(values)].max())))
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    fig = go.Figure(
        go.Heatmap(
            z=values,
            x=[months[int(m) - 1] for m in table.columns],
            y=[str(y) for y in table.index],
            colorscale=[list(stop) for stop in DIVERGING],
            zmid=0,
            zmin=-extreme,
            zmax=extreme,
            texttemplate="%{z:.1f}",
            textfont={"size": 9, "family": FONT},
            hovertemplate="%{y} %{x}: %{z:+.2f}%<extra></extra>",
            xgap=2,
            ygap=2,
            colorbar={
                "ticksuffix": "%",
                "outlinewidth": 0,
                "tickfont": {"color": INK_MUTED, "size": 10},
                "thickness": 10,
            },
        )
    )
    layout = _layout(
        f"Monthly returns — {run.label}",
        "Blue is a gain, red a loss. Every cell shows its number, so the colour is a "
        "second reading rather than the only one.",
        height=90 + 22 * len(table.index),
    )
    layout["hovermode"] = "closest"
    layout["showlegend"] = False
    layout["yaxis"]["autorange"] = "reversed"
    layout["yaxis"]["gridcolor"] = SURFACE
    fig.update_layout(**layout)
    return fig


def _allocation(run: Performance, weights: pd.DataFrame) -> go.Figure | None:
    """What the fund was holding, as a share of itself, over time.

    An equity curve cannot answer "what was it actually in during March 2020", which is
    usually the first question anyone asks. Bands are ordered by average weight and
    everything past the seventh folds into "other": the palette has eight slots that
    stay distinguishable to a colour-blind reader, and a ninth would be a colour nobody
    can name against its neighbour.
    """
    import plotly.graph_objects as go

    if weights.empty:
        return None

    ranked = weights.mean().sort_values(ascending=False)
    leading = list(ranked.index[:7])
    frame = weights[leading].copy()
    if len(ranked) > len(leading):
        frame["other"] = weights[list(ranked.index[7:])].sum(axis=1)

    fig = go.Figure()
    for index, column in enumerate(frame.columns):
        colour = SERIES[index % len(SERIES)] if column != "other" else INK_MUTED
        fig.add_trace(
            go.Scatter(
                x=frame.index,
                y=frame[column] * 100,
                name=str(column),
                mode="lines",
                stackgroup="holdings",
                # A hairline in the surface colour separates the bands, so adjacent
                # fills read as distinct without a heavy outline around each one.
                line={"color": SURFACE, "width": 1},
                fillcolor=_translucent(colour, 0.85),
                hovertemplate="%{y:.1f}%<extra></extra>",
            )
        )

    layout = _layout(
        f"Allocation — {run.label}",
        "Share of the fund in each holding at every close. The gap to 100% is cash.",
        height=380,
    )
    layout["yaxis"]["ticksuffix"] = "%"
    layout["yaxis"]["range"] = [0, 100]
    fig.update_layout(**layout)
    return fig


def _exposure(run: Performance) -> go.Figure:
    import plotly.graph_objects as go

    exposure = run.exposure * 100
    fig = go.Figure(
        go.Scatter(
            x=exposure.index,
            y=exposure,
            name="gross exposure",
            line={"color": SERIES[0], "width": 2},
            fill="tozeroy",
            fillcolor=_translucent(SERIES[0], 0.12),
            hovertemplate="%{y:.0f}%<extra></extra>",
        )
    )
    layout = _layout(
        f"Gross exposure — {run.label}",
        "How much of the fund was actually invested. The gap to 100% is cash.",
        height=280,
    )
    layout["yaxis"]["ticksuffix"] = "%"
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


# ------------------------------------------------------------------ tables

STAT_ROWS: tuple[tuple[str, str], ...] = (
    ("Total return", "total_return"),
    ("Annualised (CAGR)", "cagr"),
    ("Volatility", "volatility"),
    ("Sharpe", "sharpe"),
    ("Sortino", "sortino"),
    ("Max drawdown", "max_drawdown"),
    ("Longest drawdown", "longest_drawdown_days"),
    ("Calmar", "calmar"),
    ("Best month", "best_month"),
    ("Worst month", "worst_month"),
    ("Positive months", "positive_months"),
)

TRADING_ROWS: tuple[tuple[str, str], ...] = (
    ("Fills", "fills"),
    ("Turnover", "annual_turnover"),
    ("Cost drag", "cost_drag"),
    ("Average exposure", "average_exposure"),
)


def _format(field: str, value: float) -> str:
    """Render one statistic the way its units want to be read."""
    if field == "longest_drawdown_days":
        return f"{int(value):,}d" if value else "—"
    if field == "fills":
        return f"{int(value):,}"
    if field in {"sharpe", "sortino", "calmar"}:
        return f"{value:.2f}"
    if field == "annual_turnover":
        return f"{value:.2f}x/yr"
    if field == "cost_drag":
        return f"{value:.3%}/yr"
    if field == "volatility":
        return f"{value:.2%}"
    return f"{value:+.2%}"


def _stats_table(runs: Sequence[Performance]) -> str:
    header = "".join(f"<th>{run.label}</th>" for run in runs)
    body = []
    for label, field in STAT_ROWS:
        cells = "".join(f"<td>{_format(field, getattr(r.metrics, field))}</td>" for r in runs)
        body.append(f"<tr><th scope='row'>{label}</th>{cells}</tr>")
    body.append(f"<tr class='divider'><th scope='row'>Trading</th>{'<td></td>' * len(runs)}</tr>")
    for label, field in TRADING_ROWS:
        cells = "".join(f"<td>{_format(field, getattr(r.trading, field))}</td>" for r in runs)
        body.append(f"<tr><th scope='row'>{label}</th>{cells}</tr>")
    return (
        f"<table class='stats'><thead><tr><th></th>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _years_table(runs: Sequence[Performance]) -> str:
    """Calendar-year returns side by side.

    The most useful table on the page, and the one most reports omit. A strategy
    family's reputation is usually earned in two or three specific years, and an
    aggregate spanning them says nothing about the fifteen since.
    """
    years: dict[int, dict[str, float]] = {}
    for run in runs:
        for period in run.by_year:
            years.setdefault(period.start.year, {})[run.label] = period.total_return

    header = "".join(f"<th>{run.label}</th>" for run in runs)
    body = []
    for year in sorted(years, reverse=True):
        cells = "".join(_year_cell(years[year].get(run.label)) for run in runs)
        body.append(f"<tr><th scope='row'>{year}</th>{cells}</tr>")
    return (
        f"<table class='stats years'><thead><tr><th></th>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _attribution_table(contributions: Sequence[Contribution]) -> str:
    """Which holdings made the money, and what share of the fund they used doing it.

    Read the two numeric columns together: a large profit on a small average weight was
    luck that would not survive being sized properly, and a small profit on a large
    average weight was an expensive way to hold cash.
    """
    rows = []
    for c in contributions:
        rows.append(
            f"<tr><th scope='row'>{c.symbol}</th>"
            f"{_year_cell(float(c.net), percent=False)}"
            f"<td>{c.average_weight:.1%}</td>"
            f"<td>{c.time_held:.0%}</td>"
            f"<td>{float(c.commission):,.0f}</td></tr>"
        )
    return (
        "<table class='stats'><thead><tr><th></th><th>net P&amp;L</th>"
        "<th>avg weight</th><th>time held</th><th>commission</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _year_cell(value: float | None, *, percent: bool = True) -> str:
    """A signed figure, coloured by sign -- and stated as a number, never only coloured."""
    if value is None:
        return "<td>—</td>"
    rendered = f"{value:+.1%}" if percent else f"{value:+,.0f}"
    return f"<td class='{'up' if value > 0 else 'down'}'>{rendered}</td>"


# ------------------------------------------------------------------ assembly

CSS = f"""
:root {{ color-scheme: light; }}
body {{ margin: 0; background: {PAGE}; color: {INK}; font-family: {FONT}; }}
.sheet {{ max-width: 1180px; margin: 0 auto; padding: 32px 24px 64px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }}
.lede {{ color: {INK_SECONDARY}; font-size: 13px; margin: 0 0 28px; max-width: 60ch; line-height: 1.5; }}
h2 {{ font-size: 15px; margin: 36px 0 12px; }}
.card {{ background: {SURFACE}; border: 1px solid rgba(11,11,11,0.10); border-radius: 8px;
        padding: 8px 12px; margin-bottom: 18px; overflow-x: auto; }}
table.stats {{ border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; width: 100%; }}
table.stats th, table.stats td {{ padding: 7px 14px; text-align: right; border-bottom: 1px solid {GRID}; }}
table.stats th[scope=row] {{ text-align: left; font-weight: 400; color: {INK_SECONDARY}; }}
table.stats thead th {{ font-weight: 600; color: {INK}; border-bottom: 1px solid {AXIS}; }}
table.stats tr.divider th {{ padding-top: 18px; font-weight: 600; color: {INK}; }}
table.years td.up {{ color: #184f95; }}
table.years td.down {{ color: #a02020; }}
footer {{ margin-top: 40px; color: {INK_MUTED}; font-size: 12px; line-height: 1.6; }}
"""


def render(
    runs: Sequence[Performance],
    *,
    title: str = "",
    subtitle: str = "",
    weights: pd.DataFrame | None = None,
    contributions: Sequence[Contribution] = (),
) -> str:
    """Assemble the whole page. The first run is the subject; the rest are benchmarks."""
    if not runs:
        raise ValueError("a tearsheet needs at least one run")

    subject = runs[0]
    title = title or f"{subject.label} — backtest"
    period = f"{subject.metrics.start} to {subject.metrics.end} ({subject.metrics.years:.1f} years)"
    subtitle = subtitle or period

    figures = [_growth(runs), _underwater(runs), _rolling(runs)]
    heatmap = _heatmap(subject)
    if heatmap is not None:
        figures.append(heatmap)
    if weights is not None:
        allocation = _allocation(subject, weights)
        if allocation is not None:
            figures.append(allocation)
    figures.append(_exposure(subject))

    blocks = []
    for index, figure in enumerate(figures):
        # Plotly's javascript is inlined once, into the first figure, so the page is a
        # single file that works with no network.
        blocks.append(
            "<div class='card'>"
            + figure.to_html(
                full_html=False,
                include_plotlyjs="inline" if index == 0 else False,
                config={"displayModeBar": False, "responsive": True},
            )
            + "</div>"
        )

    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="sheet">
<h1>{title}</h1>
<p class="lede">{subtitle}. Simulated on adjusted daily bars with commission, spread and
volume-scaled market impact charged on every fill. Decisions are taken at the close and
filled at the following open.</p>
<h2>Statistics</h2>
<div class="card">{_stats_table(runs)}</div>
{"".join(blocks)}
<h2>Calendar years</h2>
<div class="card">{_years_table(runs)}</div>
{_attribution_section(contributions)}
<footer>Generated by sillage on {generated}. A backtest is a hypothesis about the past,
not a forecast. Costs are modelled estimates and have not been measured against a live
broker.</footer>
</div></body></html>"""


def _attribution_section(contributions: Sequence[Contribution]) -> str:
    if not contributions:
        return ""
    share = concentration(contributions)
    note = (
        f"The best single holding produced {share:.0%} of all profit. A strategy whose "
        "result comes overwhelmingly from one asset is a bet on that asset wearing a "
        "diversified costume."
    )
    return (
        f"<h2>Attribution</h2><p class='lede'>{note}</p>"
        f"<div class='card'>{_attribution_table(contributions)}</div>"
    )


def write(
    runs: Sequence[Performance],
    path: Path,
    *,
    title: str = "",
    subtitle: str = "",
    weights: pd.DataFrame | None = None,
    contributions: Sequence[Contribution] = (),
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render(
            runs,
            title=title,
            subtitle=subtitle,
            weights=weights,
            contributions=contributions,
        ),
        encoding="utf-8",
    )
    return path


# ------------------------------------------------------------------ small helpers


def _translucent(hex_colour: str, alpha: float) -> str:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha})"


def _isnan(values: Any) -> Any:
    import numpy as np

    return np.isnan(values)


# ------------------------------------------------------------------ validation

#: Sequential ramp for magnitude: one hue, light to dark. Never a rainbow -- a
#: multi-hue scale makes readers infer categories where there is only "more".
SEQUENTIAL = (
    "#cde2fb",
    "#9ec5f4",
    "#6da7ec",
    "#3987e5",
    "#256abf",
    "#184f95",
    "#0d366b",
)


def _sensitivity(sensitivity: dict[str, list[Any]]) -> go.Figure | None:
    """One panel per parameter: Sharpe against the value swept.

    Flat is the good outcome and the one to look for. A peak at the value the strategy
    happens to use means the value was chosen because it peaked -- by someone, at some
    point, whether or not deliberately.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    knobs = [k for k, trials in sensitivity.items() if len(trials) > 1]
    if not knobs:
        return None

    columns = min(3, len(knobs))
    rows = -(-len(knobs) // columns)
    fig = make_subplots(rows=rows, cols=columns, subplot_titles=knobs, vertical_spacing=0.18)

    for index, knob in enumerate(knobs):
        trials = sensitivity[knob]
        row, column = index // columns + 1, index % columns + 1
        fig.add_trace(
            go.Scatter(
                x=[str(getattr(t.variant, knob)) for t in trials],
                y=[t.metrics.sharpe for t in trials],
                mode="lines+markers",
                line={"color": SERIES[0], "width": 2},
                marker={"size": 8, "color": SERIES[0]},
                hovertemplate="%{x}: Sharpe %{y:.2f}<extra></extra>",
                showlegend=False,
            ),
            row=row,
            col=column,
        )

    layout = _layout(
        "Parameter sensitivity",
        "Sharpe against each parameter, others held at their defaults. A plateau means "
        "the effect is real and the exact value does not matter. A spike means it does.",
        height=200 * rows + 90,
    )
    layout["hovermode"] = "closest"
    layout["showlegend"] = False
    for key in ("xaxis", "yaxis"):
        layout.pop(key)
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont={"color": INK_MUTED, "size": 10})
    fig.update_yaxes(gridcolor=GRID, zeroline=False, tickfont={"color": INK_MUTED, "size": 10})
    for annotation in fig.layout.annotations:
        annotation.font = {"size": 12, "color": INK_SECONDARY, "family": FONT}
    return fig


def _grid_heatmap(grid: list[list[Any]], axes: tuple[Any, Any]) -> go.Figure | None:
    """Two parameters at once. A plateau should be visible as a region, not a pixel."""
    import plotly.graph_objects as go

    if not grid or not grid[0]:
        return None
    (x_knob, x_values), (y_knob, y_values) = axes
    values = [[t.metrics.sharpe for t in row] for row in grid]

    fig = go.Figure(
        go.Heatmap(
            z=values,
            x=[str(v) for v in x_values],
            y=[str(v) for v in y_values],
            colorscale=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
            texttemplate="%{z:.2f}",
            textfont={"size": 11, "family": FONT},
            hovertemplate=f"{x_knob} %{{x}}, {y_knob} %{{y}}: Sharpe %{{z:.2f}}<extra></extra>",
            xgap=2,
            ygap=2,
            colorbar={
                "outlinewidth": 0,
                "tickfont": {"color": INK_MUTED, "size": 10},
                "thickness": 10,
            },
        )
    )
    layout = _layout(
        f"Sharpe across {x_knob} and {y_knob}",
        "Every cell is a full backtest. Look for a broad region of decent results, not "
        "one bright square.",
        height=110 + 40 * len(y_values),
    )
    layout["hovermode"] = "closest"
    layout["showlegend"] = False
    layout["xaxis"]["title"] = {"text": x_knob, "font": {"size": 11, "color": INK_MUTED}}
    layout["yaxis"]["title"] = {"text": y_knob, "font": {"size": 11, "color": INK_MUTED}}
    layout["yaxis"]["gridcolor"] = SURFACE
    fig.update_layout(**layout)
    return fig


def _bootstrap(interval: Any) -> go.Figure | None:
    """The distribution the point estimate came from.

    A Sharpe ratio quoted alone implies a precision it does not have. This is the same
    number with its error bar drawn, and the error bar is usually humbling.
    """
    import plotly.graph_objects as go

    if not interval.samples:
        return None
    fig = go.Figure(
        go.Histogram(
            x=list(interval.samples),
            nbinsx=60,
            marker={"color": _translucent(SERIES[0], 0.75), "line": {"width": 0}},
            hovertemplate="Sharpe %{x:.2f}: %{y} draws<extra></extra>",
        )
    )
    for value, label, dash in (
        (interval.point, "observed", "solid"),
        (interval.low, "2.5%", "dot"),
        (interval.high, "97.5%", "dot"),
    ):
        fig.add_vline(
            x=value,
            line={"color": INK_SECONDARY if dash == "solid" else INK_MUTED, "width": 2},
            annotation_text=label,
            annotation_font={"size": 10, "color": INK_MUTED},
        )
    layout = _layout(
        "Where the Sharpe ratio might actually be",
        f"{interval.draws:,} resamples of the return series, in month-long blocks so "
        "volatility clustering survives the shuffle.",
        height=320,
    )
    layout["hovermode"] = "closest"
    layout["showlegend"] = False
    fig.update_layout(**layout)
    return fig


def _validation_summary(report: Any) -> str:
    rows = [
        ("Configurations run", f"{report.trials}"),
        (
            "In sample",
            f"Sharpe {report.train.metrics.sharpe:.2f} "
            f"({report.train.metrics.start} to {report.train.metrics.end})",
        ),
        (
            "Held out",
            f"Sharpe {report.test.metrics.sharpe:.2f} "
            f"({report.test.metrics.start} to {report.test.metrics.end})",
        ),
        ("Out-of-sample change", f"{report.held_out_gap:+.2f}"),
    ]
    if report.interval:
        rows.append(("Bootstrapped Sharpe", str(report.interval)))
    for deflation in report.deflation:
        rows.append(
            (
                f"P(edge is real), {deflation.trials:,} trials",
                f"{deflation.probability:.4f}",
            )
        )
    body = "".join(f"<tr><th scope='row'>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return f"<table class='stats'><tbody>{body}</tbody></table>"


def render_validation(report: Any, *, title: str = "Validation") -> str:
    """The whole battery as one page."""
    figures = [
        figure
        for figure in (
            _sensitivity(report.sensitivity),
            _grid_heatmap(report.grid, report.grid_axes) if report.grid_axes else None,
            _bootstrap(report.interval) if report.interval else None,
        )
        if figure is not None
    ]
    blocks = [
        "<div class='card'>"
        + figure.to_html(
            full_html=False,
            include_plotlyjs="inline" if index == 0 else False,
            config={"displayModeBar": False, "responsive": True},
        )
        + "</div>"
        for index, figure in enumerate(figures)
    ]
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>{CSS}</style></head>
<body><div class="sheet">
<h1>{title}</h1>
<p class="lede">Five attempts to show the result is an illusion: data the strategy never
saw, parameters moved off their defaults, start dates it did not choose, the return
series resampled, and the whole thing deflated for how many configurations were tried.</p>
<div class="card">{_validation_summary(report)}</div>
{"".join(blocks)}
<footer>Generated by sillage on {generated}. Passing these checks means the result is not
obviously an artefact. It does not mean the strategy will work.</footer>
</div></body></html>"""


def write_validation(report: Any, path: Path, *, title: str = "Validation") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_validation(report, title=title), encoding="utf-8")
    return path
