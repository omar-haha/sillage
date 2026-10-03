#!/usr/bin/env python3
"""Collect a redacted Sillage incident and optionally open a GitHub issue."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

COMMANDS = {
    "clock": ["date", "--iso-8601=seconds"],
    "uptime": ["uptime"],
    "disk": ["df", "-h", "/"],
    "inodes": ["df", "-i", "/"],
    "memory": ["free", "-h"],
    "api_port": ["ss", "-ltn"],
    "processes": ["ps", "-eo", "pid,ppid,stat,etimes,comm"],
    "cron": ["crontab", "-l"],
    "gateway": ["docker", "compose", "-f", "deploy/ibgateway/compose.yaml", "ps"],
    "gateway_logs": [
        "docker",
        "compose",
        "-f",
        "deploy/ibgateway/compose.yaml",
        "logs",
        "--tail=120",
    ],
    "commit": ["git", "rev-parse", "--short", "HEAD"],
    "worktree": ["git", "status", "--short", "--branch"],
}
LOGS = ("state/paper-cron.log", "state/gateway-restart.log", "state/weekly-report.log")
REDACTIONS = (
    (re.compile(r"\b(?:DU[T]?|U)\d+\b", re.I), "[ACCOUNT]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"https://hc-ping\.com/[\w-]+(?:/\w+)?"), "[HEALTHCHECK_URL]"),
    (re.compile(r"\b(?:re|gh[pousr]|github_pat)_[A-Za-z0-9_\-]{12,}\b"), "[TOKEN]"),
    (re.compile(r"(?im)(\b(?:jxBrowserKey|TWS_USERID|password|api_key|token)\s*[=:]\s*)[^\s,;]+"), r"\1[REDACTED]"),
)


def redact(value: str) -> str:
    for name in ("TWS_USERID", "TWS_PASSWORD", "SILLAGE_IB_ACCOUNT"):
        secret = os.environ.get(name)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    for pattern, replacement in REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def run(command: list[str], root: Path) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"
    output = (result.stdout + result.stderr).strip()
    return f"exit={result.returncode}\n{output}"[-12_000:]


def journal_summary(path: Path) -> str:
    """Return counts and cursor state, never holdings, orders, or account data."""
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("orders", "fills", "rejections", "nav")
        }
        latest = connection.execute("SELECT max(session) FROM nav").fetchone()[0]
        pending = connection.execute(
            "SELECT value FROM meta WHERE key = ?", ("pending_orders",)
        ).fetchone()
        connection.close()
        import json

        pending_count = len(json.loads(pending[0])) if pending else 0
        return f"counts={counts}\nlatest_nav_session={latest}\npending_orders={pending_count}"
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def collect(root: Path, trigger: str) -> dict[str, object]:
    evidence = {name: redact(run(command, root)) for name, command in COMMANDS.items()}
    evidence["journal"] = journal_summary(root / "state/ibkr.db")
    bars = root / "data/bars/daily"
    try:
        files = list(bars.glob("*.parquet"))
        newest = max(files, key=lambda path: path.stat().st_mtime)
        stamp = datetime.fromtimestamp(newest.stat().st_mtime, UTC).isoformat()
        evidence["data_store"] = f"symbols={len(files)}\nnewest_file_mtime={stamp}"
    except (OSError, ValueError) as exc:
        evidence["data_store"] = f"unavailable: {type(exc).__name__}: {exc}"
    for relative in LOGS:
        path = root / relative
        try:
            lines = path.read_text(errors="replace").splitlines()[-120:]
            evidence[relative] = redact("\n".join(lines))[-16_000:]
        except OSError as exc:
            evidence[relative] = f"unavailable: {type(exc).__name__}: {exc}"
    return {
        "kind": "sillage-incident-v1",
        "observed_at": datetime.now(UTC).isoformat(),
        "trigger": redact(trigger)[:2_000],
        "evidence": evidence,
    }


def markdown(report: dict[str, object]) -> str:
    evidence = report["evidence"]
    assert isinstance(evidence, dict)
    sections = [
        "## Sillage incident evidence",
        "",
        f"Observed: `{report['observed_at']}`",
        f"Trigger: `{report['trigger']}`",
        "",
        "> Read-only, automatically redacted evidence. Diagnose only; do not modify",
        "> infrastructure, repository state, broker state, orders, or the journal.",
    ]
    for name, value in evidence.items():
        sections.extend(("", f"### {name}", "```text", str(value), "```"))
    return "\n".join(sections)


def create_issue(body: str) -> str:
    token = os.environ["SILLAGE_GITHUB_TOKEN"]
    repository = os.environ.get("SILLAGE_GITHUB_REPOSITORY", "omar-haha/sillage")
    response = httpx.post(
        f"https://api.github.com/repos/{repository}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={
            "title": f"Sillage incident — {datetime.now(UTC):%Y-%m-%d %H:%M UTC}",
            "body": body,
            "labels": ["sillage-incident"],
        },
        timeout=20,
    )
    response.raise_for_status()
    return str(response.json()["html_url"])


def email(body: str, issue_url: str) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['SILLAGE_RESEND_API_KEY']}"},
        json={
            "from": os.environ.get("SILLAGE_REPORT_FROM", "onboarding@resend.dev"),
            "to": [os.environ["SILLAGE_REPORT_TO"]],
            "subject": "Sillage incident collected — diagnosis starting",
            "text": f"A Sillage failure was captured.\n\nOpenHands incident: {issue_url}\n\n{body[:6000]}",
        },
        timeout=20,
    )
    response.raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("SILLAGE_ROOT", ".")))
    parser.add_argument("--trigger", default="paper cycle failed")
    parser.add_argument("--dispatch", action="store_true")
    args = parser.parse_args()
    body = markdown(collect(args.root.resolve(), args.trigger))
    if not args.dispatch:
        print(body)
        return 0
    issue_url = create_issue(body)
    email(body, issue_url)
    print(issue_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
