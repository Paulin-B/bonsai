"""Running commands, opening programs, and reaching the web."""

import collections
import itertools
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
import requests
from PyQt6.QtCore import (
    QMarginsF, QSizeF, QUrl,
)
from PyQt6.QtGui import (
    QPageSize, QPdfWriter, QTextDocument,
)
from .config import (
    ALLOWED_COMMANDS, COMMAND_TIMEOUT, FORBIDDEN_COMMANDS, MACOS, MAX_DOWNLOAD_BYTES, PROTECTED_PATHS, WINDOWS, is_read_only, sandbox_argv,
)
from .store import (
    load_trusted, settings,
)
from .files import (
    _backup, clean_url, name_inside, path_is_trusted, resolve_guarded,
)
from .media import (
    PDF_MARGIN_MM, fit_images,
)
from .theme import (
    THEMES, style_rendered_document,
)


def download_file(raw):
    """DOWNLOAD: url | destination_path. Streams to disk with a size cap."""
    parts = [p.strip() for p in raw.split("|", 1)]
    if len(parts) < 2:
        return "[DOWNLOAD failed: expected 'url | destination path']"
    url, raw_dest = parts
    url = clean_url(url)
    if not url:
        return "[DOWNLOAD refused: no valid http(s) URL found in the argument.]"

    dest, err = resolve_guarded(raw_dest)
    if err:
        return err
    if dest in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    if not dest.parent.exists():
        return f"[Refused: destination folder doesn't exist: {dest.parent}]"
    if dest.exists():
        return f"[Refused: destination already exists: {dest}]"

    try:
        with requests.get(url, stream=True, timeout=60,
                          headers={"User-Agent": "Mozilla/5.0 (Bonsai/1.0)"}) as response:
            response.raise_for_status()
            declared = int(response.headers.get("content-length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                return f"[Refused: file is {declared} bytes, limit is {MAX_DOWNLOAD_BYTES}.]"
            written = 0
            with open(dest, "wb") as handle:
                for chunk in response.iter_content(chunk_size=65536):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        handle.close()
                        dest.unlink(missing_ok=True)
                        return f"[Aborted: exceeded {MAX_DOWNLOAD_BYTES} byte limit.]"
                    handle.write(chunk)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        return f"[Download failed: {exc}]"
    return f"Downloaded {written} bytes to '{dest}'."


# Flags that tell a desktop program not to open a window. With one of these it is an
# ordinary command that runs and exits, which is the only way to reach the parts of a
# program worth automating: importing a project, running a headless test, printing a
# version. Refusing these made asset import impossible - the refusal named LAUNCH
# instead, which opens a window and captures nothing.
HEADLESS_FLAGS = {
    "--headless", "-headless", "--no-window", "--background", "-b", "--batch",
    "--version", "-version", "--help", "-h", "--check-only", "--quit", "--script",
    "--dump-gdextension-interface", "--doctool",
}


def headless_invocation(argv):
    """Whether this invocation of a windowed program asks it not to open a window."""
    return any(token in HEADLESS_FLAGS or token.startswith("--export")
               for token in argv[1:])


def unquoted_path_with_spaces(argv):
    """A path that came apart into several arguments because it was not quoted.

    Reported only when rejoining the pieces names something real - a path that exists,
    or one whose folder exists and which reaches deeper than the fragment did. That
    second case is what an OUTPUT path looks like: the file is not there yet, but the
    directory it is going into is, and the fragment stopped at the first space."""
    def target(text):
        return Path(text.split("=", 1)[-1])

    for start in range(1, len(argv)):
        piece = argv[start]
        if "/" not in piece or target(piece).exists():
            continue
        depth = len(target(piece).parts)
        joined = piece
        for token in argv[start + 1:start + 10]:
            joined = f"{joined} {token}"
            whole = target(joined)
            if whole.exists() or (len(whole.parts) > depth and whole.parent.is_dir()):
                return joined
    return None


def classify_command(raw, background=False):
    """Returns (argv, working_dir, verdict) where verdict is 'allowed', 'ask' or an
    error string. Commands are never run through a shell, so pipes, redirects and
    command substitution simply aren't available to the model.

    `background` only relaxes the desktop-application refusal, which exists because RUN
    waits for the command to finish. A process started in the background is not waited
    on, so a game or a dev server is exactly what it is for."""
    parts = [p.strip() for p in raw.split("|", 1)]
    command = parts[0]
    workdir_raw = parts[1] if len(parts) > 1 else ""

    if any(ch in command for ch in ";&|><`$\n"):
        return None, None, ("[Refused: shell metacharacters aren't supported. Commands run "
                            "directly, not through a shell. Issue one plain command.]")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, None, f"[Couldn't parse command: {exc}]"
    if not argv:
        return None, None, "[Refused: empty command.]"

    split_path = unquoted_path_with_spaces(argv)
    if split_path:
        # A path with a space in it comes apart into several arguments, and the command
        # then fails somewhere far from the cause. Watched for twenty minutes: every
        # attempt on a folder called "Sprout Lands - Sprites - Basic pack" failed, and
        # the quoting that would have fixed it is what the tool description had said
        # does not work.
        return None, None, (f"[Refused: '{split_path}' has spaces in it, so it came "
                            "apart into separate arguments. Put quotes round it: "
                            f'"{split_path}".]')

    program = Path(argv[0]).name.lower()
    if program in FORBIDDEN_COMMANDS:
        return None, None, (f"[Refused: '{program}' is never allowed from here. Use the "
                            "FILE_OP and DOWNLOAD tools for file and network operations.]")
    if (not background and program not in ALLOWED_COMMANDS
            and program in desktop_programs() and not headless_invocation(argv)):
        return None, None, (f"[Refused: '{program}' is a desktop application. RUN waits for "
                            "the command to finish and hides the display from it, so this "
                            f"would hang until the timeout and then fail. Use LAUNCH: "
                            f"{command} instead.]")

    if workdir_raw:
        workdir, err = resolve_guarded(workdir_raw)
        if err:
            return None, None, err
    else:
        trusted = load_trusted()
        if not trusted:
            return None, None, ("[Refused: no working directory given and no trusted folder "
                                "configured. Pass 'command | /path/to/folder'.]")
        workdir = Path(trusted[0])

    if not workdir.is_dir():
        return None, None, f"[Working directory not found: {workdir}]"

    in_trusted = path_is_trusted(str(workdir), load_trusted())
    verdict = "allowed" if (in_trusted and program in ALLOWED_COMMANDS
                            and is_read_only(program, argv)) else "ask"
    return argv, workdir, verdict


def trim_output(output, limit):
    """Keep the head and the tail of oversized output, and say what went missing.

    Cutting only the tail silently loses the end of an alphabetical list, which is how
    half of `pacman -Qu` disappeared: the reply could discuss packages up to "l" and
    knew nothing after it, with no sign anything was absent."""
    if len(output) <= limit:
        return output
    lines = output.splitlines()
    head_budget = int(limit * 0.7)
    head, used = [], 0
    for line in lines:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1
    tail, used = [], 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > limit - head_budget:
            break
        tail.append(line)
        used += len(line) + 1
    tail.reverse()
    hidden = len(lines) - len(head) - len(tail)
    if hidden <= 0:
        return output[:limit]
    return ("\n".join(head)
            + f"\n...[{hidden} of {len(lines)} lines omitted here - the output was "
              f"{len(output)} characters. Re-run with arguments that narrow it down if "
              "you need what is missing.]\n"
            + "\n".join(tail))


def sandbox_available():
    """Whether commands can actually be confined on this machine.

    bubblewrap is Linux-only. Rather than pretend, the app says plainly when a command
    ran unconfined - a sandbox everyone believes in and nobody has is worse than none."""
    return not (WINDOWS or MACOS) and bool(shutil.which("bwrap"))


def execute_command(argv, workdir):
    config = settings()
    sandboxed = config.get("sandbox_commands", True) and sandbox_available()
    launch = (sandbox_argv(argv, workdir, config.get("sandbox_network", False))
              if sandboxed else argv)
    try:
        completed = subprocess.run(
            launch, cwd=str(workdir), capture_output=True, text=True,
            timeout=COMMAND_TIMEOUT, shell=False,
        )
    except FileNotFoundError:
        return f"[Command not found: {argv[0]}]"
    except subprocess.TimeoutExpired:
        return f"[Timed out after {COMMAND_TIMEOUT}s: {' '.join(argv)}]"
    except Exception as exc:
        return f"[Command failed: {exc}]"

    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.strip() or "(no output)"
    output = trim_output(output, config.get("max_command_chars", 15000))
    where = f"{workdir} (sandboxed)" if sandboxed else f"{workdir} (NOT sandboxed)"
    if sandboxed and completed.returncode != 0:
        # Say why it might have failed, since the sandbox is invisible otherwise.
        output += ("\n[Ran sandboxed: only this folder is writable, credentials are "
                   "hidden and there is no network"
                   + ("" if config.get("sandbox_network", False) else "")
                   + ". If the command needed any of those, say so - the user can turn "
                     "the sandbox off in Settings.]")
    return f"exit code {completed.returncode} in {where}\n{output}"


# Processes that outlive a turn: a game, a dev server, a watcher. RUN waits and returns
# output, which cannot express "start this, let it run, and tell me what it printed".
MAX_BACKGROUND = 4


# Per process. Enough to see a stack trace scroll past, bounded so a process that spins
# printing forever costs a fixed amount of memory rather than the machine.
MAX_BACKGROUND_LINES = 2000


_background = {}
_background_lock = threading.Lock()
_background_counter = itertools.count(1)


class BackgroundProcess:
    """One running command, with its recent output kept as it arrives.

    Output is drained by a thread rather than read on demand because a pipe nobody
    reads fills up and the process stops dead when it does - which would look like a
    hung program and be nothing of the sort."""

    def __init__(self, name, argv, workdir, popen, sandboxed):
        self.name = name
        self.argv = argv
        self.workdir = workdir
        self.popen = popen
        self.sandboxed = sandboxed
        self.started = time.time()
        self.lines = collections.deque(maxlen=MAX_BACKGROUND_LINES)
        self.dropped = 0
        self.reader = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()

    def _drain(self):
        try:
            for line in self.popen.stdout:
                if len(self.lines) == self.lines.maxlen:
                    self.dropped += 1
                self.lines.append(line.rstrip("\n"))
        except Exception:
            pass                    # the process died mid-read; exit code tells the story

    def alive(self):
        return self.popen.poll() is None

    def status(self):
        code = self.popen.poll()
        seconds = int(time.time() - self.started)
        if code is None:
            return f"running for {seconds}s"
        return f"exited with code {code} after {seconds}s"

    def stop(self, timeout=5):
        if not self.alive():
            return f"{self.name} had already {self.status()}."
        self.popen.terminate()
        try:
            self.popen.wait(timeout=timeout)
            return f"{self.name} stopped (exit code {self.popen.poll()})."
        except subprocess.TimeoutExpired:
            self.popen.kill()
            self.popen.wait(timeout=timeout)
            return f"{self.name} did not stop when asked and was killed."


def start_background(argv, workdir):
    """Run a command without waiting for it, and keep hold of it."""
    config = settings()
    with _background_lock:
        # Only live ones count against the limit; a finished process stays listed so
        # its output can still be read.
        running = sum(1 for p in _background.values() if p.alive())
        if running >= MAX_BACKGROUND:
            return (f"[Refused: {running} background processes are already running, which "
                    f"is the limit. BG: STOP one first, or BG: LIST to see them.]")
    sandboxed = config.get("sandbox_commands", True) and sandbox_available()
    launch = (sandbox_argv(argv, workdir, config.get("sandbox_network", False))
              if sandboxed else argv)
    try:
        popen = subprocess.Popen(
            launch, cwd=str(workdir), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True,
            bufsize=1, shell=False, start_new_session=True,
        )
    except FileNotFoundError:
        return f"[Command not found: {argv[0]}]"
    except Exception as exc:
        return f"[Could not start: {exc}]"

    name = f"bg{next(_background_counter)}"
    with _background_lock:
        _background[name] = BackgroundProcess(name, argv, workdir, popen, sandboxed)
    where = f"{workdir}{' (sandboxed)' if sandboxed else ' (NOT sandboxed)'}"
    return (f"Started {name}: {' '.join(argv)}\nin {where}\n"
            f"It is running now. Read what it prints with BG: READ {name}, and stop it "
            f"with BG: STOP {name}. It keeps running between your turns.")


def background_list():
    with _background_lock:
        processes = list(_background.values())
    if not processes:
        return "(no background processes)"
    return "\n".join(
        f"{p.name}: {' '.join(p.argv)} - {p.status()}, {len(p.lines)} line(s) of output"
        for p in processes)


def background_read(raw):
    parts = [part.strip() for part in str(raw).split("|", 1)]
    with _background_lock:
        process = _background.get(parts[0])
    if process is None:
        return f"[No background process called '{parts[0]}'. BG: LIST shows them.]"
    try:
        wanted = int(parts[1]) if len(parts) > 1 and parts[1] else 120
    except ValueError:
        return f"[Couldn't read '{parts[1]}' as a number of lines.]"
    lines = list(process.lines)[-max(1, wanted):]
    head = f"{process.name}: {' '.join(process.argv)} - {process.status()}"
    if not lines:
        return f"{head}\n(no output yet)"
    missing = ""
    if process.dropped:
        missing = (f"\n[{process.dropped} earlier line(s) scrolled out of the buffer - "
                   "only the most recent are kept.]")
    return f"{head}{missing}\n" + "\n".join(lines)


def background_stop(raw):
    name = str(raw).strip()
    with _background_lock:
        process = _background.get(name)
    if process is None:
        return f"[No background process called '{name}'. BG: LIST shows them.]"
    return process.stop()


def stop_all_background():
    """Called when the window closes. A process started from here is ours to clean up -
    leaving a game or a server running after the app is gone is not a background task,
    it is a leak."""
    with _background_lock:
        processes = list(_background.values())
        _background.clear()
    stopped = []
    for process in processes:
        if process.alive():
            try:
                process.stop(timeout=3)
                stopped.append(process.name)
            except Exception:
                pass
    return stopped


# Programs opened with LAUNCH, so input can be aimed at a window we are responsible for.
_launched = {}


# Raw Linux keycodes, because ydotool works in them: there is no way for it to know the
# keyboard layout, so key names are this side's job. These numbers are kernel ABI and do
# not change. Only keys a person would press while playing or testing are listed - a key
# that is not here is refused rather than guessed at.
KEYCODES = {
    "esc": 1, "escape": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8,
    "8": 9, "9": 10, "0": 11, "minus": 12, "equal": 13, "backspace": 14, "tab": 15,
    "q": 16, "w": 17, "e": 18, "r": 19, "t": 20, "y": 21, "u": 22, "i": 23, "o": 24,
    "p": 25, "enter": 28, "return": 28, "ctrl": 29, "leftctrl": 29,
    "a": 30, "s": 31, "d": 32, "f": 33, "g": 34, "h": 35, "j": 36, "k": 37, "l": 38,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "shift": 42, "leftshift": 42,
    "backslash": 43, "z": 44, "x": 45, "c": 46, "v": 47, "b": 48, "n": 49, "m": 50,
    "comma": 51, "dot": 52, "period": 52, "slash": 53, "rightshift": 54,
    "alt": 56, "leftalt": 56, "space": 57, "capslock": 58,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64, "f7": 65, "f8": 66,
    "f9": 67, "f10": 68, "f11": 87, "f12": 88, "rightctrl": 97, "rightalt": 100,
    "home": 102, "up": 103, "pageup": 104, "left": 105, "right": 106, "end": 107,
    "down": 108, "pagedown": 109, "insert": 110, "delete": 111,
}


# ydotool's button values carry the press and release in a bit mask, and the button on
# its own is documented as "chooses the button, but does nothing" - which it does
# silently, so the first version of this reported a click it had never performed.
CLICK_DOWN_UP = 0xC0
MOUSE_BUTTONS = {"left": 0x00, "right": 0x01, "middle": 0x02}


# A key held down for longer than this is a stuck key, not an input.
MAX_HOLD_MS = 10000


def input_socket():
    return Path(os.environ.get("YDOTOOL_SOCKET")
                or f"/run/user/{os.getuid()}/.ydotool_socket")


def input_ready():
    """Whether keys and clicks can be sent at all. Returns (ready, why not)."""
    if WINDOWS:
        return False, "[Sending input is only wired up for Linux at the moment.]"
    if not shutil.which("ydotool"):
        return False, ("[Refused: ydotool is not installed, so there is no way to send "
                       "input. Install it, then start 'ydotoold'.]")
    if not input_socket().exists():
        return False, ("[Refused: the ydotool daemon is not running, so input cannot be "
                       "sent. Start it with 'ydotoold &' and try again.]")
    return True, ""


def focused_window():
    """The window with keyboard focus, as the compositor reports it, or None.

    Input goes wherever focus is - it is injected at the kernel, not delivered to a
    window - so knowing what is focused is the only thing standing between testing a
    game and typing into whatever else happens to be open."""
    if not shutil.which("hyprctl"):
        return None
    try:
        out = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True,
                             text=True, timeout=4)
        window = json.loads(out.stdout or "{}")
        return window if window.get("pid") else None
    except Exception:
        return None


def hypr(call):
    """Run one Hyprland dispatcher. Returns whether it was accepted.

    Hyprland 0.56 reads dispatches as Lua, so the older "dispatch focuswindow class:x"
    form is a parse error rather than an action - it reported nothing and did nothing,
    and code that assumed otherwise was steering on an instruction that never ran."""
    if not shutil.which("hyprctl"):
        return False
    try:
        done = subprocess.run(["hyprctl", "dispatch", call], capture_output=True,
                              text=True, timeout=4)
    except Exception:
        return False
    return done.returncode == 0 and "error" not in (done.stdout or "").lower()


def focus_window(selector):
    """Give keyboard focus to a window, named as 'class:x' or 'address:0x...'."""
    return hypr('hl.dsp.focus{window="' + str(selector).replace('"', "") + '"}')


def restore_focus(window):
    """Put keyboard focus back on the window that had it. Returns whether it worked.

    Opening any window takes focus, which for a stage is exactly wrong: the point of
    running a program on its own display is that the person keeps working, and a window
    that grabs the keyboard the moment it appears takes that straight back."""
    if not window or not window.get("address"):
        return False
    focus_window(f"address:{window['address']}")
    time.sleep(0.3)
    return (focused_window() or {}).get("address") == window["address"]


def park_window(address, workspace):
    """Move a window to another workspace so it is not sitting under the pointer.

    With focus-follows-mouse - the default - a window that opens where the mouse is
    keeps taking focus back however many times it is handed over, so the only way to
    leave someone working is to put the window somewhere else entirely."""
    if not address:
        return False
    if not focus_window(f"address:{address}"):
        return False
    time.sleep(0.2)
    return hypr('hl.dsp.window.move{workspace="' + str(workspace) + '", silent=true}')


def open_windows():
    """Every window the compositor currently has, as {address: "class - title"}."""
    if not shutil.which("hyprctl"):
        return {}
    try:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                            capture_output=True, text=True,
                                            timeout=4).stdout or "[]")
    except Exception:
        return {}
    return {c.get("address"): f"{c.get('class') or '?'} - {c.get('title') or 'untitled'}"
            for c in clients if c.get("address")}


def window_of_pid(pid):
    """The compositor's record of the window belonging to a process, or None."""
    if not shutil.which("hyprctl"):
        return None
    try:
        clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                            capture_output=True, text=True,
                                            timeout=4).stdout or "[]")
    except Exception:
        return None
    return next((c for c in clients if c.get("pid") == pid), None)


def pid_is_ours(pid):
    """Whether a pid is a process Bonsai started, or a child of one.

    A launcher usually forks: the window belongs to a descendant of what was started,
    not to the pid that was returned, so the whole ancestry has to be walked."""
    ours = set(_launched)
    with _background_lock:
        ours |= {p.popen.pid for p in _background.values()}
    seen = set()
    while pid and pid > 1 and pid not in seen:
        if pid in ours:
            return True
        seen.add(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])
        except Exception:
            return False
    return False


# How far input may reach. "own" is the default because it is the only setting where a
# mistake cannot touch anything but a program Bonsai was told to open.
PLAY_SCOPES = {
    "own": "only windows Bonsai opened itself",
    "approved": "any window, once you have approved that window",
    "any": "any window, no questions asked",
}


def play_target(scope="own"):
    """The window input may be sent to, or a refusal saying why not.

    Under "own" the window must belong to a process Bonsai started. "approved" widens
    that to anything the user has said yes to, and "any" removes the check - but never
    the check that SOMETHING has focus, because input with nothing focused simply goes
    wherever the desktop last put it."""
    ready, why = input_ready()
    if not ready:
        return None, why
    window = focused_window()
    if window is None:
        return None, ("[Refused: there is no way to tell which window has focus on this "
                      "setup, and input goes wherever focus is. Without that check it "
                      "could land in your editor or browser. This needs hyprctl (or "
                      "another compositor query) to be available.]")
    if scope == "any" or pid_is_ours(window.get("pid")):
        return window, ""
    if scope == "approved" and window.get("address") in _approved_windows:
        return window, ""
    asking = ("Approve it for this session and it will be allowed."
              if scope == "approved" else
              "Input is only sent to a program opened from here, so it cannot type into "
              "your editor, browser or terminal. Use PLAY: FOCUS <program> first, or "
              "LAUNCH the thing you mean to test - or widen 'Input reaches' in "
              "\u2699 Settings \u2192 Screen.")
    return None, (f"[Refused: the focused window is {window.get('class') or '?'} "
                  f"({window.get('title') or 'untitled'}), which Bonsai did not start. "
                  + asking + "]")


# Windows the user has said yes to this session, by compositor address.
_approved_windows = set()


def approve_window(address):
    _approved_windows.add(address)


def cursor_position():
    """Where the pointer is, or None if the compositor cannot say."""
    if not shutil.which("hyprctl"):
        return None
    try:
        out = subprocess.run(["hyprctl", "cursorpos", "-j"], capture_output=True,
                             text=True, timeout=4)
        point = json.loads(out.stdout or "{}")
        return (int(point["x"]), int(point["y"]))
    except Exception:
        return None


def cursor_is_inside(window):
    """Whether the pointer is over this window.

    Focus decides where a KEY lands, but a click lands wherever the pointer physically
    is - so the focus check alone would let a click go to whatever happens to be under
    the mouse, which is exactly the window this tool must never touch."""
    point = cursor_position()
    if point is None or not window.get("at") or not window.get("size"):
        return False
    left, top = window["at"]
    width, height = window["size"]
    return left <= point[0] < left + width and top <= point[1] < top + height


# How far from the requested spot the pointer may land and still count as placed.
POINTER_TOLERANCE = 3


def move_pointer_to(target_x, target_y, tries=14):
    """Put the pointer at a screen position, checking where it actually went.

    Neither kind of move can be trusted to do what it says. An absolute move is in the
    input device's own coordinates, which are not the compositor's - on this machine a
    move to 400 lands at 800 - and a relative move is fed through pointer acceleration,
    so a large one overshoots. Both report success either way. So: move, look at where
    it really is, and close the gap, which converges because each correction is smaller
    and slower than the one before. Returns (position, failure)."""
    for _ in range(tries):
        at = cursor_position()
        if at is None:
            return None, ""
        dx, dy = target_x - at[0], target_y - at[1]
        if abs(dx) + abs(dy) <= POINTER_TOLERANCE:
            return at, ""
        failed = _ydotool(["mousemove", "-x", str(dx), "-y", str(dy)])
        if failed:
            return None, failed
        time.sleep(0.03)
    return cursor_position(), ""


def _ydotool(args):
    try:
        done = subprocess.run(["ydotool"] + args, capture_output=True, text=True,
                              timeout=MAX_HOLD_MS / 1000 + 10)
    except Exception as exc:
        return f"[Input failed: {exc}]"
    if done.returncode != 0:
        return f"[Input failed: {(done.stderr or done.stdout or '').strip()[:200]}]"
    return ""


def parse_keys(spec):
    """'ctrl+s' or 'space' into keycodes, or (None, refusal)."""
    names = [part.strip().lower() for part in str(spec).split("+") if part.strip()]
    if not names:
        return None, "[No key given. For example: PLAY: KEY space, or PLAY: KEY ctrl+s]"
    codes = []
    for name in names:
        if name in ("super", "meta", "win", "leftmeta", "rightmeta"):
            return None, ("[Refused: the super key belongs to the window manager, not to "
                          "the program being tested. It would act on your desktop.]")
        if name not in KEYCODES:
            close = sorted(k for k in KEYCODES if k.startswith(name[:2]))[:6]
            return None, (f"[Unknown key '{name}'."
                          + (f" Did you mean: {', '.join(close)}?" if close else "")
                          + "]")
        codes.append(KEYCODES[name])
    held = {KEYCODES["ctrl"], KEYCODES["alt"], KEYCODES["rightctrl"], KEYCODES["rightalt"]}
    if held & set(codes) and any(59 <= c <= 88 for c in codes):
        return None, ("[Refused: ctrl or alt with a function key switches virtual "
                      "console or is caught by the desktop before the program sees it.]")
    return codes, ""


def handle_play(raw, scope="own"):
    """PLAY: send keys and clicks to a program Bonsai opened, so it can be tried out.

    Looking at a game says whether it renders. Playing it is the only way to find out
    whether it works."""
    action, _, rest = str(raw).strip().partition(" ")
    action, rest = action.upper(), rest.strip()

    if action == "WINDOWS":
        if not shutil.which("hyprctl"):
            return "[No hyprctl, so open windows cannot be listed.]"
        try:
            clients = json.loads(subprocess.run(["hyprctl", "clients", "-j"],
                                                capture_output=True, text=True,
                                                timeout=4).stdout or "[]")
        except Exception as exc:
            return f"[Could not list windows: {exc}]"
        if not clients:
            return "(no windows open)"
        return "\n".join(
            f"{c.get('class') or '?'}: {c.get('title') or 'untitled'}"
            f"{'  <- opened by Bonsai' if pid_is_ours(c.get('pid')) else ''}"
            for c in clients)

    if action == "FOCUS":
        if not shutil.which("hyprctl"):
            return "[No hyprctl, so windows cannot be focused from here.]"
        if not rest:
            return "[Which window? PLAY: FOCUS <window class>, e.g. PLAY: FOCUS godot]"
        focus_window(f"class:{rest}")
        time.sleep(0.4)             # the compositor needs a moment to actually switch
        window = focused_window()
        if window is None:
            return "[Could not confirm which window has focus after asking to switch.]"
        if not pid_is_ours(window.get("pid")):
            return (f"[Focus is now {window.get('class')}, which Bonsai did not start, "
                    "so input still cannot be sent to it.]")
        return (f"Focused {window.get('class')} ({window.get('title') or 'untitled'}). "
                "Input will go here.")

    window, refusal = play_target(scope)
    if window is None:
        return refusal
    where = f"{window.get('class')} ({window.get('title') or 'untitled'})"

    if action == "KEY":
        codes, why = parse_keys(rest)
        if codes is None:
            return why
        sequence = [f"{c}:1" for c in codes] + [f"{c}:0" for c in reversed(codes)]
        failed = _ydotool(["key"] + sequence)
        return failed or f"Pressed {rest} in {where}."

    if action == "HOLD":
        parts = rest.split()
        if len(parts) != 2:
            return ("[PLAY: HOLD <key> <milliseconds>, e.g. PLAY: HOLD right 800 to walk "
                    "right for most of a second.]")
        codes, why = parse_keys(parts[0])
        if codes is None:
            return why
        try:
            millis = int(parts[1])
        except ValueError:
            return f"[Couldn't read '{parts[1]}' as a number of milliseconds.]"
        if not 0 < millis <= MAX_HOLD_MS:
            return f"[Hold must be between 1 and {MAX_HOLD_MS} ms.]"
        failed = _ydotool(["key"] + [f"{c}:1" for c in codes])
        if failed:
            return failed
        time.sleep(millis / 1000)
        failed = _ydotool(["key"] + [f"{c}:0" for c in reversed(codes)])
        return failed or f"Held {parts[0]} for {millis}ms in {where}."

    if action == "TYPE":
        if not rest:
            return "[Nothing to type.]"
        failed = _ydotool(["type", rest])
        return failed or f"Typed {len(rest)} character(s) into {where}."

    if action == "POINT":
        parts = rest.split()
        if len(parts) != 2:
            return ("[PLAY: POINT <x> <y> - where inside the window to put the pointer, "
                    "measured from its top-left corner.]")
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            return "[POINT needs two whole numbers.]"
        left, top = window.get("at", [0, 0])
        width, height = window.get("size", [0, 0])
        if not (0 <= x < width and 0 <= y < height):
            return (f"[({x}, {y}) is outside the window, which is {width}x{height}. "
                    "The pointer is only ever placed inside it.]")
        landed, failed = move_pointer_to(left + x, top + y)
        if failed:
            return failed
        if landed is None:
            return "[Moved the pointer, but the compositor will not say where it ended up.]"
        off = abs(landed[0] - (left + x)) + abs(landed[1] - (top + y))
        actual = (landed[0] - left, landed[1] - top)
        if off > POINTER_TOLERANCE:
            return (f"[The pointer is at {actual} in {where}, not ({x}, {y}) - it could "
                    "not be placed exactly. Work from where it actually is.]")
        return f"Pointer is at {actual} inside {where}."

    if action in ("CLICK", "MOVE"):
        # A click goes where the pointer is, not where focus is. Sending one blind would
        # click whatever happens to be under the mouse - which is the one thing this
        # tool exists to prevent.
        if not cursor_is_inside(window):
            return (f"[Refused: the pointer is not over {where}, and a click lands where "
                    "the pointer is rather than where focus is - so this would click "
                    "whatever is under the mouse instead. Put it inside the window "
                    "first with PLAY: POINT <x> <y>.]")

    if action == "CLICK":
        button = (rest or "left").lower()
        if button not in MOUSE_BUTTONS:
            return f"[Unknown button '{button}'. Use left, right or middle.]"
        failed = _ydotool(["click",
                           f"0x{CLICK_DOWN_UP | MOUSE_BUTTONS[button]:02x}"])
        return failed or f"Clicked {button} in {where}."

    if action == "MOVE":
        parts = rest.split()
        if len(parts) != 2:
            return "[PLAY: MOVE <dx> <dy> - a movement, not a screen position.]"
        try:
            dx, dy = int(parts[0]), int(parts[1])
        except ValueError:
            return "[MOVE needs two whole numbers.]"
        left, top = window.get("at", [0, 0])
        width, height = window.get("size", [0, 0])
        at = cursor_position() or (left, top)
        if not (left <= at[0] + dx < left + width and top <= at[1] + dy < top + height):
            return (f"[Refused: that would take the pointer outside {where}, where a "
                    "later click would land on something else. Keep it inside, or use "
                    "PLAY: POINT to place it.]")
        failed = _ydotool(["mousemove", "-x", str(dx), "-y", str(dy)])
        return failed or f"Moved the pointer by ({dx}, {dy}) in {where}."

    return ("[PLAY needs KEY, HOLD, TYPE, POINT, CLICK, MOVE, FOCUS or WINDOWS. "
            "For example: PLAY: KEY space]")


LAUNCH_SETTLE = 1.5


# Entries whose Exec line starts with one of these say nothing about which program is
# really being launched, so keep reading past them.
LAUNCH_WRAPPERS = {"env", "sh", "bash", "zsh", "fish", "flatpak", "snap", "gtk-launch",
                   "dbus-run-session", "systemd-run", "gio", "xdg-open"}


DESKTOP_DIRS = [
    "/usr/share/applications", "/usr/local/share/applications",
    "~/.local/share/applications", "/var/lib/flatpak/exports/share/applications",
    "~/.local/share/flatpak/exports/share/applications",
]


_desktop_programs = None


def desktop_programs():
    """Basenames of every program that ships a .desktop entry.

    That list is as close as this machine gets to a register of "things meant to be
    opened rather than run and waited on", and it is what lets RUN recognise that it
    has been handed an application and point at LAUNCH instead of hanging."""
    global _desktop_programs
    if _desktop_programs is not None:
        return _desktop_programs
    found = set()
    for raw in DESKTOP_DIRS:
        root = Path(raw).expanduser()
        try:
            entries = list(root.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            try:
                text = entry.read_text(errors="replace")
            except OSError:
                continue
            exec_line = re.search(r"^Exec=(.+)$", text, re.M)
            if not exec_line:
                continue
            try:
                argv = shlex.split(exec_line.group(1))
            except ValueError:
                continue
            for token in argv:
                name = Path(token).name.lower()
                if not name or token.startswith("-") or "=" in token:
                    continue
                if name in LAUNCH_WRAPPERS:
                    continue
                found.add(name)
                break
    _desktop_programs = found
    return found


def graphical_session():
    """Whether there is anywhere for a window to appear.

    Windows and macOS always have a desktop when a user is logged in; on Linux it is
    the display server's presence that decides."""
    if WINDOWS or MACOS:
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


def opener_argv(target):
    """The command that hands a file or URL to whatever normally opens it."""
    if WINDOWS:
        # `start` is a cmd builtin, not a program, and its first quoted argument is
        # taken as the window title - hence the empty one before the real target.
        return ["cmd", "/c", "start", "", target]
    if MACOS:
        found = shutil.which("open")
        return [found, target] if found else None
    for program in ("xdg-open", "gio"):
        found = shutil.which(program)
        if found:
            return [found, "open", target] if program == "gio" else [found, target]
    return None


def classify_launch(raw):
    """Returns (argv, None) or (None, refusal).

    Every token is checked against the forbidden list, not just the first: a terminal
    emulator takes a command to run, so `kitty -e rm -rf ~` would otherwise walk
    straight past a check that only looked at `kitty`."""
    command = (raw or "").split("|")[0].strip()
    if not command:
        return None, "[LAUNCH needs something to open: LAUNCH: <program> or <file or URL>]"
    if any(ch in command for ch in ";&|><`$\n"):
        return None, ("[Refused: shell metacharacters aren't supported. LAUNCH starts one "
                      "program directly, not through a shell.]")

    if re.match(r"^(?:https?|mailto|file)://|^mailto:", command):
        argv = opener_argv(command)
        return (argv, None) if argv else (None, "[No xdg-open or gio on this machine.]")

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return None, f"[Couldn't parse: {exc}]"
    if not argv:
        return None, "[Refused: empty command.]"

    for token in argv:
        if token.startswith("-"):
            continue
        if Path(token).name.lower() in FORBIDDEN_COMMANDS:
            return None, (f"[Refused: '{Path(token).name}' is never allowed from here, "
                          "including as an argument to another program.]")

    program = shutil.which(argv[0])
    if program:
        return [program] + argv[1:], None

    # Not a program - if it names a real file, open it with whatever owns that type.
    target, err = resolve_guarded(command)
    if not err and target.exists():
        opened = opener_argv(str(target))
        return (opened, None) if opened else (None, "[No xdg-open or gio on this machine.]")
    close = sorted(name for name in desktop_programs()
                   if argv[0].lower() in name or name in argv[0].lower())[:5]
    return None, (f"[Not installed: '{argv[0]}' is not on PATH."
                  + (f" Installed programs with similar names: {', '.join(close)}."
                     if close else "")
                  + " Check with RUN: which <program> before trying again.]")


def start_program(argv):
    """Start a program detached from this app and report whether it stayed up.

    Deliberately unsandboxed: an application with no display socket, no home directory
    and no network is not an application the user can use. The protection is that the
    caller has already asked. It is also deliberately not waited on - returning as
    soon as the window is up is the whole point - but waiting a moment first is the
    difference between "it started" and "it died instantly and nobody read why"."""
    if not graphical_session():
        return ("[Refused: no graphical session (neither WAYLAND_DISPLAY nor DISPLAY is "
                "set), so there is nowhere for a window to open.]")
    detach = {}
    if WINDOWS:
        # start_new_session is POSIX-only; this is the equivalent, and without it the
        # launched program dies with Bonsai.
        detach["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    else:
        detach["start_new_session"] = True
    before_windows = open_windows()
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp")
    log = runtime / f"bonsai-launch-{os.getpid()}.log"
    try:
        sink = open(log, "w")
    except OSError:
        sink, log = subprocess.DEVNULL, None
    try:
        process = subprocess.Popen(
            argv, cwd=str(Path.home()), stdin=subprocess.DEVNULL,
            stdout=sink, stderr=subprocess.STDOUT, **detach)
    except FileNotFoundError:
        return f"[Command not found: {argv[0]}]"
    except Exception as exc:
        return f"[Could not launch: {exc}]"
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()

    shown = " ".join(argv)
    deadline = time.monotonic() + LAUNCH_SETTLE
    while time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.1)
    code = process.poll()
    if code is None:
        # Remembered so input can be aimed at its window later, and only at its window.
        _launched[process.pid] = argv
        appeared = [name for address, name in open_windows().items()
                    if address not in before_windows]
        # Still running is not the same as showing something. Say which it is.
        where = (f" Its window is up: {', '.join(appeared[:3])}." if appeared else
                 " No window has appeared yet - it may still be starting, or it may be "
                 "a program that does not open one.")
        return (f"Opened '{shown}' (pid {process.pid}) and it is still running.{where}"
                " It keeps running after this turn - do not open it again. You can send "
                "it keys and clicks with PLAY once its window has focus.")

    printed = ""
    if log is not None:
        try:
            printed = trim_output(log.read_text(errors="replace").strip(), 1200)
        except OSError:
            printed = ""
    if code == 0:
        # It used to say "that usually means an instance was already open, so the
        # window is there" - a guess, stated as fact, and duly repeated back as "the
        # project is already open" when what had actually run was xdg-open on a folder.
        # A launcher exiting 0 says nothing at all; the compositor can say something.
        appeared = [name for address, name in open_windows().items()
                    if address not in before_windows]
        if appeared:
            return (f"'{shown}' opened: {', '.join(appeared[:3])}."
                    + (f"\n{printed}" if printed else ""))
        return (f"'{shown}' ran and exited straight away (code 0), and no new window "
                "appeared. For a launcher like xdg-open that is normal and says nothing "
                "about what opened - it may have gone to a program that was already "
                "running, or to nothing at all. Check before saying it is open."
                + (f"\n{printed}" if printed else ""))
    return (f"[Failed: '{shown}' exited with code {code}.]"
            + (f"\n{printed}" if printed else " It printed nothing."))


def make_pdf(raw):
    """MAKE_PDF: <output.pdf> | <markdown> - typeset markdown into a PDF.

    Qt renders the document, so there is no pandoc or headless browser to install.
    Images are resolved relative to the PDF's own folder, which means the model has to
    DOWNLOAD them first - it cannot reference a URL and hope."""
    parts = [part.strip() for part in (raw or "").split("|", 1)]
    target = parts[0]
    body = parts[1] if len(parts) > 1 else ""
    if not target:
        return "[MAKE_PDF needs an output path: MAKE_PDF: <file.pdf> | <markdown>]"
    if not body.strip():
        return "[MAKE_PDF needs markdown content after the path.]"
    path, err = resolve_guarded(target)
    if err:
        return err
    if path in PROTECTED_PATHS:
        return "[Refused: protected system directory.]"
    heading = re.search(r"^#+\s*(.+)$", body, re.M)
    path = name_inside(path, heading.group(1) if heading else "", "document", ".pdf")
    if path.suffix.lower() != ".pdf":
        path = path.with_suffix(".pdf")
    if not path.parent.exists():
        return f"[Refused: folder missing: {path.parent}. MKDIR it first.]"

    missing = [ref for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body)
               if not ref.startswith(("http://", "https://", "data:"))
               and not (path.parent / ref).exists()]
    backup = _backup(path)
    try:
        document = QTextDocument()
        document.setBaseUrl(QUrl.fromLocalFile(f"{path.parent}/"))
        document.setMarkdown(body, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        writer = QPdfWriter(str(path))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setPageMargins(QMarginsF(PDF_MARGIN_MM, PDF_MARGIN_MM,
                                        PDF_MARGIN_MM, PDF_MARGIN_MM))
        writer.setTitle(path.stem)
        inches = 1.0 / writer.resolution()
        page_width = writer.width() * inches * 72
        page_height = writer.height() * inches * 72
        document.setPageSize(QSizeF(page_width, page_height))
        # Half a page tall at most, so a screenshot illustrates the text rather than
        # replacing it.
        fit_images(document, page_width, page_height * 0.5)
        style_rendered_document(document, THEMES["light"])
        document.print(writer)
    except Exception as exc:
        return f"[Could not write the PDF: {exc}]"
    if not path.exists():
        return "[The PDF was not written, and no error was raised.]"

    note = f" Previous version backed up to '{backup}'." if backup else ""
    if missing:
        # Qt draws nothing for a missing image and says nothing about it, so a PDF with
        # silently blank figures would otherwise read as a success.
        note += (" WARNING: these images were referenced but not found next to the PDF, "
                 "so they are blank in it: " + ", ".join(missing[:6])
                 + ". DOWNLOAD them into that folder and run MAKE_PDF again.")
    return f"Wrote {path.stat().st_size} bytes to '{path}'.{note}"
