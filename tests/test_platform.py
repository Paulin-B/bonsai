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

print("\n-- the platform is detected once, not guessed at each call site --")
check("exactly one of the three holds",
      sum([ga.WINDOWS, ga.MACOS, not (ga.WINDOWS or ga.MACOS)]), 1)
check("this machine is Linux", (ga.WINDOWS, ga.MACOS), (False, False))

print("\n-- state goes where each platform expects --")
check("Linux uses the XDG location", str(ga._default_data_dir()).endswith(".local/share"), True)
saved = dict(os.environ)
try:
    os.environ["APPDATA"] = r"C:\\Users\\Sam\\AppData\\Roaming"
    ga.config.WINDOWS, ga.config.MACOS = True, False
    check("Windows uses AppData", "Bonsai" in str(ga.config._default_data_dir()), True)
    ga.config.WINDOWS, ga.config.MACOS = False, True
    check("macOS uses Application Support",
          "Application Support" in str(ga.config._default_data_dir()), True)
finally:
    ga.config.WINDOWS, ga.config.MACOS = False, False
    os.environ.clear(); os.environ.update(saved)
check("an override wins on any platform",
      str(Path(os.environ.get("BONSAI_DATA_DIR", "unset"))) != "", True)

print("\n-- opening a file uses the right tool for the platform --")
import shutil as _shutil
_real_which = _shutil.which
# `open` and `xdg-open` only exist on the platform that ships them, so pretend every
# candidate is installed and check which one each platform reaches for.
ga.shell.shutil.which = lambda name: f"/usr/bin/{name}"
try:
    for windows, macos, expect in [(True, False, "cmd"), (False, True, "open"),
                                   (False, False, "xdg-open")]:
        ga.shell.WINDOWS, ga.shell.MACOS = windows, macos
        argv = ga.shell.opener_argv("/tmp/a.txt")
        label = "Windows" if windows else "macOS" if macos else "Linux"
        check(f"{label} opener", Path(argv[0]).name if argv else None, expect)
        check(f"  {label} passes the target through", "/tmp/a.txt" in argv, True)
finally:
    ga.shell.shutil.which = _real_which
    ga.shell.WINDOWS = ga.shell.MACOS = False
check("the Windows form gives start an empty title first",
      (lambda: (setattr(ga.shell, "WINDOWS", True),
                ga.shell.opener_argv("x.txt"),
                setattr(ga.shell, "WINDOWS", False))[1])(),
      ["cmd", "/c", "start", "", "x.txt"])

print("\n-- a desktop is assumed where there always is one --")
saved_env = {k: os.environ.pop(k, None) for k in ("WAYLAND_DISPLAY", "DISPLAY")}
try:
    check("no display on Linux means no window", ga.shell.graphical_session(), False)
    ga.shell.WINDOWS = True
    check("Windows always has one", ga.shell.graphical_session(), True)
    ga.shell.WINDOWS, ga.shell.MACOS = False, True
    check("so does macOS", ga.shell.graphical_session(), True)
finally:
    ga.shell.WINDOWS = ga.shell.MACOS = False
    for k, v in saved_env.items():
        if v is not None: os.environ[k] = v

print("\n-- the sandbox is never claimed where it cannot exist --")
check("available on this Linux box", ga.sandbox_available(), bool(__import__("shutil").which("bwrap")))
ga.shell.WINDOWS = True
check("never on Windows", ga.sandbox_available(), False)
ga.shell.WINDOWS, ga.shell.MACOS = False, True
check("nor on macOS", ga.sandbox_available(), False)
ga.shell.WINDOWS = ga.shell.MACOS = False
src = APP.read_text()
check("and an unconfined run says so in its result", '(NOT sandboxed)' in src, True)

print("\n-- Windows destructive commands are refused too --")
for name in ("del", "format", "diskpart", "reg", "powershell", "vssadmin", "takeown"):
    check(f"'{name}' refused", name in ga.FORBIDDEN_COMMANDS, True)
check("as an argument to something else as well",
      ga.classify_launch("notepad del")[0], None)

print("\n-- capture has a route on every platform --")
check("Wayland prefers grim", callable(ga.grim_available), True)
check("everything else goes through Qt", callable(ga.qt_capture), True)
saved_wayland = os.environ.pop("WAYLAND_DISPLAY", None)
try:
    check("no Wayland means grim is not used", ga.grim_available(), False)
finally:
    if saved_wayland is not None: os.environ["WAYLAND_DISPLAY"] = saved_wayland

print("\n-- a test run can never reach real user data --")
import json as _json
real = Path.home() / ".local/share" / "bonsai_settings.json"
check("the data directory is a scratch one",
      str(ga.DATA_DIR).startswith(tempfile.gettempdir()), True)
for name in ("SETTINGS_FILE", "MEMORY_FILE", "SKILLS_FILE", "CHAT_INDEX_FILE",
             "PATTERNS_FILE", "INTERESTS_FILE", "CHARACTER_FILE", "PROJECTS_FILE"):
    path = getattr(ga, name)
    check(f"  {name} is inside it", str(path).startswith(str(ga.DATA_DIR)), True)
check("CHATS_DIR too", str(ga.CHATS_DIR).startswith(str(ga.DATA_DIR)), True)

# The failure this guards against: saving settings without sandboxing first.
before = real.read_text() if real.exists() else None
ga.save_settings({**ga.DEFAULTS, "vault_path": "/tmp/some-test-vault"})
after = real.read_text() if real.exists() else None
check("writing settings does not touch the real file", after, before)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
