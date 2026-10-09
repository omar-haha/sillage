#!/usr/bin/env python3
"""Collect a redacted Sillage incident and optionally open a GitHub issue."""

from __future__ import annotations

import argparse
import fcntl
import json
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
    (
        re.compile(
            r"(?im)(\b(?:jxBrowserKey|TWS_USERID|password|api_key|token|restart|IbLoginId|FIXLoginId)\s*[=:]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
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
    existing = httpx.get(
        f"https://api.github.com/repos/{repository}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        params={"state": "open", "labels": "sillage-incident", "per_page": 100},
        timeout=20,
    )
    existing.raise_for_status()
    if any("pull_request" not in issue for issue in existing.json()):
        return ""
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


def email(body: str, issue_url: str, *, reminder: bool = False) -> None:
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['SILLAGE_RESEND_API_KEY']}"},
        json={
            "from": os.environ.get("SILLAGE_REPORT_FROM", "onboarding@resend.dev"),
            "to": [os.environ["SILLAGE_REPORT_TO"]],
            "subject": "Sillage still blocked — unresolved incident"
            if reminder
            else "Sillage incident collected — diagnosis starting",
            "text": (
                "The paper cycle remains blocked. This is a daily reminder, not a new incident or recovery approval."
                if reminder
                else "A Sillage failure was captured."
            )
            + f"\n\nIncident: {issue_url}\n\n{body[:6000]}",
        },
        timeout=20,
    )
    response.raise_for_status()


def remember_notification(state: Path, issue_url: str, now: datetime) -> None:
    target = state / "incident-reminder.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({"issue": issue_url, "sent_at": now.isoformat()}) + "\n")
    temporary.chmod(0o600)
    temporary.replace(target)


def remind_existing(state: Path, body: str, *, now: datetime | None = None) -> bool:
    """At most one reminder per incident per 24 hours; never retrigger diagnosis."""
    now = now or datetime.now(UTC)
    repository = os.environ.get("SILLAGE_GITHUB_REPOSITORY", "omar-haha/sillage")
    response = httpx.get(
        f"https://api.github.com/repos/{repository}/issues",
        headers={
            "Authorization": f"Bearer {os.environ['SILLAGE_GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
        params={"state": "open", "labels": "sillage-incident", "per_page": 100},
        timeout=20,
    )
    response.raise_for_status()
    incident = next((issue for issue in response.json() if "pull_request" not in issue), None)
    if incident is None:
        return False
    issue_url = incident["html_url"]
    try:
        last = json.loads((state / "incident-reminder.json").read_text())
        if (
            last["issue"] == issue_url
            and (now - datetime.fromisoformat(last["sent_at"])).total_seconds() < 86400
        ):
            return False
    except (OSError, ValueError, KeyError, TypeError):
        pass
    email(body, issue_url, reminder=True)
    remember_notification(state, issue_url, now)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("SILLAGE_ROOT", ".")))
    parser.add_argument("--trigger", default="paper cycle failed")
    parser.add_argument("--dispatch", action="store_true")
    parser.add_argument("--resolve", action="store_true")
    args = parser.parse_args()
    if args.resolve:
        state = args.root.resolve() / "state"
        with (state / "incident-dispatch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            resolve_success(state)
        return 0
    body = markdown(collect(args.root.resolve(), args.trigger))
    if not args.dispatch:
        print(body)
        return 0
    state = args.root.resolve() / "state"
    state.mkdir(exist_ok=True)
    with (state / "incident-dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        resolve_success(state)
        issue_url = create_issue(body)
        if not issue_url:
            sent = remind_existing(state, body)
            print(
                "Existing open incident: daily blocked-status reminder sent."
                if sent
                else "Existing open incident: duplicate issue suppressed; reminder not due."
            )
            return 0
        email(body, issue_url)
        remember_notification(state, issue_url, datetime.now(UTC))
    print(issue_url)
    return 0


def resolve_success(state: Path) -> None:
    """Close only incidents predating the last actual successful scheduled cycle.

    Also used before failure dispatch: a historical success closes a stale issue,
    but never claims the current failure has recovered.
    """
    marker = state / "paper-last-success"
    if not marker.exists():
        return
    successful_day = marker.read_text().strip()
    success = datetime.fromtimestamp(marker.stat().st_mtime, UTC)
    if successful_day != success.date().isoformat():
        raise ValueError("Invalid paper success marker")
    repository = os.environ.get("SILLAGE_GITHUB_REPOSITORY", "omar-haha/sillage")
    base = f"https://api.github.com/repos/{repository}/issues"
    headers = {"Authorization": f"Bearer {os.environ['SILLAGE_GITHUB_TOKEN']}"}
    response = httpx.get(
        base,
        headers=headers,
        params={"state": "open", "labels": "sillage-incident", "per_page": 100},
        timeout=20,
    )
    response.raise_for_status()
    for issue in response.json():
        if "pull_request" in issue or "created_at" not in issue:
            continue
        created = datetime.fromisoformat(issue["created_at"].replace("Z", "+00:00"))
        if created >= success:
            continue
        url = f"{base}/{issue['number']}"
        closed = httpx.patch(
            url, headers=headers, json={"state": "closed", "state_reason": "completed"}, timeout=20
        )
        closed.raise_for_status()
        comment = httpx.post(
            url + "/comments",
            headers=headers,
            json={
                "body": f"<!-- sillage-cycle-recovered-v1 -->\nResolved by the successful scheduled paper cycle on {successful_day} (UTC). This confirms that cycle only, not ongoing Gateway health. Subsequent failures are separate incidents."
            },
            timeout=20,
        )
        comment.raise_for_status()
        print(f"Resolved incident #{issue['number']} from recorded cycle success.")


if __name__ == "__main__":
    sys.exit(main())
