"""Command-line entry point.

Every operation the system can perform is reachable from here, so that syncing data,
running a backtest, or starting the live loop are each one auditable command with its
arguments recorded -- rather than a notebook cell someone ran once and cannot reproduce.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
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
live_app = typer.Typer(name="live", help="Run the fund forward on real time.", no_args_is_help=True)
app.add_typer(live_app)

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


@app.command()
def validate(
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    start: Annotated[str, typer.Option(help="First session, YYYY-MM-DD.")] = "2005-01-03",
    end: Annotated[str, typer.Option(help="Last session, YYYY-MM-DD.")] = "",
    split: Annotated[
        str, typer.Option(help="Held-out boundary: everything after it is out of sample.")
    ] = "2018-01-02",
    cash: Annotated[float, typer.Option(help="Starting capital.")] = 100_000,
    single: Annotated[
        bool,
        typer.Option("--single", help="Sweep one rebalance date instead of four tranches."),
    ] = False,
    quick: Annotated[
        bool, typer.Option("--quick", help="A coarse pass, for checking it runs.")
    ] = False,
    report: Annotated[
        bool, typer.Option("--report", help="Write an HTML validation page to reports/.")
    ] = False,
    reports_dir: Annotated[Path, typer.Option(help="Where reports are written.")] = Path("reports"),
) -> None:
    """Try to prove the strategy wrong: held-out data, sensitivity, bootstrap, deflation.

    Takes several minutes. Sweeping one rebalance date with --single is four times
    faster and, on this strategy, mostly measures timing luck -- which is itself worth
    seeing once.
    """
    from datetime import UTC, datetime

    from sillage.backtest.runner import BacktestConfig
    from sillage.backtest.validation import run_battery
    from sillage.core.money import dec
    from sillage.data.universe import get_universe
    from sillage.strategy.momentum import build as build_momentum

    try:
        uni = get_universe(universe)
    except KeyError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None

    base = BacktestConfig(
        strategy=build_momentum(uni),
        universe=uni,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end) if end else datetime.now(UTC).date(),
        initial_cash=dec(cash),
        data_root=root,
    )

    with console.status("validating...") as status:
        try:
            battery = run_battery(
                base,
                boundary=date.fromisoformat(split),
                tranched=not single,
                quick=quick,
                progress=lambda step: status.update(f"validating: {step}"),
            )
        except FileNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from None

    _print_validation(battery)

    if report:
        from sillage.backtest.report import write_validation

        path = write_validation(battery, reports_dir / "validation.html")
        console.print(f"\n[green]validation report[/] {path}")


def _print_validation(report: object) -> None:
    """Render the battery. Each section answers one way the result could be an illusion."""
    from sillage.backtest.validation import Report

    assert isinstance(report, Report)
    m = report.baseline.metrics
    console.print(
        f"\n[bold]baseline[/]  CAGR {m.cagr:+.2%}  vol {m.volatility:.1%}  "
        f"Sharpe {m.sharpe:.2f}  maxDD {m.max_drawdown:.1%}"
    )

    console.print("\n[bold]held out[/]")
    held = Table(box=None, pad_edge=False)
    held.add_column("window", style="dim")
    for column in ("period", "CAGR", "Sharpe", "maxDD"):
        held.add_column(column, justify="right")
    for label, trial in (("in sample", report.train), ("held out", report.test)):
        t = trial.metrics
        held.add_row(
            label,
            f"{t.start} to {t.end}",
            f"{t.cagr:+.2%}",
            f"{t.sharpe:.2f}",
            f"{t.max_drawdown:.1%}",
        )
    console.print(held)
    # Stated, not graded. A pass mark against a threshold picked after seeing the number
    # is not a test, and the drawdown moving is at least as informative as the Sharpe.
    drawdown_change = report.test.metrics.max_drawdown - report.train.metrics.max_drawdown
    console.print(
        f"  out of sample: Sharpe {report.held_out_gap:+.2f}, "
        f"max drawdown {drawdown_change * 100:+.1f} points"
    )

    console.print("\n[bold]parameter sensitivity[/]  [dim](a plateau is good; a spike is not)[/]")
    for knob, trials in report.sensitivity.items():
        table = Table(box=None, pad_edge=False, title=None)
        table.add_column(knob, style="dim")
        for trial in trials:
            table.add_column(str(getattr(trial.variant, knob)), justify="right")
        table.add_row("Sharpe", *[f"{t.metrics.sharpe:.2f}" for t in trials])
        table.add_row("CAGR", *[f"{t.metrics.cagr:+.1%}" for t in trials])
        table.add_row("maxDD", *[f"{t.metrics.max_drawdown:.0%}" for t in trials])
        # Turnover belongs here rather than in a costs section: without it a knob that
        # only moves trading looks like a knob that does nothing at all, which is
        # exactly the wrong conclusion to draw from a flat Sharpe row.
        table.add_row("turnover", *[f"{t.turnover:.2f}x" for t in trials])
        console.print(table)

    if report.starts:
        console.print("\n[bold]start date[/]  [dim](an arbitrary choice nobody counts)[/]")
        table = Table(box=None, pad_edge=False)
        table.add_column("start", style="dim")
        for trial in report.starts:
            table.add_column(str(trial.start), justify="right")
        table.add_row("Sharpe", *[f"{t.metrics.sharpe:.2f}" for t in report.starts])
        table.add_row("CAGR", *[f"{t.metrics.cagr:+.1%}" for t in report.starts])
        console.print(table)

    if report.costs:
        console.print("\n[bold]cost sensitivity[/]  [dim](where does the edge vanish?)[/]")
        table = Table(box=None, pad_edge=False)
        table.add_column("costs", style="dim")
        for trial in report.costs:
            table.add_column(f"{trial.variant.cost_scale:g}x", justify="right")
        table.add_row("Sharpe", *[f"{t.metrics.sharpe:.2f}" for t in report.costs])
        table.add_row("CAGR", *[f"{t.metrics.cagr:+.2%}" for t in report.costs])
        console.print(table)

    if report.interval:
        console.print(f"\n[bold]bootstrap[/]  Sharpe {report.interval}")
        console.print(
            "  [green]excludes zero[/]"
            if report.interval.excludes_zero
            else "  [red]includes zero — the edge is not distinguishable from luck[/]"
        )

    if report.deflation:
        console.print(f"\n[bold]deflated Sharpe[/]  [dim]({report.trials} configurations run)[/]")
        table = Table(box=None, pad_edge=False)
        for column in ("assumed trials", "observed", "best of N by chance", "P(edge is real)"):
            table.add_column(column, justify="right")
        for d in report.deflation:
            table.add_row(
                f"{d.trials:,}",
                f"{d.observed:.2f}",
                f"{d.expected_maximum:.2f}",
                f"[{'green' if d.survives else 'red'}]{d.probability:.4f}[/]",
            )
        console.print(table)


StrategyOpt = Annotated[str, typer.Option("--strategy", "-s", help="Which strategy to run.")]
JournalOpt = Annotated[Path, typer.Option("--journal", help="Where live state is kept.")]


def _live_config(
    strategy: str, universe: str, root: Path, journal: Path, cash: float, drawdown: float
) -> object:
    from sillage.core.money import dec
    from sillage.data.universe import get_universe
    from sillage.live.runner import LiveConfig
    from sillage.risk.limits import RiskLimits
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

    return LiveConfig(
        strategy=chosen,
        universe=uni,
        journal_path=journal,
        data_root=root,
        initial_cash=dec(cash),
        limits=RiskLimits(max_drawdown=dec(drawdown)),
    )


@live_app.command("run-once")
def live_run_once(
    strategy: StrategyOpt = "balanced",
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    journal: JournalOpt = Path("state/live.db"),
    cash: Annotated[float, typer.Option(help="Starting capital, on first run only.")] = 100_000,
    drawdown: Annotated[
        float, typer.Option(help="Halt trading below this drawdown from the peak.")
    ] = 0.25,
    positions: Annotated[
        str,
        typer.Option(
            "--broker-positions",
            help="What the broker says it holds, as SYM=QTY,SYM=QTY. Reconciled before trading.",
        ),
    ] = "",
) -> None:
    """Process every completed session since the last one recorded, then exit.

    Safe to run on a schedule and safe to run twice: a second call finds nothing
    outstanding and does nothing. Intended for cron, once per evening after the close.
    """
    from sillage.core.money import dec
    from sillage.live.reconcile import ReconciliationError
    from sillage.live.runner import StaleDataError, run_once

    config = _live_config(strategy, universe, root, journal, cash, drawdown)
    held = None
    if positions:
        try:
            held = {
                part.split("=")[0].strip().upper(): dec(part.split("=")[1])
                for part in positions.split(",")
                if part.strip()
            }
        except (IndexError, ValueError):
            console.print("[red]--broker-positions must look like SPY=100,IEF=50[/]")
            raise typer.Exit(1) from None

    try:
        report = run_once(config, broker_positions=held)  # type: ignore[arg-type]
    except ReconciliationError as exc:
        console.print(f"[bold red]{exc}[/]")
        console.print(
            "\n[red]refusing to trade.[/] Positions must be explained before "
            "the fund places another order."
        )
        raise typer.Exit(2) from None
    except StaleDataError as exc:
        console.print(f"[bold red]stale data:[/] {exc}")
        raise typer.Exit(4) from None
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from None

    if report.reconciliation:
        console.print(f"[dim]{report.reconciliation}[/]")
    console.print(str(report))
    if report.stalled:
        console.print(
            "\n[bold yellow]nothing filled.[/] Orders were placed and every one was "
            "refused — check the rejections in the journal before the next run."
        )
    if report.halted:
        console.print(f"\n[bold red]TRADING HALTED[/] {report.halted}")
        raise typer.Exit(3)


@live_app.command("status")
def live_status(
    strategy: StrategyOpt = "balanced",
    universe: UniverseOpt = "core",
    root: RootOpt = Path("data"),
    journal: JournalOpt = Path("state/live.db"),
    cash: Annotated[float, typer.Option(help="Starting capital.")] = 100_000,
) -> None:
    """Show what the fund holds, without touching it."""
    from sillage.live.runner import status

    config = _live_config(strategy, universe, root, journal, cash, 0.25)
    state = status(config)  # type: ignore[arg-type]

    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column("", style="dim")
    table.add_column("", justify="right")
    for label, key in (
        ("journal", "journal"),
        ("last session", "last_session"),
        ("sessions recorded", "sessions_recorded"),
        ("NAV", "nav"),
        ("high-water mark", "high_water_mark"),
        ("cash", "cash"),
        ("pending orders", "pending"),
    ):
        value = state[key]
        if isinstance(value, Decimal):
            table.add_row(label, f"{float(value):,.2f}")
        else:
            table.add_row(label, str(value))
    counts = state["counts"]
    assert isinstance(counts, dict)
    table.add_row("journal rows", ", ".join(f"{k} {v}" for k, v in counts.items()))
    console.print(table)

    holdings = state["positions"]
    assert isinstance(holdings, dict)
    if holdings:
        console.print("\n[bold]positions[/]")
        for symbol, quantity in sorted(holdings.items()):
            console.print(f"  {symbol:<6} {float(quantity):>12,.4f}")
    else:
        console.print("\n[dim]no positions[/]")


if __name__ == "__main__":
    app()
