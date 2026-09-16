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

print("\n-- reading the handoff the model writes --")
reply = ("I got the renderer working but ran out of room.\n\n"
         "[HANDOFF]\n"
         "DONE: wrote /tmp/game/render.js and /tmp/game/index.html\n"
         "LEFT: collision detection and the score display\n"
         "NEXT: EDIT /tmp/game/render.js to add an AABB check in update()\n"
         "[/HANDOFF]")
clean, carried = ga.parse_handoff(reply)
check("the block is taken out of what the user sees", "[HANDOFF]" in clean, False)
check("the prose survives", clean, "I got the renderer working but ran out of room.")
check("all three fields are kept", carried.count("\n"), 2)
check("and in a fixed order", carried.split("\n")[0].startswith("DONE:"), True)
check("the next action is the last line", carried.split("\n")[-1].startswith("NEXT:"), True)

clean, carried = ga.parse_handoff("Unterminated.\n\n[HANDOFF]\nDONE: a\nNEXT: do the thing properly\n")
check("a block with no closing tag still parses", carried.startswith("DONE: a"), True)
check("and is still stripped", "[HANDOFF]" in clean, False)

clean, carried = ga.parse_handoff("All finished, nothing left to do.")
check("no block means no handoff", carried, "")
check("and the reply is untouched", clean, "All finished, nothing left to do.")

clean, carried = ga.parse_handoff("Done.\n[HANDOFF]\nDONE: everything\nLEFT: nothing\n[/HANDOFF]")
check("a block with no NEXT is a sign-off, not a handoff", carried, "")
check("but is still not shown to the user", "[HANDOFF]" in clean, False)

print("\n-- when the model writes no usable handoff, the trace becomes one --")
trace = "\n".join(f"FILE_OP: WRITE | /tmp/game/part{i}.js -> Wrote 900 characters"
                  for i in range(15))
built = ga.handoff_from_trace(trace, "steps")
check("it is built from what actually ran", "part14.js" in built, True)
check("capped rather than pasting the whole turn", built.count("FILE_OP"), 12)
check("it says why the turn ended", "ran out of steps" in built, True)
check("and it always names a next action", "NEXT:" in built, True)
check("nothing ran, nothing to hand off", ga.handoff_from_trace("", "steps"), "")

print("\n-- knowing when the context is nearly full --")
def worker(prompt_tokens, limit, max_tokens=8192):
    w = ga.Worker("x", False, [], {"context_size": limit, "max_tokens": max_tokens})
    w.prompt_tokens = prompt_tokens
    return w.context_is_tight()
check("plenty of room", worker(10_000, 65536), False)
check("getting full", worker(52_000, 65536), True)
check("right at the edge", worker(50_000, 65536), False)
check("a bigger reply needs more room sooner",
      worker(50_000, 65536, max_tokens=16384), True)
check("no reported context size means no guessing", worker(60_000, 0), False)
check("nothing measured yet means no guessing", worker(0, 65536), False)

print("\n-- the worker only hands off when it ran out --")
src = APP.read_text()
import ast as _ast
run = next(n for n in _ast.walk(_ast.parse(src))
           if isinstance(n, _ast.FunctionDef) and n.name == "run"
           and any("ran_out" in _ast.dump(c) for c in _ast.walk(n)))
emits = [n for n in _ast.walk(run) if isinstance(n, _ast.Call)
         and isinstance(n.func, _ast.Attribute) and n.func.attr == "emit"
         and isinstance(n.func.value, _ast.Attribute)
         and n.func.value.attr == "handoff"]
check("there is exactly one place it hands off", len(emits), 1)
check("the context check runs before spending another step",
      src.index("if step and self.context_is_tight():")
      < src.index('self.status.emit(f"Querying {label}..."'), True)
check("a cancelled turn returns before any handoff",
      src.index("(Cancelled.)") < src.index("self.handoff.emit"), True)

print("\n-- the window decides whether to pick it back up --")
box = Path(tempfile.mkdtemp(prefix="bonsai-resume-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False, "max_continuations": 2})
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(1200, 700)
sent = []
win.dispatch = lambda prompt, vision, retry, unattended=False: sent.append(
    {"prompt": prompt, "unattended": unattended})

def offer(summary, reason="steps"):
    win.on_handoff(reason, summary)

print("  (a plain unfinished turn)")
win.origin_prompt = "build me a little game"
offer("DONE: wrote a.js\nLEFT: b.js\nNEXT: write /tmp/b.js")
check("it carries on by itself", win.maybe_resume(trace="FILE_OP: WRITE"), True)
check("exactly one continuation was sent", len(sent), 1)
check("unattended, since nobody asked for it", sent[0]["unattended"], True)
check("the note it left itself is in the prompt", "NEXT: write /tmp/b.js" in sent[0]["prompt"], True)
check("and it is told to act before re-orienting",
      "NEXT action FIRST" in sent[0]["prompt"], True)
check("and not to redo finished work", "do not redo" in sent[0]["prompt"], True)
check("and the original request goes back in with it",
      "build me a little game" in sent[0]["prompt"], True)
check("the handoff is consumed, not reused", win.pending_handoff, None)

print("  (nothing to hand off)")
sent.clear()
check("a finished turn is left alone", win.maybe_resume(trace="FILE_OP: WRITE"), False)
check("and nothing is sent", sent, [])

print("  (the ways it must stop)")
sent.clear(); win.continuations = 0; win.last_handoff = ""
offer("DONE: x\nNEXT: y")
check("a denied permission stops it",
      win.maybe_resume(trace="RUN -> [The user did not grant permission to run this]"), False)

offer("DONE: x\nNEXT: y")
check("a change that never landed stops it",
      win.maybe_resume(trace="FILE_OP", unfinished="could not write /tmp/x"), False)

win.continuations = 0; win.last_handoff = "DONE: x\nNEXT: y"; win.last_next = "y"
offer("DONE: x\nNEXT: y")
check("ending in exactly the same place stops it",
      win.maybe_resume(trace="FILE_OP: WRITE"), False)
check("because that is stuck, not short of room", sent, [])

# The failure this was rewritten for: two rounds both ending "NEXT: FILE_OP WRITE
# main.py" without writing it, differing only in "5652 chars" vs "5652 bytes".
win.continuations = 0
win.last_handoff = "DONE: parser.py written (5652 chars)\nNEXT: FILE_OP WRITE main.py"
win.last_next = ga.handoff_next(win.last_handoff)
offer("DONE: parser.py written (5652 bytes)\nLEFT: main.py\nNEXT: FILE_OP WRITE main.py")
check("a reworded note heading for the same action still counts as stuck",
      win.maybe_resume(trace="LIST_DIR"), False)
check("and nothing is sent", sent, [])

print("  (reading the NEXT line)")
check("the action is extracted", ga.handoff_next("DONE: a\nNEXT: FILE_OP WRITE main.py"),
      "file_op write main.py")
check("trailing punctuation and case are ignored",
      ga.handoff_next("NEXT: Write `main.py`."), ga.handoff_next("next: write `main.py`"))
check("no NEXT line, nothing to compare", ga.handoff_next("DONE: everything"), "")
check("an empty handoff is not a match for another empty one",
      ga.handoff_next("") == "" and ga.handoff_next("DONE: x") == "", True)

print("  (the cap)")
sent.clear(); win.continuations = 0; win.last_handoff = ""
for i in range(4):
    offer(f"DONE: step {i}\nNEXT: step {i + 1}")
    win.maybe_resume(trace=f"FILE_OP: WRITE {i}")
check("it resumes twice and then stops", len(sent), 2)
check("and stays stopped rather than refilling its own budget", win.continuations, 2)
offer("DONE: step 9\nNEXT: step 10")
check("a further handoff is still refused", win.maybe_resume(trace="FILE_OP: WRITE 9"), False)

print("  (auto mode owns its own looping)")
sent.clear(); win.continuations = 0; win.last_handoff = ""
win.auto_running = True
offer("DONE: x\nNEXT: y")
check("self-resumption stands aside", win.maybe_resume(trace="FILE_OP: WRITE"), False)
check("leaving auto mode to continue", sent, [])
win.auto_running = False

print("  (a person interrupting resets everything)")
win.continuations = 1; win.last_handoff = "old"; win.pending_handoff = ("steps", "old")
win.input.setPlainText("never mind, do this instead")
sent.clear()
win.send()
check("the stale handoff is dropped", win.pending_handoff, None)
check("the budget starts fresh", win.continuations, 0)
check("and the memory of where it was stops blocking", win.last_handoff, "")
check("the new request becomes what any continuation serves",
      win.origin_prompt, "never mind, do this instead")

print("  (a cut-off tool call says so)")
check("an empty FILE_OP blames the token limit, not a typo",
      "cut off at the token limit" in ga.Worker("x", False, [], {}).run_file_op(" | "), True)
check("and says nothing was written",
      "NOTHING was written" in ga.Worker("x", False, [], {}).run_file_op(""), True)

print("  (turning it off)")
win.settings["max_continuations"] = 0
sent.clear(); win.continuations = 0; win.last_handoff = ""
offer("DONE: x\nNEXT: y")
check("zero means never resume", win.maybe_resume(trace="FILE_OP: WRITE"), False)
check("and nothing is sent", sent, [])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
