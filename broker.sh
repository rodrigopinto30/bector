#!/usr/bin/env bash
# Manages who may use the bundled MQTT broker: one user per node, limited to the team topic,
# and the TLS listener that lets other machines connect over an untrusted network.
set -euo pipefail

cd "$(dirname "$0")"

usage() {
  cat <<'TXT'
Usage:
  ./broker.sh add NODE      let NODE connect, or give it a new password, and print the lines for its .env
  ./broker.sh remove NODE   revoke NODE; it is disconnected at once
  ./broker.sh list          list the nodes that may connect
  ./broker.sh local         let this machine use its own broker (start.sh runs it)
  ./broker.sh tls ADDRESS...  turn on TLS for the addresses other machines use to reach this one
  ./broker.sh ca            write team-ca.crt again, the file other machines need to trust this broker
  ./broker.sh tls-off       turn TLS off and delete the broker's certificates and the team CA

NODE is the NODE_ID of the machine: letters, digits, '.', '_' or '-', at most 64 characters.
ADDRESS is an IP address or a host name of this machine, such as 192.168.1.20 or ana.local.
Node changes apply at once, without disconnecting the other nodes; tls restarts the broker.
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

is_ip() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || [[ "$1" =~ ^[0-9A-Fa-f:]+$ && "$1" == *:* ]]
}

check_address() {
  if ! is_ip "$1" &&
    ! [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$ ]]; then
    log "invalid address: '$1'"
    usage >&2
    exit 2
  fi
}

# The addresses in the broker's certificate, or nothing when TLS is off.
tls_names() {
  $DOCKER compose run --rm --no-deps -T --entrypoint /bin/sh mosquitto -c \
    'cat /mosquitto/security/tls/names 2>/dev/null || true'
}

# Runs a script in a throwaway container of the cli image, which has openssl, with the
# broker's security volume and this folder (as /app) mounted. It runs as root, with only
# the capabilities it needs to write and give away the broker's files.
with_openssl() {
  export HOST_UID HOST_GID
  HOST_UID="${SUDO_UID:-$(id -u)}"
  HOST_GID="${SUDO_GID:-$(id -g)}"
  $DOCKER compose run --rm --no-deps -T --user 0:0 --cap-add CHOWN --cap-add FOWNER \
    --cap-add DAC_OVERRIDE -v mosquitto-security:/mosquitto/security \
    -e SAN -e NAMES -e HOST_UID -e HOST_GID --entrypoint /bin/sh cli -c "$1"
}

COPY_CA='cp /mosquitto/security/tls/ca.crt /app/team-ca.crt
  chown "$HOST_UID:$HOST_GID" /app/team-ca.crt
  chmod 644 /app/team-ca.crt'

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
    names="$(tls_names)"
    log "$2 may now connect. Add these lines to the .env of that machine:"
    echo
    if [ -n "$names" ]; then
      port="$(env_value BROKER_TLS_PORT)"
      echo "MQTT_HOST=${names%% *}"
      echo "MQTT_PORT=${port:-8883}"
      echo "MQTT_TLS=true"
      echo "MQTT_CA_CERT=/app/team-ca.crt"
    else
      port="$(env_value BROKER_PORT)"
      echo "MQTT_HOST=<address of this machine>"
      echo "MQTT_PORT=${port:-1883}"
    fi
    echo "MQTT_USERNAME=$2"
    echo "MQTT_PASSWORD=$password"
    echo "NODE_ID=$2"
    echo
    if [ -n "$names" ]; then
      if [ "$names" != "${names%% *}" ]; then
        log "MQTT_HOST may be any of: $names."
      fi
      log "Also copy team-ca.crt from this folder to the project folder of that machine."
      if [ "$(env_value BROKER_TLS_BIND)" != "0.0.0.0" ]; then
        log "This broker only accepts TLS connections from this machine. To open it to the"
        log "network, set BROKER_TLS_BIND=0.0.0.0 in .env and run ./stop.sh && ./start.sh."
      fi
    else
      log "TLS is off, so passwords and messages travel unencrypted. Run ./broker.sh tls"
      log "with this machine's address before using the broker outside a trusted network."
      if [ "$(env_value BROKER_BIND)" != "0.0.0.0" ]; then
        log "This broker only accepts connections from this machine. To open it to a trusted"
        log "network, set BROKER_BIND=0.0.0.0 in .env and run ./stop.sh && ./start.sh."
      fi
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
  tls)
    shift
    if [ "$#" -eq 0 ]; then
      log "give the addresses other machines use to reach this one, such as ./broker.sh tls 192.168.1.20"
      exit 2
    fi
    SAN="DNS:mosquitto,DNS:localhost,IP:127.0.0.1"
    for name in "$@"; do
      check_address "$name"
      if is_ip "$name"; then SAN="$SAN,IP:$name"; else SAN="$SAN,DNS:$name"; fi
    done
    export SAN NAMES="$*"
    # The CA is created once and kept, so renewing the broker's certificate does not
    # require a new team-ca.crt on every machine. Its key never leaves the volume, and only
    # root can read it; the broker's own key belongs to the mosquitto user.
    result="$(with_openssl '
      set -e
      umask 077
      mkdir -p /mosquitto/security/tls /mosquitto/security/conf.d
      cd /mosquitto/security/tls
      if [ -s ca.key ] && [ -s ca.crt ]; then
        echo "ca=kept"
      else
        openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key 2>/dev/null
        openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=healer team CA" \
          -addext "basicConstraints=critical,CA:TRUE" \
          -addext "keyUsage=critical,keyCertSign,cRLSign" -out ca.crt
        echo "ca=new"
      fi
      openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out server.key.new 2>/dev/null
      openssl req -new -key server.key.new -subj "/CN=healer broker" -out server.csr
      printf "subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n" "$SAN" > server.ext
      openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 \
        -sha256 -extfile server.ext -out server.crt.new 2>/dev/null
      mv server.key.new server.key
      mv server.crt.new server.crt
      rm -f server.csr server.ext
      echo "$NAMES" > names
      printf "listener 8883\ncafile %s\ncertfile %s\nkeyfile %s\ntls_version tlsv1.2\n" \
        /mosquitto/security/tls/ca.crt /mosquitto/security/tls/server.crt \
        /mosquitto/security/tls/server.key > ../conf.d/tls.conf
      chown 0:0 ca.key ca.srl
      chown 1883:1883 . ca.crt server.crt server.key names ../conf.d ../conf.d/tls.conf
      chmod 700 . ../conf.d
      chmod 600 ca.key ca.srl server.key ../conf.d/tls.conf
      chmod 644 ca.crt server.crt names
      '"$COPY_CA"'
      openssl x509 -in server.crt -noout -enddate | sed "s/^notAfter=/until=/"
    ')"
    if [ -n "$($DOCKER compose ps -q --status running mosquitto)" ]; then
      $DOCKER compose restart mosquitto >/dev/null 2>&1
    fi
    port="$(env_value BROKER_TLS_PORT)"
    log "TLS is on, on port ${port:-8883}, for $* (and mosquitto, localhost, 127.0.0.1)."
    log "The broker's certificate is valid until $(sed -n 's/^until=//p' <<< "$result")."
    if grep -q "^ca=new" <<< "$result"; then
      log "A new team CA was created. team-ca.crt, in this folder, is what the other machines"
      log "need to trust this broker: copy it to the project folder of each one."
    else
      log "The team CA did not change: the machines that have team-ca.crt keep working."
    fi
    if [ "$(env_value BROKER_TLS_BIND)" != "0.0.0.0" ]; then
      log "To accept other machines over TLS, set BROKER_TLS_BIND=0.0.0.0 in .env and run"
      log "./stop.sh && ./start.sh."
    fi
    ;;
  ca)
    with_openssl '
      [ -s /mosquitto/security/tls/ca.crt ] || { echo "[broker] TLS is off; run ./broker.sh tls ADDRESS first" >&2; exit 3; }
      '"$COPY_CA"
    log "Wrote team-ca.crt in this folder. Copy it to the project folder of each machine."
    ;;
  tls-off)
    $DOCKER compose run --rm --no-deps -T --entrypoint /bin/sh mosquitto -c \
      'rm -rf /mosquitto/security/tls /mosquitto/security/conf.d/tls.conf'
    rm -f team-ca.crt
    if [ -n "$($DOCKER compose ps -q --status running mosquitto)" ]; then
      $DOCKER compose restart mosquitto >/dev/null 2>&1
    fi
    log "TLS is off, and its certificates and the team CA were deleted. Turning it on again"
    log "creates a new team CA, which every machine needs."
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
