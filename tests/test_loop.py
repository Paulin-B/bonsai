import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-loop-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
app = ga.QApplication(sys.argv)

REFUSAL = ('[Not added: task 6 is the same work, and is already open: "Advanced Logistics". '
           'Do not try to add it again - work on task 6 itself.]')

def drive(replies, tool_result, steps=200):
    """Run one turn with the model and the tools both scripted."""
    worker = ga.Worker("do it", False, [], {"max_tool_steps": steps, "native_tools": False})
    executed = []
    seq = list(replies)
    worker.ask_model = (lambda s, p, image=None, temperature=None, with_tools=True, stream=False:
                        (seq.pop(0) if seq else replies[-1], None))
    def run_tool(name, argument):
        executed.append((name, argument))
        return tool_result(name, argument) if callable(tool_result) else tool_result
    worker.run_tool = run_tool
    worker.model_name = lambda: "fake"
    out = {}
    worker.finished.connect(lambda r, t, e, u: out.update(reply=r, trace=t, exhausted=e))
    worker.failed.connect(lambda m: out.update(error=m))
    worker.run()
    return out, executed

print("\n-- the 180-iteration runaway --")
out, executed = drive(["[TOOL: TASK: ADD Advanced Logistics]"], REFUSAL)
check("no error raised", "error" not in out, True)
check("the tool ran twice, then was refused locally", len(executed), 2)
check("turn ended instead of looping", out["reply"].startswith("(Stopped:"), True)
check("  ...and says why", "kept being refused" in out["reply"], True)
check("  ...and shows the last calls", "TASK:" in out["reply"], True)
check("not treated as out of steps", out["exhausted"], False)

print("\n-- varied calls that are all refused also bail out --")
counter = iter(range(100))
out, executed = drive(
    [f"[TOOL: TASK: ADD item number {i}]" for i in range(40)],
    lambda name, arg: "[Refused: something different each time]")
check("stops after the refusal run", len(executed), ga.MAX_CONSECUTIVE_REFUSALS)
check("reports the stop", out["reply"].startswith("(Stopped:"), True)

print("\n-- a refusal followed by success is not a loop --")
state = {"n": 0}
def sometimes(name, argument):
    state["n"] += 1
    return "[Not found: nope]" if state["n"] < 3 else "Wrote 900 characters to '/t/a.gd'."
out, executed = drive(
    ["[TOOL: READ_FILE: /t/a.gd]", "[TOOL: READ_FILE: /t/b.gd]",
     "[TOOL: FILE_OP: WRITE | /t/a.gd | body]", "All done - the file is written."],
    sometimes)
check("ran every call", len(executed), 3)
check("finished normally", out["reply"], "All done - the file is written.")

print("\n-- normal turns are untouched --")
out, executed = drive(["[TOOL: READ_FILE: /t/a.gd]", "Here is what it says."],
                      "line 1\nline 2")
check("one tool, then a plain answer", len(executed), 1)
check("reply preserved", out["reply"], "Here is what it says.")

print("\n-- the echoed turn record is stripped from replies --")
echoed = ("Here is the plan.\n"
          "- ran TASK and got: [Not added: task 6 is the same work]\n"
          "- ran TASK and got: [Not added: task 6 is the same work]\n"
          "That is where things stand.")
cleaned = ga.RECORD_ECHO_RE.sub("", echoed).strip()
check("record lines gone", "ran TASK and got" in cleaned, False)
check("real prose kept", cleaned.startswith("Here is the plan.")
      and cleaned.endswith("That is where things stand."), True)
check("ordinary prose untouched",
      ga.RECORD_ECHO_RE.sub("", "I ran the tests and got two failures."),
      "I ran the tests and got two failures.")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
