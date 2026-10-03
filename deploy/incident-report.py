#!/usr/bin/env python3
"""Collect a redacted Sillage incident and optionally open a GitHub issue."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

COMMANDS = {
    "clock": ["date", "--iso-8601=seconds"],
    "disk": ["df", "-h", "/"],
    "memory": ["free", "-h"],
    "api_port": ["ss", "-ltn"],
    "gateway": ["docker", "compose", "-f", "deploy/ibgateway/compose.yaml", "ps"],
    "commit": ["git", "rev-parse", "--short", "HEAD"],
}
LOGS = ("state/paper-cron.log", "state/gateway-restart.log", "state/weekly-report.log")
REDACTIONS = (
    (re.compile(r"\bDU\d+\b", re.I), "[ACCOUNT]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"https://hc-ping\.com/[\w-]+(?:/\w+)?"), "[HEALTHCHECK_URL]"),
    (re.compile(r"\b(?:re|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b"), "[TOKEN]"),
)


def redact(value: str) -> str:
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


def collect(root: Path, trigger: str) -> dict[str, object]:
    evidence = {name: redact(run(command, root)) for name, command in COMMANDS.items()}
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
