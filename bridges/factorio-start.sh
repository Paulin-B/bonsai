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
# Not Factorio's default 34197: you may well have a game of your own using it, and the
# server then dies with "host address is already in use".
GAME_PORT=${GAME_PORT:-34198}
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

# auto_pause is the one that matters. A headless server with nobody connected pauses
# the game, and RCON still answers while it is paused - so mining and building appear
# to work while walking does not move at all, which reads as being stuck in one spot.
SETTINGS="$HOME_DIR/server-settings.json"
if [ ! -f "$SETTINGS" ]; then
    cat > "$SETTINGS" <<JSON
{
  "name": "Bonsai's world",
  "description": "Bonsai plays here. Join at localhost.",
  "visibility": { "public": false, "lan": true },
  "require_user_verification": false,
  "auto_pause": false,
  "autosave_interval": 10,
  "autosave_only_on_server": true
}
JSON
fi

# The body, its name and the per-tick walking all live in a mod, because walking_state
# is an input the game clears every tick and RCON cannot set it sixty times a second.
MOD="$HOME_DIR/mods/bonsai-bridge_0.1.0"
mkdir -p "$MOD"
cp "$(dirname "$0")/mod/info.json" "$(dirname "$0")/mod/control.lua" "$MOD/"

# The same mod goes into your own Factorio, or you cannot join: a client whose mods
# differ from the server's is refused, and "sync mods with server" cannot help because
# it fetches from the mod portal and this one exists only on this disk. Set
# SKIP_CLIENT_MOD=1 to leave your install alone.
CLIENT_MODS=${CLIENT_MODS:-$HOME/.factorio/mods}
if [ -z "${SKIP_CLIENT_MOD:-}" ] && [ -d "$CLIENT_MODS" ]; then
    mkdir -p "$CLIENT_MODS/bonsai-bridge_0.1.0"
    cp "$(dirname "$0")/mod/info.json" "$(dirname "$0")/mod/control.lua" \
       "$CLIENT_MODS/bonsai-bridge_0.1.0/"
    python3 - "$CLIENT_MODS/mod-list.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text()) if path.exists() else {"mods": [{"name": "base", "enabled": True}]}
for mod in data["mods"]:
    if mod["name"] == "bonsai-bridge":
        mod["enabled"] = True
        break
else:
    data["mods"].append({"name": "bonsai-bridge", "enabled": True})
path.write_text(json.dumps(data, indent=2))
PY
    echo "Installed the mod into $CLIENT_MODS too, so your client can join."
    echo "Restart Factorio if it is open - mods are only read at startup."
fi

[ -f "$WORLD" ] || { echo "Creating a new map at $WORLD"
                     "$FACTORIO" -c "$CONFIG" --create "$WORLD"; }

"$FACTORIO" -c "$CONFIG" --start-server "$WORLD" --server-settings "$SETTINGS" \
    --port "$GAME_PORT" \
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
echo "RCON is up on $RCON_PORT."
echo "Join this world from your own Factorio: Multiplayer, Connect to address, localhost:$GAME_PORT"

# Not exec: that would replace this shell and take the EXIT trap with it, leaving the
# headless server running after the bridge stops. It then holds the lock on its own
# data directory, and the next run fails with "is another instance already running?"
python3 "$(dirname "$0")/factorio.py" \
    --rcon-password "$PASSWORD" --rcon-port "$RCON_PORT" \
    --neuro-port "$NEURO_PORT" --server-log "$LOG"
