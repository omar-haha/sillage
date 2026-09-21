#!/usr/bin/env bash
set -Eeuo pipefail

: "${SILLAGE_ROOT:=/opt/sillage}"
: "${SILLAGE_IB_HOST:=127.0.0.1}"
: "${SILLAGE_IB_PORT:=4002}"
: "${SILLAGE_IB_CLIENT_ID:=17}"

failed() {
    status=$?
    if [[ -n "${SILLAGE_HEALTHCHECK_URL:-}" ]]; then
        curl --fail --silent --show-error --max-time 10 \
            "${SILLAGE_HEALTHCHECK_URL}/fail" >/dev/null || true
    fi
    exit "$status"
}
trap failed ERR

cd "$SILLAGE_ROOT/deploy/ibgateway"

# IBKR's weekday restart token is invalid across the weekend reset. Remove it so
# Sunday performs a credential login rather than replaying a stale session.
docker compose exec -T ib-gateway \
    find /home/ibgateway/Jts -type f -name autorestart -delete
docker compose restart ib-gateway

for _ in $(seq 1 18); do
    if "$SILLAGE_ROOT/.venv/bin/sillage" broker-check \
        --host "$SILLAGE_IB_HOST" \
        --port "$SILLAGE_IB_PORT" \
        --client-id "$SILLAGE_IB_CLIENT_ID"; then
        exit 0
    fi
    sleep 10
done

echo "IB Gateway did not become API-ready after a full restart" >&2
exit 1
