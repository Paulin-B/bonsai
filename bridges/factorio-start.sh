#!/usr/bin/env bash
# Start a headless Factorio and the Bonsai bridge against it.
#
# Three moving parts have to line up - the map, the server with RCON open, and the
# bridge - and getting the order or the paths wrong fails in ways that look like the
# game is broken. So this does it in one step.
#
#   bridges/factorio-start.sh [world.zip]
#
# Then switch "Game link" on in Bonsai, and join localhost from your own Factorio
# client if you want to play in the same world.
set -euo pipefail

FACTORIO=${FACTORIO:-/media/D/SteamLibrary/steamapps/common/Factorio/bin/x64/factorio}
HOME_DIR=${FACTORIO_HOME:-$HOME/.factorio-bonsai}
WORLD=${1:-$HOME_DIR/world.zip}
PASSWORD=${RCON_PASSWORD:-bonsai}
RCON_PORT=${RCON_PORT:-27015}
NEURO_PORT=${NEURO_PORT:-8000}
LOG="$HOME_DIR/server.log"
CONFIG="$HOME_DIR/config.ini"

[ -x "$FACTORIO" ] || { echo "No Factorio at $FACTORIO - set FACTORIO=..." >&2; exit 1; }
mkdir -p "$HOME_DIR"

# Its own write-data directory, which is the whole reason this file exists. Factorio
# takes an exclusive lock on it, so a headless server sharing ~/.factorio with a
# Factorio you are already playing simply refuses to start - and says only "is another
# instance already running?", which sounds like a leftover process rather than your
# own game.
if [ ! -f "$CONFIG" ]; then
    cat > "$CONFIG" <<INI
[path]
read-data=$(dirname "$(dirname "$(dirname "$FACTORIO")")")/data
write-data=$HOME_DIR
INI
fi

[ -f "$WORLD" ] || { echo "Creating a new map at $WORLD"
                     "$FACTORIO" -c "$CONFIG" --create "$WORLD"; }

"$FACTORIO" -c "$CONFIG" --start-server "$WORLD" \
    --rcon-port "$RCON_PORT" --rcon-password "$PASSWORD" > "$LOG" 2>&1 &
SERVER=$!
# Killed on the way out however this script ends, so a stray headless server is not
# left holding the port and the save file.
trap 'kill $SERVER 2>/dev/null || true' EXIT INT TERM

echo "Factorio starting (pid $SERVER), log at $LOG"
for _ in $(seq 60); do
    grep -q "Starting RCON interface" "$LOG" 2>/dev/null && break
    sleep 1
done
grep -q "Starting RCON interface" "$LOG" || { echo "RCON never opened:"; tail -5 "$LOG"; exit 1; }
echo "RCON is up on $RCON_PORT. Join this world at localhost from your own client."

# Not exec: that would replace this shell and take the EXIT trap with it, leaving the
# headless server running after the bridge stops. It then holds the lock on its own
# data directory, and the next run fails with "is another instance already running?"
python3 "$(dirname "$0")/factorio.py" \
    --rcon-password "$PASSWORD" --rcon-port "$RCON_PORT" \
    --neuro-port "$NEURO_PORT" --server-log "$LOG"
