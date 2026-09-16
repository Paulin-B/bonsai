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

box = Path(tempfile.mkdtemp(prefix="bonsai-edit-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False})
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
win = ga.Bonsai(); win.resize(1100, 700); win.show()
def settle(n=4):
    for _ in range(n): app.processEvents()
settle()

chat = ga.new_chat(); ga.title_chat(chat, "shader work")
ga.save_chat(chat, [
    {"role": "user", "content": "make the tint warmer"},
    {"role": "assistant", "content": "Done - multiplied by a warm vec3."},
    {"role": "user", "content": "now make it purple"},
    {"role": "assistant", "content": "Purple applied."},
])
win.switch_chat(chat)
settle()

def bubbles():
    return win.messages.findChildren(ga.UserMessage)

def buttons(name):
    return [b for b in win.messages.findChildren(ga.QPushButton)
            if b.objectName() == name]

print("\n-- every message you sent can be changed --")
check("one editable bubble per user message", len(bubbles()), 2)
check("each has an edit button", len(buttons("editButton")), 2)
check("and a branch button beside it", len(buttons("branchButton")), 2)

print("\n-- editing opens the message for typing --")
first = bubbles()[0]
check("no editor until asked", first.editor, None)
first.begin_edit(); settle()
check("the editor appears", first.editor is not None, True)
check("prefilled with what was said", first.editor.toPlainText(), "make the tint warmer")
check("the read-only text is hidden", first.body.isVisible(), False)
check("and the buttons get out of the way",
      any(b.isVisible() for b in first.tools), False)
check("asking twice does not stack editors",
      (first.begin_edit(), first.editor)[1] is not None, True)

print("\n-- cancelling puts it back exactly as it was --")
first.editor.setPlainText("something else entirely")
first.end_edit(); settle()
check("the editor is gone", first.editor, None)
check("the original text is showing again", first.body.isVisible(), True)
check("nothing was sent", len(win.history), 4)
check("and the chat did not change", win.chat_id, chat)

print("\n-- newlines survive the round trip --")
multi = ga.UserMessage("first line<br>second line", index=0)
multi.begin_edit()
check("stored <br> is edited as a real newline",
      multi.editor.toPlainText(), "first line\nsecond line")

print("\n-- asking again forks rather than rewriting history --")
sent = {}
real_dispatch = ga.Bonsai.dispatch
win.dispatch = lambda prompt, vision, retry, unattended=False: sent.update(
    prompt=prompt, retry=retry)
second = bubbles()[1]
second.begin_edit()
second.editor.setPlainText("make it teal instead")
second._commit(); settle()

check("the reworded message was sent", sent.get("prompt"), "make it teal instead")
check("not as a retry", sent.get("retry"), False)
check("we are on a new chat", win.chat_id != chat, True)
index = {c["id"]: c for c in ga.load_chat_index()["chats"]}
check("which knows where it came from", index[win.chat_id]["parent"], chat)
check("holding only what came before that message", len(win.history), 2)
check("the original still has all four", len(ga.load_chat(chat)), 4)
check("including the wording you replaced",
      ga.load_chat(chat)[2]["content"], "now make it purple")

print("\n-- an edit that changes nothing is not a new turn --")
sent.clear()
win.switch_chat(chat); settle()
same = bubbles()[0]
same.begin_edit()
same._commit(); settle()
check("nothing sent when the text is unchanged", sent, {})
check("nor when it is only whitespace-different",
      (same.begin_edit(), same.editor.setPlainText("  make the tint warmer  "),
       same._commit(), sent)[3], {})
settle()
check("and the chat is untouched", win.chat_id, chat)

print("\n-- an empty edit is refused rather than sending nothing --")
sent.clear()
blank = bubbles()[0]
blank.begin_edit(); blank.editor.setPlainText("   "); blank._commit(); settle()
check("nothing sent", sent, {})
win.on_edit_message(1, "   ")
check("and the window refuses it too", sent, {})

print("\n-- it will not cut in on a turn that is still running --")
class Busy:
    def isRunning(self): return True
win.worker = Busy()
sent.clear()
before = win.chat_id
win.on_edit_message(1, "change of plan")
settle()
check("nothing sent mid-turn", sent, {})
check("and the chat does not move", win.chat_id, before)
win.worker = None

print("\n-- editing belongs to this line of work, so auto mode stops --")
win.auto_running = True
win.continuations = 2
win.pending_handoff = ("steps", "DONE: x\nNEXT: y")
win.on_edit_message(1, "try it another way")
settle()
check("auto mode stopped", win.auto_running, False)
check("continuation budget reset", win.continuations, 0)
check("stale handoff dropped", win.pending_handoff, None)
check("and the new wording is what a continuation would serve",
      win.origin_prompt, "try it another way")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
