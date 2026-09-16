import sys, importlib.util, tempfile, shutil
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

sandbox = Path(tempfile.mkdtemp(prefix="bonsai-tasks-"))
ga.TASKS_FILE = sandbox / "tasks.json"
real = sandbox / "item_manager.gd"; real.write_text("extends Node\n" + "x" * 900)
stub = sandbox / "stub.gd"; stub.write_text("extends Node")

print("\n-- replaying the run that closed 11 tasks against 4 files --")
ga.handle_task("ADD Data-Oriented Item Manager")
ga.handle_task("ADD Bot Navigation & Task State Machine")

r = ga.handle_task("DONE 1")
check("bare DONE refused", r.startswith("[Not marked done: TASK DONE needs the file"), True)
check("  ...task still open", ga.load_tasks()[0]["done"], False)

r = ga.handle_task(f"DONE 1 | {sandbox}/never_written.gd")
check("DONE naming a missing file refused", "does not exist" in r, True)
check("  ...task still open", ga.load_tasks()[0]["done"], False)

r = ga.handle_task(f"DONE 1 | {stub}")
check("DONE naming a 12-byte stub refused", "is a stub" in r or "which is a stub" in r, True)
check("  ...task still open", ga.load_tasks()[0]["done"], False)

r = ga.handle_task(f"DONE 1 | {real}")
check("DONE with real evidence accepted", "Marked done: Data-Oriented Item Manager" in r, True)
check("  ...reports the verified file", f"verified {real}" in r, True)
check("  ...task now closed", ga.load_tasks()[0]["done"], True)
check("  ...evidence stored on the task", ga.load_tasks()[0]["evidence"], str(real))
check("  ...listing shows what proved it", str(real) in ga.format_tasks(ga.load_tasks()), True)

r = ga.handle_task(f"DONE 1 | {real}")
check("re-closing an already-done task is a no-op", "was already done" in r, True)

r = ga.handle_task(f"DONE 1 2 | {real}")
check("closing several at once refused", "one task done at a time" in r, True)

print("\n-- the duplicate tasks 7-11 that shadowed 2-6 --")
for restated, original in [
    ("Implement Data-Oriented Item Manager: Create a system for efficient item tracking.",
     "Data-Oriented Item Manager"),
    ("Implement Bot Navigation & Task State Machine: Create bot AI with task switching.",
     "Bot Navigation & Task State Machine"),
]:
    r = ga.handle_task(f"ADD {restated}")
    check(f"restated '{original[:28]}' rejected", "Not added: task" in r and original in r, True)

before = len(ga.load_tasks())
ga.handle_task("ADD Implement Grid Reservation Layer: Create a system to reserve cells.")
check("genuinely new task still accepted", len(ga.load_tasks()), before + 1)
r = ga.handle_task("ADD Belt Transport & Handshake Protocol")
check("unrelated task not mistaken for a duplicate", "Added task" in r, True)

print("\n-- similarity judgement --")
tasks = [{"id": 1, "text": "Mother Machine & Buffer Logistics", "done": False, "evidence": ""}]
check("same work, restated", bool(ga.similar_task(
    "Implement Mother Machine & Buffer Logistics: Create the primary production unit.", tasks)), True)
check("different subsystem", ga.similar_task("Belt Transport & Handshake Protocol", tasks), None)
check("different subsystem 2", ga.similar_task("Flow Field & Weight Map Foundation", tasks), None)

print("\n-- native call carries evidence --")
check("DONE", ga.native_call_to_tool("task", {"action": "DONE", "number": 3, "evidence": "/t/a.gd"}),
      ("TASK", "DONE 3 | /t/a.gd"))
check("DONE without evidence reaches the refusal",
      ga.native_call_to_tool("task", {"action": "DONE", "number": 3}), ("TASK", "DONE 3 | "))
check("REMOVE unchanged", ga.native_call_to_tool("task", {"action": "REMOVE", "number": 3}),
      ("TASK", "REMOVE 3"))
check("ADD unchanged", ga.native_call_to_tool("task", {"action": "ADD", "text": "x"}), ("TASK", "ADD x"))

print("\n-- other actions still work --")
check("LIST", ga.handle_task("LIST").startswith("Tasks:"), True)
r = ga.handle_task("REMOVE 2")
check("REMOVE by id", "Removed:" in r, True)
check("CLEAR", ga.handle_task("CLEAR"), "Task list cleared.")
check("empty list", ga.handle_task("LIST"), "Tasks:\n(the list is empty)")

shutil.rmtree(sandbox)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
