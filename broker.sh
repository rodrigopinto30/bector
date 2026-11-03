#!/usr/bin/env bash
# Manages who may use the bundled MQTT broker: one user per node, limited to the team topic.
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  cat <<'TXT'
Usage:
  ./broker.sh add NODE      let NODE connect, or give it a new password, and print the lines for its .env
  ./broker.sh remove NODE   revoke NODE; it is disconnected at once
  ./broker.sh list          list the nodes that may connect
  ./broker.sh local         let this machine use its own broker (start.sh runs it)

NODE is the NODE_ID of the machine: letters, digits, '.', '_' or '-', at most 64 characters.
Changes apply at once, without disconnecting the other nodes.
TXT
}

log() { echo "[broker] $*"; }

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

env_value() {
  if [ -f .env ]; then
    sed -n "s/^$1=//p" .env | tail -n 1
  fi
  return 0
}

TOPIC="$(env_value MQTT_TOPIC)"
TOPIC="${TOPIC:-healer/solutions/v1}"
if ! [[ "$TOPIC" =~ ^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$ ]]; then
  log "MQTT_TOPIC must be plain topic levels, without wildcards: $TOPIC"
  exit 2
fi
export TOPIC

check_node() {
  if ! [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]]; then
    log "invalid node name: '${1:-}'"
    usage >&2
    exit 2
  fi
}

new_password() {
  od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

# Runs a script in a throwaway broker container that shares the security volume, then
# rewrites the ACL from the list of users. The broker reads these files as the mosquitto
# user (1883) and warns unless they belong to it; mosquitto_passwd's advice to give them to
# root would stop the broker from reading them, so its warnings about that are dropped.
security() {
  $DOCKER compose run --rm --no-deps -T --entrypoint /bin/sh \
    -e NODE -e PASSWORD -e TOPIC mosquitto -c "
      set -e
      mkdir -p /mosquitto/security
      cd /mosquitto/security
      touch passwd acl
      $1
      cut -d: -f1 passwd | while read -r user; do
        [ -n \"\$user\" ] && printf 'user %s\ntopic readwrite %s/#\n\n' \"\$user\" \"\$TOPIC\"
      done > acl
      chown 1883:1883 . passwd acl
      chmod 700 .
      chmod 600 passwd acl
    "
}

# A reload applies the change without dropping the other nodes, and disconnects a node
# that was removed.
apply() {
  if [ -n "$($DOCKER compose ps -q --status running mosquitto)" ]; then
    $DOCKER compose kill -s HUP mosquitto >/dev/null 2>&1
  fi
}

set_password() {
  export NODE="$1" PASSWORD="$2"
  security 'mosquitto_passwd -b passwd "$NODE" "$PASSWORD" 2>&1 >/dev/null | grep -v -E "^Warning: File|^To fix this" >&2 || true'
  unset PASSWORD
  apply
}

case "${1:-}" in
  add)
    check_node "${2:-}"
    password="$(new_password)"
    set_password "$2" "$password"
    port="$(env_value BROKER_PORT)"
    log "$2 may now connect. Add these lines to the .env of that machine:"
    echo
    echo "MQTT_HOST=<address of this machine>"
    echo "MQTT_PORT=${port:-1883}"
    echo "MQTT_USERNAME=$2"
    echo "MQTT_PASSWORD=$password"
    echo "NODE_ID=$2"
    echo
    if [ "$(env_value BROKER_BIND)" != "0.0.0.0" ]; then
      log "This broker only accepts connections from this machine. To open it to a trusted"
      log "network, set BROKER_BIND=0.0.0.0 in .env and run ./stop.sh && ./start.sh."
    fi
    ;;
  remove)
    check_node "${2:-}"
    export NODE="$2"
    security 'grep -q "^$NODE:" passwd || { echo "[broker] $NODE is not a node of this broker" >&2; exit 3; }
      mosquitto_passwd -D passwd "$NODE" 2>&1 >/dev/null | grep -v -E "^Warning: File|^To fix this" >&2 || true'
    apply
    log "$2 can no longer connect."
    ;;
  list)
    $DOCKER compose run --rm --no-deps -T --entrypoint /bin/sh mosquitto -c \
      'cut -d: -f1 /mosquitto/security/passwd 2>/dev/null || true'
    ;;
  local)
    host="$(env_value MQTT_HOST)"
    if [ -n "$host" ] && [ "$host" != "mosquitto" ]; then
      log "This machine uses the broker at $host; nothing to prepare here."
      exit 0
    fi
    user="$(env_value MQTT_USERNAME)"
    password="$(env_value MQTT_PASSWORD)"
    if [ -z "$user" ] || [ -z "$password" ]; then
      user="$(env_value NODE_ID)"
      user="${user:-$(hostname -s 2>/dev/null || echo local)}"
      user="$(printf '%s' "$user" | tr -c 'A-Za-z0-9_.-' '-' | cut -c1-64)"
      password="$(new_password)"
      {
        echo ""
        echo "# Credentials of this machine for its own MQTT broker, created by start.sh"
        echo "MQTT_USERNAME=$user"
        echo "MQTT_PASSWORD=$password"
      } >> .env
      log "Created MQTT_USERNAME=$user and its password in .env."
    fi
    check_node "$user"
    set_password "$user" "$password"
    log "$user may use the broker of this machine."
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
