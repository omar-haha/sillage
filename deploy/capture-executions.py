"""Same-day paper execution evidence capture; cannot submit orders or run a strategy."""

import fcntl
import logging
import os
import re
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from sillage.core.calendar import TradingCalendar
from sillage.execution.ibkr import IBGatewayClient


def main():
    now = datetime.now(ZoneInfo("America/Toronto"))
    if not time(9, 30) <= now.time() <= time(16, 15) or not TradingCalendar().is_session(
        now.date()
    ):
        return
    root = Path(__file__).resolve().parent.parent
    journal = root / "state/ibkr.db"
    if not journal.is_file():
        raise ValueError("existing paper journal required")
    with (root / "state/paper.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Execution capture skipped: paper lock held")
            return
        logging.disable(logging.CRITICAL)
        client = IBGatewayClient(
            host="127.0.0.1",
            port=4002,
            client_id=17,
            readonly=True,
            request_timeout=20,
            execution_archive=journal,
            account=os.environ.get("SILLAGE_IB_ACCOUNT", ""),
        )
        stage = "connection"
        try:
            client.connect()
            stage = "paper-account validation"
            accounts = client._require().managedAccounts()
            if len(accounts) != 1 or not re.fullmatch(r"DU[T]?\d+", accounts[0]):
                raise ValueError("capture restricted to a single paper account")
            stage = "execution/commission capture"
            evidence = client.executions(since=now.date())
            print(
                f"{now.isoformat()}: retained {len(evidence)} confirmed same-day executions; no orders submitted"
            )
        except Exception as exc:
            print(f"{now.isoformat()}: capture failed at {stage} ({type(exc).__name__})")
            raise
        finally:
            client.disconnect()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Execution capture failed ({type(exc).__name__}); no trading attempted")
        raise SystemExit(1) from None
