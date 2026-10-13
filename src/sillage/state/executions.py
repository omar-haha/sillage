"""Durable broker evidence, separate from imported ledger fills."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sillage.core.money import dec

if TYPE_CHECKING:
    from sillage.execution.ibkr import Execution


class ExecutionArchive:
    def __init__(self, path: Path):
        self.path = path

    def save(self, account: str, executions: Iterable[Execution]) -> None:
        if not self.path.is_file():
            raise ValueError("execution capture requires an existing journal")
        with sqlite3.connect(self.path) as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS broker_execution_evidence (account TEXT NOT NULL, execution_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(account, execution_id))"
            )
            for execution in executions:
                if not execution.execution_id or not execution.order_ref:
                    raise ValueError("execution lacks stable identifiers")
                if (
                    not execution.quantity.is_finite()
                    or not execution.price.is_finite()
                    or not execution.commission.is_finite()
                    or execution.quantity == 0
                    or execution.price <= 0
                    or execution.commission < 0
                    or execution.ts.utcoffset() is None
                ):
                    raise ValueError("invalid confirmed execution evidence")
                payload = json.dumps(asdict(execution), default=str, sort_keys=True)
                old = c.execute(
                    "SELECT payload FROM broker_execution_evidence WHERE account=? AND execution_id=?",
                    (account, execution.execution_id),
                ).fetchone()
                if old and old[0] != payload:
                    raise ValueError("broker execution changed; human amendment required")
                # IB corrections differ only after the last period; do not silently
                # append a correction as a second independent execution.
                if "." in execution.execution_id:
                    prefix = execution.execution_id.rsplit(".", 1)[0]
                    previous = c.execute(
                        "SELECT execution_id FROM broker_execution_evidence WHERE account=?",
                        (account,),
                    ).fetchall()
                    if any(
                        x[0] != execution.execution_id and x[0].rsplit(".", 1)[0] == prefix
                        for x in previous
                    ):
                        raise ValueError("broker execution correction requires human review")
                c.execute(
                    "INSERT OR IGNORE INTO broker_execution_evidence VALUES (?,?,?)",
                    (account, execution.execution_id, payload),
                )

    def load(self, account: str, *, since: date | None = None) -> list[Execution]:
        from sillage.execution.ibkr import Execution

        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as c:
            if not c.execute(
                "SELECT 1 FROM sqlite_master WHERE name='broker_execution_evidence'"
            ).fetchone():
                return []
            rows = c.execute(
                "SELECT payload FROM broker_execution_evidence WHERE account=?", (account,)
            ).fetchall()
        values = []
        for (payload,) in rows:
            row = json.loads(payload)
            stamp = datetime.fromisoformat(row["ts"])
            if since is not None and stamp.date() < since:
                continue
            values.append(
                Execution(
                    order_ref=row["order_ref"],
                    execution_id=row["execution_id"],
                    symbol=row["symbol"],
                    quantity=dec(row["quantity"]),
                    price=dec(row["price"]),
                    commission=dec(row["commission"]),
                    ts=stamp,
                )
            )
        return sorted(values, key=lambda x: (x.ts, x.execution_id))

    def imported_ids(self) -> set[str]:
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as c:
            if not c.execute(
                "SELECT 1 FROM sqlite_master WHERE name='broker_execution_imports'"
            ).fetchone():
                return set()
            return {
                row[0] for row in c.execute("SELECT execution_id FROM broker_execution_imports")
            }

    def legacy_order_refs(self) -> set[str]:
        with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as c:
            if not c.execute(
                "SELECT 1 FROM sqlite_master WHERE name='broker_execution_imports'"
            ).fetchone():
                return {row[0] for row in c.execute("SELECT DISTINCT order_id FROM fills")}
            return {
                row[0]
                for row in c.execute(
                    "SELECT DISTINCT f.order_id FROM fills f LEFT JOIN broker_execution_imports i ON i.fill_id=f.id WHERE i.execution_id IS NULL"
                )
            }
