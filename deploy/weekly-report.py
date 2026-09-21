#!/usr/bin/env python3
"""Send a compact weekly paper-portfolio summary through Resend."""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import httpx


@dataclass(frozen=True)
class Snapshot:
    session: str
    nav: Decimal
    cash: Decimal
    gross: Decimal
    positions: int
    allocation: dict[str, Decimal]


def snapshot(row: tuple[object, ...]) -> Snapshot:
    allocation = json.loads(str(row[5]))
    return Snapshot(
        session=str(row[0]),
        nav=Decimal(str(row[1])),
        cash=Decimal(str(row[2])),
        gross=Decimal(str(row[3])),
        positions=int(row[4]),
        allocation={symbol: Decimal(str(weight)) for symbol, weight in allocation.items()},
    )


def money(value: Decimal) -> str:
    return f"${value:,.2f}"


def percent(value: Decimal) -> str:
    return f"{value:.2%}"


def build_report(journal: Path, initial_cash: Decimal) -> tuple[str, str, str]:
    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    rows = connection.execute(
        "SELECT session, nav, cash, gross_exposure, holdings, weights "
        "FROM nav ORDER BY session DESC LIMIT 6"
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no NAV history in {journal}")
    latest = snapshot(rows[0])
    comparison = snapshot(rows[-1])
    weekly = latest.nav / comparison.nav - 1 if comparison.nav else Decimal(0)
    cumulative = latest.nav / initial_cash - 1
    counts = {
        table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        for table in ("orders", "fills", "rejections")
    }
    pending_raw = connection.execute(
        "SELECT value FROM meta WHERE key = 'pending_orders'"
    ).fetchone()
    pending = len(json.loads(str(pending_raw[0]))) if pending_raw else 0
    connection.close()

    subject = f"Sillage weekly — {latest.session} — {money(latest.nav)}"
    holdings = "".join(
        f"<tr><td>{html.escape(symbol)}</td><td style='text-align:right'>"
        f"{percent(weight)}</td></tr>"
        for symbol, weight in sorted(
            latest.allocation.items(), key=lambda item: item[1], reverse=True
        )
    )
    body = f"""<!doctype html><html><body style="font-family:system-ui,sans-serif;color:#17202a">
<h2>Sillage paper portfolio</h2>
<p>Successfully processed and broker-reconciled through <strong>{latest.session}</strong>.</p>
<table cellpadding="6" cellspacing="0">
<tr><td>NAV</td><td><strong>{money(latest.nav)}</strong></td></tr>
<tr><td>Week</td><td>{percent(weekly)}</td></tr>
<tr><td>Since $25K launch</td><td>{percent(cumulative)}</td></tr>
<tr><td>Cash</td><td>{money(latest.cash)}</td></tr>
<tr><td>Gross exposure</td><td>{percent(latest.gross)}</td></tr>
<tr><td>Positions</td><td>{latest.positions}</td></tr>
<tr><td>Pending orders</td><td>{pending}</td></tr>
</table>
<h3>Allocation</h3><table cellpadding="5" cellspacing="0">{holdings}</table>
<p style="color:#667085">Journal totals: {counts['orders']} orders, {counts['fills']} fills,
{counts['rejections']} rejections. Paper performance is operational evidence, not a live return claim.</p>
</body></html>"""
    text = (
        f"Sillage paper portfolio through {latest.session}\n"
        f"NAV: {money(latest.nav)}\nWeek: {percent(weekly)}\n"
        f"Since $25K launch: {percent(cumulative)}\nCash: {money(latest.cash)}\n"
        f"Gross exposure: {percent(latest.gross)}\nPositions: {latest.positions}\n"
        f"Pending orders: {pending}\n"
    )
    return subject, body, text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path, default=Path("state/ibkr.db"))
    parser.add_argument("--cash", type=Decimal, default=Decimal("25000"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    subject, body, text = build_report(args.journal, args.cash)
    if args.dry_run:
        print(subject)
        print(text)
        return 0

    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['SILLAGE_RESEND_API_KEY']}"},
        json={
            "from": os.environ["SILLAGE_REPORT_FROM"],
            "to": [os.environ["SILLAGE_REPORT_TO"]],
            "subject": subject,
            "html": body,
            "text": text,
        },
        timeout=20,
    )
    response.raise_for_status()
    print(f"weekly report sent: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
