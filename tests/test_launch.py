import os
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

box = Path(tempfile.mkdtemp(prefix="bonsai-launch-"))
for n in ["SETTINGS_FILE","MEMORY_FILE","CHARACTER_FILE","PROJECTS_FILE","SCREEN_LOG_FILE",
          "TASKS_FILE","SKILLS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE","BACKUP_DIR"]:
    setattr(ga, n, box / n.lower())
ga.save_settings(ga.DEFAULTS)
ga.save_trusted([str(box)])

print("\n-- a program on PATH resolves to its full path --")
argv, refusal = ga.classify_launch("ls")
check("no refusal", refusal, None)
check("resolved absolutely", argv[0].endswith("/ls"), True)
argv, refusal = ga.classify_launch("ls -la /tmp")
check("arguments are kept", argv[1:], ["-la", "/tmp"])

print("\n-- the forbidden list covers arguments, not just the program --")
argv, refusal = ga.classify_launch("sudo pacman -Syu")
check("sudo is refused outright", "sudo" in refusal and argv is None, True)
argv, refusal = ga.classify_launch("kitty -e rm -rf /home")
check("a terminal cannot smuggle rm in as an argument", argv, None)
check("and the refusal names the real offender", "'rm'" in refusal, True)
argv, refusal = ga.classify_launch("kitty -e bash")
check("nor a shell", argv, None)
argv, refusal = ga.classify_launch("kitty; rm -rf /")
check("shell metacharacters are refused", "metacharacters" in refusal, True)
argv, refusal = ga.classify_launch("")
check("an empty target is refused", argv, None)

print("\n-- files and URLs go to whatever opens them --")
opener = ga.opener_argv("x")
note = box / "reading.md"; note.write_text("# hello\n")
argv, refusal = ga.classify_launch(str(note))
if opener:
    check("an existing file is handed to the system opener", argv[0], opener[0])
    check("and the file itself is the argument", str(note) in argv, True)
    argv, refusal = ga.classify_launch("https://wiki.archlinux.org/title/Hyprland")
    check("a URL is handed to the system opener", argv[0], opener[0])
else:
    check("no opener installed, so it says so", "xdg-open" in (refusal or ""), True)

print("\n-- a program that is not installed says so, and how to check --")
argv, refusal = ga.classify_launch("definitelynotaprogram9000")
check("refused", argv, None)
check("named as missing", "Not installed" in refusal, True)
check("suggests checking first", "which" in refusal, True)

print("\n-- RUN hands desktop applications over to LAUNCH --")
apps = ga.desktop_programs()
check("the desktop register was actually read", len(apps) > 5, True)
check("wrappers are looked past, not recorded", "env" in apps, False)
picked = next((a for a in ("kitty", "firefox", "code", "nautilus", "alacritty")
               if a in apps and a not in ga.ALLOWED_COMMANDS), None)
if picked:
    argv, workdir, verdict = ga.classify_command(f"{picked} | {box}")
    check(f"RUN refuses {picked}", argv, None)
    check("and points at LAUNCH", "LAUNCH:" in verdict, True)
    check("explaining why RUN cannot do it", "hang" in verdict, True)
else:
    check("no graphical app installed to check against", True, True)
argv, workdir, verdict = ga.classify_command(f"git status | {box}")
check("ordinary command-line tools are untouched", verdict, "allowed")

print("\n-- launching needs somewhere to put a window --")
saved = {k: os.environ.pop(k, None) for k in ("WAYLAND_DISPLAY", "DISPLAY")}
try:
    check("no session means no launch", ga.graphical_session(), False)
    out = ga.start_program(["/bin/true"])
    check("and it refuses rather than starting anything", "no graphical session" in out, True)
    os.environ["DISPLAY"] = ":0"
    check("a display is enough", ga.graphical_session(), True)

    print("\n-- what actually happened is reported, not assumed --")
    out = ga.start_program(["/bin/false"])
    check("an immediate failure is reported as one", out.startswith("[Failed:"), True)
    check("with the exit code", "code 1" in out, True)
    out = ga.start_program(["/bin/sleep", "30"])
    check("a program that stays up is reported as running", "still running" in out, True)
    check("with its pid", "pid " in out, True)
    pid = int(out.split("pid ")[1].split(")")[0])
    check("and it really is detached and alive", Path(f"/proc/{pid}").exists(), True)
    os.kill(pid, 15)
    out = ga.start_program(["/definitely/not/here"])
    check("a missing binary is reported", "not found" in out.lower(), True)
finally:
    os.environ.pop("DISPLAY", None)
    for key, value in saved.items():
        if value is not None:
            os.environ[key] = value

print("\n-- what opened is looked up, not guessed at --")
# Asked to open the Factory Sim project, a run launched xdg-open on a folder, and the
# app replied "that usually means it handed the request to an instance that was already
# open, so the window is there". The model repeated it as "the project is already open".
# The app invented that; a launcher exiting 0 says nothing at all.
seen = {"0xaaa": "codium - editor"}
ga.shell.open_windows = lambda: dict(seen)

class _Exited:
    pid = 4242
    def poll(self): return 0
    def wait(self, timeout=None): return 0
real_popen, real_session = ga.shell.subprocess.Popen, ga.shell.graphical_session
ga.shell.graphical_session = lambda: True
ga.shell.subprocess.Popen = lambda *a, **k: _Exited()
try:
    out = ga.start_program(["/usr/bin/xdg-open", "/media/D/Bonsai-Storage"])
    check("it does not claim a window is there", "the window is there" in out, False)
    check("it says no new window appeared", "no new window appeared" in out, True)
    check("and that this proves nothing either way", "says nothing about what opened" in out, True)
    check("telling it to check before claiming", "Check before saying it is open" in out, True)

    # Now one that really does put a window up. The window has to be absent when the
    # launch starts and present afterwards, or "appeared" means nothing.
    calls = {"n": 0}
    def windows_then():
        calls["n"] += 1
        return dict(seen) if calls["n"] == 1 else {**seen, "0xbbb": "godot - Factory Sim"}
    ga.shell.open_windows = windows_then
    out = ga.start_program(["/usr/bin/godot", "--path", "."])
    check("a window that appeared is named", "godot - Factory Sim" in out, True)
    check("and it says it opened", "' opened:" in out, True)
finally:
    ga.shell.subprocess.Popen, ga.shell.graphical_session = real_popen, real_session
    ga.shell.open_windows = ga.shell.__dict__["open_windows"]

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
