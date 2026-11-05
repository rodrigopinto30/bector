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

# The containers run as the developer, so what they write to the project is the developer's.
if ! grep -qE '^HOST_UID=' .env; then
  {
    echo ""
    echo "# The user the containers run as (your own), created by start.sh"
    echo "HOST_UID=${SUDO_UID:-$(id -u)}"
    echo "HOST_GID=${SUDO_GID:-$(id -g)}"
  } >> .env
  log "Created HOST_UID and HOST_GID in .env."
fi
HOST_UID="$(grep -E '^HOST_UID=' .env | tail -n 1 | cut -d= -f2-)"
HOST_GID="$(grep -E '^HOST_GID=' .env | tail -n 1 | cut -d= -f2-)"
export HOST_UID HOST_GID
if [ "$HOST_UID" = "0" ]; then
  log "Warning: HOST_UID is 0, so the containers will run as root."
fi

log "Preparing the broker credentials of this machine..."
./broker.sh local

log "Building the images..."
$DOCKER compose build

# Data volumes and tool caches that earlier versions, running as root, left behind.
log "Giving this user the data volumes and caches..."
$DOCKER compose run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add DAC_READ_SEARCH \
  -e HOST_UID -e HOST_GID --entrypoint /bin/sh cli -c '
    for path in /data/chroma /data/healer /home/healer/.cache/chroma \
      /app/.mypy_cache /app/.ruff_cache /app/.pytest_cache /app/.coverage \
      /workspace/.pytest_cache /workspace/.ruff_cache; do
      [ -e "$path" ] && find "$path" ! -user "$HOST_UID" -exec chown -h "$HOST_UID:$HOST_GID" {} +
    done
    true'

log "Starting services..."
$DOCKER compose up -d

log "Ready. Try: $DOCKER compose exec cli healer doctor"
