import importlib.util, tempfile, sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-uitest-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
ga.CHATS_DIR = box / "chats"
ga.Bonsai.start_docker = lambda self: None

app = ga.QApplication(sys.argv)
w = ga.Bonsai(); w.resize(1180, 760); w.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

def message_count():
    return w.messages.column.count() - 1        # minus the trailing stretch

print("\n-- messages land in the list --")
start = message_count()
w.messages.add_user("first question")
w.messages.add_assistant("an answer")
w.log("a tool line")
w.log("a warning", "orange")
settle()
check("four widgets added", message_count(), start + 4)

print("\n-- switching chats clears the previous one completely --")
first_id = w.chat_id
w.start_new_chat(); settle()
check("new chat is empty", message_count(), 0)
check("no orphaned widgets left parented",
      len([c for c in w.messages.widget().children() if isinstance(c, ga.QWidget)]), 0)
check("chat list has two entries", w.chat_list.count(), 2)

print("\n-- history renders when switching back --")
w.history = [{"role": "user", "content": "what are my tasks?"},
             {"role": "assistant", "content": "Three are open.\nStarting on the first."}]
ga.save_chat(first_id, w.history)
w.switch_chat(first_id); settle()
check("both stored messages rendered", message_count(), 2)
check("current chat followed the switch", w.chat_id, first_id)
check("title bar followed the switch", w.chat_title.text(), w.chat_list.currentItem().text())

print("\n-- clicking a chat in the sidebar switches to it --")
other = [w.chat_list.item(i) for i in range(w.chat_list.count())
         if w.chat_list.item(i).data(ga.Qt.ItemDataRole.UserRole) != first_id][0]
# Hold the id, not the item: switching rebuilds the list and destroys every item.
other_id = other.data(ga.Qt.ItemDataRole.UserRole)
w.chat_list.setCurrentItem(other); settle()
check("selection drove switch_chat", w.chat_id, other_id)

print("\n-- the rest of the chrome still works --")
w.on_tokens_used(12400, 0); w.context_size = 32768
w.on_tokens_used(12400, 0)
check("context label updates", "38%" in w.context_label.text(), True)
w.settings["theme"] = "Paper"; w.apply_theme(); settle()
check("a light theme applies", ga.PALETTES["Paper"]["bg"] in (app.styleSheet() or w.styleSheet()), True)
w.settings["theme"] = "Midnight"; w.apply_theme(); settle()
check("a dark theme applies", ga.PALETTES["Midnight"]["bg"] in (app.styleSheet() or w.styleSheet()), True)
w.trust_input.setText(str(box)); w.add_trusted(); settle()
check("trusted folder added and listed", w.trust_picker.count() >= 1, True)
check("composer wired to send", w.send_button.isEnabled(), True)
check("stop starts disabled", w.stop_button.isEnabled(), False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
