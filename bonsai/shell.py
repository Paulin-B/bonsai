"""Running commands, opening programs, and reaching the web."""

import os
import re
import shlex
import shutil
import subprocess
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


def classify_command(raw):
    """Returns (argv, working_dir, verdict) where verdict is 'allowed', 'ask' or an
    error string. Commands are never run through a shell, so pipes, redirects and
    command substitution simply aren't available to the model."""
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

    program = Path(argv[0]).name.lower()
    if program in FORBIDDEN_COMMANDS:
        return None, None, (f"[Refused: '{program}' is never allowed from here. Use the "
                            "FILE_OP and DOWNLOAD tools for file and network operations.]")
    if program not in ALLOWED_COMMANDS and program in desktop_programs():
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
        return (f"Opened '{shown}' (pid {process.pid}) and it is still running, so its "
                "window is up. It keeps running after this turn - do not open it again.")

    printed = ""
    if log is not None:
        try:
            printed = trim_output(log.read_text(errors="replace").strip(), 1200)
        except OSError:
            printed = ""
    if code == 0:
        return (f"'{shown}' started and exited immediately with code 0. That usually "
                "means it handed the request to an instance that was already open, so "
                "the window is there."
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
