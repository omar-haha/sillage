#!/usr/bin/env bash
set -Eeuo pipefail

: "${SILLAGE_ROOT:=/home/deploy/sillage}"
set -a
. /home/deploy/.config/sillage/paper.env
set +a
export SILLAGE_ROOT
case "${1:-}" in
  check-gateway)
    exec "$SILLAGE_ROOT/.venv/bin/sillage" broker-check --host "${SILLAGE_IB_HOST:-127.0.0.1}" --port "${SILLAGE_IB_PORT:-4002}" --client-id 92
    ;;
  restart-gateway)
    exec "$SILLAGE_ROOT/deploy/restart-gateway.sh"
    ;;
  rerun-cycle)
    # run-paper.sh retains the pre-08:30 Toronto cutoff, lock, reconciliation and timeout.
    exec "$SILLAGE_ROOT/deploy/run-paper.sh"
    ;;
  *)
    echo "unknown recovery action" >&2
    exit 64
    ;;
esac
