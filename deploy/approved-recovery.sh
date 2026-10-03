#!/usr/bin/env bash
set -Eeuo pipefail

: "${SILLAGE_ROOT:=/home/deploy/sillage}"
case "${1:-}" in
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
