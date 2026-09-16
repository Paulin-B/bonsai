import os
import shutil
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

if not shutil.which("bwrap"):
    print("bubblewrap not installed - skipping"); sys.exit(0)

box = Path(tempfile.mkdtemp(prefix="bonsai-sbx-"))
work = box / "project"; work.mkdir()
(work / "keep.txt").write_text("original\n")
# Deliberately in $HOME, not /tmp: /tmp is a fresh tmpfs inside the sandbox, so a
# write there succeeds into a throwaway rather than failing. $HOME is ro-bound, so a
# write there is the real test of "cannot touch the rest of the machine".
outside = Path.home() / "bonsai_sandbox_escape_probe.txt"
outside.write_text("untouched\n")
ga.SETTINGS_FILE = box / "settings.json"
ga.TRUSTED_PATHS_FILE = box / "trusted.json"
ga.save_trusted([str(work)])
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": True, "sandbox_network": False})

def run(command):
    argv, workdir, verdict = ga.classify_command(f"{command} | {work}")
    assert argv is not None, verdict
    return ga.execute_command(argv, workdir)

print("\n-- it can work in its own folder --")
(work / "hi.py").write_text("print('hello-from-sandbox')\n")
out = run("python3 hi.py")
check("command runs", "hello-from-sandbox" in out, True)
check("labelled as sandboxed", "(sandboxed)" in out, True)
# The realistic shape: it writes a script, then runs it to check its own work.
# (shlex strips quotes, so python3 -c with string literals will not survive.)
(work / "make_file.py").write_text("open('made.txt', 'w').write('written')\n")
out = run("python3 make_file.py")
check("writing inside the folder succeeds", "exit code 0" in out, True)
check("  ...and the file is really there", (work / "made.txt").read_text(), "written")

print("\n-- and nowhere else --")
(work / "escape.py").write_text(f"open({str(outside)!r}, 'w').write('pwned')\n")
out = run("python3 escape.py")
check("writing into $HOME fails", "exit code 0" in out, False)
check("  ...with a read-only error", "Read-only file system" in out, True)
check("the host file is untouched", outside.read_text(), "untouched\n")
check("the failure explains the sandbox", "only this folder is writable" in out, True)

# A write to /tmp does not error - it lands in the sandbox's own tmpfs and vanishes.
(work / "tmpwrite.py").write_text("open('/tmp/vanishes.txt','w').write('gone')\n")
out = run("python3 tmpwrite.py")
check("a /tmp write succeeds inside the sandbox", "exit code 0" in out, True)
check("  ...but leaves nothing on the host", Path("/tmp/vanishes.txt").exists(), False)
outside.unlink(missing_ok=True)

print("\n-- credentials are masked, not merely read-only --")
(work / "peek.py").write_text(
    "import os\nprint(sorted(os.listdir(os.path.expanduser('~/.local/share')))[:4])\n")
out = run("python3 peek.py")
check("the app's own data directory is hidden", "bonsai_memory.json" in out, False)

print("\n-- no network --")
(work / "net.py").write_text(
    "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=4)\n")
out = run("python3 net.py")
check("outbound connections fail", "exit code 0" in out, False)

print("\n-- switching it off restores plain execution --")
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": False})
(work / "hello.py").write_text("print('unsandboxed')\n")
out = run("python3 hello.py")
check("still runs", "unsandboxed" in out, True)
check("not labelled sandboxed", "(sandboxed)" in out, False)

print("\n-- the wrapper is shaped correctly --")
argv = ga.sandbox_argv(["pytest", "-q"], work)
check("starts with bwrap", argv[0], "bwrap")
check("working folder bound writable", f"--bind" in argv and str(work) in argv, True)
check("network severed by default", "--unshare-net" in argv, True)
check("network allowed on request",
      "--unshare-net" in ga.sandbox_argv(["x"], work, allow_network=True), False)
check("the command itself is last", argv[-2:], ["pytest", "-q"])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
