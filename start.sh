#!/usr/bin/env bash
# Starts the project: validates/installs Docker, prepares .env and brings up the stack.
set -euo pipefail

cd "$(dirname "$0")"

log() { echo "[start] $*"; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

install_docker() {
  log "Docker not found. Installing..."
  case "$(uname -s)" in
    Linux)
      command -v curl >/dev/null 2>&1 || { log "curl is required to install Docker."; exit 1; }
      curl -fsSL https://get.docker.com | $SUDO sh
      $SUDO systemctl enable --now docker 2>/dev/null || true
      if [ -n "$SUDO" ]; then
        $SUDO usermod -aG docker "$USER" || true
      fi
      ;;
    *)
      log "Automatic installation is only supported on Linux. Install Docker Desktop manually."
      exit 1
      ;;
  esac
}

if ! command -v docker >/dev/null 2>&1; then
  install_docker
fi

# Use sudo for docker if the current user cannot reach the daemon yet (e.g. just added to the group).
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  if $SUDO docker info >/dev/null 2>&1; then
    DOCKER="$SUDO docker"
  else
    log "Docker daemon is not running or not reachable."
    exit 1
  fi
fi

if ! $DOCKER compose version >/dev/null 2>&1; then
  log "Docker Compose plugin not found. Installing..."
  $SUDO apt-get update && $SUDO apt-get install -y docker-compose-plugin
fi

if [ ! -f .env ]; then
  cp .env.example .env
  log "Created .env from .env.example. Set ANTHROPIC_API_KEY in it."
fi

WORKSPACE_PATH="$(grep -E '^WORKSPACE_PATH=' .env | cut -d= -f2- || true)"
mkdir -p "${WORKSPACE_PATH:-./workspace}"

log "Building and starting services..."
$DOCKER compose up -d --build

log "Ready. Try: $DOCKER compose exec healer healer doctor"
