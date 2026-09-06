"""Command-line entry point.

Every operation the system can perform is reachable from here, so that syncing data,
running a backtest, or starting the live loop are each one auditable command with its
arguments recorded -- rather than a notebook cell someone ran once and cannot reproduce.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from sillage import __version__

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


@app.command()
def backtest(
    strategy: Annotated[
        str, typer.Option("--strategy", "-s", help="Which strategy to run.")
    ] = "60-40",
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
) -> None:
    """Replay a strategy over stored history."""
    from datetime import UTC, datetime

    from sillage.backtest.runner import BacktestConfig, run_backtest
    from sillage.core.money import dec
    from sillage.data.universe import get_universe
    from sillage.execution.costs import CostModel
    from sillage.portfolio.rebalance import Rebalancer
    from sillage.strategy.registry import build, names

    uni = get_universe(universe)
    try:
        chosen = build(strategy, uni)
    except KeyError:
        console.print(f"[red]unknown strategy {strategy!r}[/]; known: {', '.join(names())}")
        raise typer.Exit(1) from None

    config = BacktestConfig(
        strategy=chosen,
        universe=uni,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end) if end else datetime.now(UTC).date(),
        initial_cash=dec(cash),
        costs=CostModel().scaled(cost_scale),
        rebalancer=Rebalancer(band=dec(band)),
        data_root=root,
    )

    with console.status(f"replaying {chosen.name}..."):
        try:
            result = run_backtest(config)
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from None

    if not result.nav_points:
        console.print("[red]no sessions in range.[/]")
        raise typer.Exit(1)

    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("", style="dim")
    table.add_column("", justify="right")
    first, last = result.nav_points[0], result.nav_points[-1]
    years = (last.session - first.session).days / 365.25
    from sillage.backtest.runner import annualised

    for label, value in (
        ("strategy", chosen.name),
        (
            "period",
            f"{first.session} to {last.session}  ({years:.1f}y, {result.sessions} sessions)",
        ),
        ("starting NAV", f"{float(result.initial_nav):,.2f}"),
        ("final NAV", f"{float(result.final_nav):,.2f}"),
        ("total return", f"{float(result.total_return):+.2%}"),
        ("annualised", f"{float(annualised(result.total_return, years)):+.2%}"),
        ("fills", f"{len(result.fills):,}"),
        ("traded notional", f"{float(result.traded_notional):,.0f}"),
        ("commission", f"{float(result.total_commission):,.2f}"),
        ("slippage", f"{float(result.total_slippage):,.2f}"),
        ("rejections", f"{len(result.rejections):,}"),
        ("final cash", f"{float(last.cash):,.2f}"),
        ("final exposure", f"{float(last.gross_exposure):.1%}"),
    ):
        table.add_row(label, str(value))
    console.print(table)

    if result.rejections:
        console.print("\n[yellow]rejected orders[/]")
        for rejection in result.rejections[:10]:
            console.print(f"  {rejection}")
        if len(result.rejections) > 10:
            console.print(f"  ... and {len(result.rejections) - 10} more")

    console.print(
        "\n[dim]Risk and performance metrics arrive in Phase 2. This is the raw record.[/]"
    )


if __name__ == "__main__":
    app()
