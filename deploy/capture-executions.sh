#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Serialize the whole monitor, including retries and health-state updates.
exec 9>state/execution-monitor.lock
flock -n 9 || exit 0
# No cold restart, order placement, success heartbeat or journal fill inference.
status=1
for attempt in 1 2; do
    if timeout --kill-after=5s 75s .venv/bin/python deploy/capture-executions.py; then
        status=0
        break
    else
        status=$?
    fi
    [[ "$status" != 10 ]] || exit 0 # outside session or paper lock held: not evidence of health
    [[ "$status" != 2 ]] || break # validation/evidence conflict requires review, not retries
    # Only transient/operational errors are retried; collector remains read-only.
    [[ "$attempt" = 2 ]] || sleep 3
done
decision=$(.venv/bin/python deploy/capture-health.py "$status")
if [[ "$decision" = alert ]]; then
    timeout 90s .venv/bin/python deploy/incident-report.py --root "$PWD" \
        --trigger "execution collector failed on three consecutive checks; daily trading health is separate" --dispatch >>state/incident-report.log 2>&1 || true
fi
exit "$status"
