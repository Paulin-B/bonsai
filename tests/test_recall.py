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

print("\n-- invented recall is spotted --")
for reply in [
    "In an earlier conversation, we decided the belt handshake should be non-blocking.",
    "The belt handshake was settled in a previous chat: each party sends a pulse.",
    "Last time we discussed this, you wanted flow fields over A*.",
    "We previously agreed to keep the grid at 64x64.",
]:
    check(f"flagged: {reply[:46]}", ga.invented_recall(reply, "READ_FILE: /a.gd -> ok"), True)

print("\n-- but not when it actually looked --")
check("HISTORY in the trace clears it",
      ga.invented_recall("In an earlier conversation we decided on two-phase handshakes.",
                         "HISTORY: belt handshake -> Earlier conversations mentioning that:"), False)

print("\n-- nor when it says it cannot remember --")
for honest in [
    "I have no record of that earlier conversation.",
    "I don't have access to previous chats, so I can't say.",
    "I don't remember a previous discussion about belts.",
    "There's no record of an earlier chat covering that.",
]:
    check(f"not flagged: {honest[:46]}", ga.invented_recall(honest, ""), False)

print("\n-- nor for ordinary talk about this conversation --")
for innocent in [
    "We decided to use a packed array, and I've written it.",
    "I read the previous version of the file from the backup.",
    "The belt moves items at a fixed rate.",
]:
    check(f"not flagged: {innocent[:46]}", ga.invented_recall(innocent, ""), False)

print("\n-- an empty write is refused, not silently created --")
box = Path(tempfile.mkdtemp(prefix="bonsai-write-"))
ga.SETTINGS_FILE = box / "settings.json"
ga.BACKUP_DIR = box / "backups"
ga.save_settings(ga.DEFAULTS)
target = box / "item.gd"
for empty in ["", "   ", "\n\n"]:
    r = ga.do_write(str(target), empty)
    check(f"refused {empty!r}", r.startswith("[Refused: no content"), True)
check("no file left behind", target.exists(), False)
r = ga.do_write(str(target), "extends Node\n")
check("real content still writes", r.startswith("Wrote"), True)
check("  ...and lands on disk", target.read_text(), "extends Node\n")

print("\n-- the correction fires once, then gives up --")
from bonsai_under_test import APP
text = APP.read_text()
check("every nudge has its own one-shot flag",
      [flag for flag in ["corrected", "pressed", "reminded", "searched",
                         "recalled"] if f"{flag} = True" not in text],
      [])
check("nudge tells it the truth about memory",
      "You have no memory of" in text, True)
check("prompt states it too", "You cannot remember earlier conversations" in ga.build_system_prompt(), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
