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

box = Path(tempfile.mkdtemp(prefix="bonsai-record-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings({**ga.DEFAULTS, "vault_path": "", "briefing_on_open": False,
                  "briefing_enabled": False})

print("\n-- repeated calls collapse to one line --")
# A turn that called the same thing over and over stored one identical line per call.
# That is a page of text carrying a single fact, and it is the fuel a model needs to
# multiply the record when it starts copying it back.
trace = "\n".join(["LIST_DIR: /a -> Contents of /a"] * 12
                  + ["READ_FILE: /b.gd -> 1| extends Node"])
narrated = ga.narrate_trace(trace)
check("twelve identical calls become one line", len(narrated.splitlines()), 2)
check("and the line says how many times", "12 times" in narrated, True)
check("the different call is still there", "READ_FILE" in narrated, True)
check("a single call reads plainly",
      ga.narrate_trace("SEARCH: belts -> 3 results"), "- ran SEARCH and got: 3 results")
check("an empty trace narrates to nothing", ga.narrate_trace(""), "")
check("the same tool with different results stays separate",
      len(ga.narrate_trace("FIND: a -> one\nFIND: b -> two").splitlines()), 2)

print("\n-- a record copied back is recognised, terminated or not --")
# Verbatim shape of the reply that ended the real run: the header, hundreds of identical
# lines, and nothing else - it hit the token limit mid-line, so there is no closing "]".
UNTERMINATED = (ga.RECORD_HEADER + "\n"
                + "\n".join(["- ran LIST_DIR and got: Contents of /home/paulinb/Doc"] * 315))
check("the unterminated record is caught", ga.is_record_echo(UNTERMINATED), True)
check("and the old stripper now clears it",
      ga.ACTIONS_ECHO_RE.sub("", UNTERMINATED).strip(), "")
TERMINATED = (ga.RECORD_HEADER + "\n- ran LIST_DIR and got: Contents of /a\n"
              "Anything not listed as succeeding here did NOT happen.]")
check("a properly closed one too", ga.is_record_echo(TERMINATED), True)
check("the collapsed form is recognised as well",
      ga.is_record_echo(ga.RECORD_HEADER + "\n- ran LIST_DIR 12 times, getting the "
                        "same answer every time: Contents of /a"), True)

print("\n-- and ordinary replies are not --")
for label, text in [
    ("plain prose", "The hunger bar is missing from MotherMachine. Adding it now."),
    ("prose about having run something",
     "I ran the tests and they pass. mother_machine.gd now has the hunger bar."),
    ("a short confirmation", "Done - task 5 is marked DONE."),
    ("one quoted record line followed by real work",
     "- ran READ_FILE and got: 1| extends Node\n\nThat confirms the file is there, so I "
     "will add the hunger bar to MotherMachine and wire it to BufferManager per ADR 001."),
    ("an empty reply", ""),
]:
    check(f"{label} is not an echo", ga.is_record_echo(text), False)

print("\n-- the worker must not quietly tidy an empty turn away --")
# This is the seam the first version of the fix fell through. The worker strips stray
# record lines before the window ever sees the reply, so once the stripper was taught to
# handle an unterminated record it removed the whole thing and the window received an
# empty string - and a check for "is this reply only the record?" could never fire.
check("a reply that is only the record survives the strip intact",
      ga.strip_record_echo(UNTERMINATED), UNTERMINATED)
check("so the window can still recognise it",
      ga.is_record_echo(ga.strip_record_echo(UNTERMINATED)), True)
check("a stray record line inside a real reply is still removed",
      ga.strip_record_echo("- ran READ_FILE and got: ok\n\nThe hunger bar is missing, "
                           "so I am adding it to MotherMachine now."),
      "The hunger bar is missing, so I am adding it to MotherMachine now.")
check("and an ordinary reply is untouched",
      ga.strip_record_echo("Added the hunger bar."), "Added the hunger bar.")

print("\n-- in the window --")
ga.Bonsai.start_docker = lambda self: None
app = ga.QApplication(sys.argv)
w = ga.Bonsai()
noted = []
w.log = lambda text, colour=None, **k: noted.append((text, colour))
w.maybe_learn_skill = lambda *a, **k: None
w.maybe_consolidate = lambda *a, **k: None
w.dispatch = lambda *a, **k: None       # no turn is actually sent from a test
# Auto mode stops the moment the list is clear, so a round that is meant to carry on
# needs something left to do.
ga.save_tasks([{"id": 1, "text": "implement the hunger bar", "done": False,
                "evidence": ""}], 2)

def turn(reply, trace, auto=True):
    """One turn, from a clean slate - auto_last_trace included, or the second scenario
    to use a given trace stops as an exact repeat of the first, which is auto mode
    working correctly and has nothing to do with what is being tested here."""
    noted.clear()
    w.history = []
    w.auto_running = auto
    w.auto_last_trace = None
    w.auto_round = 0
    w.sent_prompt = "do the work"
    w.last_vision = False
    w.on_reply(reply, trace)
    return w.history

print("\n-- the record is no longer part of what the model said --")
history = turn("I read the file and added the hunger bar.", "READ_FILE: /b.gd -> ok")
roles = [m["role"] for m in history]
check("the turn is stored as prompt, reply, then the record", roles,
      ["user", "assistant", "user"])
assistant = next(m["content"] for m in history if m["role"] == "assistant")
check("the assistant message is only what it said", assistant,
      "I read the file and added the hunger bar.")
check("the record is not inside it", ga.RECORD_HEADER in assistant, False)
check("it is in the message after", ga.RECORD_HEADER in history[-1]["content"], True)
check("which does not come from the assistant", history[-1]["role"], "user")
check("and still carries what ran", "ran READ_FILE" in history[-1]["content"], True)

history = turn("Nothing to do here.", "")
check("a turn with no tools stores no record", [m["role"] for m in history],
      ["user", "assistant"])

print("\n-- a turn that only copies the record is thrown away --")
history = turn(UNTERMINATED, "")
stored = next(m["content"] for m in history if m["role"] == "assistant")
check("the echo is not stored", ga.RECORD_HEADER in stored, False)
check("a note is stored in its place", stored.startswith("(Discarded:"), True)
check("nothing of the 26k reply survives", len(stored) < 200, True)
check("the user is told", any("copy of the turn record" in t for t, _ in noted), True)
check("in orange", any(c == "orange" for _, c in noted), True)
check("and auto mode is stopped", w.auto_running, False)

print("\n-- a real reply does not trip it --")
history = turn("Added the hunger bar to MotherMachine.", "READ_FILE: /b.gd -> ok")
check("auto mode keeps going", w.auto_running, True)
check("and the reply is stored as written",
      next(m["content"] for m in history if m["role"] == "assistant"),
      "Added the hunger bar to MotherMachine.")

print("\n-- a crash in a turn leaves a traceback behind --")
# 882KB of real debug log held no traceback at all, so every crash in the worker was
# invisible afterwards: the user saw "Error: ..." and there was nothing to look at.
ga.DEBUG_LOG_FILE.write_text("")
try:
    raise ValueError("something went wrong deep inside")
except ValueError:
    import traceback as _tb
    ga.worker.debug_note("CRASH", _tb.format_exc())
written = ga.DEBUG_LOG_FILE.read_text()
check("the entry is tagged", "=== " in written and "CRASH ===" in written, True)
check("and holds the traceback, not just the message", "Traceback" in written, True)
check("naming where it happened", "test_record.py" in written, True)

print("\n-- the project the prompt describes is the one with work in it --")
# Only writes add to a ledger. Reading documents in another trusted folder stamped its
# timestamp while recording nothing, and that empty folder then outranked the project
# being worked on - so the prompt described a folder with no files, and the ledger
# naming the exact source file went unmentioned. The model hunted for that file in the
# only place the prompt pointed at, found nothing, and looped until the turn died.
code = box / "code"
docs = box / "docs"
for d in (code, docs):
    (d / "src" / "logistics").mkdir(parents=True, exist_ok=True)
(code / "src" / "logistics" / "mother_machine.gd").write_text("extends Node\n")
ga.save_trusted([str(code), str(docs)])

def ledgers(code_entry, docs_entry):
    ga.save_json(ga.PROJECTS_FILE, {"projects": {str(code): code_entry,
                                                 str(docs): docs_entry}})

worked_in = {"files": {"src/logistics/mother_machine.gd": "2026-09-11T14:41"},
             "notes": ["Godot factory-sim project."], "updated": "2026-09-11T14:58"}
only_read = {"files": {}, "notes": [], "updated": "2026-09-14T22:34"}

ledgers(worked_in, only_read)
check("a folder that was only read does not become the project",
      ga.active_project(), str(code))
check("and its files reach the prompt",
      "mother_machine.gd" in ga.project_summary(), True)

ledgers(worked_in, {**only_read, "notes": ["the design notes live here"]})
check("but a folder with notes of its own can win on recency",
      ga.active_project(), str(docs))

ledgers({**worked_in, "updated": "2026-09-20T09:00"},
        {**only_read, "notes": ["the design notes live here"]})
check("and recency still decides between two that both have content",
      ga.active_project(), str(code))

ledgers({"files": {}, "notes": [], "updated": ""}, only_read)
check("with nothing recorded anywhere it still picks one",
      ga.active_project() in (str(code), str(docs)), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
