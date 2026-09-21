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


def metric_cell(label: str, value: str) -> str:
    return (
        "<td style='padding:9px 12px;border:1px solid #dfe3e8'>"
        f"<span style='display:block;color:#667085;font-size:12px'>{html.escape(label)}</span>"
        f"<strong style='display:block;margin-top:2px;font-size:16px'>{html.escape(value)}</strong>"
        "</td>"
    )


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
    metrics = (
        ("NAV", money(latest.nav)),
        ("Week", percent(weekly)),
        ("Since $25K launch", percent(cumulative)),
        ("Cash", money(latest.cash)),
        ("Gross exposure", percent(latest.gross)),
        ("Positions", str(latest.positions)),
        ("Pending orders", str(pending)),
        ("As of", latest.session),
    )
    metric_rows = "".join(
        f"<tr>{metric_cell(*metrics[index])}{metric_cell(*metrics[index + 1])}</tr>"
        for index in range(0, len(metrics), 2)
    )
    allocation = sorted(latest.allocation.items(), key=lambda item: item[1], reverse=True)
    allocation_cells = [
        "<td style='padding:7px 10px;border-bottom:1px solid #eaecf0'>"
        f"<strong>{html.escape(symbol)}</strong>"
        f"<span style='float:right;margin-left:12px'>{percent(weight)}</span></td>"
        for symbol, weight in allocation
    ]
    allocation_cells.extend(
        "<td style='padding:7px 10px'></td>" for _ in range((-len(allocation_cells)) % 3)
    )
    allocation_rows = "".join(
        f"<tr>{''.join(allocation_cells[index : index + 3])}</tr>"
        for index in range(0, len(allocation_cells), 3)
    )
    body = f"""<!doctype html><html><body style="margin:0;background:#f5f7fa">
<div style="max-width:680px;margin:0 auto;padding:24px;font-family:Arial,sans-serif;color:#17202a">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="background:#fff;border:1px solid #e4e7ec;border-radius:10px">
<tr><td style="padding:22px 24px 12px">
<h2 style="margin:0 0 6px;font-size:22px">Sillage paper portfolio</h2>
<p style="margin:0;color:#667085;font-size:14px">Broker-reconciled through
 <strong style="color:#344054">{latest.session}</strong></p>
</td></tr>
<tr><td style="padding:8px 24px 14px">
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;table-layout:fixed">
{metric_rows}</table>
</td></tr>
<tr><td style="padding:0 24px 18px">
<h3 style="margin:6px 0 8px;font-size:16px">Allocation</h3>
<table width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;table-layout:fixed;font-size:14px">
{allocation_rows}</table>
</td></tr>
<tr><td style="padding:12px 24px 18px;border-top:1px solid #eaecf0;color:#667085;font-size:12px">
Journal: {counts['orders']} orders &middot; {counts['fills']} fills &middot;
 {counts['rejections']} rejections. Paper performance is operational evidence, not a live return claim.
</td></tr></table></div></body></html>"""
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
