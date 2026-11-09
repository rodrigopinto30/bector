#!/usr/bin/env bash
# Runs the tools of the panel (panel/: Next.js, TypeScript, Tailwind and shadcn) in a Node
# container, as your own user, so Node is never needed on this computer.
set -euo pipefail

cd "$(dirname "$0")"

# The same image the Dockerfile builds the panel with.
NODE_IMAGE="node:26.13.0-trixie-slim"

usage() {
  cat <<'TXT'
Usage:
  ./panel.sh check    format, lint, types, tests and build, as CI runs them
  ./panel.sh quick    the same without the build, as the pre-commit hook runs them
  ./panel.sh format   rewrite the files of panel/ in the project style
  ./panel.sh audit    check the locked packages against the known vulnerabilities
  ./panel.sh dev      a panel that reloads on every change, at http://localhost:3000,
                      with the data of the running panel container (./start.sh first)
  ./panel.sh demo     real events, one at a time, on a demo panel at http://localhost:8766;
                      ./panel.sh demo clean removes it. Your own data is never touched
  ./panel.sh npm ...  any npm command, such as: ./panel.sh npm install --save-exact NAME

The packages come from panel/package-lock.json, with install scripts off. After changing
panel/, run ./stop.sh && ./start.sh so http://localhost:8765 serves the new build.
TXT
}

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

env_value() {
  grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}
uid="$(env_value HOST_UID)"
gid="$(env_value HOST_GID)"
uid="${uid:-$(id -u)}"
gid="${gid:-$(id -g)}"

in_node() {
  $DOCKER run --rm -u "$uid:$gid" -e HOME=/tmp/home -e NEXT_TELEMETRY_DISABLED=1 \
    -v "$PWD/panel:/panel" -w /panel "$@"
}

# Installs only when panel/node_modules is missing or older than the lock.
install() {
  if [ ! -f panel/node_modules/.package-lock.json ] ||
    [ panel/package-lock.json -nt panel/node_modules/.package-lock.json ]; then
    echo "[panel] Installing the locked packages..."
    in_node "$NODE_IMAGE" npm ci --ignore-scripts --no-audit --no-fund --loglevel=error
  fi
}

case "${1:-}" in
  check)
    install
    in_node "$NODE_IMAGE" npm run --silent check
    ;;
  quick)
    install
    in_node "$NODE_IMAGE" sh -c 'npm run --silent lint && npm run --silent typecheck && npm run --silent test'
    ;;
  format)
    install
    in_node "$NODE_IMAGE" npm run --silent format
    ;;
  audit)
    in_node "$NODE_IMAGE" npm audit
    ;;
  dev)
    install
    network="$($DOCKER inspect healer-panel \
      --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' 2>/dev/null || true)"
    if [ -z "$network" ]; then
      echo "[panel] The panel container is not running; start it with ./start.sh." >&2
      exit 1
    fi
    # A second panel server for the data, reachable only inside Docker's network. It also
    # answers to its container name, which the dev server sends; the panel at :8765 does not.
    api="healer-panel-dev-api"
    $DOCKER rm -f "$api" >/dev/null 2>&1 || true
    $DOCKER compose run --rm -d --no-deps --name "$api" panel \
      healer panel --host 0.0.0.0 --port 8765 --allow-host "$api" >/dev/null
    trap '$DOCKER stop "$api" >/dev/null 2>&1 || true' EXIT
    echo "[panel] Open http://localhost:3000; stop with Ctrl+C."
    in_node -it --network "$network" -p 127.0.0.1:3000:3000 -e "PANEL_API=http://$api:8765" \
      "$NODE_IMAGE" npx next dev --hostname 0.0.0.0 --port 3000
    ;;
  demo)
    shift
    exec scripts/panel-demo.sh "$@"
    ;;
  npm)
    shift
    in_node -it "$NODE_IMAGE" npm "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
