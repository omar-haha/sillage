"""A read-only window onto the live fund.

**Every route is a GET, and that is a design constraint rather than an accident of
scope.** An interface that can place an order is an interface that can be made to place
one — by a misconfigured CORS policy, a stale browser tab, a bookmarked URL. The fund
trades from `sillage live run-once`, invoked by cron on a machine nobody browses to, and
this serves what happened afterwards. Nothing here imports a broker.

**It reads through `SqliteJournal` rather than mapping the tables again.** The roadmap
called for SQLModel; defining the schema a second time would give two sources of truth
for the same four tables, and the one that drifts is always the one nobody is looking at.
Pydantic still describes the *responses*, which is what makes the OpenAPI document worth
having.

**Backtests are not served here.** The plan had `/backtests/{id}`, which needs a job
queue: a tranched twenty-year replay takes seconds, far too long for a request and far
too much machinery for one endpoint. Backtests already produce a self-contained HTML
tearsheet that needs no server at all. This serves the thing that genuinely changes daily
and has nowhere else to live — the live fund.
"""

# Deliberately *without* `from __future__ import annotations`. FastAPI resolves
# dependency types by evaluating annotations at runtime, in module globals; postponed
# evaluation turns them into strings that cannot see anything defined inside
# `create_app`, which is where the per-application dependency lives. The failure is a
# `PydanticUserError` raised only when something requests the OpenAPI document, so it
# would otherwise surface as a broken /docs page long after the change that caused it.

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Query

from sillage import __version__
from sillage.api import models
from sillage.core.money import ZERO, dec, safe_div
from sillage.core.types import Instrument
from sillage.data.universe import Universe, get_universe
from sillage.state.journal import SqliteJournal, positions_of

DEFAULT_JOURNAL = Path("state/live.db")

#: The repository's own `web/dist`, four levels up from this file.
DEFAULT_DASHBOARD = Path(__file__).resolve().parents[3] / "web" / "dist"

#: Opening capital, used only to rebuild the book from an empty journal.
DEFAULT_CASH: Final = dec("100000")


@dataclass(frozen=True, slots=True)
class ApiConfig:
    """What fund this server is looking at."""

    journal_path: Path = DEFAULT_JOURNAL
    #: The universe itself rather than a name to look up. The API has to value positions
    #: and read the store, both of which need the instruments; resolving a name here
    #: would mean only registered universes could ever be served.
    universe: Universe = field(default_factory=lambda: get_universe("core"))
    strategy_name: str = "balanced"
    data_root: Path = Path("data")
    initial_cash: Decimal = DEFAULT_CASH
    #: Browser origins allowed to call this. Empty means same-origin only, which is the
    #: right default: the dashboard is served from here.
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    #: Where the built dashboard lives. Configurable rather than derived from the package
    #: location so that a container can put it elsewhere, and so a test can point at a
    #: directory that does not exist and see what an unbuilt deployment looks like.
    dashboard_dir: Path = field(default_factory=lambda: DEFAULT_DASHBOARD)

    @property
    def universe_name(self) -> str:
        return self.universe.name

    @property
    def instruments(self) -> dict[str, Instrument]:
        return {i.symbol: i for i in self.universe.all_instruments}


class Fund:
    """Everything the routes need, resolved once per request.

    A thin layer over the journal, existing so that "the journal does not exist yet" is
    answered in one place. A fund that has never run is a normal state -- a freshly
    deployed dashboard is in it -- and every route returning an empty result beats every
    route returning a stack trace.
    """

    def __init__(self, config: ApiConfig) -> None:
        self.config = config
        self.ready = config.journal_path.exists()
        self.journal = SqliteJournal(config.journal_path) if self.ready else None

    def require(self) -> SqliteJournal:
        if self.journal is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"no fund at {self.config.journal_path}: run "
                    f"`sillage live run-once` before expecting anything here"
                ),
            )
        return self.journal

    def prices(self) -> dict[str, Decimal]:
        """Last known close per instrument, from the store rather than the journal.

        The journal records what the fund was worth, not what anything cost. Valuing a
        position needs a price, and the store is where prices live.
        """
        from sillage.data.store import BarStore

        store = BarStore(self.config.data_root)
        latest: dict[str, Decimal] = {}
        for symbol in self.config.instruments:
            if not store.has(symbol):
                continue
            coverage = store.coverage(symbol)
            if coverage is None:
                continue
            frame = store.read(symbol)
            if not frame.empty:
                latest[symbol] = dec(float(frame["close"].iloc[-1]))
        return latest


def create_app(config: ApiConfig | None = None) -> FastAPI:
    """Build the application. A factory so tests can point it at their own fund."""
    settings = config or ApiConfig()

    app = FastAPI(
        title="sillage",
        version=__version__,
        summary="A read-only view of a systematic fund.",
        description=(
            "Every route is a GET. The fund trades from `sillage live run-once`; this "
            "shows what happened. Money crosses this boundary as floats -- the API is a "
            "view, the journal is the record."
        ),
    )

    if settings.allowed_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=False,
            # Read-only, and the CORS policy says so too rather than relying on there
            # being no write routes today.
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    def fund() -> Fund:
        return Fund(settings)

    # A type alias, not a variable, despite sitting inside a function: FastAPI resolves
    # dependencies from annotations, so this is the only place it can be spelled once.
    held_fund = Annotated[Fund, Depends(fund)]

    @app.get("/api/health", response_model=models.Health, tags=["meta"])
    def health(held: held_fund) -> models.Health:
        return models.Health(
            version=__version__,
            journal=str(settings.journal_path),
            ready=held.ready,
        )

    @app.get("/api/universe", response_model=models.Universe, tags=["meta"])
    def universe() -> models.Universe:
        uni = settings.universe
        return models.Universe(
            name=uni.name,
            cash_proxy=uni.cash_proxy.symbol,
            instruments=[
                models.Instrument(
                    symbol=i.symbol,
                    asset_class=str(i.asset_class),
                    currency=i.currency,
                    exchange=i.exchange,
                    trades_continuously=i.trades_continuously,
                )
                for i in uni.all_instruments
            ],
        )

    @app.get("/api/status", response_model=models.Status, tags=["fund"])
    def status(held: held_fund) -> models.Status:
        journal = held.require()
        history = journal.nav_history()
        portfolio = journal.portfolio(settings.instruments, initial_cash=settings.initial_cash)
        nav = history[-1].nav if history else settings.initial_cash
        peak = journal.high_water_mark()
        return models.Status(
            strategy=settings.strategy_name,
            universe=settings.universe_name,
            last_session=journal.last_session(),
            sessions_recorded=len(history),
            nav=float(nav),
            cash=float(portfolio.cash),
            high_water_mark=float(peak),
            drawdown=float(max(ZERO, safe_div(peak - nav, peak))) if peak > ZERO else 0.0,
            positions=len(positions_of(portfolio)),
            pending_orders=len(journal.load_pending(settings.instruments)),
            data_staleness_sessions=_staleness(settings),
            counts=journal.counts(),
        )

    @app.get("/api/nav", response_model=list[models.NavPoint], tags=["fund"])
    def nav(
        held: held_fund,
        since: Annotated[date | None, Query(description="Only sessions on or after this.")] = None,
    ) -> list[models.NavPoint]:
        return [
            models.NavPoint(
                session=point.session,
                nav=float(point.nav),
                cash=float(point.cash),
                gross_exposure=float(point.gross_exposure),
                holdings=point.holdings,
            )
            for point in held.require().nav_history()
            if since is None or point.session >= since
        ]

    @app.get("/api/positions", response_model=list[models.Position], tags=["fund"])
    def positions(held: held_fund) -> list[models.Position]:
        journal = held.require()
        portfolio = journal.portfolio(settings.instruments, initial_cash=settings.initial_cash)
        prices = held.prices()
        held_quantities = positions_of(portfolio)
        total = portfolio.nav({s: p for s, p in prices.items() if s in held_quantities})

        rows = []
        for symbol, quantity in sorted(held_quantities.items()):
            price = prices.get(symbol)
            value = quantity * price if price is not None else None
            rows.append(
                models.Position(
                    symbol=symbol,
                    quantity=float(quantity),
                    price=float(price) if price is not None else None,
                    value=float(value) if value is not None else None,
                    weight=float(safe_div(value, total)) if value is not None and total else None,
                )
            )
        return rows

    @app.get("/api/fills", response_model=list[models.Fill], tags=["fund"])
    def fills(
        held: held_fund, limit: Annotated[int, Query(ge=1, le=1000)] = 100
    ) -> list[models.Fill]:
        recorded = held.require().fills(settings.instruments)
        return [
            models.Fill(
                ts=fill.ts,
                symbol=fill.instrument.symbol,
                quantity=float(fill.quantity),
                price=float(fill.price),
                commission=float(fill.commission),
                order_id=fill.order_id,
            )
            # Newest first: a blotter is read from the top.
            for fill in reversed(recorded[-limit:])
        ]

    @app.get("/api/orders", response_model=list[models.Order], tags=["fund"])
    def orders(
        held: held_fund, limit: Annotated[int, Query(ge=1, le=1000)] = 100
    ) -> list[models.Order]:
        rows = held.require().recent_orders(limit)
        return [
            models.Order(
                client_order_id=row["client_order_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                symbol=row["symbol"],
                quantity=float(row["quantity"]),
                order_type=row["order_type"],
                reason=row["reason"],
            )
            for row in rows
        ]

    @app.get("/api/rejections", response_model=list[models.Rejection], tags=["fund"])
    def rejections(
        held: held_fund, limit: Annotated[int, Query(ge=1, le=1000)] = 100
    ) -> list[models.Rejection]:
        return [
            models.Rejection(
                ts=datetime.fromisoformat(row["ts"]) if row["ts"] else None,
                symbol=row["symbol"],
                quantity=float(row["quantity"]),
                reason=row["reason"],
            )
            for row in held.require().recent_rejections(limit)
        ]

    @app.get("/api/metrics", response_model=models.Metrics | None, tags=["fund"])
    def metrics(held: held_fund) -> models.Metrics | None:
        """Risk and return of the live fund, or null until there is enough of it.

        Null rather than zeroes. A fund with three sessions has no meaningful Sharpe
        ratio, and returning 0.00 would put a number on a dashboard that someone would
        eventually quote.
        """
        from sillage.backtest.metrics import from_nav, nav_series

        history = held.require().nav_history()
        if len(history) < 2:
            return None
        computed = from_nav(nav_series(history))
        return models.Metrics(
            start=computed.start,
            end=computed.end,
            years=computed.years,
            total_return=computed.total_return,
            cagr=computed.cagr,
            volatility=computed.volatility,
            sharpe=computed.sharpe,
            sortino=computed.sortino,
            max_drawdown=computed.max_drawdown,
            calmar=computed.calmar,
            longest_drawdown_days=computed.longest_drawdown_days,
            positive_months=computed.positive_months,
        )

    _mount_dashboard(app, settings.dashboard_dir)
    return app


def _staleness(settings: ApiConfig) -> int | None:
    """How many sessions behind the newest stored bar is.

    On the status endpoint because a fund whose data stopped updating keeps reporting a
    NAV and looks entirely healthy, which is how that failure goes unnoticed.
    """
    from sillage.core.calendar import TradingCalendar
    from sillage.data.store import BarStore

    store = BarStore(settings.data_root)
    newest: date | None = None
    for symbol in settings.instruments:
        coverage = store.coverage(symbol) if store.has(symbol) else None
        if coverage is not None and (newest is None or coverage.end > newest):
            newest = coverage.end
    if newest is None:
        return None
    today = datetime.now(UTC).date()
    if newest >= today:
        return 0
    return max(0, len(TradingCalendar().sessions(newest, today)) - 1)


def _mount_dashboard(app: FastAPI, built: Path) -> None:
    """Serve the built dashboard, if someone has built it.

    Absent, the API still works and `/` explains how to get one. A server that refuses
    to start because a frontend was not compiled is a server that cannot be used to
    debug why the frontend will not compile.
    """
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    if built.is_dir():
        app.mount("/", StaticFiles(directory=built, html=True), name="dashboard")
        return

    @app.get("/", include_in_schema=False)
    def placeholder() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>sillage</title>"
            "<body style='font:14px system-ui;margin:3rem;max-width:40rem'>"
            "<h1>sillage</h1><p>The API is running. The dashboard has not been built.</p>"
            "<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>"
            "<p><a href='/docs'>API documentation</a></p>"
        )
