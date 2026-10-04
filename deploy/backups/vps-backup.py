#!/usr/bin/env python3
"""Daily logical backups. Secrets are never passed in argv or emitted in logs."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from urllib.parse import parse_qs, unquote, urlsplit

BUCKET = "omar-haha-vps-backups-753654067906-ca-central-1-an"
STATE = Path("/home/deploy/.local/state/vps-backup")
PROJECTS = {
    "rcca": {"env": "/opt/rcca/.env.production", "db": "SUPABASE_DB_URL",
             "files": ["/opt/rcca/.env.production", "/opt/rcca/Caddyfile",
                       "/opt/rcca/docker-compose.override.yml"]},
    "pizzaroma": {"env": "/home/deploy/pizza-roma/.env.production",
                  "db": "DATABASE_DIRECT_URL", "files": [
                      "/home/deploy/pizza-roma/.env.production"]},
    "sillage": {"sqlite": "/home/deploy/sillage/state/ibkr.db", "files": [
        "/home/deploy/.config/sillage/paper.env",
        "/home/deploy/sillage/deploy/ibgateway/.env",
        "/home/deploy/sillage/deploy/ibgateway/secrets/tws_password",
        "/home/deploy/sillage/deploy/ibgateway/secrets/vnc_password"]},
}


def log(message):
    line = f"{dt.datetime.now(dt.timezone.utc).isoformat()} {message}"
    print(line, flush=True)
    with (STATE / "backup.log").open("a") as stream:
        stream.write(line + "\n")


def load_env(path):
    values = {}
    if not Path(path).exists():
        return values
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if sep:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def command(argv, env=None):
    result = subprocess.run(argv, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=900)
    if result.returncode:
        # stderr can contain connection strings. Never forward it to logs.
        raise RuntimeError(f"{Path(argv[0]).name} failed (exit {result.returncode}); output suppressed")
    return result.stdout


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pg_environment(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise RuntimeError("Invalid PostgreSQL connection configuration")
    env = os.environ.copy()
    env.update(PGHOST=parsed.hostname, PGPORT=str(parsed.port or 5432),
               PGDATABASE=unquote(parsed.path.lstrip("/")),
               PGUSER=unquote(parsed.username or ""),
               PGPASSWORD=unquote(parsed.password or ""),
               PGCONNECT_TIMEOUT="30", PGOPTIONS="-c statement_timeout=600000")
    env["PGSSLMODE"] = parse_qs(parsed.query).get("sslmode", ["require"])[0]
    return env


def backup_project(name, config, now):
    stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
    with tempfile.TemporaryDirectory(prefix="vps-backup-", dir=STATE) as tmp:
        work = Path(tmp)
        contents = work / "contents"
        contents.mkdir(mode=0o700)
        manifest = {"project": name, "created_utc": now.isoformat(),
                    "format_version": 1, "files": []}
        if "db" in config:
            values = load_env(config["env"])
            values.update(load_env("/home/deploy/.config/vps-backup/databases.env"))
            url = values.get(config["db"])
            if not url:
                raise RuntimeError(f"Missing {config['db']}; no complete backup created")
            env = pg_environment(url)
            size = command(["psql", "-X", "-A", "-t", "-c",
                            "SELECT pg_database_size(current_database())"], env)
            manifest["database_bytes"] = int(size.strip())
            command(["pg_dump", "--format=custom", "--no-owner", "--no-acl",
                     "--file", str(contents / "database.dump")], env)
            command(["pg_restore", "--list", str(contents / "database.dump")])
        else:
            source = Path(config["sqlite"])
            if not source.is_file():
                raise RuntimeError("SQLite source missing")
            target = contents / "ibkr.db"
            with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30) as src:
                with sqlite3.connect(target) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("SQLite backup integrity check failed")
            manifest["database_bytes"] = target.stat().st_size
        for filename in config["files"]:
            source = Path(filename)
            if not source.is_file():
                raise RuntimeError(f"Required recovery file missing: {filename}")
            dest = contents / "files" / filename.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            dest.chmod(0o600)
            manifest["files"].append(filename)
        (contents / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        archive = work / f"{name}-{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(contents, arcname=".")
        digest = checksum(archive)
        tiers = ["daily"]
        if now.weekday() == 6:
            tiers.append("weekly")
        if now.day == 1:
            tiers.append("monthly")
        keys = []
        for tier in tiers:
            key = f"{name}/{tier}/{now:%Y/%m}/{archive.name}"
            command(["aws", "s3", "cp", str(archive), f"s3://{BUCKET}/{key}",
                     "--only-show-errors", "--sse", "AES256"])
            keys.append(key)
        # A backup is successful only after an actual download and hash check.
        downloaded = work / "verification.tar.gz"
        command(["aws", "s3", "cp", f"s3://{BUCKET}/{keys[0]}",
                 str(downloaded), "--only-show-errors"])
        if checksum(downloaded) != digest:
            raise RuntimeError("S3 round-trip checksum mismatch")
        status = dict(manifest, keys=keys, archive_bytes=archive.stat().st_size,
                      sha256=digest)
        status_path = STATE / f"{name}-success.json"
        temporary = status_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, indent=2) + "\n")
        temporary.replace(status_path)
        log(f"SUCCESS {name}: {status['archive_bytes']} bytes; {keys[0]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", choices=list(PROJECTS))
    args = parser.parse_args()
    os.umask(0o077)
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    with (STATE / "backup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("Another backup is running; refusing overlapping execution")
            return 1
        # Clean only our own uniquely named staging directories after a hard kill.
        for entry in STATE.iterdir():
            if (re.fullmatch(r"vps-backup-[a-z0-9_]{8}", entry.name)
                    and not entry.is_symlink() and entry.is_dir()
                    and entry.stat().st_uid == os.getuid()):
                shutil.rmtree(entry)
        # Rotate logs at a bounded size; backup archives never remain locally.
        logfile = STATE / "backup.log"
        if logfile.exists() and logfile.stat().st_size > 1_000_000:
            logfile.replace(STATE / "backup.log.1")
        failed = False
        for name, config in PROJECTS.items():
            if args.project and name != args.project:
                continue
            try:
                backup_project(name, config, dt.datetime.now(dt.timezone.utc))
            except Exception as exc:
                # Unknown exception messages may contain secrets; whitelist our own errors.
                detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
                log(f"FAILED {name}: {detail}")
                failed = True
        return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
