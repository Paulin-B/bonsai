import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-vis-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
app = ga.QApplication(sys.argv)

captures = {"n": 0}
ga.capture_screen = lambda: (captures.__setitem__("n", captures["n"] + 1), "FRAMEDATA")[1]

def drive(replies, config=None):
    """Run one vision turn and record whether each request carried the image."""
    cfg = {"max_tool_steps": 20, "native_tools": False}
    cfg.update(config or {})
    worker = ga.Worker("what is on my screen?", True, [], cfg)
    sent = []
    seq = list(replies)
    def ask_model(system, prompt, image=None, temperature=None, with_tools=True,
                  stream=False):
        sent.append(bool(image))
        return (seq.pop(0) if seq else replies[-1], None)
    worker.ask_model = ask_model
    worker.run_tool = lambda name, arg: "file contents from disk"
    worker.model_name = lambda: "fake"
    out = {}
    worker.finished.connect(lambda r, t, e, u: out.update(reply=r))
    worker.failed.connect(lambda m: out.update(error=m))
    worker.run()
    return sent, out

print("\n-- the turn from your transcript: look, then read a file, then answer --")
captures["n"] = 0
sent, out = drive(["[TOOL: READ_FILE: /x/bot_agent.gd]", "Your screen shows the editor."])
check("no error", "error" not in out, True)
check("two loop steps plus the voice pass", len(sent), 3)
check("image on both steps and the voice pass", sent, [True, True, True])
check("captured once, not per step", captures["n"], 1)

print("\n-- a longer tool chain keeps it too --")
captures["n"] = 0
sent, _ = drive(["[TOOL: READ_FILE: /a]", "[TOOL: LIST_DIR: /b]", "[TOOL: FIND: c | /d]",
                 "Here is what I found."])
check("image on every step and the voice pass", sent, [True] * 5)
check("still only one capture", captures["n"], 1)

print("\n-- the step-limit wrap-up still has the screen --")
captures["n"] = 0
sent, _ = drive(["[TOOL: READ_FILE: /a]"] * 3, {"max_tool_steps": 3})
check("wrap-up call included", len(sent), 4)
check("all carried the image", sent, [True] * 4)

print("\n-- the old behaviour is still available --")
captures["n"] = 0
sent, _ = drive(["[TOOL: READ_FILE: /a]", "done"], {"vision_all_steps": False})
check("first step only when switched off", sent, [True, False, False])

print("\n-- a text turn attaches nothing and captures nothing --")
captures["n"] = 0
worker = ga.Worker("hello", False, [], {"max_tool_steps": 5, "native_tools": False})
sent = []
worker.ask_model = (lambda s, p, image=None, temperature=None, with_tools=True, stream=False:
                    (sent.append(bool(image)), ("hi", None))[1])
worker.model_name = lambda: "fake"
worker.finished.connect(lambda *a: None)
worker.run()
check("no image on either call", sent, [False, False])
check("grim never invoked", captures["n"], 0)

print("\n-- the prompt tells it which source to answer from --")
prompt = ga.build_system_prompt()
check("screen-vs-disk rule present", "answer from the screenshot you were given" in prompt, True)
check("  ...and says why", "unsaved" in prompt, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
