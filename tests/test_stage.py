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

box = Path(tempfile.mkdtemp(prefix="bonsai-stage-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
work = box / "proj"
work.mkdir()
ga.save_trusted([str(work)])
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": False})

print("\n-- with nothing installed, it says what to install --")
real_which = ga.stage.shutil.which
ga.stage.shutil.which = lambda name: None
ready, why = ga.tooling_ready()
check("not ready", ready, False)
check("and it names the packages, not the binaries", "xorg-server-xephyr" in why, True)
check("xdotool too", "xdotool" in why, True)
check("starting refuses rather than half-working",
      ga.handle_stage("START godot").startswith("[Refused:"), True)
ga.stage.shutil.which = real_which

print("\n-- nothing works until a stage is open --")
for call in ["KEY space", "TYPE hello", "HOLD Right 200", "CLICK left", "MOVE 5 5"]:
    check(f"{call.split()[0]} says there is no stage",
          "No stage is open" in ga.handle_stage(call), True)
check("LOOK says so too", ga.stage_look()[0].startswith("[No stage"), True)
check("and hands back no picture", ga.stage_look()[1], None)
check("status is plain about it", ga.handle_stage("STATUS"), "(no stage is open)")
check("stopping nothing is not an error", ga.handle_stage("STOP"), "(no stage was open)")

print("\n-- the display it picks is a free one, clear of a real session --")
check("it never picks :0 or :1", ga.FIRST_DISPLAY > 1, True)
free = ga.free_display()
check("a free display is offered", free.startswith(":"), True)
check("inside the range it reserves",
      ga.FIRST_DISPLAY <= int(free[1:]) <= ga.LAST_DISPLAY, True)
check("and it is genuinely unused", Path(f"/tmp/.X11-unix/X{free[1:]}").exists(), False)

print("\n-- a display that never comes up is not reported as a stage --")
# The lesson from PLAY, twice over: a process existing is not the same as the thing
# working. An X server that fails still leaves a process behind for a moment.
class _Dead:
    pid = 424242            # the compositor is asked which window belongs to this
    def __init__(self, *a, **k): pass
    def poll(self): return 1
    def terminate(self): pass
    def wait(self, timeout=None): return 1
    def kill(self): pass
real_popen = ga.stage.subprocess.Popen
ga.stage.shutil.which = lambda name: f"/usr/bin/{name}"
ga.stage.subprocess.Popen = _Dead
ga.stage.display_is_up = lambda display: False
out = ga.handle_stage(f"START python3 loop.py | {work}")
check("it says the server died rather than claiming a stage",
      "display server exited immediately" in out, True)
check("and no stage is left behind", ga.handle_stage("STATUS"), "(no stage is open)")

print("\n-- nor is a program that exits the moment it starts --")
(work / "loop.py").write_text("print('boom')\n")
class _Alive(_Dead):
    def poll(self): return None
ga.stage.display_is_up = lambda display: True
ga.stage.subprocess.Popen = lambda *a, **k: _Alive()
# No real window belongs to a fake pid, and these tests must not touch the desktop.
ga.stage.window_of_pid = lambda pid: None
ga.stage.restore_focus = lambda window: True
out = ga.handle_stage(f"START python3 loop.py | {work}")
check("a live program gives a stage", out.startswith("Stage open on"), True)
check("which says input cannot reach the desktop", "desktop is untouched" in out, True)
check("a second stage is refused while one is open",
      "already open" in ga.handle_stage(f"START python3 loop.py | {work}"), True)
check("and it can be closed", ga.handle_stage("STOP").startswith("Stage on"), True)

ga.stage.subprocess.Popen = lambda *a, **k: _Dead()
out = ga.handle_stage(f"START python3 loop.py | {work}")
check("a program that exits at once is reported, not counted as running",
      "exited immediately" in out, True)
check("leaving no stage open", ga.handle_stage("STATUS"), "(no stage is open)")
ga.stage.subprocess.Popen = real_popen

print("\n-- the refusals a command gets anywhere else still apply --")
ga.stage.shutil.which = lambda name: f"/usr/bin/{name}"
check("a forbidden command is still forbidden",
      "never allowed" in ga.handle_stage(f"START sudo rm -rf / | {work}"), True)
check("shell metacharacters are still refused",
      "shell metacharacters" in ga.handle_stage(f"START python3 x.py; rm -rf ~ | {work}"),
      True)
ga.stage.shutil.which = real_which

print("\n-- input is aimed by environment, not by focus --")
# This is the whole point: there is no focus to check and no pointer of yours to move,
# because the display the program sees is not the one you are using.
stage = ga.stage.Stage(":80", _Alive(), ["godot"], True, "1280x720")
env = stage.env
check("the program is pointed at the stage", env["DISPLAY"], ":80")
check("and cannot see the real session", "WAYLAND_DISPLAY" in env, False)
check("nor be told it is a wayland one", "XDG_SESSION_TYPE" in env, False)

print("\n-- what gets run for each action --")
ran = []
ga.stage._stage = stage
# subprocess is the shared module object, so this patch is process-wide: put it back
# before anything else in this file runs a command for real.
real_run = ga.stage.subprocess.run
ga.stage.subprocess.run = lambda argv, **k: ran.append(argv) or type(
    "R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
ga.handle_stage("KEY space")
check("a key press", ran[-1][:2], ["xdotool", "key"])
check("with modifiers cleared, so a held shift cannot leak in",
      "--clearmodifiers" in ran[-1], True)
ga.handle_stage("TYPE hello")
check("typing", ran[-1][:2], ["xdotool", "type"])
ga.handle_stage("MOVE 40 90")
check("a move is to a position on the stage, not a movement",
      ran[-1], ["xdotool", "mousemove", "--sync", "40", "90"])
ran.clear()
ga.handle_stage("CLICK left 40 90")
check("a click can carry where to click", ran[0][:3], ["xdotool", "mousemove", "--sync"])
check("and then clicks the left button", ran[1], ["xdotool", "click", "1"])
ran.clear()
ga.handle_stage("CLICK right")
check("right is button 3", ran[-1], ["xdotool", "click", "3"])
check("an unknown button is refused", "Unknown button" in ga.handle_stage("CLICK up"), True)
ran.clear()
ga.handle_stage("HOLD Right 30")
check("holding is a keydown and a keyup",
      [r[1] for r in ran], ["keydown", "keyup"])
check("a silly duration is refused",
      ga.handle_stage(f"HOLD Right {ga.MAX_STAGE_HOLD_MS + 1}").startswith("[Hold must be"),
      True)

ga.stage.subprocess.run = real_run

print("\n-- and the window closing takes the stage with it --")
check("there is a stage to close", ga.stage._stage is not None, True)
check("closing names where it was", ga.stop_stage(), ":80")
check("and nothing is left open", ga.stage._stage, None)
check("closing again is harmless", ga.stop_stage(), None)

print("\n-- the tool form --")
check("START carries the folder",
      ga.native_call_to_tool("stage", {"action": "START", "command": "godot --path .",
                                       "working_directory": "/media/D/Bonsai-Storage"}),
      ("STAGE", "START godot --path . | /media/D/Bonsai-Storage"))
check("CLICK can name a spot",
      ga.native_call_to_tool("stage", {"action": "CLICK", "button": "left",
                                       "x": 200, "y": 90}),
      ("STAGE", "CLICK left 200 90"))
check("and can leave one out",
      ga.native_call_to_tool("stage", {"action": "CLICK", "button": "right"}),
      ("STAGE", "CLICK right"))
check("LOOK needs nothing",
      ga.native_call_to_tool("stage", {"action": "LOOK"}), ("STAGE", "LOOK"))
check("it is offered as a tool",
      "stage" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)
check("and loads for a request about testing a game while working",
      "stage" in ga.relevant_tools("run the game and test it while I work"), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
