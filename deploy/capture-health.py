"""Independent collector health; never changes the daily-cycle heartbeat."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx


def update(path: Path, success: bool, notify):
    state = json.loads(path.read_text()) if path.exists() else {}
    now = datetime.now(UTC).isoformat()
    if success:
        state.update(failures=0, last_success=now)
        if state.get("alerted"):
            state["recovery_pending"] = True
        state["alerted"] = False
    else:
        state["failures"] = state.get("failures", 0) + 1
        state["last_failure"] = now
    alert = not success and state["failures"] >= 3 and not state.get("alerted")
    if alert:
        state["alerted"] = True
    if state.get("recovery_pending") and success:
        try:
            notify()
        except Exception as exc:
            print(f"Collector recovery email pending ({type(exc).__name__})", file=sys.stderr)
        else:
            state.pop("recovery_pending", None)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)
    return "alert" if alert else "ok"


def notify():
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['SILLAGE_RESEND_API_KEY']}"},
        json={
            "from": os.environ.get("SILLAGE_REPORT_FROM", "onboarding@resend.dev"),
            "to": [os.environ["SILLAGE_REPORT_TO"]],
            "subject": "Sillage execution collector recovered",
            "text": "Read-only execution collection is succeeding again. This is not a trading-cycle success signal and does not approve recovery or close a trading incident. Check the daily-cycle heartbeat separately.",
        },
        timeout=15,
    )
    response.raise_for_status()


if __name__ == "__main__":
    print(update(Path("state/execution-health.json"), sys.argv[1] == "0", notify))
