#!/bin/sh
# Bello launcher: the server, the Bluetooth relay when asked, and the three staff dashboards.
#   ./bello-server.sh                    LAN: server on 0.0.0.0:8766 + care queue, inbox, admin
#   ./bello-server.sh relay              Bluetooth: server on loopback + relay + the dashboards
#   ./bello-server.sh --seed-demo        extra flags go to the server
#   ./bello-server.sh --port 9000        ...or set BELLO_PORT / BELLO_TK_PYTHON
# The token lives in ~/.bello-token, the optional cloud archive in ~/.bello-cloud: both outside git.
# Ctrl-C stops the server and the relay; the dashboards are ordinary windows to close.
cd "$(dirname "$0")" || exit 1
BELLO_API_TOKEN=$(cat "$HOME/.bello-token"); export BELLO_API_TOKEN
[ -f "$HOME/.bello-cloud" ] && . "$HOME/.bello-cloud"
PY=hospital-dashboard/.venv/bin/python      # the server: models, no Tkinter
[ -x "$PY" ] || PY=python3
TK=${BELLO_TK_PYTHON:-/opt/homebrew/bin/python3}   # the dashboards: Tkinter, no models
PORT=${BELLO_PORT:-8766}
BELLO_SERVER=http://127.0.0.1:$PORT; export BELLO_SERVER

relay=''
if [ "$1" = relay ]; then relay=1; shift; fi
[ -n "$relay" ] || set -- --host 0.0.0.0 "$@"      # the relay talks to the server over loopback

caffeinate -i "$PY" hospital-dashboard/backend.py --port "$PORT" "$@" &
server=$!
relaypid=''
trap 'kill $server $relaypid 2>/dev/null' INT TERM EXIT

# The dashboards report "Cannot reach the server" if they open before it answers.
ready=''
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  if curl -sf -o /dev/null -H "X-Bello-Token: $BELLO_API_TOKEN" "$BELLO_SERVER/api/languages"; then ready=1; break; fi
  sleep 1
done
if [ -z "$ready" ]; then
  echo "Bello: the server has not answered on $BELLO_SERVER after 30s, so the dashboards stay closed." >&2
  echo "Bello: its own output above says why. Ctrl-C to stop it." >&2
  wait "$server"
  exit 1
fi

if [ -n "$relay" ]; then
  BELLO_NO_APPS=1 caffeinate -i "$PY" ble-link/relay.py &   # the loop below owns the dashboards
  relaypid=$!
fi
for app in clerk inbox admin; do "$TK" "staff-app/$app.py" & done

echo "Bello: server $BELLO_SERVER, dashboards open$([ -n "$relay" ] && echo ', relay listening for the bedside Pi'). Ctrl-C stops the server."
wait
