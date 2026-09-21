"""The mood that outlives the chat.

The point of this one is that the mood is *earned*. It is not the model saying it
feels tired - it is the residue of tool calls that actually failed and files that
actually got written, which the turn loop was already working out and discarding.
So the checks are mostly about that: does what happened reach it, does it fade, and
can it ever become an excuse to do the job less well.
"""
import sys
from datetime import datetime, timedelta
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

def reset():
    character = ga.load_character()
    character.pop("mood", None)
    ga.save_json(ga.CHARACTER_FILE, character)

def set_mood(value, hours_ago=0, why=""):
    character = ga.load_character()
    character["mood"] = {
        "value": value, "why": why,
        "at": (datetime.now() - timedelta(hours=hours_ago)).isoformat(timespec="seconds")}
    ga.save_json(ga.CHARACTER_FILE, character)

print("\n-- a character file with no mood in it is simply steady --")
reset()
check("no mood is neutral, not an error", ga.load_mood()[0], 0.0)
check("and it is called steady", ga.load_mood()[1], "steady")
check("which the prompt does not mention at all", ga.mood_line(), "")

print("\n-- what happened in a turn is what moves it --")
reset()
ga.shift_mood(ga.MOOD_STEPS["refused"] * 3, ga.mood_reason({"refused": 3}))
value, name, why = ga.load_mood()
check("three refusals push it negative", value < 0, True)
check("and it knows what did it", why, "3 tool calls came back refused")
reset()
ga.shift_mood(ga.MOOD_STEPS["closed"] + ga.MOOD_STEPS["landed"],
              ga.mood_reason({"closed": 1, "landed": 1}))
check("finishing something pushes it positive", ga.load_mood()[0] > 0, True)
check("and says so in the user's terms", ga.load_mood()[2], "1 task got finished")

print("\n-- one turn cannot decide the whole personality --")
reset()
ga.shift_mood(-50.0, "a catastrophe")
check("an enormous swing is capped at one turn's worth",
      round(ga.load_mood()[0], 3), -ga.MOOD_TURN_LIMIT)
check("and stays inside the scale", ga.load_mood()[0] >= -1.0, True)
for _ in range(20):
    ga.shift_mood(-50.0, "again")
check("repeated bad turns still never leave the scale", ga.load_mood()[0] >= -1.0, True)

print("\n-- it fades, because sulking for a week is not a personality --")
set_mood(-0.8, hours_ago=0)
fresh = ga.load_mood()[0]
set_mood(-0.8, hours_ago=ga.MOOD_HALF_LIFE_HOURS)
half = ga.load_mood()[0]
set_mood(-0.8, hours_ago=ga.MOOD_HALF_LIFE_HOURS * 8)
old = ga.load_mood()[0]
check("a fresh mood is felt in full", round(fresh, 2), -0.8)
check("one half-life later it is half of it", round(half, 2), -0.4)
check("a couple of days on it has gone", abs(old) < 0.02, True)
check("and it reads as steady again", ga.mood_name(old), "steady")

print("\n-- a clock that jumped is not a mood --")
set_mood(-0.8, hours_ago=-5)        # stamped in the future
check("a future stamp does not amplify it", ga.load_mood()[0], -0.8)
character = ga.load_character()
character["mood"] = {"value": -0.8, "at": "not a date", "why": ""}
ga.save_json(ga.CHARACTER_FILE, character)
check("an unreadable stamp is dropped, not crashed on", ga.load_mood()[0], 0.0)

print("\n-- the bands --")
check("well up", ga.mood_name(0.8), "on a roll")
check("up", ga.mood_name(0.3), "pleased")
check("level", ga.mood_name(0.0), "steady")
check("down", ga.mood_name(-0.3), "frustrated")
check("well down", ga.mood_name(-0.9), "fed up")

print("\n-- it reaches the prompt, and only when there is something to say --")
set_mood(0.0)
check("steady is silent", "HOW YOU ARE TODAY" in ga.build_system_prompt(), False)
set_mood(-0.7, why="3 tool calls came back refused")
prompt = ga.build_system_prompt()
check("a bad afternoon is not", "HOW YOU ARE TODAY" in prompt, True)
check("it names the mood", "fed up" in prompt, True)
check("and what caused it", "3 tool calls came back refused" in prompt, True)
check("it says the mood carried over rather than starting fresh",
      "carried over" in prompt, True)

print("\n-- and it can never become a reason to do the job worse --")
# The failure this guards against: a model told it is fed up deciding that a fed-up
# assistant checks less, or takes it out on the user. The mood is about voice only.
line = ga.mood_line()
check("it is explicitly only about voice", "voice and nothing else" in line, True)
check("checking is unconditional", "check exactly as carefully" in line, True)
check("and the user is not the target", "take it out on the user" in line, True)

print("\n-- a whole turn's worth of events, the way the worker counts them --")
reset()
events = {"refused": 2, "landed": 1}
ga.shift_mood(sum(ga.MOOD_STEPS[k] * n for k, n in events.items()), ga.mood_reason(events))
check("two failures against one success comes out negative", ga.load_mood()[0] < 0, True)
check("and is described by the half that dominated",
      ga.load_mood()[2], "2 tool calls came back refused")
check("a turn stopped for looping says that instead",
      ga.mood_reason({"looping": 1, "refused": 4, "landed": 2}),
      "the turn had to be stopped for going in circles")
check("a turn with nothing in it says nothing", ga.mood_reason({}), "")

print("\n-- it survives the process, which is the whole point --")
set_mood(-0.6, why="3 tool calls came back refused")
stored = ga.load_json(ga.CHARACTER_FILE, {})
check("it is written into the character file, not held in memory",
      round(stored["mood"]["value"], 2), -0.6)
check("and comes back from a cold read", round(ga.load_mood()[0], 2), -0.6)

print("\n-- and the turn loop really does feed it --")
# The helpers above can all be right while nothing calls them. This drives a whole
# turn with the model and the tools both scripted, and looks at what the mood did.
app = ga.QApplication(sys.argv)

def drive(replies, tool_result, steps=200):
    worker = ga.Worker("do it", False, [], {"max_tool_steps": steps, "native_tools": False})
    seq = list(replies)
    worker.ask_model = (lambda s, p, image=None, temperature=None, with_tools=True, stream=False:
                        (seq.pop(0) if seq else replies[-1], None))
    worker.run_tool = lambda name, argument: (
        tool_result(name, argument) if callable(tool_result) else tool_result)
    worker.model_name = lambda: "fake"
    worker.run()
    return worker

reset()
drive(["[TOOL: READ_FILE: /nope]"], "[No such file.]")
after_failures = ga.load_mood()
check("a turn where everything was refused leaves it negative", after_failures[0] < 0, True)
check("and the mood says what went wrong, in the user's terms",
      "refused" in after_failures[2] or "circles" in after_failures[2], True)

reset()
drive(["[TOOL: FILE_OP: WRITE /tmp/x.txt\nhello]", "Done."],
      "Wrote 5 characters.")
check("a turn that actually wrote something leaves it positive",
      ga.load_mood()[0] > 0, True)
check("and says so", "written" in ga.load_mood()[2], True)

reset()
drive(["Just answering, no tools needed."], "")
check("a turn with no tool work leaves the mood alone", ga.load_mood()[0], 0.0)
check("  ...and writes nothing to the character file",
      "mood" in ga.load_json(ga.CHARACTER_FILE, {}), False)

reset()
crashed = ga.Worker("do it", False, [], {"native_tools": False})
crashed.model_name = lambda: "fake"
def explode(*a, **k):
    crashed.note_mood("refused")
    raise RuntimeError("the server fell over mid-turn")
crashed.ask_model = explode
crashed.run()
check("a turn that crashed still records what happened before it did",
      ga.load_mood()[0] < 0, True)

print("\n-- the tree wears it --")
for theme in ga.PALETTES:
    colours = ga.palette(theme)
    plain = ga.mood_colour(0.0, theme)
    check(f"{theme}: neutral is the ordinary muted colour", plain, colours["muted"])
    up, down = ga.mood_colour(0.8, theme), ga.mood_colour(-0.8, theme)
    check(f"{theme}: a good mood and a bad one do not look the same", up == down, False)
    check(f"{theme}: neither looks like neutral", up != plain and down != plain, True)
    readable = min(ga.contrast(c, colours["bg"]) for c in (up, down, plain))
    check(f"{theme}: a tinted tree is still readable against the background",
          readable >= 3.0, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
