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

box = Path(tempfile.mkdtemp(prefix="bonsai-play-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
ga.save_settings(ga.DEFAULTS)

# Input is injected at the keyboard, so nothing here may actually send any: a test that
# really pressed a key would press it into whatever the person running it was doing.
sent = []
ga.shell._ydotool = lambda args: sent.append(args) or ""

OURS = {"pid": 4242, "class": "godot", "title": "Factory Sim", "address": "0xaaa",
        "at": [100, 100], "size": [800, 600]}
THEIRS = {"pid": 9999, "class": "codium", "title": "settings.py - VSCodium",
          "address": "0xbbb", "at": [0, 0], "size": [1920, 1080]}

def stage(window, ours=True, cursor=(500, 400), ready=True):
    sent.clear()
    ga.shell.focused_window = lambda: window
    ga.shell.pid_is_ours = lambda pid: ours
    ga.shell.cursor_position = lambda: cursor
    ga.shell.input_ready = lambda: (ready, "" if ready else "[Refused: no daemon.]")

print("\n-- keys only ever go to a window Bonsai opened --")
# Input is injected at the kernel and lands in whatever has focus, so this check is the
# only thing between testing a game and typing into the editor behind it.
for call in ["KEY space", "TYPE hello", "HOLD right 300", "CLICK left", "POINT 10 10"]:
    stage(THEIRS, ours=False)
    out = ga.handle_play(call)
    check(f"{call.split()[0]} is refused when the editor has focus",
          out.startswith("[Refused:"), True)
    check(f"and {call.split()[0]} sent nothing", sent, [])
stage(THEIRS, ours=False)
check("the refusal names what has focus", "codium" in ga.handle_play("KEY space"), True)
check("and says how to aim properly", "PLAY: FOCUS" in ga.handle_play("KEY space"), True)

print("\n-- and do go to one it did --")
stage(OURS)
out = ga.handle_play("KEY space")
check("a key is pressed", out.startswith("Pressed space"), True)
check("as a press and a release", sent, [["key", "57:1", "57:0"]])
stage(OURS)
ga.handle_play("KEY ctrl+s")
check("a combination presses in order and releases in reverse",
      sent, [["key", "29:1", "31:1", "31:0", "29:0"]])
stage(OURS)
ga.handle_play("TYPE hello")
check("typing passes the text through", sent, [["type", "hello"]])

print("\n-- holding a key, which is how you walk --")
stage(OURS)
out = ga.handle_play("HOLD right 50")
check("it goes down and comes back up", [s[1] for s in sent], ["106:1", "106:0"])
check("and says how long for", "50ms" in out, True)
stage(OURS)
check("a silly duration is refused",
      ga.handle_play(f"HOLD right {ga.MAX_HOLD_MS + 1}").startswith("[Hold must be"), True)
stage(OURS)
check("so is one that is not a number",
      "Couldn't read" in ga.handle_play("HOLD right soon"), True)

print("\n-- keys that belong to the desktop rather than the program --")
stage(OURS)
check("super is refused", "window manager" in ga.handle_play("KEY super+d"), True)
stage(OURS)
check("so is ctrl with a function key",
      "virtual console" in ga.handle_play("KEY ctrl+f1"), True)
stage(OURS)
check("nothing was sent for either", sent, [])
stage(OURS)
out = ga.handle_play("KEY nosuchkey")
check("an unknown key is refused rather than guessed at",
      out.startswith("[Unknown key"), True)

print("\n-- a click goes where the pointer is, not where focus is --")
# Found the hard way: the focus check passes, the click still lands on whatever is
# under the mouse. The first version of this tool clicked into thin air for that reason,
# and on a different pointer position would have clicked in the editor.
stage(OURS, cursor=(50, 50))          # outside the window at (100,100) 800x600
out = ga.handle_play("CLICK left")
check("clicking with the pointer elsewhere is refused",
      out.startswith("[Refused:"), True)
check("and it explains the difference from focus", "lands where" in out, True)
check("pointing at the fix", "PLAY: POINT" in out, True)
check("nothing was clicked", sent, [])

stage(OURS, cursor=(500, 400))        # inside
out = ga.handle_play("CLICK left")
check("with the pointer inside, the click happens", out.startswith("Clicked left"), True)
# 0xC0 not 0x00: the button value alone is documented as doing nothing at all, which
# it does without complaining, so a click was reported that never happened.
check("as a full press and release of the left button", sent, [["click", "0xc0"]])
stage(OURS, cursor=(500, 400))
ga.handle_play("CLICK right")
check("right is a different button", sent, [["click", "0xc1"]])
stage(OURS, cursor=(500, 400))
check("an unknown button is refused",
      "Unknown button" in ga.handle_play("CLICK sideways"), True)

print("\n-- the pointer is never moved out of the window --")
stage(OURS, cursor=(500, 400))
check("a move that would leave it is refused",
      "outside" in ga.handle_play("MOVE 5000 0").lower(), True)
check("and nothing moved", sent, [])
stage(OURS, cursor=(500, 400))
out = ga.handle_play("MOVE 20 20")
check("a move that stays inside is allowed", out.startswith("Moved the pointer"), True)
stage(OURS)
check("POINT outside the window is refused",
      "outside the window" in ga.handle_play("POINT 9999 10"), True)

print("\n-- POINT reports where the pointer really ended up --")
# Neither kind of move can be trusted: an absolute move is in the input device's own
# coordinates, not the compositor's, and a relative one is fed through pointer
# acceleration. Both report success regardless, so the position has to be read back.
moved = {"at": (0, 0)}
ga.shell.cursor_position = lambda: moved["at"]
def creep(args):
    # Stands in for a device that moves less than it is asked to.
    dx, dy = int(args[2]), int(args[4])
    moved["at"] = (moved["at"][0] + dx // 2, moved["at"][1] + dy // 2)
    return ""
ga.shell._ydotool = creep
ga.shell.focused_window = lambda: OURS
ga.shell.pid_is_ours = lambda pid: True
out = ga.handle_play("POINT 400 300")
check("it converges on the target anyway", out.startswith("Pointer is at"), True)
# The contract is "close enough, and honest about where that is" - not an exact landing,
# which no amount of correction can promise through an accelerated pointer.
landed = tuple(int(n) for n in out.split("(")[1].split(")")[0].split(","))
check("within the tolerance it claims",
      abs(landed[0] - 400) + abs(landed[1] - 300) <= ga.POINTER_TOLERANCE, True)

stuck = {"at": (0, 0)}
ga.shell.cursor_position = lambda: stuck["at"]
ga.shell._ydotool = lambda args: ""        # claims success, never moves
out = ga.handle_play("POINT 400 300")
check("a pointer that will not move is reported, not claimed",
      out.startswith("[The pointer is at"), True)
check("naming where it actually is", "(-100, -100)" in out, True)
ga.shell._ydotool = lambda args: sent.append(args) or ""

print("\n-- how far input may reach is a setting --")
check("the scopes are the three offered", sorted(ga.PLAY_SCOPES),
      ["any", "approved", "own"])
check("and the default is the narrowest", ga.DEFAULTS["play_scope"], "own")
stage(THEIRS, ours=False)
check("under 'own' someone else's window is refused",
      ga.handle_play("KEY space", "own").startswith("[Refused:"), True)
stage(THEIRS, ours=False)
check("under 'approved' it is refused until approved",
      ga.handle_play("KEY space", "approved").startswith("[Refused:"), True)
check("and the refusal says approval would settle it",
      "Approve it" in ga.handle_play("KEY space", "approved"), True)
ga.approve_window("0xbbb")
stage(THEIRS, ours=False)
check("once approved it goes through",
      ga.handle_play("KEY space", "approved").startswith("Pressed"), True)
stage(THEIRS, ours=False)
check("under 'any' nothing is checked",
      ga.handle_play("KEY space", "any").startswith("Pressed"), True)

print("\n-- with no way to tell what has focus, nothing is sent --")
stage(OURS)
ga.shell.focused_window = lambda: None
out = ga.handle_play("KEY space")
check("it refuses rather than firing blind", out.startswith("[Refused:"), True)
check("saying why it cannot be sure", "which window has focus" in out, True)
check("and nothing was sent", sent, [])
stage(OURS, ready=False)
check("nor when the daemon is not running",
      ga.handle_play("KEY space"), "[Refused: no daemon.]")

print("\n-- the tool form --")
check("HOLD carries the duration",
      ga.native_call_to_tool("play", {"action": "HOLD", "keys": "right",
                                      "milliseconds": 800}),
      ("PLAY", "HOLD right 800"))
check("POINT uses the same two numbers as MOVE",
      ga.native_call_to_tool("play", {"action": "POINT", "dx": 40, "dy": 90}),
      ("PLAY", "POINT 40 90"))
check("FOCUS names a window class",
      ga.native_call_to_tool("play", {"action": "FOCUS", "window": "godot"}),
      ("PLAY", "FOCUS godot"))
check("it is offered as a tool",
      "play" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)
check("and it loads when the request is about trying a game",
      "play" in ga.relevant_tools("start the game and see if the bot walks"), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
