"""Command-line entry point.

Every operation the system can perform is reachable from here, so that syncing data,
running a backtest, or starting the live loop are each one auditable command with its
arguments recorded -- rather than a notebook cell someone ran once and cannot reproduce.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from sillage import __version__

if TYPE_CHECKING:
    # Type-only. Every command imports what it needs inside its own body so that
    # `sillage --help` does not pay for pandas.
    import pandas as pd

    from sillage.backtest.attribution import Contribution
    from sillage.backtest.metrics import Performance
    from sillage.backtest.runner import BacktestConfig, BacktestResult

app = typer.Typer(
    name="sillage",
    help="A systematic multi-asset fund manager.",
    no_args_is_help=True,
    add_completion=False,
)
data_app = typer.Typer(name="data", help="Fetch and inspect market data.", no_args_is_help=True)
app.add_typer(data_app)

console = Console()

RootOpt = Annotated[Path, typer.Option("--root", help="Where the local data store lives.")]
UniverseOpt = Annotated[str, typer.Option("--universe", "-u", help="Universe name.")]


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"sillage [bold cyan]{__version__}[/]")


@app.command()
def doctor() -> None:
    """Check that the environment is set up correctly."""
    import sys

    console.print(f"python              [bold]{sys.version.split()[0]}[/]")
    ok = True
    for module in ("numpy", "pandas", "pyarrow", "duckdb", "exchange_calendars", "yfinance"):
        try:
            __import__(module)
        except ImportError:
            console.print(f"{module:<20}[bold red]missing[/]")
            ok = False
        else:
            console.print(f"{module:<20}[green]ok[/]")
    if not ok:
        raise typer.Exit(1)
    console.print("\n[green]environment looks good.[/]")


@data_app.command("sync")
def data_sync(
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    start: Annotated[str, typer.Option(help="Earliest date to fetch, YYYY-MM-DD.")] = "2005-01-01",
    full: Annotated[
        bool, typer.Option("--full", help="Refetch all history, not just recent.")
    ] = False,
) -> None:
    """Download price history for a universe into the local store."""
    from sillage.data.providers.yahoo import YahooProvider
    from sillage.data.store import BarStore
    from sillage.data.sync import sync_universe
    from sillage.data.universe import get_universe

    uni = get_universe(universe)
    store = BarStore(root)
    console.print(f"syncing [bold]{uni.name}[/] ({len(uni.all_instruments)} symbols) into {root}/")

    with console.status("fetching..."):
        results = sync_universe(
            uni,
            store,
            YahooProvider(),
            start=date.fromisoformat(start),
            incremental=not full,
        )

    table = Table(box=None, pad_edge=False)
    table.add_column("symbol", style="bold")
    table.add_column("fetched", justify="right")
    table.add_column("stored", justify="right")
    table.add_column("status")
    for result in results:
        status = "[green]ok[/]" if result.ok else f"[red]{result.error}[/]"
        table.add_row(result.symbol, str(result.fetched), str(result.stored), status)
    console.print(table)

    failed = [r for r in results if not r.ok]
    if failed:
        console.print(f"\n[red]{len(failed)} symbol(s) failed.[/]")
        raise typer.Exit(1)
    console.print(f"\n[green]synced {len(results)} symbols.[/]")


@data_app.command("check")
def data_check(
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
) -> None:
    """Inspect stored data for gaps, suspicious jumps, and stale prices."""
    from sillage.data.quality import Severity, check_symbol, summarise
    from sillage.data.store import BarStore
    from sillage.data.universe import get_universe

    uni = get_universe(universe)
    store = BarStore(root)

    coverage_table = Table(title="coverage", box=None, pad_edge=False)
    for column, justify in (
        ("symbol", "left"),
        ("from", "left"),
        ("to", "left"),
        ("sessions", "right"),
        ("years", "right"),
    ):
        coverage_table.add_column(column, justify=justify)  # type: ignore[arg-type]

    all_issues = []
    for instrument in uni.all_instruments:
        coverage = store.coverage(instrument.symbol)
        if coverage is None:
            console.print(f"[red]{instrument.symbol}: nothing stored[/]")
            continue
        coverage_table.add_row(
            instrument.symbol,
            coverage.start.isoformat(),
            coverage.end.isoformat(),
            f"{coverage.rows:,}",
            f"{coverage.span_years:.1f}",
        )
        all_issues.extend(
            check_symbol(
                instrument.symbol,
                store.read(instrument.symbol),
                continuous=instrument.trades_continuously,
            )
        )

    console.print(coverage_table)

    if not all_issues:
        console.print("\n[green]no issues found.[/]")
        return

    colour = {Severity.ERROR: "red", Severity.WARN: "yellow", Severity.INFO: "dim"}
    issue_table = Table(title="issues", box=None, pad_edge=False)
    issue_table.add_column("symbol", style="bold")
    issue_table.add_column("severity")
    issue_table.add_column("kind")
    issue_table.add_column("detail")
    for issue in all_issues:
        issue_table.add_row(
            issue.symbol,
            f"[{colour[issue.severity]}]{issue.severity}[/]",
            issue.kind,
            issue.detail,
        )
    console.print(issue_table)

    counts = summarise(all_issues)
    console.print(
        f"\n{counts[Severity.ERROR]} error(s), "
        f"{counts[Severity.WARN]} warning(s), {counts[Severity.INFO]} note(s)."
    )
    if counts[Severity.ERROR]:
        raise typer.Exit(1)


def _build_config(
    strategy: str,
    universe: str,
    root: Path,
    start: str,
    end: str,
    cash: float,
    band: float,
    cost_scale: float,
) -> BacktestConfig:
    """Assemble a BacktestConfig from command-line options, or exit with an explanation."""
    from datetime import UTC, datetime

    from sillage.backtest.runner import BacktestConfig
    from sillage.core.money import dec
    from sillage.data.universe import get_universe
    from sillage.execution.costs import CostModel
    from sillage.portfolio.rebalance import Rebalancer
    from sillage.strategy.registry import build, names

    try:
        uni = get_universe(universe)
    except KeyError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None
    try:
        chosen = build(strategy, uni)
    except KeyError:
        console.print(f"[red]unknown strategy {strategy!r}[/]; known: {', '.join(names())}")
        raise typer.Exit(1) from None

    return BacktestConfig(
        strategy=chosen,
        universe=uni,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end) if end else datetime.now(UTC).date(),
        initial_cash=dec(cash),
        costs=CostModel().scaled(cost_scale),
        rebalancer=Rebalancer(band=dec(band)),
        data_root=root,
    )


def _run(config: BacktestConfig) -> BacktestResult:
    from sillage.backtest.runner import run_backtest

    try:
        result = run_backtest(config)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None
    if not result.nav_points:
        console.print("[red]no sessions in range.[/]")
        raise typer.Exit(1)
    return result


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


@app.command()
def backtest(
    strategy: Annotated[
        str, typer.Option("--strategy", "-s", help="Which strategy to run.")
    ] = "momentum",
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    start: Annotated[str, typer.Option(help="First session, YYYY-MM-DD.")] = "2005-01-03",
    end: Annotated[str, typer.Option(help="Last session, YYYY-MM-DD.")] = "",
    cash: Annotated[float, typer.Option(help="Starting capital.")] = 100_000,
    band: Annotated[
        float, typer.Option(help="No-trade band: relative drift tolerated before trading.")
    ] = 0.20,
    cost_scale: Annotated[
        float, typer.Option(help="Multiply every cost assumption. 0 disables costs.")
    ] = 1.0,
    benchmark: Annotated[
        list[str] | None,
        typer.Option(
            "--benchmark", "-b", help="Also run this strategy for comparison. Repeatable."
        ),
    ] = None,
    report: Annotated[
        bool, typer.Option("--report", help="Write an HTML tearsheet to reports/.")
    ] = False,
    attribution: Annotated[
        bool, typer.Option("--attribution", help="Show which holdings made the money.")
    ] = False,
    reports_dir: Annotated[Path, typer.Option(help="Where tearsheets are written.")] = Path(
        "reports"
    ),
) -> None:
    """Replay a strategy over stored history and report how it did."""
    from sillage.backtest.attribution import attribute
    from sillage.backtest.metrics import analyse

    options = (universe, root, start, end, cash, band, cost_scale)
    subject = _run(_build_config(strategy, *options))
    runs = [analyse(subject)]
    for name in benchmark or []:
        runs.append(analyse(_run(_build_config(name, *options))))

    _print_performance(runs)
    contributions = attribute(subject.final_portfolio, subject.final_prices, subject.nav_points)
    if attribution:
        _print_attribution(contributions)

    if report:
        from sillage.backtest.report import write

        path = write(
            runs,
            reports_dir / f"{_slug(runs[0].label)}.html",
            weights=_weights_frame(subject),
            contributions=contributions,
        )
        console.print(f"\n[green]tearsheet[/] {path}")


def _weights_frame(result: BacktestResult) -> pd.DataFrame:
    """Per-session holding weights, as a frame the allocation chart can stack."""
    import pandas as pd

    rows = [{s: float(w) for s, w in p.weights.items()} for p in result.nav_points]
    index = pd.DatetimeIndex([p.session for p in result.nav_points], name="session")
    return pd.DataFrame(rows, index=index).fillna(0.0)


def _print_attribution(contributions: Sequence[Contribution]) -> None:
    from sillage.backtest.attribution import concentration

    table = Table(box=None, pad_edge=False)
    table.add_column("symbol", style="bold")
    for column in ("net P&L", "avg weight", "time held", "commission"):
        table.add_column(column, justify="right")
    for c in contributions:
        table.add_row(
            c.symbol,
            f"{float(c.net):+,.0f}",
            f"{c.average_weight:.1%}",
            f"{c.time_held:.0%}",
            f"{float(c.commission):,.0f}",
        )
    console.print()
    console.print(table)
    console.print(
        f"\n[dim]best single holding is {concentration(contributions):.0%} of all profit.[/]"
    )


def _print_performance(runs: list[Performance]) -> None:
    """The same statistics the tearsheet shows, for people who live in a terminal."""
    subject = runs[0]
    table = Table(box=None, pad_edge=False)
    table.add_column("", style="dim")
    for run in runs:
        table.add_column(run.label, justify="right")

    def row(label: str, render: Callable[[Performance], str]) -> None:
        table.add_row(label, *[render(run) for run in runs])

    m = subject.metrics
    console.print(
        f"\n[bold]{m.start} to {m.end}[/]  ({m.years:.1f} years, {len(subject.nav):,} sessions)\n"
    )
    row("final NAV", lambda r: f"{float(r.metrics.final_nav):,.0f}")
    row("total return", lambda r: f"{r.metrics.total_return:+.1%}")
    row("annualised", lambda r: f"{r.metrics.cagr:+.2%}")
    row("volatility", lambda r: f"{r.metrics.volatility:.1%}")
    row("Sharpe", lambda r: f"{r.metrics.sharpe:.2f}")
    row("Sortino", lambda r: f"{r.metrics.sortino:.2f}")
    row("max drawdown", lambda r: f"{r.metrics.max_drawdown:.1%}")
    row("longest drawdown", lambda r: f"{r.metrics.longest_drawdown_days:,}d")
    row("Calmar", lambda r: f"{r.metrics.calmar:.2f}")
    row("positive months", lambda r: f"{r.metrics.positive_months:.0%}")
    row("worst month", lambda r: f"{r.metrics.worst_month:.1%}")
    row("fills", lambda r: f"{r.trading.fills:,}")
    row("turnover", lambda r: f"{r.trading.annual_turnover:.2f}x/yr")
    row("cost drag", lambda r: f"{r.trading.cost_drag:.3%}/yr")
    row("avg exposure", lambda r: f"{r.trading.average_exposure:.0%}")
    console.print(table)


@app.command("timing-luck")
def timing_luck(
    strategy: Annotated[
        str, typer.Option("--strategy", "-s", help="Which strategy to test.")
    ] = "60-40",
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    start: Annotated[str, typer.Option(help="First session, YYYY-MM-DD.")] = "2005-01-03",
    end: Annotated[str, typer.Option(help="Last session, YYYY-MM-DD.")] = "",
    cash: Annotated[float, typer.Option(help="Starting capital.")] = 100_000,
    band: Annotated[float, typer.Option(help="No-trade band.")] = 0.20,
    cost_scale: Annotated[float, typer.Option(help="Multiply every cost assumption.")] = 1.0,
) -> None:
    """Run one strategy on several rebalance dates and report how much the date mattered.

    Nothing makes the last session of the month a better day to trade than the
    third-to-last. If the two disagree by much, the backtest's headline number is
    partly luck.
    """
    from sillage.backtest.timing import study

    config = _build_config(strategy, universe, root, start, end, cash, band, cost_scale)
    try:
        result = study(config)
    except TypeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None

    table = Table(box=None, pad_edge=False)
    table.add_column("rebalance date", style="dim")
    for column in ("annualised", "Sharpe", "max DD", "final NAV"):
        table.add_column(column, justify="right")
    for offset, metrics in zip(result.offsets, result.metrics, strict=True):
        label = "month end" if offset == 0 else f"{offset} sessions earlier"
        table.add_row(
            label,
            f"{metrics.cagr:+.2%}",
            f"{metrics.sharpe:.2f}",
            f"{metrics.max_drawdown:.1%}",
            f"{float(metrics.final_nav):,.0f}",
        )
    console.print(table)
    console.print(f"\n{result.summary()}.")
    console.print(
        "\n[dim]A fixed-weight strategy should show almost nothing here -- it wants the\n"
        "same weights whichever day it looks. A selection strategy will not.[/]"
    )


if __name__ == "__main__":
    app()
