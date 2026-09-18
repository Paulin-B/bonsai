"""A nested X display Bonsai can run a program on and drive by itself.

PLAY sends input through the kernel, which means it goes wherever keyboard focus is:
it can only ever drive the window in front, and it takes the machine over while it does.
A stage is the other approach. The program gets an X server of its own, and input is
delivered to that server rather than to the seat, so nothing here can reach the desktop
even in principle - there is no focus to steal and no pointer to move. The person at the
keyboard carries on working while the program is being tried out beside them.

Xephyr shows the stage in a window you can watch; Xvfb is the same thing with nothing to
see. Either way the program believes it has a normal display.
"""

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from .media import encode_frame
from .shell import (
    classify_command, focused_window, park_window, restore_focus, trim_output,
    window_of_pid,
)


# Display numbers are picked from here upward. High enough to stay clear of a real
# session, which is :0 or :1.
FIRST_DISPLAY = 80
LAST_DISPLAY = 96


# A stage that never came up is worth giving up on rather than hanging a turn.
DISPLAY_TIMEOUT = 15


MAX_STAGE_HOLD_MS = 10000


# Where a watchable stage is put so it is not under the pointer. With focus-follows-mouse
# a window sitting where the mouse is takes focus back however often it is handed over.
STAGE_WORKSPACE = 9


_stage = None


class Stage:
    """One nested display and the program running on it."""

    def __init__(self, display, server, argv, visible, size):
        self.display = display
        self.server = server
        self.argv = argv
        self.visible = visible
        self.size = size
        self.program = None
        self.started = time.time()
        self.log = Path(tempfile.gettempdir()) / f"bonsai-stage{display.lstrip(':')}.log"

    @property
    def env(self):
        env = dict(os.environ)
        env["DISPLAY"] = self.display
        # A program that can see the real Wayland session will use it in preference and
        # open on the desktop instead of on the stage, which is the whole thing this is
        # meant to avoid.
        for name in ("WAYLAND_DISPLAY", "XDG_SESSION_TYPE"):
            env.pop(name, None)
        return env

    def alive(self):
        return self.server.poll() is None

    def program_alive(self):
        return self.program is not None and self.program.poll() is None

    def status(self):
        seconds = int(time.time() - self.started)
        if not self.alive():
            return f"the stage itself has stopped (after {seconds}s)"
        if self.program is None:
            return f"up for {seconds}s with nothing running on it"
        code = self.program.poll()
        if code is None:
            return f"running {Path(self.argv[0]).name} for {seconds}s"
        return f"{Path(self.argv[0]).name} exited with code {code} after {seconds}s"

    def stop(self):
        for process in (self.program, self.server):
            if process is None or process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def tooling_ready(visible=True):
    """Whether a stage can be run at all. Returns (ready, what is missing)."""
    server = "Xephyr" if visible else "Xvfb"
    missing = [name for name in (server, "xdotool") if not shutil.which(name)]
    if not shutil.which("import") and not shutil.which("ffmpeg"):
        missing.append("imagemagick (for 'import') or ffmpeg")
    if not missing:
        return True, ""
    packages = {"Xephyr": "xorg-server-xephyr", "Xvfb": "xorg-server-xvfb",
                "xdotool": "xdotool"}
    named = ", ".join(packages.get(m, m) for m in missing)
    return False, (f"[Refused: a stage needs {', '.join(missing)}, which "
                   f"{'is' if len(missing) == 1 else 'are'} not installed. "
                   f"Install {named} and try again.]")


def display_is_up(display):
    """Whether an X server is actually answering on this display.

    Asked of the server rather than assumed from the fact that a process was spawned:
    an X server that fails to start still leaves a process behind for a moment."""
    if not shutil.which("xdotool"):
        return False
    env = dict(os.environ, DISPLAY=display)
    env.pop("WAYLAND_DISPLAY", None)
    try:
        done = subprocess.run(["xdotool", "getdisplaygeometry"], env=env,
                              capture_output=True, text=True, timeout=4)
        return done.returncode == 0
    except Exception:
        return False


def free_display():
    for number in range(FIRST_DISPLAY, LAST_DISPLAY + 1):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return f":{number}"
    return None


def start_stage(raw, visible=True, size="1280x720"):
    """Open a nested display and run a program on it."""
    global _stage
    ready, why = tooling_ready(visible)
    if not ready:
        return why
    if _stage is not None and _stage.alive():
        return (f"[A stage is already open on {_stage.display}, {_stage.status()}. "
                "STAGE: STOP it before starting another.]")

    argv, workdir, verdict = classify_command(raw, background=True)
    if argv is None:
        return verdict

    display = free_display()
    if display is None:
        return "[Refused: no free display number between :80 and :96.]"

    # Noted before anything opens, so the keyboard can be handed straight back. A stage
    # that takes focus has defeated its own purpose.
    was_focused = focused_window()

    server_argv = (["Xephyr", "-screen", size, "-resizeable", display] if visible
                   else ["Xvfb", display, "-screen", "0", f"{size}x24"])
    try:
        server = subprocess.Popen(server_argv, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        return f"[Could not start the display server: {exc}]"

    waited = 0.0
    while waited < DISPLAY_TIMEOUT and not display_is_up(display):
        if server.poll() is not None:
            return (f"[The display server exited immediately (code {server.poll()}), so "
                    "there is no stage to run on.]")
        time.sleep(0.4)
        waited += 0.4
    if not display_is_up(display):
        server.terminate()
        return f"[The display on {display} never came up within {DISPLAY_TIMEOUT}s.]"

    stage = Stage(display, server, argv, visible, size)
    try:
        with open(stage.log, "w") as sink:
            stage.program = subprocess.Popen(
                argv, cwd=str(workdir), env=stage.env, stdout=sink,
                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                start_new_session=True)
    except FileNotFoundError:
        stage.stop()
        return f"[Command not found: {argv[0]}]"
    except Exception as exc:
        stage.stop()
        return f"[Could not start the program: {exc}]"

    _stage = stage
    time.sleep(1.5)                 # long enough to fail loudly rather than silently
    parked = False
    if visible:
        shown = window_of_pid(server.pid)
        if shown:
            parked = park_window(shown.get("address"), STAGE_WORKSPACE)
    handed_back = restore_focus(was_focused)
    if not stage.program_alive():
        printed = trim_output(stage.log.read_text(errors="replace").strip(), 900)
        code = stage.program.poll()
        stage.stop()
        _stage = None
        return (f"[The program exited immediately with code {code}, so there is nothing "
                "on the stage.]" + (f"\n{printed}" if printed else ""))
    seen = stage_windows()
    return (f"Stage open on {display}"
            + (" (a window you can watch)" if visible else " (nothing to see)")
            + f", running {' '.join(argv)}.\n"
            + (f"It has {len(seen)} window(s): {', '.join(seen[:4])}.\n" if seen else
               "It has no window yet - give it a moment and STAGE: LOOK.\n")
            + "Input from STAGE goes only here, so the desktop is untouched and you can "
              "keep using it."
            + (f"\nIt is on workspace {STAGE_WORKSPACE}, out of the way - switch there to "
               "watch it." if parked else "")
            + ("" if handed_back or not visible else
               "\n[Note: the stage window took keyboard focus and it could not be handed "
               "back. Click where you were working, or start it with 'hidden' next time "
               "so there is no window at all.]"))


def stage_windows():
    if _stage is None or not _stage.alive() or not shutil.which("xdotool"):
        return []
    try:
        found = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", "."],
            env=_stage.env, capture_output=True, text=True, timeout=4)
        names = []
        for window in (found.stdout or "").split():
            titled = subprocess.run(["xdotool", "getwindowname", window],
                                    env=_stage.env, capture_output=True, text=True,
                                    timeout=4)
            name = (titled.stdout or "").strip()
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def stage_look():
    """A picture of the stage, for the vision slot. Returns (note, frame or None)."""
    if _stage is None or not _stage.alive():
        return "[No stage is open. STAGE: START <command> first.]", None
    shot = Path(tempfile.gettempdir()) / f"bonsai-stage{_stage.display.lstrip(':')}.png"
    if shutil.which("import"):
        argv = ["import", "-window", "root", str(shot)]
    else:
        argv = ["ffmpeg", "-y", "-f", "x11grab", "-video_size", _stage.size,
                "-i", _stage.display, "-frames:v", "1", str(shot)]
    try:
        done = subprocess.run(argv, env=_stage.env, capture_output=True, text=True,
                              timeout=20)
    except Exception as exc:
        return f"[Could not photograph the stage: {exc}]", None
    if done.returncode != 0 or not shot.exists():
        return (f"[Could not photograph the stage: "
                f"{(done.stderr or done.stdout or '').strip()[:200]}]"), None
    try:
        with Image.open(shot) as image:
            frame = encode_frame(image.copy())
    except Exception as exc:
        return f"[The picture of the stage could not be read: {exc}]", None
    windows = stage_windows()
    return (f"Looking at the stage on {_stage.display} - {_stage.status()}"
            + (f", showing: {', '.join(windows[:4])}" if windows else "")
            + ".", frame)


def _xdotool(args):
    if _stage is None or not _stage.alive():
        return "[No stage is open. STAGE: START <command> first.]"
    try:
        done = subprocess.run(["xdotool"] + args, env=_stage.env,
                              capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return f"[Input failed: {exc}]"
    if done.returncode != 0:
        return f"[Input failed: {(done.stderr or done.stdout or '').strip()[:200]}]"
    return ""


def handle_stage(raw):
    """STAGE: START <command> | <folder>, LOOK, KEY, HOLD, TYPE, CLICK, MOVE, WINDOWS,
    STOP - run a program on a display of its own and drive it there.

    None of PLAY's aiming problems exist here. Input is delivered to this display and
    nowhere else, so there is no focus to check and no pointer of yours to move."""
    global _stage
    action, _, rest = str(raw).strip().partition(" ")
    action, rest = action.upper(), rest.strip()

    if action == "START":
        hidden = rest.lower().startswith("hidden ")
        return start_stage(rest[7:].strip() if hidden else rest, visible=not hidden)

    if action == "STOP":
        if _stage is None:
            return "(no stage was open)"
        where = _stage.display
        _stage.stop()
        _stage = None
        return f"Stage on {where} closed."

    if action == "STATUS":
        if _stage is None:
            return "(no stage is open)"
        return f"Stage on {_stage.display}: {_stage.status()}."

    if action == "WINDOWS":
        if _stage is None:
            return "[No stage is open.]"
        names = stage_windows()
        return ("\n".join(names) if names else
                "(the stage has no visible window yet)")

    if action == "OUTPUT":
        if _stage is None:
            return "[No stage is open.]"
        try:
            printed = _stage.log.read_text(errors="replace").strip()
        except OSError:
            printed = ""
        return trim_output(printed, 6000) if printed else "(the program printed nothing)"

    if _stage is None or not _stage.alive():
        return "[No stage is open. STAGE: START <command> first.]"

    if action == "KEY":
        if not rest:
            return "[Which key? For example STAGE: KEY space, or STAGE: KEY ctrl+s]"
        return _xdotool(["key", "--clearmodifiers", rest]) or f"Pressed {rest} on the stage."

    if action == "HOLD":
        parts = rest.split()
        if len(parts) != 2:
            return ("[STAGE: HOLD <key> <milliseconds>, e.g. STAGE: HOLD Right 800 to "
                    "walk for most of a second.]")
        try:
            millis = int(parts[1])
        except ValueError:
            return f"[Couldn't read '{parts[1]}' as a number of milliseconds.]"
        if not 0 < millis <= MAX_STAGE_HOLD_MS:
            return f"[Hold must be between 1 and {MAX_STAGE_HOLD_MS} ms.]"
        failed = _xdotool(["keydown", parts[0]])
        if failed:
            return failed
        time.sleep(millis / 1000)
        failed = _xdotool(["keyup", parts[0]])
        return failed or f"Held {parts[0]} for {millis}ms on the stage."

    if action == "TYPE":
        if not rest:
            return "[Nothing to type.]"
        return _xdotool(["type", "--clearmodifiers", rest]) or \
            f"Typed {len(rest)} character(s) on the stage."

    if action == "MOVE":
        parts = rest.split()
        if len(parts) != 2:
            return "[STAGE: MOVE <x> <y> - a position on the stage, from its top-left.]"
        return _xdotool(["mousemove", "--sync", parts[0], parts[1]]) or \
            f"Pointer moved to ({parts[0]}, {parts[1]}) on the stage."

    if action == "CLICK":
        buttons = {"left": "1", "middle": "2", "right": "3"}
        parts = rest.split()
        button = (parts[0] if parts else "left").lower()
        if button not in buttons:
            return f"[Unknown button '{button}'. Use left, right or middle.]"
        if len(parts) == 3:
            failed = _xdotool(["mousemove", "--sync", parts[1], parts[2]])
            if failed:
                return failed
        return _xdotool(["click", buttons[button]]) or f"Clicked {button} on the stage."

    return ("[STAGE needs START, LOOK, KEY, HOLD, TYPE, MOVE, CLICK, WINDOWS, OUTPUT, "
            "STATUS or STOP.]")


def stop_stage():
    """Called when the window closes: the stage and everything on it are ours."""
    global _stage
    if _stage is None:
        return None
    where = _stage.display
    _stage.stop()
    _stage = None
    return where
