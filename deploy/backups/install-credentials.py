#!/usr/bin/env python3
"""Transfer AWS keys over SSH stdin; never place secrets in command arguments."""
import argparse
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("secret_file", type=Path)
parser.add_argument("host")
args = parser.parse_args()
values = {}
for line in args.secret_file.read_text().splitlines():
    key, sep, value = line.partition("=")
    if sep and key.strip() in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}:
        values[key.strip()] = value.strip().strip("\"'")
if set(values) != {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}:
    raise SystemExit("Credential file does not contain the required key names")
payload = ("[default]\naws_access_key_id = " + values["AWS_ACCESS_KEY_ID"]
           + "\naws_secret_access_key = " + values["AWS_SECRET_ACCESS_KEY"] + "\n").encode()
remote = """import os,sys,pathlib,tempfile
p=pathlib.Path('/home/deploy/.aws');p.mkdir(mode=0o700,exist_ok=True);p.chmod(0o700)
target=p/'credentials'
if target.exists(): raise SystemExit('Existing credentials preserved; review before replacing')
fd,name=tempfile.mkstemp(dir=p)
try:
 os.fchmod(fd,0o600)
 with os.fdopen(fd,'wb') as f: f.write(sys.stdin.buffer.read())
 os.replace(name,target)
finally:
 if os.path.exists(name): os.unlink(name)
config=p/'config'
if config.exists(): raise SystemExit('Existing AWS config preserved; review region separately')
fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f: f.write('[default]\\nregion = ca-central-1\\noutput = json\\n')
print('AWS credential files installed; no credential values displayed')
"""
import shlex
result = subprocess.run(["ssh", "-o", "BatchMode=yes", args.host,
                         "python3 -c " + shlex.quote(remote)], input=payload,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if result.returncode:
    raise SystemExit("Credential installation failed; remote output suppressed for safety")
print("AWS credential files installed securely for deploy")
