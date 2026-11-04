#!/usr/bin/env bash
# Stops the project. Pass --clean to also remove volumes (Chroma and Mosquitto data).
set -euo pipefail

cd "$(dirname "$0")"

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

if [ "${1:-}" = "--clean" ]; then
  echo "[stop] Stopping services and removing volumes..."
  $DOCKER compose --profile team down -v --remove-orphans
else
  echo "[stop] Stopping services..."
  $DOCKER compose --profile team down --remove-orphans
fi

# The copies volume lives in memory and holds nothing worth keeping. Docker keeps an
# existing volume as it was created, so removing it lets a new SANDBOX_DISK apply.
$DOCKER volume rm healer-sandbox-work >/dev/null 2>&1 || true
