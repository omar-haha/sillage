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

cd "$SILLAGE_ROOT"
# Reporting is deliberately read-only. The weekday broker cycle owns all trading;
# running it here used to let a Saturday email retry ambiguous Friday orders.
.venv/bin/python deploy/weekly-report.py \
    --journal state/ibkr.db \
    --cash "${SILLAGE_CASH:-25000}"
