"""Install one deploy-owned capture cron entry; preserve unrelated projects/jobs."""

import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent.parent
    if root != Path("/home/deploy/sillage") or os.geteuid() == 0:
        raise ValueError("installer must run as deploy in its production checkout")
    old = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    entry = (
        "*/5 * * * * set -a; . /home/deploy/.config/sillage/paper.env; set +a; "
        "bash /home/deploy/sillage/deploy/capture-executions.sh "
        ">> /home/deploy/sillage/state/execution-capture.log 2>&1"
    )
    matches = [line for line in old.splitlines() if "/deploy/capture-executions.sh" in line]
    if matches:
        if matches == [entry]:
            print("Same-day capture schedule already installed")
            return
        raise ValueError("existing different capture job requires review")
    directory = root / "state/deployment" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, mode=0o700)
    (directory / "crontab.before").write_text(old)
    with (
        sqlite3.connect(root / "state/ibkr.db") as source,
        sqlite3.connect(directory / "ibkr.before.db") as target,
    ):
        source.backup(target)
    new = old.rstrip() + "\n" + entry + "\n"
    subprocess.run(["crontab", "-"], input=new, text=True, check=True)
    verified = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    if verified != new:
        raise ValueError("cron verification failed; preserved prior crontab for review")
    print(
        "Capture scheduled every five minutes; Python guard restricts to XNYS 09:30-16:15 Toronto"
    )
    print(
        "Prior crontab and online journal snapshot retained privately; unrelated entries preserved"
    )


if __name__ == "__main__":
    main()
