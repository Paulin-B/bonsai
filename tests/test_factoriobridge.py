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
# Counting guards against calls is not enough: some use `c and ...` instead, which is
# equally safe. So each call site is looked at on its own.
GUARDS = ("Bonsai has no body right now", "if not c then", "c and ")
sites = [after.split('"""')[0] for after in bridge.split("local c = bot()")[1:]]
unguarded = [block[:90] for block in sites
             if not any(guard in block for guard in GUARDS)]
check("no bot() call dereferences a missing body", unguarded, [])
check("and there are several of them", len(sites) > 4, True)

print("\n-- what a person says reaches the decision --")
check("it is collected", "said_to_us" in bridge, True)
check("  ...and put into the forced state, not only a side channel",
      '"state": state + urgent + heard' in bridge, True)
check("  ...told it is a person", "They are a person, not a prompt" in bridge, True)
check("  ...and to do that instead", "instead of what you had planned" in bridge, True)
check("and nothing announces it heard you before it has answered",
      "game.print('[Bonsai heard you]')" in bridge, False)

print("\n-- the mod still does what it was for --")
for wanted in ["script.on_event(defines.events.on_tick", "walking_state",
               "request_path", "rendering.draw_text", "add_chart_tag"]:
    check(f"still there: {wanted}", wanted in mod, True)

print("\n-- goals are questions the game answers, not the model --")
sys.path.insert(0, str(ROOT / "bridges"))
import factorio as fb

for text, kind in [("have iron-plate 50", "have"), ("build stone-furnace 2", "build"),
                   ("fuel stone-furnace 1", "fuel")]:
    goal, complaint = fb.parse_goal(text)
    check(f"accepted: {text}", (complaint, goal[0]), (None, kind))
for bad, why in [("have", "three words"), ("juggle iron 3", "not a kind"),
                 ("have iron-plate zero", "not a count"),
                 ("have iron;rm 3", "not a Factorio name")]:
    goal, complaint = fb.parse_goal(bad)
    check(f"refused: {bad!r}", (goal, why in complaint), (None, True))

class FakeRcon:
    def __init__(self, answer): self.answer, self.asked = answer, None
    def lua(self, code): self.asked = code; return self.answer

r = FakeRcon("18|carrying 18 of 50 iron-plate")
done, where = fb.goal_progress(r, ("have", "iron-plate", 50))
check("progress is read from the game", (done, where), (False, "carrying 18 of 50 iron-plate"))
check("  ...and the item reaches the query", "iron-plate" in r.asked, True)
check("reaching the number finishes it",
      fb.goal_progress(FakeRcon("50|carrying 50"), ("have", "iron-plate", 50))[0], True)
check("passing it finishes it too",
      fb.goal_progress(FakeRcon("80|carrying 80"), ("have", "iron-plate", 50))[0], True)
check("an unreadable answer is not success",
      fb.goal_progress(FakeRcon("no body"), ("have", "iron-plate", 50))[0], False)

print("\n-- it can find out how things are made --")
check("there is a recipe action", "recipe" in fb.ACTIONS, True)
code, _ = fb.build_lua("recipe", {"item": "burner-mining-drill"})
check("  ...asked of the game's own recipe data", "prototypes.recipe" in code, True)
check("  ...including what it already has", "get_item_count" in code, True)
check("and it is told to ask rather than guess",
      "rather than guessing" in bridge, True)

print("\n-- dying and being attacked are facts it is given --")
check("death is caught with a filter, not every entity",
      'filter = "name", name = "character"' in mod, True)
check("  ...and reported once, then cleared", "took_death = function()" in mod, True)
check("  ...with what it cost spelled out", "YOU DIED" in bridge, True)
check("  ...and that it lost what it carried", "carrying died" in bridge, True)
check("threats are counted every turn", "threats =" in mod, True)
check("  ...and put in front of it", "enemies are within 40 tiles" in bridge, True)
check("it is told not to mine through a fight",
      "do not stand there" in bridge, True)
check("there is something to fight with", "fight = function()" in mod, True)
check("  ...and a gun to do it with", "local function arm(character)" in mod, True)
check("arming does not stack up every turn", "guns.is_empty()" in mod, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
