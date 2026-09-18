#!/usr/bin/env bash
set -Eeuo pipefail

: "${SILLAGE_ROOT:=/opt/sillage}"
: "${SILLAGE_IB_HOST:=127.0.0.1}"
: "${SILLAGE_IB_PORT:=4002}"
: "${SILLAGE_IB_CLIENT_ID:=17}"
: "${SILLAGE_CASH:=25000}"
: "${SILLAGE_STRATEGY:=balanced}"
: "${SILLAGE_UNIVERSE:=core}"

healthcheck() {
    if [[ -n "${SILLAGE_HEALTHCHECK_URL:-}" ]]; then
        curl --fail --silent --show-error --max-time 10 "$1" >/dev/null || true
    fi
}

failed() {
    status=$?
    healthcheck "${SILLAGE_HEALTHCHECK_URL:-}/fail"
    exit "$status"
}
trap failed ERR

cd "$SILLAGE_ROOT"
mkdir -p state/backups
exec 9>state/paper.lock
flock --nonblock 9

.venv/bin/sillage data sync --universe "$SILLAGE_UNIVERSE" --root data

.venv/bin/sillage data check --universe "$SILLAGE_UNIVERSE" --root data

# A persistent timer may fire late after a reboot. Never let a missed pre-open job turn
# today's opening order into an ambiguously queued order for a later auction.
toronto_time=$(TZ=America/Toronto date +%H%M)
if ((10#$toronto_time >= 830)); then
    echo "refusing broker cycle after 08:30 America/Toronto" >&2
    exit 6
fi

.venv/bin/sillage live run-once \
    --strategy "$SILLAGE_STRATEGY" \
    --universe "$SILLAGE_UNIVERSE" \
    --root data \
    --journal state/ibkr.db \
    --cash "$SILLAGE_CASH" \
    --broker ibkr \
    --host "$SILLAGE_IB_HOST" \
    --port "$SILLAGE_IB_PORT" \
    --client-id "$SILLAGE_IB_CLIENT_ID"

backup="state/backups/ibkr-$(date -u +%Y%m%dT%H%M%SZ).db"
cp --reflink=auto state/ibkr.db "$backup"
find state/backups -type f -name 'ibkr-*.db' -mtime +35 -delete
healthcheck "${SILLAGE_HEALTHCHECK_URL:-}"
