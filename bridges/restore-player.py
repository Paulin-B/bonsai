#!/usr/bin/env python3
"""Put a player back on their feet after the bridge went wrong.

An early version of the mod cleared "stray" characters by name to keep Bonsai's body
unambiguous. A player's character is a character entity, so anyone playing alongside
was deleted along with everything they carried. That is fixed, but a world where it
already happened needs the person putting back.

    python3 bridges/restore-player.py GamingCreep [--rcon-password pw]

Gives them a character if they have none, and the freeplay starting items if they are
empty-handed. The item list is read out of the running game rather than written down
here, so it is whatever this version of Factorio actually starts you with.
"""
import argparse
import sys

from factorio import Rcon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("player", help="the player's in-game name")
    parser.add_argument("--rcon-host", default="127.0.0.1")
    parser.add_argument("--rcon-port", type=int, default=27015)
    parser.add_argument("--rcon-password", default="bonsai")
    parser.add_argument("--force", action="store_true",
                        help="give the items even if they are carrying something")
    args = parser.parse_args()

    try:
        rcon = Rcon(args.rcon_host, args.rcon_port, args.rcon_password)
    except OSError as exc:
        sys.exit(f"Could not reach RCON on {args.rcon_host}:{args.rcon_port} ({exc}). "
                 "Is the server running?")

    name = args.player.replace("'", "")
    print(rcon.lua(f"""
local p = game.get_player('{name}')
if not p then
  local names = {{}}
  for _, other in pairs(game.players) do names[#names+1] = other.name end
  rcon.print('No player called {name}. Known players: '..
    (#names > 0 and table.concat(names, ', ') or 'none yet - they have to join once'))
  return
end
local made = false
if not (p.character and p.character.valid) then
  p.create_character()
  made = true
end
if not (p.character and p.character.valid) then
  rcon.print('Could not give {name} a character.')
  return
end
local inv = p.character.get_main_inventory()
local carrying = 0
for _, stack in pairs(inv.get_contents()) do carrying = carrying + stack.count end
if carrying > 0 and not {str(args.force).lower()} then
  rcon.print((made and 'Gave {name} a character. ' or '') ..
    '{name} is already carrying '..carrying..' item(s), so nothing was added. '..
    'Pass --force to top them up anyway.')
  return
end
local ok, items = pcall(remote.call, 'freeplay', 'get_created_items')
if not ok or not items then
  rcon.print('Gave {name} a character, but this scenario has no starting items to copy.')
  return
end
local given = {{}}
for item, count in pairs(items) do
  local moved = inv.insert{{name = item, count = count}}
  if moved > 0 then given[#given+1] = moved..' '..item end
end
table.sort(given)
rcon.print((made and 'Gave {name} a new character, and ' or 'Gave {name} ') ..
  'the starting items: '..table.concat(given, ', ')..'.')
"""))


if __name__ == "__main__":
    main()
