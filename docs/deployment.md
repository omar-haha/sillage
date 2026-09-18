# Paper deployment on a VPS

The supported operating model is one supervised process per trading day, not a daemon
inside Sillage. At 05:00 America/Toronto the job fetches the prior session's bars,
validates them, reconciles IBKR, imports fills, and submits any next-open orders. Every
failure is non-zero; systemd records it and an optional health-check URL receives a
failure ping.

## Broker constraint

Use IB Gateway paper on port 4002. IBKR supports automatic daily restart during the
week, but not permanently unattended authentication. The supplied optional Docker
deployment uses the community-maintained `gnzsnz/ib-gateway` image and IBC to drive the
GUI login; this is useful for paper testing but is outside IBKR's officially supported
headless model. It keeps passwords in mode-0600 Docker secret files rather than Compose
environment variables. Do not expose ports 4002 or 5900 to the internet: both supplied
port mappings bind to VPS localhost and should be reached only through SSH tunnels.

## Install layout

- `/opt/sillage`: a clone of this repository, owned by the `sillage` service account.
- `/etc/sillage/paper.env`: non-secret runtime configuration copied from
  `deploy/paper.env.example` and mode `0640`.
- `/etc/systemd/system/sillage-paper.{service,timer}`: copies of the supplied units.
- `state/ibkr.db`: the existing paper journal copied securely from the laptop.

Create the virtual environment with `uv sync --extra ibkr`. Confirm a read-only
connection with `uv run sillage broker-check --port 4002` before enabling the timer.
Then run one supervised cycle by hand:

```bash
sudo systemctl start sillage-paper.service
sudo journalctl -u sillage-paper.service -n 100 --no-pager
```

Only after that succeeds:

```bash
sudo systemctl enable --now sillage-paper.timer
systemctl list-timers sillage-paper.timer
```

The timer is persistent, so a job missed while the VPS was rebooting runs after startup.
The wrapper refuses to contact the broker at or after 08:30 Toronto time, preventing a
late catch-up from ambiguously queuing an opening order for the wrong session. Sillage
itself still refuses stale data, unexplained positions, unacknowledged orders, and
drawdown-limit breaches. Backups are retained locally for 35 days; copy them to a separate
host or object store before treating the VPS as durable.

## Optional containerized Gateway

Copy `deploy/ibgateway/.env.example` to `.env`, create the two files
`secrets/tws_password` and `secrets/vnc_password`, and set all three files to mode 0600.
Gateway settings persist in a Docker-managed volume initialized from the image. The
Compose project is deliberately fixed to paper mode. Start it with:

```bash
cd deploy/ibgateway
docker compose pull
docker compose up -d
docker compose logs --tail=100
```

To inspect the GUI, create an SSH tunnel from the operator laptop and connect a local
VNC client to `localhost:5900`:

```bash
ssh -L 5900:127.0.0.1:5900 deploy@your-vps
```

Second-factor approval is still controlled by IBKR and may require the registered mobile
device. Never reuse this Compose file for live trading without a separate security and
operational review.

## Weekly routine

1. Sunday: log into IB Gateway paper and confirm automatic restart is enabled.
2. Check that `broker-check` connects on port 4002.
3. Leave the timer alone during the week; investigate only an alert or failed unit.
4. Do not use the same IBKR username in another trading application while Gateway is
   expected to remain connected.
