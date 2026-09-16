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

box = Path(tempfile.mkdtemp(prefix="bonsai-attach-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","PROJECTS_FILE",
          "INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False})
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(1480, 760); win.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

files = []
for name in ("alpha.py", "beta.md", "gamma.txt"):
    path = box / name
    path.write_text(f"content of {name}\n")
    files.append(path)

def drop(paths):
    for path in paths:
        text = ga.read_file(str(path), numbered=False)
        win.attachments.append({"path": str(path), "name": path.name,
                                "text": text, "chars": len(text)})
    win.attach_bar.show_files(win.attachments)
    settle()

def chips():
    """The file names currently shown as chips, in order."""
    found = []
    for i in range(win.attach_bar.row.count()):
        widget = win.attach_bar.row.itemAt(i).widget()
        if widget is not None and widget.objectName() == "attachChip":
            found.append(widget.findChild(ga.QLabel).text())
    return found

print("\n-- queued files are visible, which is what makes them removable --")
check("nothing queued, nothing shown", win.attach_bar.isVisible(), False)
drop(files)
check("the bar appears", win.attach_bar.isVisible(), True)
check("one chip per file", chips(), ["alpha.py", "beta.md", "gamma.txt"])
check("and a Clear all, since there are several",
      any(win.attach_bar.row.itemAt(i).widget() is not None
          and isinstance(win.attach_bar.row.itemAt(i).widget(), ga.QPushButton)
          for i in range(win.attach_bar.row.count())), True)

print("\n-- removing the wrong one leaves the right ones --")
win.remove_attachment(1)
settle()
check("the middle file is gone", [a["name"] for a in win.attachments],
      ["alpha.py", "gamma.txt"])
check("and the chips agree", chips(), ["alpha.py", "gamma.txt"])
win.remove_attachment(0)
settle()
check("removing the first works too", chips(), ["gamma.txt"])
check("one file needs no Clear all",
      any(isinstance(win.attach_bar.row.itemAt(i).widget(), ga.QPushButton)
          for i in range(win.attach_bar.row.count())), False)
win.remove_attachment(0)
settle()
check("removing the last empties the queue", win.attachments, [])
check("and hides the bar", win.attach_bar.isVisible(), False)

print("\n-- an index that isn't there changes nothing --")
drop(files[:2])
win.remove_attachment(9)
settle()
check("out of range is ignored", len(win.attachments), 2)
win.remove_attachment(-1)
settle()
check("clear all empties it", win.attachments, [])
check("and hides the bar", win.attach_bar.isVisible(), False)

print("\n-- the x button is wired to the file beside it --")
drop(files)
removed = []
win.attach_bar.removed.connect(removed.append)
buttons = [w for w in win.attach_bar.findChildren(ga.QPushButton)
           if w.objectName() == "attachDrop"]
check("one x per file", len(buttons), 3)
buttons[2].click()
check("the third x asks to drop the third file", removed[-1], 2)
settle()
check("and it is the one that went", chips(), ["alpha.py", "beta.md"])

print("\n-- what is queued is what gets sent, through the real send path --")
win.attachments = []
win.attach_bar.show_files([])
drop([files[0], files[1]])

# Stand in for the Worker so dispatch runs exactly as it does in the app, but nothing
# is sent anywhere. Testing a copy of dispatch's logic would not have caught a change
# to dispatch itself, which is the thing that could drop an attachment.
sent = {}
class FakeWorker(ga.Worker):
    def __init__(self, prompt, vision, history, config):
        super().__init__(prompt, vision, history, config)
        sent["prompt"] = prompt
    def start(self):
        pass
real_worker = ga.Worker
ga.Worker = FakeWorker
try:
    win.input.setPlainText("summarise these")
    win.send()
    settle()
finally:
    ga.Worker = real_worker

check("both files rode along", sent["prompt"].count("--- FILE:"), 2)
check("the first is there", "alpha.py" in sent["prompt"], True)
check("the one that was removed earlier is not", "gamma.txt" in sent["prompt"], False)
check("and the message itself is still there", "summarise these" in sent["prompt"], True)
check("sending empties the queue", win.attachments, [])
check("and hides the bar", win.attach_bar.isVisible(), False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
