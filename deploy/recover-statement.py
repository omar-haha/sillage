#!/usr/bin/env python3
"""Preview by default; apply only after identical preview and fresh broker checks."""

import argparse
import fcntl
import json
import logging
import os
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ib_async import IB

from sillage.state.journal import SqliteJournal
from sillage.state.recovery import fingerprint, repair


def snapshot(source, destination):
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin,
        sqlite3.connect(destination) as target,
    ):
        origin.backup(target)
    destination.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--statement", type=Path, required=True)
    parser.add_argument("--confirmations", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/home/deploy/sillage"))
    parser.add_argument("--through", type=date.fromisoformat, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    root = args.root.resolve()
    source = root / "state/ibkr.db"
    # Take the same advisory lock as cron so no strategy cycle can race the repair.
    with (root / "state/paper.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ib = IB()
        ib.RequestTimeout = 25
        try:
            ib.connect("127.0.0.1", 4002, clientId=90, timeout=15, readonly=True)
            if ib.reqAllOpenOrders():
                raise ValueError("Broker has open orders; manual review required")
            accounts = {position.account for position in ib.positions()}
            if len(accounts) != 1:
                raise ValueError("Exactly one broker account with positions is required")
            account = next(iter(accounts))
            if not account.upper().startswith("DU"):
                raise ValueError("Statement recovery is restricted to paper accounts")
            positions = {
                position.contract.symbol: Decimal(str(position.position))
                for position in ib.positions()
                if position.position
            }
            directory = root / "state/recovery" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            directory.mkdir(parents=True, mode=0o700)
            original = directory / "original.db"
            preview = directory / "preview.db"
            snapshot(source, original)
            snapshot(original, preview)
            result = repair(
                preview,
                args.statement,
                args.confirmations,
                root / "data",
                args.through,
                positions,
                expected_account=account,
            )
            if args.apply and not result.get("already_imported"):
                # Canonical audit schema is installed before logical fingerprint comparison.
                # No source fill/NAV/pending record is changed by schema installation.
                SqliteJournal(source)
                with sqlite3.connect(source) as connection:
                    if fingerprint(connection) != result["before_fingerprint"]:
                        raise ValueError(
                            "Production journal changed after preview; refusing repair"
                        )
                ib.reqPositions()
                current = {
                    position.contract.symbol: Decimal(str(position.position))
                    for position in ib.positions()
                    if position.position
                }
                if current != positions or ib.reqAllOpenOrders():
                    raise ValueError("Broker changed after preview; refusing repair")
                applied = repair(
                    source,
                    args.statement,
                    args.confirmations,
                    root / "data",
                    args.through,
                    current,
                    expected_account=account,
                )
                if applied != result:
                    raise RuntimeError(
                        "Applied repair differs from preview; operator investigation required"
                    )
                result["applied"] = True
            else:
                result["applied"] = False
            (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
            print("Private online backup and preview retained under state/recovery/")
        finally:
            ib.disconnect()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not expose exception reprs that could contain account/statement data.
        print(
            "Recovery failed:",
            str(exc) if type(exc) in {ValueError, RuntimeError} else type(exc).__name__,
        )
        raise SystemExit(1) from None
