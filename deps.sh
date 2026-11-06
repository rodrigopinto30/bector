#!/usr/bin/env bash
# Keeps the dependencies locked and audited. requirements.lock pins every package the
# image installs, each with its hashes, and is generated from pyproject.toml.
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  cat <<'TXT'
Usage:
  ./deps.sh lock      pin what pyproject.toml needs, keeping the versions already locked
  ./deps.sh upgrade   move every package to the newest version pyproject.toml allows
  ./deps.sh audit     check the locked versions against the known vulnerabilities

After lock or upgrade, run ./stop.sh && ./start.sh to rebuild the images, and then the
tests. Audit ignores only the vulnerabilities listed, with a reason, in audit-exceptions.txt.
TXT
}

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

in_cli() {
  $DOCKER compose run --rm --no-deps -T -w /app cli "$@"
}

LOCK=(uv pip compile pyproject.toml --extra dev --universal --python-version 3.11
  --generate-hashes --custom-compile-command "./deps.sh lock" -o requirements.lock --quiet)

case "${1:-}" in
  lock)
    in_cli "${LOCK[@]}"
    echo "[deps] requirements.lock is up to date with pyproject.toml."
    ;;
  upgrade)
    in_cli "${LOCK[@]}" --upgrade
    echo "[deps] requirements.lock now has the newest allowed versions. Review the change with"
    echo "[deps] git diff requirements.lock, then rebuild with ./stop.sh && ./start.sh and run the tests."
    ;;
  audit)
    ignored=()
    while read -r id _; do
      ignored+=(--ignore-vuln "$id")
    done < <(grep -v -E '^\s*(#|$)' audit-exceptions.txt)
    in_cli pip-audit -r requirements.lock --require-hashes --disable-pip \
      --progress-spinner off "${ignored[@]}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
