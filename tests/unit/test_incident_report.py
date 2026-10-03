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
