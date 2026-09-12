"""Tests for the read-only API.

Two things matter here beyond the shapes. The first is that a fund which has never run
is a *normal* state -- a freshly deployed dashboard is in it -- and must produce
something explicable rather than a stack trace. The second is that nothing can trade
through this: the strongest guarantee is structural, so it is asserted structurally.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support import etf, synthetic_store

from sillage.api.app import ApiConfig, create_app
from sillage.core.money import ZERO, dec
from sillage.core.types import AssetClass, Fill, Instrument, Order
from sillage.data.universe import Universe
from sillage.engine.journal import NavPoint
from sillage.execution.broker import Rejection
from sillage.state.journal import SqliteJournal

CASH = Instrument("CASH", AssetClass.ETF)
UNIVERSE = Universe("api", (etf("AAA"), etf("BBB")), cash_proxy=CASH)
TS = datetime(2026, 3, 12, 21, tzinfo=UTC)


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("api-data")
    synthetic_store(root, {"AAA": 0.0005, "BBB": 0.0002, "CASH": 0.00004}, sessions=60)
    return root


@pytest.fixture
def journal_path(tmp_path: Path) -> Path:
    """A fund that has traded: two positions, a fill, a refusal and a NAV history."""
    path = tmp_path / "live.db"
    journal = SqliteJournal(path)
    for day, nav in ((10, "100000"), (11, "101000"), (12, "100500")):
        journal.record_nav(
            NavPoint(
                session=date(2026, 3, day),
                ts=datetime(2026, 3, day, 21, tzinfo=UTC),
                nav=dec(nav),
                cash=dec("500"),
                gross_exposure=dec("0.99"),
                holdings=2,
                weights={"AAA": dec("0.6"), "BBB": dec("0.39")},
            )
        )
    journal.record_fill(Fill(etf("AAA"), TS, dec(100), dec(100), commission=dec("0.35")))
    journal.record_fill(Fill(etf("BBB"), TS, dec(50), dec(80), commission=dec("0.35")))
    journal.save_pending([Order(etf("AAA"), dec(5), created_at=TS, reason="rebalance AAA")])
    journal.record_rejection(
        Rejection(Order(etf("BBB"), dec(9), created_at=TS), "insufficient cash", TS)
    )
    return path


@pytest.fixture
def client(journal_path: Path, store: Path) -> TestClient:
    return TestClient(
        create_app(
            ApiConfig(
                journal_path=journal_path,
                universe=UNIVERSE,
                data_root=store,
                initial_cash=dec(100_000),
            )
        )
    )


@pytest.fixture
def empty(tmp_path: Path) -> TestClient:
    """No fund, and no dashboard built either -- a fresh checkout."""
    return TestClient(
        create_app(
            ApiConfig(
                journal_path=tmp_path / "never-ran.db",
                dashboard_dir=tmp_path / "no-dist",
            )
        )
    )


# ------------------------------------------------------------------ read-only


def test_a_built_dashboard_is_served_at_the_root(tmp_path: Path) -> None:
    built = tmp_path / "dist"
    built.mkdir()
    (built / "index.html").write_text("<!doctype html><title>built</title>")
    client = TestClient(create_app(ApiConfig(journal_path=tmp_path / "x.db", dashboard_dir=built)))
    assert "built" in client.get("/").text


def test_nothing_can_trade_through_this() -> None:
    """The guarantee is structural, so it is asserted structurally rather than trusted.

    A window onto a trading account that grows a POST is a window that can be made to
    trade by a stale browser tab or a misconfigured CORS policy.
    """
    app = create_app(
        ApiConfig(journal_path=Path("/nonexistent.db"), dashboard_dir=Path("/nonexistent"))
    )
    methods = {m for route in app.routes for m in getattr(route, "methods", set())}
    assert methods <= {"GET", "HEAD"}


def test_cors_is_restricted_to_reads_when_it_is_configured(tmp_path: Path) -> None:
    app = create_app(
        ApiConfig(journal_path=tmp_path / "x.db", allowed_origins=("http://localhost:5173",))
    )
    client = TestClient(app)
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert "GET" in response.headers.get("access-control-allow-methods", "")
    assert "POST" not in response.headers.get("access-control-allow-methods", "")


# ------------------------------------------------------------------ a fund that never ran


def test_health_answers_even_with_no_fund(empty: TestClient) -> None:
    body = empty.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["ready"] is False


def test_the_other_routes_explain_themselves_rather_than_crashing(empty: TestClient) -> None:
    for path in ("/api/status", "/api/nav", "/api/positions", "/api/fills", "/api/metrics"):
        response = empty.get(path)
        assert response.status_code == 503, path
        assert "live run-once" in response.json()["detail"]


def test_the_universe_is_known_without_a_fund(empty: TestClient) -> None:
    """Static configuration, not fund state. It should not need one."""
    assert empty.get("/api/universe").status_code == 200


def test_the_root_page_says_how_to_build_the_dashboard(empty: TestClient) -> None:
    """A server that refused to start without a compiled frontend could not be used to
    debug why the frontend would not compile."""
    body = empty.get("/").text
    assert "npm run build" in body


def test_it_publishes_an_openapi_document(empty: TestClient) -> None:
    schema = empty.get("/openapi.json").json()
    assert "/api/status" in schema["paths"]


# ------------------------------------------------------------------ a fund that has


def test_status_answers_is_the_fund_alright(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["sessions_recorded"] == 3
    assert body["last_session"] == "2026-03-12"
    assert body["nav"] == pytest.approx(100_500)
    assert body["pending_orders"] == 1


def test_drawdown_is_measured_from_the_peak_not_the_start(client: TestClient) -> None:
    """The fund went 100k, 101k, 100.5k. It is half a percent below its peak, not up."""
    assert client.get("/api/status").json()["drawdown"] == pytest.approx(0.00495, abs=1e-4)


def test_the_nav_series_comes_back_in_order(client: TestClient) -> None:
    sessions = [p["session"] for p in client.get("/api/nav").json()]
    assert sessions == sorted(sessions)


def test_the_nav_series_can_be_narrowed(client: TestClient) -> None:
    assert len(client.get("/api/nav?since=2026-03-12").json()) == 1


def test_positions_are_valued_at_the_latest_price(client: TestClient) -> None:
    rows = {p["symbol"]: p for p in client.get("/api/positions").json()}
    assert set(rows) == {"AAA", "BBB"}
    assert rows["AAA"]["value"] == pytest.approx(rows["AAA"]["quantity"] * rows["AAA"]["price"])
    assert 0 < rows["AAA"]["weight"] < 1


def test_a_blotter_is_read_from_the_top(client: TestClient) -> None:
    fills = client.get("/api/fills").json()
    assert len(fills) == 2
    assert fills[0]["ts"] >= fills[-1]["ts"]


def test_the_blotter_can_be_limited(client: TestClient) -> None:
    assert len(client.get("/api/fills?limit=1").json()) == 1


def test_an_absurd_limit_is_refused(client: TestClient) -> None:
    assert client.get("/api/fills?limit=99999").status_code == 422


def test_refusals_get_their_own_endpoint(client: TestClient) -> None:
    """It is the surface every quiet live failure shows up on; a fund placing orders and
    filling none looks healthy from every other view."""
    rows = client.get("/api/rejections").json()
    assert rows[0]["reason"] == "insufficient cash"
    assert rows[0]["symbol"] == "BBB"


def test_orders_carry_the_reason_they_were_raised(client: TestClient) -> None:
    reasons = [o["reason"] for o in client.get("/api/orders").json()]
    assert "rebalance AAA" in reasons


def test_metrics_are_computed_on_the_same_definitions_as_the_backtest(
    client: TestClient,
) -> None:
    body = client.get("/api/metrics").json()
    assert body["max_drawdown"] < 0
    assert body["start"] == "2026-03-10"


def test_metrics_are_null_rather_than_zero_when_there_is_too_little(tmp_path: Path) -> None:
    """A fund with one session has no Sharpe ratio, and 0.00 is a number someone would
    eventually quote."""
    journal = SqliteJournal(tmp_path / "one.db")
    journal.record_nav(NavPoint(date(2026, 3, 10), TS, dec(100_000), ZERO, dec(1), 0, {}))
    client = TestClient(create_app(ApiConfig(journal_path=tmp_path / "one.db")))
    assert client.get("/api/metrics").json() is None


# ------------------------------------------------------------------ shapes


def test_money_crosses_the_boundary_as_a_number(client: TestClient) -> None:
    """Deliberate and lossy: JSON has no decimal type and neither does JavaScript. The
    API is a view; the journal is the record."""
    nav = client.get("/api/nav").json()[0]
    assert isinstance(nav["nav"], float)
    assert not isinstance(nav["nav"], Decimal)


def test_the_universe_reports_what_can_be_held(client: TestClient) -> None:
    body = client.get("/api/universe").json()
    assert body["cash_proxy"] == "CASH"
    assert {i["symbol"] for i in body["instruments"]} == {"AAA", "BBB", "CASH"}


def test_data_staleness_is_on_the_front_page(client: TestClient) -> None:
    """A fund whose data stopped updating keeps reporting a NAV and looks fine."""
    assert client.get("/api/status").json()["data_staleness_sessions"] is not None
