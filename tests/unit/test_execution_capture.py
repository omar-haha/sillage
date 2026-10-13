import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from sillage.state.journal import SqliteJournal


def module(tmp_path, monkeypatch, stamp):
    spec = importlib.util.spec_from_file_location("capture", Path("deploy/capture-executions.py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    monkeypatch.setattr(value, "__file__", str(tmp_path / "deploy/capture-executions.py"))
    monkeypatch.setattr(value, "datetime", SimpleNamespace(now=lambda tz: stamp))
    (tmp_path / "state").mkdir()
    SqliteJournal(tmp_path / "state/ibkr.db")
    return value


@pytest.mark.parametrize(
    "stamp",
    [
        datetime(2026, 10, 13, 8, tzinfo=ZoneInfo("America/Toronto")),
        datetime(2026, 10, 11, 10, tzinfo=ZoneInfo("America/Toronto")),
    ],
)
def test_outside_market_window_never_connects(tmp_path, monkeypatch, stamp):
    value = module(tmp_path, monkeypatch, stamp)
    monkeypatch.setattr(value, "IBGatewayClient", lambda **kw: pytest.fail("must not connect"))
    value.main()


def test_capture_readonly_same_client_and_no_order_path(tmp_path, monkeypatch, capsys):
    stamp = datetime(2026, 10, 13, 10, tzinfo=ZoneInfo("America/Toronto"))
    value = module(tmp_path, monkeypatch, stamp)
    calls = {}

    def client(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            connect=lambda: None,
            disconnect=lambda: None,
            _require=lambda: SimpleNamespace(managedAccounts=lambda: ["DU123"]),
            executions=lambda **kw: [],
        )

    monkeypatch.setattr(value, "IBGatewayClient", client)
    value.main()
    assert calls["readonly"] and calls["port"] == 4002 and calls["client_id"] == 17
    assert calls["execution_archive"] == tmp_path / "state/ibkr.db"
    assert "DU123" not in capsys.readouterr().out
    assert SqliteJournal(tmp_path / "state/ibkr.db").counts()["fills"] == 0


def test_live_account_refused_before_execution_request(tmp_path, monkeypatch):
    value = module(
        tmp_path, monkeypatch, datetime(2026, 10, 13, 10, tzinfo=ZoneInfo("America/Toronto"))
    )
    monkeypatch.setattr(
        value,
        "IBGatewayClient",
        lambda **kw: SimpleNamespace(
            connect=lambda: None,
            disconnect=lambda: None,
            _require=lambda: SimpleNamespace(managedAccounts=lambda: ["U123"]),
            executions=lambda **kw: pytest.fail("live account must not be captured"),
        ),
    )
    with pytest.raises(ValueError, match="paper"):
        value.main()
