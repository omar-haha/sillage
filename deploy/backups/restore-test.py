#!/usr/bin/env python3
"""Restore S3 archives into disposable isolated PostgreSQL and SQLite databases."""
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import tarfile
import tempfile
import time
import uuid

spec = importlib.util.spec_from_file_location("backup", Path(__file__).with_name("vps-backup.py"))
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def run(argv, data=None, check=True):
    result = subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=300)
    if check and result.returncode:
        detail = ""
        if "psql" in argv:
            # These queries contain only test DDL and aggregate counts, never user records.
            for classification in ("connection refused", "No such file or directory",
                                   "cannot run inside a transaction block", "permission denied",
                                   "syntax error", "does not exist", "could not translate host name"):
                if classification in result.stderr.decode(errors="replace"):
                    detail = ": " + classification
                    break
        raise RuntimeError(f"{Path(argv[0]).name} operation {argv[1]} failed{detail}; output suppressed")
    return result


def sql(container, database, query):
    return run(["docker", "exec", "-i", container, "psql", "-X", "-A", "-t",
                "-v", "ON_ERROR_STOP=1", "-h", "/tmp", "-U", "postgres", "-d", database],
               query.encode()).stdout.decode().strip()


def main():
    os.umask(0o077)
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    container = "sillage-restore-test-" + uuid.uuid4().hex[:12]
    started = False
    report = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "scope": "database restore, file integrity; not full application cutover",
              "projects": {}}
    try:
        # No host ports, network access, persistent volumes, or production credentials.
        run(["docker", "run", "-d", "--name", container, "--network", "none",
             "--memory", "512m", "--cpus", "0.5", "--pids-limit", "256",
             "--tmpfs", "/tmp:rw,size=512m,mode=1777", "--user", "postgres",
             "--entrypoint", "/bin/sh", "public.ecr.aws/supabase/postgres:17.6.1.167",
             "-c", "initdb -D /tmp/restore-db -A trust -U postgres >/dev/null && exec postgres -D /tmp/restore-db -c shared_preload_libraries='' -c unix_socket_directories=/tmp"])
        started = True
        for attempt in range(60):
            if run(["docker", "exec", container, "pg_isready", "-h", "/tmp", "-U", "postgres"], check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        with tempfile.TemporaryDirectory(prefix="vps-restore-test-") as directory:
            root = Path(directory)
            for name in backup.PROJECTS:
                status = json.loads((backup.STATE / f"{name}-success.json").read_text())
                archive = root / f"{name}.tar.gz"
                key = status["keys"][0]
                run(["aws", "s3", "cp", f"s3://{backup.BUCKET}/{key}", str(archive), "--only-show-errors"])
                if backup.checksum(archive) != status["sha256"]:
                    raise RuntimeError("Downloaded archive checksum mismatch")
                recovered = root / name
                recovered.mkdir(mode=0o700)
                with tarfile.open(archive) as bundle:
                    if any(not (member.isfile() or member.isdir()) for member in bundle.getmembers()):
                        raise RuntimeError("Unexpected link or special file in archive")
                    bundle.extractall(recovered, filter="data")
                manifest = json.loads((recovered / "manifest.json").read_text())
                if manifest["project"] != name:
                    raise RuntimeError("Archive project mismatch")
                for filename in manifest["files"]:
                    if not (recovered / "files" / filename.lstrip("/")).is_file():
                        raise RuntimeError("Missing recovery configuration file")
                result = {"key": key, "checksum_verified": True,
                          "recovery_files": len(manifest["files"])}
                if name == "sillage":
                    with sqlite3.connect(recovered / "ibkr.db") as connection:
                        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise RuntimeError("Restored SQLite integrity failed")
                        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                        result["tables"] = len(tables)
                        result["rows"] = sum(connection.execute('SELECT count(*) FROM "' + table[0].replace('"', '""') + '"').fetchone()[0] for table in tables)
                    result["restore"] = "passed"
                else:
                    database = "restore_" + name
                    print(f"{name}: creating disposable database", flush=True)
                    sql(container, "postgres", f'CREATE DATABASE "{database}";')
                    dump = recovered / "database.dump"
                    # Restore client is on host; connects via docker exec Unix socket only.
                    run(["docker", "exec", "-i", container, "/bin/sh", "-c",
                         f"umask 077; cat > /tmp/{name}.dump"], data=dump.read_bytes())
                    print(f"{name}: archive copied into disposable container", flush=True)
                    created_roles = []
                    for attempt in range(20):
                        restored = run(["docker", "exec", container, "pg_restore", "--exit-on-error",
                                        "--no-owner", "--no-acl", "-h", "/tmp", "-U", "postgres", "-d", database,
                                        f"/tmp/{name}.dump"], check=False)
                        if not restored.returncode:
                            break
                        errors = restored.stderr.decode(errors="replace")
                        missing_roles = re.findall(r'role "([a-zA-Z0-9_-]+)" does not exist', errors)
                        if not missing_roles:
                            break
                        for role in set(missing_roles):
                            sql(container, "postgres", f'CREATE ROLE "{role}" NOLOGIN;')
                            created_roles.append(role)
                        # Only this script's database in its private container is recreated.
                        sql(container, "postgres", f'DROP DATABASE "{database}"; CREATE DATABASE "{database}";')
                    result["test_roles_created"] = created_roles
                    if restored.returncode:
                        # Record error classifications only; never log SQL/data/error text.
                        errors = restored.stderr.decode(errors="replace")
                        result["restore"] = "failed"
                        result["missing_extensions"] = re.findall(r'extension "([a-zA-Z0-9_-]+)" is not available', errors)
                        result["missing_roles"] = re.findall(r'role "([a-zA-Z0-9_-]+)" does not exist', errors)
                        result["error_output_suppressed"] = True
                    else:
                        result["restore"] = "passed"
                        result["tables"] = int(sql(container, database, "SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');"))
                        result["rows"] = int(sql(container, database, "SELECT coalesce(sum((xpath('/row/n/text()',query_to_xml(format('SELECT count(*) AS n FROM %I.%I',schemaname,tablename),false,true,'')))[1]::text::bigint),0) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema');"))
                report["projects"][name] = result
                print(json.dumps({"project": name, **result}), flush=True)
    finally:
        if started:
            run(["docker", "rm", "-f", container])
        backup.STATE.mkdir(parents=True, exist_ok=True)
        path = backup.STATE / "restore-test.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        path.chmod(0o600)
    return 0 if len(report["projects"]) == 3 and all(item["restore"] == "passed" for item in report["projects"].values()) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Restore test failed: " + (str(exc) if type(exc) is RuntimeError else type(exc).__name__))
        raise SystemExit(1)
