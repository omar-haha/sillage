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


if __name__ == "__main__":
    app()
