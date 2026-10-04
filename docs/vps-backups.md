# Production off-site backups

Server: `148.113.253.107`. Backup user: `deploy` (no sudo).
Administrator: `ubuntu`, with `sudo`. AWS bucket:
`omar-haha-vps-backups-753654067906-ca-central-1-an` in `ca-central-1`.

## Inventory and coverage

| Project | Persistent data | Included | Recreated/excluded |
|---|---|---|---|
| RCCA (`/opt/rcca`) | Remote Supabase PostgreSQL; production env and local proxy override | Logical PostgreSQL dump; `.env.production`, `Caddyfile`, `docker-compose.override.yml`, private backup database configuration | Git source/migrations, image/build/dependencies; Upstash rate-limit counters |
| PizzaRoma (`/home/deploy/pizza-roma`) | Remote PostgreSQL database `postgres`, approximately 11.7 MB; production env | Logical PostgreSQL dump via `DATABASE_DIRECT_URL`; `.env.production` | Git source/migrations, image/build/dependencies |
| Sillage (`/home/deploy/sillage`) | SQLite trading journal, approximately 45 KB consistent snapshot; paper and Gateway secrets | SQLite online backup plus integrity check; `paper.env`, Gateway `.env`, `tws_password`, `vnc_password` | Git/config tracked in Git, cached market data, logs, Gateway sessions |

All three projects have passed logical backup and S3 round-trip verification.
RCCA's `SUPABASE_DB_URL` is installed in
`/home/deploy/.config/vps-backup/databases.env`, owned by deploy,
mode `0600`, using a secure editor/transfer (never paste the password in shell
commands or chat). The Supabase API service key is not the database password.
Use an actual direct or session-pooler PostgreSQL connection URL, not the
transaction pooler. The URL is held only in process environment for dump tools.

No local user-upload directories or object-storage usage were found in either
website. Re-inventory if uploads or storage services are added. There are no
local PostgreSQL containers or database volumes. Containers are `rcca-app`,
`pizza-roma-app-1`, shared `rcca-caddy`, and
`sillage-paper-gateway-ib-gateway-1`. Shared Caddy volumes `rcca_caddy_data` and
`rcca_caddy_config` contain regenerable certificates/config. Gateway volume
`sillage-paper-gateway_gateway-settings` contains regenerable login/session
settings. All three volume payloads are intentionally excluded.

Compose: RCCA `docker-compose.yml` + local override; PizzaRoma `compose.yaml`;
Sillage Gateway `deploy/ibgateway/compose.yaml`. Website containers have no
persistent mounts. Caddy binds RCCA's Caddyfile and PizzaRoma's shared proxy
templates. Gateway binds its two secret files. Fresh Gateway setup still needs
IBKR authentication/2FA; backed-up passwords cannot bypass it.

## Operation

Daily timer: **05:15 UTC plus up to 10 minutes of jitter**. Missed runs execute
after boot (`Persistent=true`). Sunday runs additionally upload a weekly copy;
first-of-month runs additionally upload a monthly copy. Archives have unique UTC
timestamps and use `PROJECT/{daily,weekly,monthly}/YYYY/MM/*.tar.gz`.

Each database is dumped without stopping services. SQLite uses its backup API,
not a live-file copy. Each archive contains `manifest.json`, `database.dump` or
`ibkr.db`, and recovery config under `files/` with original absolute paths encoded
as relative paths. Secrets are included deliberately for disaster recovery;
archives remain private, SSE-S3 encrypted. AWS credentials themselves are never
included. Temporary files are private and removed on success or ordinary failure.
All projects are attempted even if one fails; any failure yields nonzero status.
Success requires S3 upload, download, and SHA-256 verification. PostgreSQL archives
also pass `pg_restore --list`; SQLite passes `PRAGMA integrity_check`.

On VPS as deploy:

```bash
systemctl list-timers vps-backup.timer --all --no-pager
systemctl status vps-backup.service --no-pager
cat /home/deploy/.local/state/vps-backup/*-success.json
tail -n 40 /home/deploy/.local/state/vps-backup/backup.log
aws s3 ls s3://omar-haha-vps-backups-753654067906-ca-central-1-an/ --recursive --human-readable
```

Success JSON records last success, archive bytes, database bytes, object keys and
checksum per project. A failed later run does not overwrite last success: compare
timestamps with logs. To manually back up safely (same non-overlap lock as timer):

```bash
python3 /home/deploy/sillage/deploy/backups/vps-backup.py
# Or one project:
python3 /home/deploy/sillage/deploy/backups/vps-backup.py --project sillage
```

As administrator, service logs and a service-managed manual run:

```bash
sudo journalctl -u vps-backup.service -n 60 --no-pager
sudo systemctl start vps-backup.service
```

Do not grant deploy sudo. Installing units uses ubuntu's admin access; backup
execution does not. No backup-specific email alert is configured yet; use the
checks above. Existing Sillage trading Healthchecks is separate.

## Retention — approved, NOT applied

`deploy/backups/lifecycle-proposed.json` proposes daily points for 14 days,
weekly points for 56 days and monthly points for 180 days. Versioned expiration
first creates delete markers: noncurrent data expires after a further 30 days.
Expired delete markers and abandoned multipart uploads are cleaned up. Test
objects expire after 7 days. Lifecycle processing is asynchronous. See
[AWS lifecycle expiration documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html).

**Until applied by the bucket administrator, retention is not
active and storage grows.** Review existing lifecycle rules first; applying an
entire lifecycle document replaces the existing configuration. Do not execute
this with the restricted VPS writer or broaden its permissions.

## Disaster recovery (new infrastructure only)

Never run restore commands on current production. Provision a fresh Ubuntu
server, deploy user, SSH access, Docker/Compose, AWS CLI v2 and PostgreSQL client.
Install fresh restricted AWS read credentials privately. Clone each project's
GitHub SSH repository into its original path; use the repositories configured
in your GitHub account. PizzaRoma's current deployed directory is a copied
checkout without `.git`, so record its repository and deployed revision before
the old server is retired. Sillage example:

```bash
git clone git@github.com:omar-haha/sillage.git /home/deploy/sillage
```

Choose an exact object key from `aws s3 ls ... --recursive`. In a private restore
directory on the NEW server:

```bash
umask 077
mkdir -p /home/deploy/restore
cd /home/deploy/restore
aws s3 ls s3://omar-haha-vps-backups-753654067906-ca-central-1-an/ --recursive
# Replace PROJECT/TIER/YYYY/MM/FILE.tar.gz with the chosen exact key:
aws s3 cp s3://omar-haha-vps-backups-753654067906-ca-central-1-an/PROJECT/TIER/YYYY/MM/FILE.tar.gz backup.tar.gz
tar -tzf backup.tar.gz
mkdir recovered
tar -xzf backup.tar.gz -C recovered --no-same-owner
```

Inspect `manifest.json` (it has paths and size metadata, not secrets). Do not
display backed-up `.env` or password files. Download each project's archive to
a separate private directory.

### PostgreSQL restore: RCCA and PizzaRoma

Create a **new empty PostgreSQL/Supabase destination**, preserving the old service
if it still exists. For Supabase, use its direct/session SQL credentials, not
API keys. Recreate provider-managed roles/extensions through the provider;
ordinary users' pg_dump cannot necessarily capture managed/global objects.
Save the NEW database URL as `RESTORE_DATABASE_URL` in a private `restore.env`
file; never paste it into command arguments. From the archive's recovered directory:

```bash
python3 - <<'PY'
import importlib.util, pathlib, subprocess
p = pathlib.Path('/home/deploy/sillage/deploy/backups/vps-backup.py')
s = importlib.util.spec_from_file_location('backup', p)
b = importlib.util.module_from_spec(s); s.loader.exec_module(b)
env = b.pg_environment(b.load_env('/home/deploy/restore/restore.env')['RESTORE_DATABASE_URL'])
subprocess.run(['pg_restore', '--exit-on-error', '--single-transaction',
                '--no-owner', '--no-acl', '--dbname', env['PGDATABASE'],
                'database.dump'], env=env, check=True)
PY
```

This must target the NEW empty database only. RCCA must restore its tables,
functions and triggers; Supabase RLS, role grants/auth/storage infrastructure
need provider-specific validation because dumps omit ownership/ACLs. If a
provider-managed schema causes a restore conflict, stop and test a scoped restore
on the disposable destination rather than dropping production objects.

Copy config on the NEW server (from `recovered`), with necessary target
directories created first:

```bash
# RCCA: ubuntu must create /opt/rcca and assign it to deploy first.
install -m 600 files/opt/rcca/.env.production /opt/rcca/.env.production
install -m 600 files/opt/rcca/Caddyfile /opt/rcca/Caddyfile
install -m 600 files/opt/rcca/docker-compose.override.yml /opt/rcca/docker-compose.override.yml
# PizzaRoma:
install -m 600 files/home/deploy/pizza-roma/.env.production /home/deploy/pizza-roma/.env.production
```

Securely edit restored configuration to point to the NEW DB (and RCCA's NEW
Supabase project API URL/keys if that project changed). RCCA's shared proxy
configuration references PizzaRoma templates, so clone both sites and restore
both configurations before starting the shared Caddy stack. Bring up apps on the
NEW host only:

```bash
cd /home/deploy/pizza-roma
docker compose --env-file .env.production -f compose.yaml up -d --build
cd /opt/rcca
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.override.yml up -d --build
docker ps --format '{{.Names}} {{.Status}}'
```

Check both website home pages, health endpoints configured in Compose, staff/admin
login, and expected historical orders/reviews. Check shared proxy routes before
switching DNS. Avoid test orders that cause emails/payments unless using a test
environment. Do not point old and new apps at different databases with live
traffic until the cutover plan is settled.

### Sillage restore

Keep trading cron/timers disabled on the NEW host until reconciliation passes.
From its recovered directory:

```bash
install -d -m 700 /home/deploy/sillage/state /home/deploy/.config/sillage
install -d -m 700 /home/deploy/sillage/deploy/ibgateway/secrets
install -m 600 ibkr.db /home/deploy/sillage/state/ibkr.db
install -m 600 files/home/deploy/.config/sillage/paper.env /home/deploy/.config/sillage/paper.env
install -m 600 files/home/deploy/sillage/deploy/ibgateway/.env /home/deploy/sillage/deploy/ibgateway/.env
install -m 640 files/home/deploy/sillage/deploy/ibgateway/secrets/tws_password /home/deploy/sillage/deploy/ibgateway/secrets/tws_password
install -m 640 files/home/deploy/sillage/deploy/ibgateway/secrets/vnc_password /home/deploy/sillage/deploy/ibgateway/secrets/vnc_password
cd /home/deploy/sillage
python3 -m venv .venv
.venv/bin/pip install -e '.[ibkr]'
docker compose --env-file deploy/ibgateway/.env -f deploy/ibgateway/compose.yaml up -d
```

Before starting Gateway, securely update `SECRET_GID` in its restored `.env` to
the new server's `id -g deploy` so the container can read the group-readable
password mounts. Do not make passwords world-readable.

Follow `docs/ibkr.md` and the deployed paper configuration for Gateway
authentication. Validate the restored SQLite integrity, Gateway connection,
broker positions/pending orders against the journal, and latest processed
session **before** enabling trading. Existing historical journal/broker
disagreements survive a backup and still need their recovery procedure; a
restore is not permission to reset the journal or re-submit orders. Restore
cron schedules from deployment documentation only after checks succeed.

### Reinstall backups

On the new server, install AWS credentials privately as deploy. As ubuntu:

```bash
sudo install -d -o deploy -g deploy -m 700 /home/deploy/.local/state/vps-backup /home/deploy/.config/vps-backup
sudo install -m 644 /home/deploy/sillage/deploy/backups/vps-backup.service /etc/systemd/system/
sudo install -m 644 /home/deploy/sillage/deploy/backups/vps-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vps-backup.timer
```

## Limitations and security

Verified archive integrity/round-trip is not a full application restore drill.
Test restores on disposable infrastructure regularly. Logical databases are
individually consistent, not one simultaneous transaction across all projects.
No point-in-time/WAL capture: worst-case loss is roughly one day, longer after
failures. Configuration and database are read sequentially. The 90-minute service
budget and 512 MB cap should be reviewed as data grows.

Backups contain production secrets and customer data. AWS SSE-S3 protects
storage, but a compromised writer key can also read archives; client-side
encryption and separate read credentials are possible future improvements.
Versioning does not provide Object Lock: this IAM user cannot delete, but bucket
administrators still can. Credentials are not committed or included in archives.
TemporaryDirectory cleans ordinary failures and SIGTERM; a hard kill/power loss
can leave a private staging directory. The next run, after acquiring its lock,
removes only deploy-owned, non-symlink staging directories with the script's
exact temporary-name pattern.
