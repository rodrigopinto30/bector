#!/usr/bin/env bash
# Runs the tools of the desktop app (desktop/: Electron and TypeScript) in a Node container, as
# your own user, so Node is never needed on this computer. Only `run` starts the app itself,
# on your desktop, with the Electron program that `electron` downloads, after asking you.
set -euo pipefail

cd "$(dirname "$0")"

# The same image the panel is built with.
NODE_IMAGE="node:26.13.0-trixie-slim"

usage() {
  cat <<'TXT'
Usage:
  ./desktop.sh check     format, lint, types, tests and build, as CI runs them
  ./desktop.sh quick     the same without the build, as the pre-commit hook runs them
  ./desktop.sh format    rewrite the files of desktop/ in the project style
  ./desktop.sh audit     check the locked packages against the known vulnerabilities
  ./desktop.sh electron  download the Electron program (270 MB unpacked), after asking you
  ./desktop.sh run       build the app and open it on your desktop. Add --no-sandbox only if
                         Chromium's sandbox fails (Ubuntu 24.04 restricts it): it is weaker
  ./desktop.sh npm ...   any npm command, such as: ./desktop.sh npm install --save-exact NAME

The packages come from desktop/package-lock.json, with install scripts off, so nothing is
downloaded but the packages themselves.
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
  local tty=()
  if [ -t 0 ] && [ -t 1 ]; then
    tty=(-it)
  fi
  $DOCKER run --rm "${tty[@]}" -u "$uid:$gid" -e HOME=/tmp/home \
    -v "$PWD/desktop:/desktop" -w /desktop "$@"
}

# Installs only when desktop/node_modules is missing or older than the lock.
install() {
  if [ ! -f desktop/node_modules/.package-lock.json ] ||
    [ desktop/package-lock.json -nt desktop/node_modules/.package-lock.json ]; then
    echo "[desktop] Installing the locked packages..."
    in_node "$NODE_IMAGE" npm ci --ignore-scripts --no-audit --no-fund --loglevel=error
  fi
}

ELECTRON="desktop/node_modules/electron/dist/electron"

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
  electron)
    install
    if [ -x "$ELECTRON" ]; then
      echo "[desktop] The Electron program is already downloaded."
      exit 0
    fi
    cat <<'TXT'
This downloads the Electron program from the releases of github.com/electron, and checks it
against the checksums that Electron publishes. It takes about 270 MB once unpacked. It is the
only thing downloaded besides the locked packages. It is kept in desktop/node_modules, and
nothing is installed on the rest of your computer.
TXT
    read -r -p "Download it? [y/N] " answer
    if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
      echo "Nothing was downloaded."
      exit 1
    fi
    in_node "$NODE_IMAGE" node node_modules/electron/install.js
    ;;
  run)
    shift
    install
    in_node "$NODE_IMAGE" npm run --silent build
    if [ ! -x "$ELECTRON" ]; then
      echo "[desktop] The Electron program is not downloaded yet: run ./desktop.sh electron" >&2
      exit 1
    fi
    HEALER_HOME="$PWD" exec "$ELECTRON" "$@" desktop
    ;;
  npm)
    shift
    in_node "$NODE_IMAGE" npm "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
