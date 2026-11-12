#!/usr/bin/env bash
# A demo of the panel: real events, one at a time, shown by a panel of its own at
# http://localhost:8766. Everything lives in a volume of its own and on a topic of its own,
# so it never touches your data, your workspace or your team. It costs no tokens: the fix
# comes from a teammate first and then from the local cache, and Claude is never asked.
#
#   ./panel.sh demo         run the demo; the demo panel stays open at the end
#   ./panel.sh demo clean   remove everything the demo created
set -euo pipefail

cd "$(dirname "$0")/.."

VOLUME="healer-panel-demo"
PANEL="healer-panel-demo"
LISTENER="healer-listener-demo"
NODE="laptop-luis"
PORT="${PANEL_DEMO_PORT:-8766}"
KEY="demo-team-key-0123456789abcdef"

DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi

env_value() {
  grep -E "^$1=" .env 2>/dev/null | tail -n 1 | cut -d= -f2- || true
}
team_topic="$(env_value MQTT_TOPIC)"
TOPIC="${team_topic:-healer/solutions/v1}/demo"
uid="$(env_value HOST_UID)"
gid="$(env_value HOST_GID)"

DATA_ENV=(
  -e CHROMA_PATH=/demo/chroma
  -e LEDGER_PATH=/demo/ledger.sqlite3
  -e OUTBOX_PATH=/demo/outbox.sqlite3
  -e RESULTS_PATH=/demo/results.sqlite3
  -e HISTORY_PATH=/demo/history.sqlite3
  -e LISTENER_STATUS_PATH=/demo/listener.json
  -e "NODE_ID=$NODE"
  -e "MQTT_TOPIC=$TOPIC"
  -e SHARE_SOLUTIONS=true
  -e "SHARE_KEY=$KEY"
)

say() { echo "[demo] $*"; }

compose_run() {
  $DOCKER compose run --rm --no-deps -T -v "$VOLUME:/demo" "${DATA_ENV[@]}" "$@"
}

helper() {
  compose_run -w /app cli python scripts/panel_demo.py "$1"
}

fix_the_bug() {
  # With no API key, a run that needed Claude would stop instead of spending tokens. A fix
  # from the team is always confirmed by a person, even with --apply: the demo says yes.
  printf 'y\n' |
    compose_run -e ANTHROPIC_API_KEY= cli healer run --path /demo/project --apply || true
}

pause() {
  echo
  if [ -t 0 ]; then
    read -r -p "[demo] Look at the panel, then press Enter for the next event... " _
  else
    sleep "${PANEL_DEMO_PAUSE:-12}"
  fi
  echo
}

event() {
  echo
  echo "============================================================"
  say "$1"
  say "Watch http://localhost:$PORT: Recent activity, in the tab Overview, shows it"
  say "within 5 seconds."
  echo "============================================================"
}

forget_session() {
  # The broker keeps what is published for a listener with a fixed name; a clean
  # connection under that name drops it.
  local user password
  user="$(env_value MQTT_USERNAME)"
  password="$(env_value MQTT_PASSWORD)"
  $DOCKER compose exec -T mosquitto mosquitto_sub -i "healer-listener-$NODE" \
    -u "$user" -P "$password" -t "$TOPIC" -W 1 >/dev/null 2>&1 || true
}

clean() {
  $DOCKER rm -f "$PANEL" "$LISTENER" >/dev/null 2>&1 || true
  forget_session
  $DOCKER volume rm "$VOLUME" >/dev/null 2>&1 || true
}

broker_stopped=false
restore_broker() {
  if [ "$broker_stopped" = true ]; then
    say "Starting the broker again..."
    $DOCKER compose start mosquitto >/dev/null
  fi
}

case "${1:-}" in
  clean)
    clean
    say "Removed the demo panel, its listener and its data."
    exit 0
    ;;
  "") ;;
  *)
    echo "Usage: ./panel.sh demo [clean]" >&2
    exit 2
    ;;
esac

for service in cli mosquitto sandbox; do
  if [ -z "$($DOCKER compose ps -q --status running "$service" 2>/dev/null)" ]; then
    say "The $service container is not running; start the project with ./start.sh." >&2
    exit 1
  fi
done

trap restore_broker EXIT

say "Preparing the demo: its own data, panel and team listener..."
clean
$DOCKER compose run --rm --no-deps -T --user 0:0 --cap-add CHOWN -v "$VOLUME:/demo" \
  --entrypoint chown cli "${uid:-$(id -u)}:${gid:-$(id -g)}" /demo
helper project
$DOCKER compose run --rm -d --no-deps --name "$PANEL" -p "127.0.0.1:$PORT:8765" \
  -v "$VOLUME:/demo" "${DATA_ENV[@]}" panel healer panel --host 0.0.0.0 --port 8765 >/dev/null
$DOCKER compose run --rm -d --no-deps --name "$LISTENER" -v "$VOLUME:/demo" \
  "${DATA_ENV[@]}" cli healer listen >/dev/null
sleep 4

event "1/10  Open http://localhost:$PORT"
say "A new node, $NODE, with no incidents yet. Its team listener is connected: the"
say "card Team listener is OK, with 0 received. The panel refreshes every 5 seconds,"
say "and each new event lights up in green in Recent activity."
pause

event "2/10  A teammate shares a fix"
helper teammate
say "laptop-ana fixed a bug of this project on that computer and shared it. Tab Team:"
say "one suggestion, with its change. The card Team listener counts 1 received."
pause

event "3/10  Another teammate shares a different fix for the same bug"
helper rival
say "laptop-eva fixed the same bug another way. Both fixes are kept: tab Team shows two"
say "suggestions for the same error. Team listener: 2 received."
pause

event "4/10  A forged message arrives"
helper forged
say "Someone without the team key sends a fix. The listener refuses it: 1 rejected,"
say "and nothing new in the tab Team."
pause

event "5/10  healer run fixes the bug with a teammate's fix"
fix_the_bug
say "laptop-eva's fix, the newest, was tried first: these tests reject it. Then"
say "laptop-ana's passed in the sandbox and was applied, for 0 tokens. This computer"
say "told the team both results; its listener ignores them, since it sent them: 2"
say "ignored. Tab Team: laptop-ana's fix worked on 1 node and is now tried 1st;"
say "laptop-eva's failed on 1. Overview: 1 incident, solved by the team. Tab Incidents:"
say "Show, under Steps, opens the run step by step, with both attempts and their changes."
pause

event "6/10  Another teammate says how laptop-ana's fix did there"
helper result
say "laptop-sol tested laptop-ana's fix and it worked there. Recent activity: Result"
say "received. Tab Team: laptop-ana's fix worked on 2 nodes. Team listener: 3 received."
pause

event "7/10  The message broker goes down"
broker_stopped=true
$DOCKER compose stop mosquitto 2>&1 | tail -n 1
sleep 3
say "The cards Message broker and Team listener stop being OK: the panel cannot reach"
say "the broker, and the listener keeps retrying."
pause

event "8/10  This computer shares a fix of its own, but the broker is down"
helper share
say "A fix this computer found with Claude for another bug is kept in its local cache and"
say "shared. The broker is down, so it waits: tab Outbox, with the error, and the card"
say "Outbox asks for attention. (The demo writes that fix itself, so Claude costs nothing.)"
pause

event "9/10  The broker comes back"
$DOCKER compose start mosquitto 2>&1 | tail -n 1
broker_stopped=false
sleep 6
say "The listener reconnects and sends what was waiting: the Outbox is empty again. The"
say "listener ignores that message, because this node sent it: 3 ignored."
pause

event "10/10  The other bug shows up, and the local cache fixes it"
helper bug
fix_the_bug
say "The local cache already had the fix, so it was tested and applied for 0 tokens, and"
say "shared again. Overview: 2 incidents, one solved by the team and one by the cache, and"
say "7,000 tokens saved. The listener ignores its own message: 4 ignored."
pause

$DOCKER stop "$LISTENER" >/dev/null
say "Last change: the listener was stopped, so its card says it is off."
say "The demo panel stays at http://localhost:$PORT. Remove it with: ./panel.sh demo clean"
