#!/usr/bin/env bash
# Must be called while holding paper.lock. Never submits or cancels orders.
set -Eeuo pipefail
: "${SILLAGE_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
: "${SILLAGE_IB_HOST:=127.0.0.1}"
: "${SILLAGE_IB_PORT:=4002}"
: "${SILLAGE_IB_CLIENT_ID:=17}"
cd "$SILLAGE_ROOT"
if [[ "$SILLAGE_IB_HOST" != 127.0.0.1 || "$SILLAGE_IB_PORT" != 4002 ]]; then
    echo "Automatic Gateway recovery is restricted to the local paper endpoint." >&2
    exit 1
fi
check() {
    timeout --kill-after=5s 45s .venv/bin/sillage broker-check \
        --host "$SILLAGE_IB_HOST" --port "$SILLAGE_IB_PORT" \
        --client-id "$SILLAGE_IB_CLIENT_ID"
}
if check; then exit 0; fi
echo "Gateway evidence unavailable; retrying readiness once." >&2
if check; then exit 0; fi
echo "Performing one bounded Gateway-only cold restart; IB Key approval may be needed." >&2
# This restart happens BEFORE the trading cycle, never after ambiguous submission.
# Do not send a trading healthcheck failure for intermediate readiness failures.
SILLAGE_HEALTHCHECK_URL= timeout --kill-after=10s 300s bash deploy/restart-gateway.sh
check
