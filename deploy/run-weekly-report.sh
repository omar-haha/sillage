#!/usr/bin/env bash
set -Eeuo pipefail

: "${SILLAGE_ROOT:=/opt/sillage}"

failed() {
    status=$?
    if [[ -n "${SILLAGE_HEALTHCHECK_URL:-}" ]]; then
        curl --fail --silent --show-error --max-time 10 \
            "${SILLAGE_HEALTHCHECK_URL}/fail" >/dev/null || true
    fi
    exit "$status"
}
trap failed ERR

# Process Friday's close and reconcile the broker before reporting. Suppress the normal
# success heartbeat because Healthchecks expects it only Monday-Friday.
SILLAGE_HEALTHCHECK_URL= "$SILLAGE_ROOT/deploy/run-paper.sh"

cd "$SILLAGE_ROOT"
.venv/bin/python deploy/weekly-report.py \
    --journal state/ibkr.db \
    --cash "${SILLAGE_CASH:-25000}"
