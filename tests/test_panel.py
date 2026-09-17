import sys
import tempfile
from pathlib import Path
from bonsai_under_test import load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-panel-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": ""})
ga.save_json(ga.MEMORY_FILE, {"core": ["uses fish shell", "building a factory sim"],
                              "archival": ["mentioned a cat"]})
ga.save_tasks([{"id": 1, "text": "Flow field", "done": True,
                "evidence": "/src/flow_field.gd"},
               {"id": 2, "text": "Belt handshake", "done": False, "evidence": ""}], 3)

ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
w = ga.Bonsai(); w.resize(1480, 760); w.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

print("\n-- five tabs --")
check("tab labels", [w.side_tabs.tabText(i) for i in range(w.side_tabs.count())],
      ["Notes", "Memory", "Tasks", "Skills", "Preview"])

print("\n-- memory tab shows and saves --")
w.side_tabs.setCurrentIndex(1); settle()
text = w.memory_editor.toPlainText()
check("core facts shown", "uses fish shell" in text, True)
check("archival shown too", "mentioned a cat" in text, True)
check("sections are headed", ga.MEMORY_ARCHIVAL_HEADER in text, True)

w.memory_editor.setPlainText(text.replace("uses fish shell", "uses the fish shell daily"))
w.save_memory(); settle()
check("edit persisted to disk", "uses the fish shell daily" in ga.load_memory()["core"], True)
check("old wording gone", "uses fish shell" in ga.load_memory()["core"], False)
check("archival untouched", ga.load_memory()["archival"], ["mentioned a cat"])
check("it reaches the system prompt", "uses the fish shell daily" in ga.build_system_prompt(), True)

print("\n-- demoting a fact to archival by moving the line --")
text = w.memory_editor.toPlainText()
text = text.replace("building a factory sim\n", "")
text = text.rstrip() + "\nbuilding a factory sim\n"
w.memory_editor.setPlainText(text); w.save_memory(); settle()
m = ga.load_memory()
check("moved out of core", "building a factory sim" in m["core"], False)
check("  ...and into archival", "building a factory sim" in m["archival"], True)

print("\n-- tasks tab --")
w.side_tabs.setCurrentIndex(2); settle()
check("both tasks listed", w.task_list.count(), 2)
check("done state reflected",
      w.task_list.item(0).checkState() == ga.Qt.CheckState.Checked, True)
check("evidence shown as a tooltip", "/src/flow_field.gd" in w.task_list.item(0).toolTip(), True)

print("\n-- ticking a task closes it without evidence (you are the authority) --")
r = ga.handle_task("DONE 2")
check("the model still cannot", r.startswith("[Not marked done"), True)
w.task_list.item(1).setCheckState(ga.Qt.CheckState.Checked); settle()
check("but you can", ga.load_tasks()[1]["done"], True)
check("  ...and it says who closed it", ga.load_tasks()[1]["evidence"], "closed in the app by you")

print("\n-- reopening, adding, removing --")
w.task_list.item(1).setCheckState(ga.Qt.CheckState.Unchecked); settle()
check("reopened", ga.load_tasks()[1]["done"], False)
check("  ...evidence cleared", ga.load_tasks()[1]["evidence"], "")
w.task_input.setText("Mother machine buffers"); w.add_task(); settle()
check("added", [t["text"] for t in ga.load_tasks()][-1], "Mother machine buffers")
check("list refreshed", w.task_list.count(), 3)
w.task_list.setCurrentRow(2); w.remove_task(); settle()
check("removed", w.task_list.count(), 2)

print("\n-- switching tabs does not lose an unsaved edit in another --")
w.side_tabs.setCurrentIndex(1); settle()
w.memory_editor.setPlainText("# CORE\nhalf-typed thought")
w.side_tabs.setCurrentIndex(2); settle()
check("memory file not overwritten by a tab switch",
      "half-typed thought" in ga.load_memory()["core"], False)

print("\n-- the failure from the screenshot: claimed removals that never happened --")
fabricated = ("I've reviewed the src folder and removed the task associated with each "
              "completed file.\nTask [1] removed.\nTask [2] removed.\n"
              "All completed tasks have been cleared from the system!")
check("claim is detected", bool(ga.CLAIMED_TASK_RE.search(fabricated)), True)
check("TASK: LIST alone is not evidence",
      bool(ga.TASK_MUTATION_RE.search("TASK: LIST -> Tasks:")), False)
check("a real removal is evidence",
      bool(ga.TASK_MUTATION_RE.search("TASK: REMOVE 1 -> Removed: Flow field")), True)
for innocent in ["I'll look at the task list next.",
                 "Task 4 is still open and needs the bot controller.",
                 "Removed the unused import from flow_field.gd."]:
    check(f"not flagged: {innocent[:38]}", bool(ga.CLAIMED_TASK_RE.search(innocent)), False)

print("\n-- CLEAR_DONE removes exactly the completed ones --")
ga.save_tasks([{"id": 1, "text": "Flow field", "done": True, "evidence": "/a.gd"},
               {"id": 2, "text": "Belts", "done": False, "evidence": ""},
               {"id": 3, "text": "Items", "done": True, "evidence": "/b.gd"}], 4)
out = ga.handle_task("CLEAR_DONE")
check("reports how many went", "Removed 2 completed task(s)" in out, True)
left = ga.load_tasks()
check("only the open one remains", [t["text"] for t in left], ["Belts"])
check("nothing to clear says so", ga.handle_task("CLEAR_DONE").startswith("[No completed"), True)

print("\n-- one call can remove several --")
ga.save_tasks([{"id": i, "text": f"T{i}", "done": False, "evidence": ""} for i in (1,2,3)], 4)
check("native maps a list of ids",
      ga.native_call_to_tool("task", {"action": "REMOVE", "numbers": [1, 3]}),
      ("TASK", "REMOVE 1 3"))
ga.handle_task("REMOVE 1 3")
check("both gone in one call", [t["id"] for t in ga.load_tasks()], [2])
check("CLEAR_DONE is offered in the schema",
      "CLEAR_DONE" in [s for s in ga.build_tool_schemas()
                       if s["function"]["name"] == "task"][0]["function"]["parameters"]["properties"]["action"]["enum"], True)

print("\n-- the button does the same thing --")
ga.save_tasks([{"id": 1, "text": "Done one", "done": True, "evidence": "/a.gd"},
               {"id": 2, "text": "Open one", "done": False, "evidence": ""}], 3)
w.refresh_tasks(); settle()
w.clear_completed_tasks(); settle()
check("completed cleared from disk", [t["text"] for t in ga.load_tasks()], ["Open one"])
check("list redrawn", w.task_list.count(), 1)

print("\n-- Remove selected: the silent no-op --")
ga.save_tasks([{"id": i, "text": f"task {i}", "done": False, "evidence": ""}
               for i in (1, 2, 3)], 4)
w.refresh_tasks(); settle()
said = []
real_log = w.log
w.log = lambda message, colour=None, italic=True: said.append(message)
try:
    # currentItem() is unset until a row has actually been clicked, so pressing the
    # button first did nothing and said nothing about why.
    check("rows are listed", w.task_list.count(), 3)
    check("nothing is selected to begin with", w.task_list.selectedItems(), [])
    w.remove_task(); settle()
    check("nothing is removed", len(ga.load_tasks()), 3)
    check("but it says so now", "Nothing selected" in said[-1], True)
    check("and explains the tick box is a different thing",
          "Clear completed" in said[-1], True)

    print("\n-- and it removes several at once --")
    w.task_list.item(0).setSelected(True)
    w.task_list.item(2).setSelected(True)
    w.remove_task(); settle()
    check("both went", [t["text"] for t in ga.load_tasks()], ["task 2"])
    check("and both are named", said[-1], "Removed: task 1, task 3")
    check("the list redrew", w.task_list.count(), 1)

    check("the list allows more than one row to be picked",
          w.task_list.selectionMode(),
          ga.QAbstractItemView.SelectionMode.ExtendedSelection)
finally:
    w.log = real_log

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
