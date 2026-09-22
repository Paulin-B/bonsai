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

print("\n-- the settings pages fit, whatever is on them --")
# A page taller than the dialog used to squeeze its rows past their own minimum heights
# and draw them on top of each other: the wrapped note about Servers landed across the
# box below it. Measured from real geometry, and only after raising each tab, because
# a page that has never been shown reports a default 640x480 and would pass anything.
app.setStyleSheet(ga.stylesheet("Midnight", "System", 13))
settings = dict(ga.DEFAULTS)
settings["endpoints"] = [
    {"name": f"a rather long server name number {n}",
     "url": f"http://localhost:808{n}/v1/chat/completions", "model": "",
     "compose": f"/media/D/Projects/Local_AI/docker-compose.number-{n}.yml"}
    for n in range(3)]
dialog = ga.SettingsDialog(settings, None)
dialog.resize(560, 562)      # the size it opens at on a laptop screen
dialog.show()

def rows_of(page):
    form = dialog.forms[page]
    found = []
    for i in range(form.rowCount()):
        for role in (ga.QFormLayout.ItemRole.LabelRole,
                     ga.QFormLayout.ItemRole.FieldRole,
                     ga.QFormLayout.ItemRole.SpanningRole):
            item = form.itemAt(i, role)
            if item and item.widget():
                found.append((i, item.widget()))
    return found

for index, page in enumerate(dialog.PAGES):
    dialog.tabs.setCurrentIndex(index)
    settle(6)
    rows = rows_of(page)
    clashes = {(a_row, b_row)
               for a_row, a in rows for b_row, b in rows
               if a_row < b_row and a.geometry().isValid() and b.geometry().isValid()
               and a.geometry().intersects(b.geometry())}
    check(f"nothing overlaps on {page}", clashes, set())

# A checkbox label cannot wrap, so one long enough simply runs off the side of the
# dialog and the end of the sentence is invisible. That is what happened on Voice.
for index, page in enumerate(dialog.PAGES):
    dialog.tabs.setCurrentIndex(index)
    settle(6)
    viewport = dialog.tabs.widget(index).viewport().width()
    spilling = [w for _, w in rows_of(page)
                if w.geometry().isValid() and w.geometry().right() > viewport]
    check(f"nothing runs off the side of {page}", spilling, [])

check("every page scrolls rather than squeezing",
      all(isinstance(dialog.tabs.widget(i), ga.QScrollArea)
          for i in range(dialog.tabs.count())), True)

print("\n-- and the Model page reads in the right order --")
dialog.tabs.setCurrentIndex(0)
settle(6)
labels = [w.text() for _, w in rows_of("Model")
          if isinstance(w, ga.QLabel) and w.text().endswith(":")]
check("the server in use comes first", labels[0], "Server URL:")
check("then what it is serving", labels[1], "Model name:")
check("and the list you switch between sits under that", labels[2], "Servers:")
check("before the numbers", labels[3], "Max response length (tokens):")

check("the servers box does not wrap, so one line is one server",
      dialog.endpoints_editor.lineWrapMode(), ga.QTextEdit.LineWrapMode.NoWrap)
shown = dialog.endpoints_editor.toPlainText().splitlines()
check("all three servers are on their own line", len(shown), 3)
check("and the box is tall enough to show them at once",
      dialog.endpoints_editor.height()
      >= dialog.endpoints_editor.fontMetrics().lineSpacing() * 3, True)
dialog.close()

print("\n-- the transcript keeps up with what is added to it --")
# It used to scroll on append, which read the scrollbar maximum as it was BEFORE the
# new block had been measured - so the view sat one message behind and a long reply
# arrived mostly below the fold. Measured from the real scrollbar, not from the call.
feed = ga.MessageList(ga.THEMES["dark"])
# Fixed, not resize(): a shown top-level grows to fit its contents, so the viewport
# ended up taller than everything in it and the scrollbar had a range of nothing.
# Every check below then passed by being unable to scroll at all.
feed.setFixedSize(700, 400)
feed.show()
settle(8)
bar = feed.verticalScrollBar()
long_reply = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))

def at_end():
    return bar.value() >= bar.maximum()

feed.add_note("ran READ_FILE")
settle(8)
check("a short note leaves it at the end", at_end(), True)
feed.add_assistant(long_reply)
settle(8)
check("a reply taller than the window still lands at the end", at_end(), True)
check("and there is something to scroll", bar.maximum() > 0, True)
feed.add_assistant(long_reply)
settle(8)
check("and the next one too", at_end(), True)

print("\n-- but it does not drag you away from what you are reading --")
bar.setValue(200)
settle(4)
check("scrolling up stops it following", feed.following, False)
feed.add_assistant(long_reply)
settle(8)
check("a reply arriving leaves you where you were", bar.value(), 200)
feed.add_note("ran FIND")
settle(8)
check("so does a tool note", bar.value(), 200)

feed.add_user("what about the belts?")
settle(8)
check("but your own message brings you back down", at_end(), True)
check("because sending one means you want to see it", feed.following, True)

bar.setValue(150)
settle(4)
check("scrolled away again", feed.following, False)
bar.setValue(bar.maximum())
settle(4)
check("returning to the bottom resumes following", feed.following, True)
feed.add_assistant(long_reply)
settle(8)
check("and the next reply keeps up", at_end(), True)

check("a near-miss of the bottom still counts as following",
      (bar.setValue(bar.maximum() - 2), settle(2), feed.following)[2], True)
feed.close()

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
