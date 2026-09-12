"""The shapes the API returns.

Declared rather than assembled ad hoc, because these are a published contract: a
dashboard is written against them, and a field quietly renamed is a broken chart nobody
notices until someone looks. Declaring them also gives FastAPI enough to generate the
OpenAPI document, which means the contract is readable without reading this file.

**Money is sent as a float, and that is a deliberate loss.** Everything inside the system
keeps `Decimal` because cents must not drift over thousands of trades. JSON has no
decimal type and neither does JavaScript, so a number crossing this boundary is going to
become a float somewhere; doing it here, once, visibly, is better than pretending
otherwise by sending strings that the client will parse into floats anyway. The
consequence to keep in mind: **the API is a view, the journal is the record.** Anything
that needs exactness reads the journal.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Health(BaseModel):
    """Enough to tell a load balancer whether to route here, and no more."""

    status: str = "ok"
    version: str
    journal: str
    #: False when the journal file does not exist yet. Not an error: a fund that has
    #: never run has nothing to serve, and saying so beats a 500.
    ready: bool


class Position(BaseModel):
    symbol: str
    quantity: float
    price: float | None = Field(default=None, description="Last close, or null if unpriced.")
    value: float | None = None
    weight: float | None = Field(default=None, description="Share of NAV.")


class NavPoint(BaseModel):
    session: date
    nav: float
    cash: float
    gross_exposure: float
    holdings: int


class Fill(BaseModel):
    ts: datetime
    symbol: str
    quantity: float
    price: float
    commission: float
    order_id: str


class Order(BaseModel):
    client_order_id: str
    created_at: datetime | None
    symbol: str
    quantity: float
    order_type: str
    reason: str


class Rejection(BaseModel):
    """A refused order.

    Its own endpoint because it is the surface every quiet live failure shows up on. A
    fund placing orders and filling none looks healthy from every other view.
    """

    ts: datetime | None
    symbol: str
    quantity: float
    reason: str


class Metrics(BaseModel):
    """Risk and return of the live fund, on the same definitions the backtest uses."""

    start: date
    end: date
    years: float
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    longest_drawdown_days: int
    positive_months: float


class Status(BaseModel):
    """One call that answers "is the fund alright"."""

    strategy: str
    universe: str
    last_session: date | None
    sessions_recorded: int
    nav: float
    cash: float
    high_water_mark: float
    #: Positive fraction below the peak. The kill-switch measures against this.
    drawdown: float
    positions: int
    pending_orders: int
    #: Sessions between the newest stored bar and today. A live fund whose data has
    #: stopped updating keeps reporting a NAV, so this is worth showing on the front page.
    data_staleness_sessions: int | None = None
    counts: dict[str, int]


class Instrument(BaseModel):
    symbol: str
    asset_class: str
    currency: str
    exchange: str | None
    trades_continuously: bool


class Universe(BaseModel):
    name: str
    cash_proxy: str
    instruments: list[Instrument]
