# Paper deployment on a VPS

The supported operating model is one supervised process per trading day, not a daemon
inside Sillage. At 05:00 America/Toronto the job fetches the prior session's bars,
validates them, reconciles IBKR, imports fills, and submits any next-open orders. Every
failure is non-zero; systemd records it and an optional health-check URL receives a
failure ping.

## Broker constraint

Use IB Gateway paper on port 4002. IBKR supports automatic daily restart during the
week, but not permanently unattended authentication: log in through its GUI after the
weekend reset. Do not store IBKR credentials in this repository or expose port 4002 to
the internet. The API and Gateway should communicate over localhost.

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

## Weekly routine

1. Sunday: log into IB Gateway paper and confirm automatic restart is enabled.
2. Check that `broker-check` connects on port 4002.
3. Leave the timer alone during the week; investigate only an alert or failed unit.
4. Do not use the same IBKR username in another trading application while Gateway is
   expected to remain connected.
