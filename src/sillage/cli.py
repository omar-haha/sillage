"""Command-line entry point.

Every operation the system can perform is reachable from here, so that running a
backtest, syncing data, or starting the live loop are all one auditable command with
its arguments recorded rather than a notebook cell someone ran once.
"""

from __future__ import annotations

import typer
from rich.console import Console

from sillage import __version__

app = typer.Typer(
    name="sillage",
    help="A systematic multi-asset fund manager.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"sillage [bold cyan]{__version__}[/]")


@app.command()
def doctor() -> None:
    """Check that the environment is set up correctly."""
    import sys

    console.print(f"python      [bold]{sys.version.split()[0]}[/]")
    ok = True
    for module in ("numpy", "pandas", "pyarrow", "duckdb", "exchange_calendars"):
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


if __name__ == "__main__":
    app()
