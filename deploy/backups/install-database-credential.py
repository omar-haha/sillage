#!/usr/bin/env python3
"""Install RCCA's SQL connection from a local secret file over encrypted stdin."""
import argparse
from pathlib import Path
import shlex
import subprocess
from urllib.parse import urlsplit

parser = argparse.ArgumentParser()
parser.add_argument("secret_file", type=Path)
parser.add_argument("host")
args = parser.parse_args()
values = {}
for line in args.secret_file.read_text().splitlines():
    key, sep, value = line.partition("=")
    if sep and key.strip() == "DB_URL":
        values[key.strip()] = value.strip().strip("\"'")
url = values.get("DB_URL", "")
try:
    parsed = urlsplit(url)
    valid = parsed.scheme in {"postgres", "postgresql"} and bool(parsed.hostname)
except ValueError:
    valid = False
if not valid:
    raise SystemExit("DB_URL must contain a PostgreSQL connection URL; value not displayed")
remote = """import os,sys,pathlib,tempfile
os.umask(0o077)
p=pathlib.Path('/home/deploy/.config/vps-backup');p.mkdir(mode=0o700,parents=True,exist_ok=True);p.chmod(0o700)
target=p/'databases.env'
if target.exists(): raise SystemExit('Existing database credentials preserved')
fd,name=tempfile.mkstemp(dir=p)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'wb') as f: f.write(sys.stdin.buffer.read())
 os.replace(name,target)
finally:
 if os.path.exists(name): os.unlink(name)
"""
result = subprocess.run(["ssh", "-o", "BatchMode=yes", args.host,
                         "python3 -c " + shlex.quote(remote)],
                        input=("SUPABASE_DB_URL=" + url + "\n").encode(),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if result.returncode:
    raise SystemExit("Database credential installation failed; output suppressed for safety")
print("RCCA database credential installed privately for deploy")
