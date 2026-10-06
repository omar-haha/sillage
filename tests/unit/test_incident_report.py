"""The incident reporter must not leak operational secrets."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "deploy" / "incident-report.py"
SPEC = importlib.util.spec_from_file_location("incident_report", MODULE_PATH)
assert SPEC and SPEC.loader
incident_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(incident_report)


def test_redacts_account_email_healthcheck_and_tokens() -> None:
    raw = (
        "account DU123456 email owner@example.com "
        "https://hc-ping.com/bfbaef35-a622-4c35-b2cb-123456789abc/fail "
        "re_abcdefghijklmnop ghp_abcdefghijklmnop"
    )
    cleaned = incident_report.redact(raw)
    assert "DU123456" not in cleaned
    assert "owner@example.com" not in cleaned
    assert "hc-ping.com" not in cleaned
    assert "abcdefghijklmnop" not in cleaned


def test_markdown_explicitly_forbids_mutation(tmp_path: Path) -> None:
    report = incident_report.collect(tmp_path, "test failure")
    rendered = incident_report.markdown(report)
    assert "Read-only" in rendered
    assert "do not modify" in rendered
    assert "test failure" in rendered


def test_redacts_gateway_secrets_and_extended_accounts(monkeypatch) -> None:
    monkeypatch.setenv("TWS_USERID", "private_login")
    raw = "DUT138047 U123456 private_login jxBrowserKey = abc123 ghu_abcdefghijklmnop ghs_abcdefghijklmnop"
    cleaned = incident_report.redact(raw)
    for secret in ("DUT138047", "U123456", "private_login", "abc123", "abcdefghijklmnop"):
        assert secret not in cleaned


def test_existing_incident_suppresses_new_issue(monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("SILLAGE_GITHUB_TOKEN", "test")
    response = httpx.Response(
        200, json=[{"number": 1}], request=httpx.Request("GET", "https://api.github.com")
    )
    monkeypatch.setattr(incident_report.httpx, "get", lambda *args, **kwargs: response)

    def forbidden(*args, **kwargs):
        raise AssertionError("Must not create a second incident")

    monkeypatch.setattr(incident_report.httpx, "post", forbidden)
    assert incident_report.create_issue("evidence") == ""


def test_unresolved_incident_reminds_once_per_day(monkeypatch, tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    import httpx

    monkeypatch.setenv("SILLAGE_GITHUB_TOKEN", "test")
    response = httpx.Response(
        200,
        json=[{"number": 1, "html_url": "https://github.com/test/repo/issues/1"}],
        request=httpx.Request("GET", "https://api.github.com"),
    )
    monkeypatch.setattr(incident_report.httpx, "get", lambda *args, **kwargs: response)
    sent = []
    monkeypatch.setattr(incident_report, "email", lambda *args, **kwargs: sent.append(kwargs))
    now = datetime(2026, 10, 5, 10, tzinfo=UTC)
    assert incident_report.remind_existing(tmp_path, "redacted evidence", now=now)
    assert not incident_report.remind_existing(
        tmp_path, "redacted evidence", now=now + timedelta(hours=2)
    )
    assert incident_report.remind_existing(
        tmp_path, "redacted evidence", now=now + timedelta(days=1)
    )
    assert len(sent) == 2
    assert all(item["reminder"] for item in sent)


def test_failed_reminder_delivery_does_not_suppress_retry(monkeypatch, tmp_path) -> None:
    import httpx

    monkeypatch.setenv("SILLAGE_GITHUB_TOKEN", "test")
    response = httpx.Response(
        200,
        json=[{"number": 1, "html_url": "https://github.com/test/repo/issues/1"}],
        request=httpx.Request("GET", "https://api.github.com"),
    )
    monkeypatch.setattr(incident_report.httpx, "get", lambda *args, **kwargs: response)

    def failure(*args, **kwargs):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(incident_report, "email", failure)
    import pytest

    with pytest.raises(RuntimeError):
        incident_report.remind_existing(tmp_path, "evidence")
    assert not (tmp_path / "incident-reminder.json").exists()
