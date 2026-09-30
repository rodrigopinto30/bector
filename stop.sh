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
  $DOCKER compose down -v
else
  echo "[stop] Stopping services..."
  $DOCKER compose down
fi
