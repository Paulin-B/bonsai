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

box = Path(tempfile.mkdtemp(prefix="bonsai-branch-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False})

print("\n-- forking keeps what came before and drops what came after --")
original = ga.new_chat()
ga.title_chat(original, "shader work")
messages = [
    {"role": "user", "content": "make the tint warmer"},
    {"role": "assistant", "content": "Done, warmer."},
    {"role": "user", "content": "now make it purple"},
    {"role": "assistant", "content": "Purple applied."},
]
ga.save_chat(original, messages)

forked = ga.fork_chat(original, 2)
check("the branch has only what came before", len(ga.load_chat(forked)), 2)
check("keeping the earlier exchange",
      [m["content"] for m in ga.load_chat(forked)],
      ["make the tint warmer", "Done, warmer."])
check("the original is untouched", len(ga.load_chat(original)), 4)
check("it is a different chat", forked != original, True)

index = {c["id"]: c for c in ga.load_chat_index()["chats"]}
check("the branch knows its parent", index[forked]["parent"], original)
check("and is named after it", index[forked]["title"], "shader work (branch 1)")
check("the parent has no parent", "parent" in index[original], False)

print("\n-- branching twice from the same point numbers them --")
second = ga.fork_chat(original, 2)
index = {c["id"]: c for c in ga.load_chat_index()["chats"]}
check("the second is branch 2", index[second]["title"], "shader work (branch 2)")
third = ga.fork_chat(second, 1)
index = {c["id"]: c for c in ga.load_chat_index()["chats"]}
check("branching a branch does not stack the suffix",
      index[third]["title"], "shader work (branch 1)")
check("but it points at the branch it came from", index[third]["parent"], second)

print("\n-- edge cases --")
check("forking at zero gives an empty chat", ga.load_chat(ga.fork_chat(original, 0)), [])
whole = ga.fork_chat(original, 99)
check("forking past the end copies everything", len(ga.load_chat(whole)), 4)

print("\n-- in the window --")
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(1200, 700); win.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

win.switch_chat(original)
settle()
def branch_buttons():
    return [b for b in win.messages.findChildren(ga.QPushButton)
            if b.objectName() == "branchButton"]
check("one branch button per user message", len(branch_buttons()), 2)
check("assistant messages have none", len(branch_buttons()) < len(messages), True)

before = len(ga.load_chat_index()["chats"])
branch_buttons()[1].click()
settle()
check("clicking forks the conversation", len(ga.load_chat_index()["chats"]), before + 1)
check("and switches to the new branch", win.chat_id != original, True)
check("which holds everything above that message", len(win.history), 2)
check("the message is put back in the box to rephrase",
      win.input.text(), "now make it purple")
check("the original still has all four", len(ga.load_chat(original)), 4)

print("\n-- the sidebar marks branches --")
titles = [win.chat_list.item(i).text() for i in range(win.chat_list.count())]
check("a branch is marked", any(t.startswith("⎇ ") for t in titles), True)
check("the original is not", any(t == "shader work" for t in titles), True)

print("\n-- branching resets what belongs to the old line of work --")
win.continuations = 2
win.pending_handoff = ("steps", "DONE: x\nNEXT: y")
win.on_branch(1, "try again")
settle()
check("no stale continuation budget", win.continuations, 0)
check("no stale handoff", win.pending_handoff, None)

print("\n-- it refuses while a turn is still running --")
class Busy:
    def isRunning(self): return True
win.worker = Busy()
current = win.chat_id
win.on_branch(1, "nope")
settle()
check("the chat does not change mid-turn", win.chat_id, current)
win.worker = None

print("\n-- and stops auto mode, which belonged to the other branch --")
win.auto_running = True
win.on_branch(1, "different approach")
settle()
check("auto mode is stopped", win.auto_running, False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
