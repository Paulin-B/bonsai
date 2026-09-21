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
# Find the body, never create one. A console command and a mod do not share `storage`
# - the console's table belongs to the scenario - so a bot() that created its own
# character made a SECOND one: the mod walked its body across the map while every
# other action was performed by a different character standing back at the origin,
# each reporting its own position perfectly truthfully.
BOT = """
local function bot()
  local found = game.surfaces.nauvis.find_entities_filtered{name='character', limit=1}[1]
  if not found then
    remote.call('bonsai', 'spawn', 'Bonsai')
    found = game.surfaces.nauvis.find_entities_filtered{name='character', limit=1}[1]
  end
  return found
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
        # Handled in Python rather than one shot of Lua, because walking takes time
        # and the interesting part is whether it got there.
        "walk": True,
    },
    "insert": {
        "description": "Put something you are carrying into a nearby machine - coal "
                       "into a furnace to fuel it, ore into a furnace to smelt.",
        "schema": {"type": "object", "required": ["item", "into"],
                   "properties": {"item": {"type": "string"},
                                  "into": {"type": "string"},
                                  "count": {"type": "integer"}}},
        "lua": BOT + """
local c = bot() local s = c.surface
local machine = s.find_entities_filtered{position=c.position, radius=12,
                                         name='ARG_into', limit=1}[1]
if not machine then
  rcon.print('There is no ARG_into within reach. Place one first, or walk to it.')
  return
end
local have = c.get_item_count('ARG_item')
if have < 1 then rcon.print('You are not carrying any ARG_item.') return end
local moved = machine.insert{name='ARG_item', count=math.min(have, ARG_count)}
if moved == 0 then
  rcon.print('The ARG_into would not take ARG_item - wrong slot for it, or it is full.')
else
  c.remove_item{name='ARG_item', count=moved}
  rcon.print('Put '..moved..' ARG_item into the ARG_into.')
end
""",
    },
    "take": {
        "description": "Take everything out of a nearby machine - finished plates out "
                       "of a furnace, for instance.",
        "schema": {"type": "object", "required": ["from"],
                   "properties": {"from": {"type": "string"}}},
        "lua": BOT + """
local c = bot() local s = c.surface
local machine = s.find_entities_filtered{position=c.position, radius=12,
                                         name='ARG_from', limit=1}[1]
if not machine then rcon.print('There is no ARG_from within reach.') return end
local took = {}
for _, which in pairs({defines.inventory.furnace_result, defines.inventory.chest,
                       defines.inventory.furnace_source}) do
  local inv = machine.get_inventory(which)
  if inv then
    for _, stack in pairs(inv.get_contents()) do
      local moved = c.insert{name=stack.name, count=stack.count}
      if moved > 0 then
        inv.remove{name=stack.name, count=moved}
        took[#took+1] = moved..' '..stack.name
      end
    end
  end
end
if #took == 0 then
  rcon.print('The ARG_from had nothing ready. A furnace needs fuel and ore in it, and '..
    'a few seconds to work.')
else
  rcon.print('Took '..table.concat(took, ', ')..' out of the ARG_from.')
end
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


WALK_TIMEOUT = 45


async def walk(rcon, x, y):
    """Ask the mod to walk there, then watch until it arrives or gives up.

    Reported by where it ended up, not by the request having been accepted. A walk
    that was blocked by a cliff and a walk that worked look identical to whoever
    issued it, and only one of them is worth saying out loud."""
    # Already there is the commonest request and the least useful one: the arrival
    # radius is a couple of tiles, so walking one tile "succeeds" without moving, and
    # a turn spent doing that is a turn spent standing still. Say so instead.
    here = rcon.lua("local c = game.surfaces.nauvis.find_entities_filtered"
                    "{name='character', limit=1}[1] "
                    "rcon.print(string.format('%.1f %.1f', c.position.x, c.position.y))")
    try:
        at_x, at_y = (float(part) for part in here.split())
    except ValueError:
        at_x = at_y = None
    if at_x is not None and abs(at_x - x) < 4 and abs(at_y - y) < 4:
        return (f"You are already at {at_x:.0f},{at_y:.0f}, which is close enough to "
                f"{int(x)},{int(y)} to work there. Walking again changes nothing - do "
                "the thing you came here for.")

    rcon.lua(f"remote.call('bonsai','walk_to', {int(x)}, {int(y)})")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WALK_TIMEOUT
    while loop.time() < deadline:
        await asyncio.sleep(0.6)
        report = rcon.lua(
            "local s = remote.call('bonsai','state') "
            "rcon.print(string.format('%d %.1f %.1f %.1f %s %s', s.arrived and 1 or 0, "
            "s.x, s.y, s.remaining, tostring(s.stuck), tostring(s.unreachable)))")
        parts = report.split()
        if len(parts) != 6:
            return f"Something went wrong walking there: {report[:120]}"
        arrived, px, py, left, stuck, unreachable = parts
        where = f"{float(px):.0f},{float(py):.0f}"
        if unreachable == "true":
            return (f"There is no way to walk to {int(x)},{int(y)} - water or cliffs "
                    f"in between. You are still at {where}.")
        if stuck == "true":
            return (f"Something blocked the way and you stopped at {where}, "
                    f"{float(left):.0f} tiles short. Try going somewhere else first.")
        if arrived == "1":
            return f"Walked to {where}."
    return f"Still walking after {WALK_TIMEOUT} seconds - it is a long way."


async def play(rcon, url, quiet, server_log=None):
    async with websockets.connect(url) as ws:
        async def send(command, data=None):
            await ws.send(json.dumps({"command": command, "game": "Factorio",
                                      **({"data": data} if data else {})}))

        # A body with a name over it, so someone watching can see where it is and
        # what it is doing, instead of entities appearing out of nowhere.
        spawned = rcon.lua("local p = remote.call('bonsai','spawn','Bonsai') "
                           "rcon.print(string.format('%.0f,%.0f', p.x, p.y))")
        if spawned.startswith("Cannot execute"):
            print("The bonsai-bridge mod is not loaded, so there is no body to walk "
                  "around with. Start the server with bridges/factorio-start.sh, which "
                  "installs it.")
        else:
            print(f"body spawned at {spawned}, named on the map")
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
                    if ACTIONS[name].get("walk"):
                        outcome = await walk(rcon, data.get("x", 0), data.get("y", 0))
                        complaint = None
                    else:
                        code, complaint = build_lua(name, data)
                        outcome = None if complaint else (rcon.lua(code)
                                                          or "Nothing happened.")
                    if complaint:
                        await send("action/result", {"id": body["id"], "success": False,
                                                     "message": complaint})
                    else:
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
    try:
        rcon = Rcon(args.rcon_host, args.rcon_port, args.rcon_password)
    except OSError as exc:
        sys.exit(f"Could not reach Factorio's RCON on {args.rcon_host}:"
                 f"{args.rcon_port} ({exc}). Is the server running, and was it "
                 "started with --rcon-port and --rcon-password?")
    print(f"RCON connected to {args.rcon_host}:{args.rcon_port}")
    url = f"ws://127.0.0.1:{args.neuro_port}"
    try:
        asyncio.run(play(rcon, url, args.quiet, args.server_log))
    except KeyboardInterrupt:
        print("\nstopped")
    except OSError as exc:
        # By far the likeliest way to arrive here, and a bare traceback about a
        # refused connection does not say the one thing that would fix it.
        sys.exit(f"Could not reach Bonsai at {url} ({exc}).\n"
                 "Switch 'Game link' on in Bonsai, then start this again. If it is "
                 "already on, check the Game link port in Settings matches "
                 f"--neuro-port ({args.neuro_port}).")


if __name__ == "__main__":
    main()
