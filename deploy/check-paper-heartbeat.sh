#!/usr/bin/env bash
# Run weekdays at 12:15 UTC, after the paper cycle's Healthchecks grace period.
set -Eeuo pipefail
: "${SILLAGE_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$SILLAGE_ROOT"
today=$(date -u +%Y-%m-%d)
if [[ $(date -u +%u) -gt 5 ]]; then exit 0; fi
if [[ -f state/paper-last-success && $(< state/paper-last-success) == "$today" ]]; then
    exit 0
fi
exec .venv/bin/python deploy/incident-report.py --root "$SILLAGE_ROOT" \
    --trigger "No successful paper cycle recorded for $today by 12:15 UTC; scheduled cycle may have failed or never started" --dispatch
