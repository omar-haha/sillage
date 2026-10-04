#!/usr/bin/env python3
"""Run on the VPS as deploy; leave one harmless test object, no S3 deletion."""
import datetime as dt
from pathlib import Path
import subprocess
import tempfile

bucket = "omar-haha-vps-backups-753654067906-ca-central-1-an"
with tempfile.TemporaryDirectory(prefix="vps-backup-access-") as directory:
    source = Path(directory) / "source.txt"
    downloaded = Path(directory) / "downloaded.txt"
    source.write_text("VPS off-site backup access verification\n")
    key = f"test/{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S%fZ}.txt"
    for origin, target in [(str(source), f"s3://{bucket}/{key}"),
                           (f"s3://{bucket}/{key}", str(downloaded))]:
        subprocess.run(["aws", "s3", "cp", origin, target, "--only-show-errors"], check=True)
    if source.read_bytes() != downloaded.read_bytes():
        raise SystemExit("S3 verification mismatch")
    print(f"Verified upload/download; harmless object retained at s3://{bucket}/{key}")
