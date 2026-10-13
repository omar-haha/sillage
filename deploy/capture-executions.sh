#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# No cold restart, order placement, success heartbeat or journal fill inference.
if ! timeout --kill-after=5s 75s .venv/bin/python deploy/capture-executions.py; then
    if [[ -n "${SILLAGE_HEALTHCHECK_URL:-}" ]]; then
        curl --fail --silent --show-error --max-time 10 "${SILLAGE_HEALTHCHECK_URL}/fail" >/dev/null || true
    fi
    timeout 90s .venv/bin/python deploy/incident-report.py --root "$PWD" \
        --trigger "same-day execution capture failed" --dispatch >>state/incident-report.log 2>&1 || true
    exit 1
fi
