#!/usr/bin/env bash
set -Eeuo pipefail

case "${SSH_ORIGINAL_COMMAND:-}" in
  "/home/deploy/sillage/deploy/approved-recovery.sh restart-gateway")
    exec /home/deploy/sillage/deploy/approved-recovery.sh restart-gateway
    ;;
  "/home/deploy/sillage/deploy/approved-recovery.sh rerun-cycle")
    exec /home/deploy/sillage/deploy/approved-recovery.sh rerun-cycle
    ;;
  *)
    echo "command not allowed" >&2
    exit 126
    ;;
esac
