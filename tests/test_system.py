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

CURLY = "’"

print("\n-- typographic punctuation no longer defeats the detectors --")
check("plain_text converts the apostrophe", ga.plain_text(f"can{CURLY}t"), "can't")
check("and the dashes and ellipsis",
      ga.plain_text("a—b–c…"), "a-b-c...")
for name, sentence in [
    ("CLAIMED_ACTION_RE", f"I{CURLY}ve written the file"),
    ("DENIES_TOOL_RE", f"I can{CURLY}t check for updates"),
    ("DENIES_KNOWING_USER_RE", f"I don{CURLY}t have your setup"),
]:
    rx = getattr(ga, name)
    check(f"{name} catches the curly form", bool(rx.search(ga.plain_text(sentence))), True)
check("unverified_files sees a curly claim",
      ga.unverified_files(f"I{CURLY}ve updated main.py", "READ_FILE: other.py"), ["main.py"])

print("\n-- denying a capability it has --")
for reply in [
    "I can't check for updates because I don't have access to your package manager.",
    "I'm unable to run commands on your system.",
    "I don't have direct access to your files.",
    "I'm just a language model, so I can't inspect that.",
]:
    check(f"flagged: {reply[:44]}", bool(ga.DENIES_TOOL_RE.search(ga.plain_text(reply))), True)

print("\n-- but not an honest report after actually trying --")
for reply in [
    "I ran pacman -Qu and there are 311 updates available.",
    "The command returned exit code 1, so the database is out of date.",
    "sudo is refused here, which is why the upgrade could not be applied.",
]:
    fired = bool(ga.DENIES_TOOL_RE.search(ga.plain_text(reply)))
    check(f"not flagged: {reply[:44]}", fired, False)

print("\n-- the nudge is one-shot and only when nothing ran --")
from bonsai_under_test import APP
text = APP.read_text()
check("gated on an empty trace", "not prodded and not trace and DENIES_TOOL_RE" in text, True)
check("one-shot", text.count("prodded = True"), 1)

print("\n-- the machine describes itself --")
facts = ga.system_facts()
check("names the distro", "CachyOS" in facts, True)
check("names the package manager", "package manager: pacman" in facts, True)
check("says sudo is unavailable", "cannot use sudo" in facts, True)
check("does not claim a shell that may contradict memory", "shell:" in facts, False)
check("reaches the system prompt", "package manager: pacman" in ga.build_system_prompt(), True)

print("\n-- pacman is runnable, root is still not --")
check("pacman allowed", "pacman" in ga.ALLOWED_COMMANDS, True)
check("checkupdates allowed", "checkupdates" in ga.ALLOWED_COMMANDS, True)
check("sudo still forbidden", "sudo" in ga.FORBIDDEN_COMMANDS, True)
argv, workdir, verdict = ga.classify_command("sudo pacman -Syu | /home/sam")
check("sudo is refused outright", argv, None)

print("\n-- long command output keeps both ends --")
listing = "\n".join(f"package{i:03d} 1.0-{i} -> 1.1-{i}" for i in range(400))
trimmed = ga.trim_output(listing, 6000)
check("first line kept", trimmed.splitlines()[0], "package000 1.0-0 -> 1.1-0")
check("last line kept", trimmed.splitlines()[-1], "package399 1.0-399 -> 1.1-399")
check("says how much went", "of 400 lines omitted here" in trimmed, True)
check("within budget", len(trimmed) < 6400, True)
check("short output untouched", ga.trim_output("two\nlines", 6000), "two\nlines")
check("cap is configurable", ga.DEFAULTS["max_command_chars"], 15000)

print("\n-- only the querying forms skip the prompt --")
import tempfile as _tf
sandbox = Path(_tf.mkdtemp(prefix="bonsai-cmd-"))
ga.SETTINGS_FILE = sandbox / "settings.json"
ga.TRUSTED_PATHS_FILE = sandbox / "trusted.json"
ga.save_settings(ga.DEFAULTS)
ga.save_trusted([str(sandbox)])

def verdict(cmd):
    return ga.classify_command(f"{cmd} | {sandbox}")[2]

for cmd in ["pacman -Qu", "pacman -Q", "pacman -Si firefox", "pacman -Ss godot",
            "git status", "git log --oneline", "git diff HEAD", "ls -la", "uname -a"]:
    check(f"reads without asking: {cmd}", verdict(cmd), "allowed")

for cmd in ["pacman -Syu", "pacman -Syu --needed", "pacman -S firefox",
            "pacman -Rns vim", "pacman -U ./thing.pkg.tar.zst",
            "git push origin main", "git commit -m wip", "git reset --hard"]:
    check(f"asks first: {cmd}", verdict(cmd), "ask")

check("a bare argument-sensitive command asks", verdict("pacman"), "ask")
check("sudo is still refused outright, not merely asked",
      ga.classify_command(f"sudo pacman -Syu | {sandbox}")[0], None)
check("commands with no argument rules are unaffected",
      ga.is_read_only("ls", ["ls", "-la"]), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
