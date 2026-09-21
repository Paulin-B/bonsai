"""Factorio, as a game that speaks the Neuro API.

Factorio has no idea Bonsai exists, and its Lua runs inside the game with no sockets,
so it cannot speak the protocol itself. What it does have is RCON: a console that
takes Lua and hands back whatever that Lua prints. That is enough to read the whole
world and to act in it, which is the same channel airi-factorio uses.

So this sits in the middle. It talks RCON to Factorio and the Neuro API to Bonsai,
and Bonsai needs no knowledge of Factorio at all - it sees a game that registered
some actions, exactly like any other.

    factorio --start-server world.zip --rcon-port 27015 --rcon-password <pw>
    python3 bridges/factorio.py --rcon-password <pw> --neuro-port 8000

Playing through the console rather than the screen is the point. The alternative is
photographing a dense real-time UI and aiming a mouse at it, which is how you play a
game that will not talk to you - not how you play one that will.
"""
import argparse
import asyncio
import json
import re
import socket
import struct
import sys

try:
    import websockets
except ImportError:
    sys.exit("This needs the websockets package: pip install websockets")


class Rcon:
    """Source RCON, which is what Factorio speaks. Small enough not to be a dependency."""

    def __init__(self, host, port, password):
        self.sock = socket.create_connection((host, port), timeout=20)
        self.id = 0
        if self._exchange(3, password) is None:
            raise RuntimeError("RCON authentication failed - wrong password?")

    def _exchange(self, kind, body):
        self.id += 1
        payload = struct.pack("<ii", self.id, kind) + body.encode() + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        size = struct.unpack("<i", self._exactly(4))[0]
        frame = self._exactly(size)
        ident, _ = struct.unpack("<ii", frame[:8])
        return None if ident == -1 else frame[8:-2].decode(errors="replace")

    def _exactly(self, count):
        buffer = b""
        while len(buffer) < count:
            chunk = self.sock.recv(count - len(buffer))
            if not chunk:
                raise ConnectionError("the Factorio server closed the connection")
            buffer += chunk
        return buffer

    def lua(self, code):
        """Run Lua in the game and return whatever it printed.

        RCON takes one line, so the newlines go - and that is why the comments have
        to go first. A `--` comment with the newline removed comments out the entire
        rest of the program, which looks exactly like the game silently doing
        nothing."""
        flat = " ".join(line.split("--")[0] if line.strip().startswith("--") else line
                        for line in code.splitlines())
        return (self._exchange(2, "/silent-command " + flat) or "").strip()


# Every action is one piece of Lua that prints a sentence saying what happened. The
# sentence goes straight back to Bonsai as the action result, so it has to be true:
# "there is no iron ore within 200 tiles" is a useful thing to be told, and a cheerful
# "mined!" that mined nothing is the failure this whole app is built to avoid.
BOT = """
local function bot()
  if storage.bot and storage.bot.valid then return storage.bot end
  storage.bot = game.surfaces.nauvis.create_entity{name='character', position={0,0}, force='player'}
  return storage.bot
end
"""

ACTIONS = {
    "look_around": {
        "description": "Look at what is around you: the nearest ore patches, what you "
                       "are carrying, and where you are standing.",
        "lua": BOT + """
local c = bot() local s = c.surface local p = c.position
local found = {}
for _, name in pairs({'iron-ore','copper-ore','coal','stone','crude-oil'}) do
  local e = s.find_entities_filtered{position=p, radius=250, name=name, limit=1}[1]
  if e then found[#found+1] = name..' at '..math.floor(e.position.x)..','..math.floor(e.position.y) end
end
local held = {}
for _, stack in pairs(c.get_main_inventory().get_contents()) do
  held[#held+1] = stack.name..' x'..stack.count
end
rcon.print('You are at '..math.floor(p.x)..','..math.floor(p.y)..
  '. Nearby: '..(#found>0 and table.concat(found, ', ') or 'nothing notable')..
  '. Carrying: '..(#held>0 and table.concat(held, ', ') or 'nothing')..'.')
""",
    },
    "walk_to": {
        "description": "Walk to a point on the map. Use coordinates you were told about.",
        "schema": {"type": "object", "required": ["x", "y"],
                   "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}},
        "lua": BOT + """
local c = bot()
local was = c.position
if math.abs(was.x - ARG_x) < 2 and math.abs(was.y - ARG_y) < 2 then
  rcon.print('You are already standing at '..math.floor(was.x)..','..math.floor(was.y)..
    ', so that changed nothing. Do something here instead of walking again.')
  return
end
c.teleport({ARG_x, ARG_y})
rcon.print('Walked to '..math.floor(c.position.x)..','..math.floor(c.position.y)..'.')
""",
    },
    "mine": {
        "description": "Mine the nearest patch of a resource, a few units at a time.",
        "schema": {"type": "object", "required": ["resource"],
                   "properties": {
                       "resource": {"enum": ["iron-ore", "copper-ore", "coal", "stone"]},
                       "count": {"type": "integer"}}},
        "lua": BOT + """
local c = bot() local s = c.surface
local want = ARG_count
local got = 0
for _ = 1, want do
  local e = s.find_entities_filtered{position=c.position, radius=40, name='ARG_resource', limit=1}[1]
  if not e then break end
  if e.mine{inventory=c.get_main_inventory(), force=true} then got = got + 1 else break end
end
if got == 0 then
  local far = s.find_entities_filtered{position=c.position, radius=250, name='ARG_resource', limit=1}[1]
  if far then
    rcon.print('No ARG_resource within reach. The nearest is at '..
      math.floor(far.position.x)..','..math.floor(far.position.y)..' - walk there first.')
  else
    rcon.print('There is no ARG_resource anywhere near here.')
  end
else
  rcon.print('Mined '..got..' ARG_resource. You now have '..
    c.get_item_count('ARG_resource')..'.')
end
""",
    },
    "craft": {
        "description": "Craft an item by hand, if you have the ingredients.",
        "schema": {"type": "object", "required": ["item"],
                   "properties": {"item": {"type": "string"},
                                  "count": {"type": "integer"}}},
        "lua": BOT + """
local c = bot()
local recipe = prototypes.recipe['ARG_item']
if not recipe then
  rcon.print('There is no recipe called ARG_item. Factorio names look like '..
    '"iron-plate", "iron-gear-wheel", "stone-furnace", "burner-mining-drill".')
  return
end
local made = c.begin_crafting{recipe='ARG_item', count=ARG_count, silent=true}
if made > 0 then rcon.print('Crafting '..made..' ARG_item.') return end
-- Saying "ingredients are missing" is useless when the whole problem is not knowing
-- which. The game knows, so it says.
local short = {}
for _, ing in pairs(recipe.ingredients) do
  local have = c.get_item_count(ing.name)
  if have < ing.amount then
    short[#short+1] = ing.amount..' '..ing.name..' (you have '..have..')'
  end
end
if #short == 0 then
  rcon.print('Cannot hand-craft ARG_item here, even though you have the ingredients - '..
    'it needs a machine.')
else
  rcon.print('Cannot craft ARG_item. It needs '..table.concat(short, ' and ')..
    '. Anything ending in -plate is smelted in a furnace from ore, not crafted by hand.')
end
""",
    },
    "place": {
        "description": "Place something you are carrying onto the map.",
        "schema": {"type": "object", "required": ["entity", "x", "y"],
                   "properties": {"entity": {"type": "string"},
                                  "x": {"type": "integer"}, "y": {"type": "integer"}}},
        "lua": BOT + """
local c = bot() local s = c.surface
if c.get_item_count('ARG_entity') < 1 then
  rcon.print('You are not carrying a ARG_entity, so there is nothing to place.')
  return
end
local spot = s.find_non_colliding_position('ARG_entity', {ARG_x, ARG_y}, 10, 1)
if not spot then rcon.print('Nowhere to put a ARG_entity near there.') return end
local e = s.create_entity{name='ARG_entity', position=spot, force='player'}
if e then
  c.remove_item{name='ARG_entity', count=1}
  rcon.print('Placed a ARG_entity at '..math.floor(spot.x)..','..math.floor(spot.y)..'.')
else
  rcon.print('That would not go there.')
end
""",
    },
}


DEFAULTS_FOR = {"count": 5}


def build_lua(name, data):
    """Substitute the action's arguments into its Lua, as literals.

    Every value is forced to the type the schema asked for before it is pasted in.
    The data came from a language model by way of a websocket, so it is treated the
    way any other untrusted input into a command would be."""
    action = ACTIONS[name]
    code = action["lua"]
    schema = action.get("schema") or {}
    properties = schema.get("properties") or {}
    for key, rule in properties.items():
        value = data.get(key, DEFAULTS_FOR.get(key, 1))
        if rule.get("type") == "integer" or key == "count":
            try:
                value = max(-100000, min(100000, int(value)))
            except (TypeError, ValueError):
                value = DEFAULTS_FOR.get(key, 1)
        elif rule.get("enum"):
            if value not in rule["enum"]:
                return None, f"{value!r} is not one of {rule['enum']}"
            value = str(value)
        else:
            value = str(value)
            if not all(c.isalnum() or c in "-_" for c in value) or not value:
                return None, (f"{value!r} is not a name Factorio would recognise - "
                              "they look like 'iron-gear-wheel' or 'stone-furnace'")
        code = code.replace(f"ARG_{key}", str(value))
    return code, None


CHAT_RE = re.compile(r"\[(CHAT|JOIN|LEAVE)\]\s*(.+)")


async def watch_chat(path, on_line):
    """Follow the server's own output for chat.

    Factorio prints "[CHAT] name: message" to its console, so someone playing in the
    same world can simply talk and be heard - no mod, and nothing for them to learn.
    The file is followed from the end, because the backlog is not a conversation."""
    loop = asyncio.get_running_loop()
    handle = open(path, errors="replace")
    handle.seek(0, 2)
    while True:
        line = await loop.run_in_executor(None, handle.readline)
        if not line:
            await asyncio.sleep(0.4)
            continue
        found = CHAT_RE.search(line)
        if found and "<server>" not in found.group(2):
            await on_line(found.group(1), found.group(2).strip())


async def play(rcon, url, quiet, server_log=None):
    async with websockets.connect(url) as ws:
        async def send(command, data=None):
            await ws.send(json.dumps({"command": command, "game": "Factorio",
                                      **({"data": data} if data else {})}))

        await send("startup")
        await send("actions/register", {"actions": [
            {"name": name, "description": spec["description"],
             **({"schema": spec["schema"]} if spec.get("schema") else {})}
            for name, spec in ACTIONS.items()]})
        print(f"connected to Bonsai at {url}, {len(ACTIONS)} actions registered")

        state = rcon.lua(ACTIONS["look_around"]["lua"])
        await send("context", {"message": f"You have just arrived on Nauvis. {state}",
                               "silent": quiet})
        done = asyncio.Event()

        async def relay(kind, text):
            print(f"CHAT {text}")
            if kind == "CHAT":
                rcon.lua(f"game.print('[Bonsai heard you]')")
            await send("context", {
                "message": (f"Someone playing alongside you said: {text}" if kind == "CHAT"
                            else f"{text} ({kind.lower()}ed the game)"),
                "silent": False})

        async def reader():
            nonlocal state
            async for raw in ws:
                message = json.loads(raw)
                command = message.get("command")
                if command == "bonsai/say":
                    # Into the game's own chat, so whoever is playing alongside sees it.
                    said = str(message["data"].get("text", "")).replace("'", "\\'")
                    rcon.lua(f"game.print('[color=0.4,0.9,0.5]Bonsai:[/color] {said}')")
                    continue
                if command != "action":
                    continue
                body = message["data"]
                name = body.get("name")
                try:
                    data = json.loads(body.get("data") or "{}")
                except ValueError:
                    data = {}
                if name not in ACTIONS:
                    await send("action/result", {"id": body["id"], "success": False,
                        "message": f"There is no action called {name}."})
                else:
                    code, complaint = build_lua(name, data)
                    if complaint:
                        await send("action/result", {"id": body["id"], "success": False,
                                                     "message": complaint})
                    else:
                        outcome = rcon.lua(code) or "Nothing happened."
                        print(f"  {name}({json.dumps(data)}) -> {outcome}")
                        # success stays true even when the attempt achieved nothing:
                        # the spec retries the whole force on a false, and "there is no
                        # iron ore here" is information to act on, not a mistake.
                        await send("action/result", {"id": body["id"], "success": True,
                                                     "message": outcome})
                        state = rcon.lua(ACTIONS["look_around"]["lua"])
                done.set()

        async def driver():
            while True:
                done.clear()
                await send("actions/force", {
                    "state": state,
                    "query": "It is your turn. Do one thing that gets you closer to an "
                             "automated factory - you will need iron, and a burner "
                             "mining drill on an ore patch is the usual first step.",
                    "action_names": list(ACTIONS)})
                await done.wait()

        jobs = [asyncio.ensure_future(reader()), asyncio.ensure_future(driver())]
        if server_log:
            jobs.append(asyncio.ensure_future(watch_chat(server_log, relay)))
            print(f"following {server_log} for chat - talk in game and it will hear you")
        await asyncio.gather(*jobs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", required=True)
    parser.add_argument("--neuro-port", type=int, default=8000)
    parser.add_argument("--server-log",
                        help="the file the Factorio server's own output goes to. Given "
                             "this, chat from anyone playing in the same world reaches "
                             "Bonsai, and what Bonsai says goes back into game chat.")
    parser.add_argument("--quiet", action="store_true",
                        help="add context silently, without inviting a reply")
    args = parser.parse_args()
    rcon = Rcon(args.rcon_host, args.rcon_port, args.rcon_password)
    print(f"RCON connected to {args.rcon_host}:{args.rcon_port}")
    asyncio.run(play(rcon, f"ws://127.0.0.1:{args.neuro_port}", args.quiet,
                     args.server_log))


if __name__ == "__main__":
    main()
