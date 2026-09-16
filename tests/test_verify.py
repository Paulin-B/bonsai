import sys, importlib.util
from bonsai_under_test import APP, load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

print("\n-- the replies that slipped through are now caught --")
check("'I have completed Task 4' (zero tools ran)",
      bool(ga.CLAIMED_ACTION_RE.search("I have completed Task 4: Bot Navigation.")), True)
check("'I've implemented a robust state machine'",
      bool(ga.CLAIMED_ACTION_RE.search("I've implemented a robust state machine.")), True)
check("'I have successfully established the architecture'",
      bool(ga.CLAIMED_ACTION_RE.search("I have successfully established the architecture.")), True)
check("'I've set up the buffer'", bool(ga.CLAIMED_ACTION_RE.search("I've set up the buffer.")), True)
print("  -- and ordinary talk still isn't --")
for s in ["Should I place this in src/items/item_manager.gd?",
          "This task is complete in the sense that the design is settled.",
          "Next I would create the BotController.",
          "The build finished with two warnings."]:
    check(f"not flagged: {s[:42]}", bool(ga.CLAIMED_ACTION_RE.search(s)), False)

print("\n-- mutation targets, so a retry clears its own failure --")
check("FILE_OP WRITE", ga.mutation_target("FILE_OP", "WRITE | /a/b.gd | extends Node"), "/a/b.gd")
check("FILE_OP EDIT", ga.mutation_target("FILE_OP", "EDIT | /a/b.gd | x\n|||\ny"), "/a/b.gd")
check("FILE_OP MKDIR", ga.mutation_target("FILE_OP", "MKDIR | /a/items"), "/a/items")
check("DOWNLOAD", ga.mutation_target("DOWNLOAD", "https://x/f.zip | /a/f.zip"), "/a/f.zip")

print("\n-- replaying the exact transcript sequence --")
def replay(calls):
    """Mirrors the bookkeeping the worker loop does around each tool result."""
    unfinished = {}
    for name, argument, result in calls:
        if name in ga.MUTATING_TOOLS:
            target = ga.mutation_target(name, argument)
            if result.startswith("["):
                unfinished[target] = f"{name} on {target} - {result.splitlines()[0]}"
            else:
                unfinished.pop(target, None)
    return unfinished

round2 = replay([
    ("TASK", "LIST", "Tasks:\n[2] ..."),
    ("FILE_OP", "WRITE | /srv/factory-game/src/items/item_manager.gd | extends Node",
     "[Refused: parent folder missing: /srv/factory-game/src/items. MKDIR it first.]"),
    ("FILE_OP", "MKDIR | /srv/factory-game/src/items",
     "Created folder '/srv/factory-game/src/items'."),
])
check("the never-written file is still outstanding",
      list(round2), ["/srv/factory-game/src/items/item_manager.gd"])
check("the folder that was created is not flagged",
      "/srv/factory-game/src/items" in round2, False)
check("the failure carries the fix forward",
      "MKDIR it first" in round2["/srv/factory-game/src/items/item_manager.gd"], True)

retried = replay([
    ("FILE_OP", "WRITE | /a/x.gd | body", "[Refused: parent folder missing: /a. MKDIR it first.]"),
    ("FILE_OP", "MKDIR | /a", "Created folder '/a'."),
    ("FILE_OP", "WRITE | /a/x.gd | body", "Wrote 400 characters to '/a/x.gd'."),
])
check("a successful retry clears the failure", retried, {})

readonly = replay([
    ("SEARCH", "godot flow field", "[No search results found.]"),
    ("RECALL", "belts", "[Nothing in archival memory matches 'belts'. 3 facts stored.]"),
])
check("read-only tools returning nothing aren't failures", readonly, {})

print("\n-- signal and slot agree --")
import inspect
src = APP.read_text()
check("finished signal has 4 args", "finished = pyqtSignal(str, str, bool, str)" in src, True)
import ast as _ast
_emits = [n for n in _ast.walk(_ast.parse(src))
          if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
          and n.func.attr == "emit" and isinstance(n.func.value, _ast.Attribute)
          and n.func.value.attr == "finished"]
check("every finished.emit passes all 4 args",
      sorted({len(n.args) for n in _emits}), [4])
check("on_reply accepts unfinished", "def on_reply(self, reply, trace, hit_step_limit=False, unfinished=\"\")" in src, True)
check("maybe_continue accepts unfinished", "def maybe_continue(self, reply, trace, hit_step_limit, unfinished=\"\")" in src, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
