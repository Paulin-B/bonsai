import sys
import tempfile
from pathlib import Path
from bonsai_under_test import APP, load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-hist-"))
ga.CHAT_INDEX_FILE = box / "index.json"
ga.CHATS_DIR = box / "chats"
ga.SETTINGS_FILE = box / "settings.json"
ga.save_settings({**ga.DEFAULTS, "max_history_messages": 40})

def make(title, messages):
    cid = ga.new_chat()
    ga.title_chat(cid, title)
    ga.save_chat(cid, messages)
    return cid

belts = make("Belt handshake design", [
    {"role": "user", "content": "How should the belt handshake work?"},
    {"role": "assistant", "content":
     "[SYSTEM RECORD - what the app actually did this turn:\n- ran READ_FILE and got: ok\n"
     "Anything not listed as succeeding here did NOT happen.]\n\n"
     "Use a two-phase handshake: the belt reserves a slot, then the machine confirms. "
     "That way an item is never in two places and never vanishes."},
])
grid = make("Grid reservation", [
    {"role": "user", "content": "Why reserve tiles at all?"},
    {"role": "assistant", "content": "So two bots never claim the same tile in one tick."},
])
current = make("Today's chat", [
    {"role": "user", "content": "belt handshake again, in the current chat"},
])

print("\n-- finds what was decided in another chat --")
out = ga.search_chats("belt handshake", current)
check("names the chat it came from", 'From "Belt handshake design"' in out, True)
check("quotes the decision", "two-phase handshake" in out, True)
check("labels who said it", "You said:" in out, True)
check("strips the SYSTEM RECORD noise", "SYSTEM RECORD" in out, False)
check("warns it is not the current conversation", "not the current one" in out, True)

print("\n-- the current chat is excluded --")
check("no hit from the open conversation", "Today's chat" in out, False)
check("  ...but it is found when not excluded",
      'From "Today\'s chat"' in ga.search_chats("belt handshake", None), True)

print("\n-- ranking and misses --")
out = ga.search_chats("tile")
check("matches the grid chat", "never claim the same tile" in out, True)
out = ga.search_chats("quantum entanglement", current)
check("clean miss", out.startswith("[Nothing in"), True)
check("  ...says how many chats it looked at", "earlier chat(s) mentions" in out, True)
check("no search terms", ga.search_chats("a of", current), "[Give HISTORY something to search for.]")

print("\n-- output stays bounded --")
long_chat = make("Wall of text", [
    {"role": "assistant", "content": "belts " + "padding " * 4000},
])
out = ga.search_chats("belts", current)
check("capped well under the context window", len(out) < 4500, True)
check("still returns something useful", "Wall of text" in out or "Belt handshake" in out, True)

print("\n-- no chats at all --")
ga.CHATS_DIR = box / "empty"
ga.CHAT_INDEX_FILE = box / "empty.json"
check("does not crash", ga.search_chats("anything").startswith("[Nothing in"), True)

print("\n-- reaches the tool plumbing --")
check("native call", ga.native_call_to_tool("history", {"query": "belts"}), ("HISTORY", "belts"))
check("bracketed text form", ga.extract_tool_call("[TOOL: HISTORY: belt handshake]"),
      ("HISTORY", "belt handshake"))
check("bare line form", ga.extract_tool_call("HISTORY: belts"), ("HISTORY", "belts"))
check("listed in the schemas",
      "history" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)
_help = APP.read_text().split("Available tools:")[1][:200]
check("offered in the unknown-tool help", "HISTORY" in _help, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
