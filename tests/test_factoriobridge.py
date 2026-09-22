"""The Factorio bridge's Lua, checked as text because there is no Factorio here.

These exist because of one incident. ensure_body cleared "stray" characters by name,
a player's character IS a character entity, and someone playing alongside had their
body and everything in their inventory destroyed. The same assumption ran through the
bridge: every action took "the first character on the surface", which once a person
joined was a coin flip between Bonsai's body and theirs.
"""
import re
import sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

ROOT = Path(__file__).resolve().parent.parent
mod = (ROOT / "bridges/mod/control.lua").read_text()
bridge = (ROOT / "bridges/factorio.py").read_text()

print("\n-- it must never destroy a person --")
# It did. A player's character is a character entity, and clearing "strays" by name
# deleted the person playing alongside and everything they carried. The fix is not a
# better guard - it is that nothing needs deleting once the body is found by id.
check("no character is destroyed anywhere in the mod",
      re.search(r"\bcharacter\w*\.destroy\(\)|stray\.destroy\(\)", mod), None)
check("nothing sweeps characters by name",
      "find_entities_filtered{name = \"character\"}" in mod, False)
check("the body is identified instead of isolated",
      "unit_number" in mod, True)

print("\n-- and it must never act as someone else's body --")
check("the mod can name its own body", "body_id = function()" in mod, True)
check("  ...by unit number, which is unique",
      "unit_number" in mod, True)
check("the bridge asks for that body", "remote.call('bonsai', 'body_id')" in bridge, True)
check("  ...and matches on it rather than taking the first character",
      "c.unit_number == wanted" in bridge, True)
check("taking the first character is gone",
      "find_entities_filtered{name='character', limit=1}[1]" in bridge, False)

print("\n-- every action copes with there being no body --")
actions = bridge.count("local c = bot()")
check("each one checks", bridge.count("Bonsai has no body right now"), actions)
check("and there is more than one of them", actions > 1, True)

print("\n-- what a person says reaches the decision --")
check("it is collected", "said_to_us" in bridge, True)
check("  ...and put into the forced state, not only a side channel",
      'state": state + heard' in bridge, True)
check("  ...told it is a person", "They are a person, not a prompt" in bridge, True)
check("  ...and to do that instead", "instead of what you had planned" in bridge, True)
check("and nothing announces it heard you before it has answered",
      "game.print('[Bonsai heard you]')" in bridge, False)

print("\n-- the mod still does what it was for --")
for wanted in ["script.on_event(defines.events.on_tick", "walking_state",
               "request_path", "rendering.draw_text", "add_chart_tag"]:
    check(f"still there: {wanted}", wanted in mod, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
