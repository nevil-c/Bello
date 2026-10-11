#!/bin/sh
# Bello launcher: the server, the Bluetooth relay and the three staff dashboards.
#   ./bello-server.sh                    Bluetooth: server on loopback + relay + care queue, inbox, admin
#   ./bello-server.sh lan                Wi-Fi instead: server on 0.0.0.0, no relay
#   ./bello-server.sh --seed-demo        extra flags go to the server
#   ./bello-server.sh --port 9000        ...or set BELLO_PORT / BELLO_TK_PYTHON
# `relay` is still accepted as a word, so older notes and shell history keep working.
# The token lives in ~/.bello-token, the optional cloud archive in ~/.bello-cloud: both outside git.
# So does the department call: ~/.bello-twilio carries TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN /
# TWILIO_FROM. Without it, Send + call still sends and logs why no call went out.
# Ctrl-C stops what this command started.
cd "$(dirname "$0")" || exit 1
BELLO_API_TOKEN=$(cat "$HOME/.bello-token"); export BELLO_API_TOKEN
[ -f "$HOME/.bello-cloud" ] && . "$HOME/.bello-cloud"
[ -f "$HOME/.bello-twilio" ] && . "$HOME/.bello-twilio"
PY=hospital-dashboard/.venv/bin/python      # the server: models, no Tkinter
[ -x "$PY" ] || PY=python3
TK=${BELLO_TK_PYTHON:-/opt/homebrew/bin/python3}   # the dashboards: Tkinter, no models
PORT=${BELLO_PORT:-8766}
BELLO_SERVER=http://127.0.0.1:$PORT; export BELLO_SERVER

relay=1                                                  # Bluetooth is the way this runs
case "$1" in
  relay) shift ;;
  lan) relay=''; shift; set -- --host 0.0.0.0 "$@" ;;     # the escape hatch for Wi-Fi bedside units
esac

# One server per port, and the data directory only allows one. A second server here dies on that lock
# while the readiness probe below still passes against whatever is already listening, which is how this
# prints "dashboards open" while owning nothing. So look first: if a server is already answering, use it
# and start only the rest. The relay only needs something to forward to.
server=''
if curl -sf -o /dev/null -H "X-Bello-Token: $BELLO_API_TOKEN" "$BELLO_SERVER/api/languages"; then
  echo "Bello: a server is already answering on $BELLO_SERVER; using it and starting the rest."
else
  caffeinate -i "$PY" hospital-dashboard/backend.py --port "$PORT" "$@" &
  server=$!
fi
relaypid=''; apps=''
trap 'kill $server $relaypid $apps 2>/dev/null' INT TERM HUP EXIT

# The dashboards report "Cannot reach the server" if they open before it answers.
ready=''
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
  if curl -sf -o /dev/null -H "X-Bello-Token: $BELLO_API_TOKEN" "$BELLO_SERVER/api/languages"; then ready=1; break; fi
  sleep 1
done
if [ -z "$ready" ]; then
  echo "Bello: the server has not answered on $BELLO_SERVER after 30s, so the dashboards stay closed." >&2
  echo "Bello: its own output above says why." >&2
  [ -n "$server" ] && wait "$server"
  exit 1
fi

if [ -n "$relay" ]; then
  BELLO_NO_APPS=1 caffeinate -i "$PY" ble-link/relay.py &   # the loop below owns the dashboards
  relaypid=$!
fi
for app in clerk inbox admin; do "$TK" "staff-app/$app.py" & apps="$apps $!"; done

if [ -n "$server" ]; then
  echo "Bello: server $BELLO_SERVER, dashboards open$([ -n "$relay" ] && echo ', relay listening for the bedside Pi'). Ctrl-C stops all of it."
else
  echo "Bello: dashboards open$([ -n "$relay" ] && echo ', relay listening for the bedside Pi') against the server already on $BELLO_SERVER. Ctrl-C stops these, not that server."
fi
wait
