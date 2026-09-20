import sys
import tempfile
import time
from pathlib import Path
from bonsai_under_test import load
ga = load()

ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

box = Path(tempfile.mkdtemp(prefix="bonsai-bg-"))
for n in ["SETTINGS_FILE","CHARACTER_FILE","MEMORY_FILE","SCREEN_LOG_FILE","TASKS_FILE",
          "SKILLS_FILE","PATTERNS_FILE","TRUSTED_PATHS_FILE","CHAT_INDEX_FILE",
          "PROJECTS_FILE","INTERESTS_FILE","BRIEFING_FILE","DEBUG_LOG_FILE"]:
    setattr(ga, n, box / f"{n.lower()}.json")
ga.CHATS_DIR = box / "chats"
work = box / "proj"
work.mkdir()
ga.save_trusted([str(work)])
# The sandbox is a separate mechanism with its own tests; these are about lifecycle.
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": False})

# Stands in for a game loop or a dev server: prints steadily, never exits on its own.
(work / "loop.py").write_text(
    "import time\n"
    "for i in range(100000):\n"
    "    print(f'frame {i}', flush=True)\n"
    "    time.sleep(0.02)\n")
(work / "quick.py").write_text("print('done')\n")
(work / "noisy.py").write_text(
    "for i in range(5000):\n    print(f'line {i}')\n")

def started(script):
    argv, workdir, _ = ga.classify_command(f"python3 {script} | {work}", background=True)
    return ga.start_background(argv, workdir)

try:
    print("\n-- RUN refuses a desktop program; a background process is what it is for --")
    # RUN waits for the command to finish and hides the display from it, so a game there
    # would hang until the timeout. Not waiting is the whole point of this tool.
    desktop = next(iter(ga.desktop_programs()), None)
    if desktop and desktop not in ga.ALLOWED_COMMANDS:
        _, _, waiting = ga.classify_command(f"{desktop} | {work}")
        _, _, backgrounded = ga.classify_command(f"{desktop} | {work}", background=True)
        check(f"RUN still refuses {desktop!r}", "desktop application" in str(waiting), True)
        check("but it can be started in the background",
              "desktop application" in str(backgrounded), False)
    else:
        check("no desktop program to test with - skipped", True, True)
        check("no desktop program to test with - skipped", True, True)

    print("\n-- the refusals that matter still apply --")
    _, _, verdict = ga.classify_command(f"sudo rm -rf / | {work}", background=True)
    check("a forbidden command is still forbidden", "never allowed" in str(verdict), True)
    _, _, verdict = ga.classify_command(f"python3 loop.py | /etc", background=True)
    check("an untrusted folder is still not allowed outright", verdict, "ask")
    _, _, verdict = ga.classify_command("python3 x.py; rm -rf ~ | " + str(work),
                                        background=True)
    check("shell metacharacters are still refused",
          "shell metacharacters" in str(verdict), True)

    print("\n-- a process starts, keeps running, and can be read --")
    out = started("loop.py")
    check("it reports the name it can be addressed by", out.startswith("Started bg"), True)
    check("and says it keeps running between turns", "between your turns" in out, True)
    name = out.split()[1].rstrip(":")
    time.sleep(0.8)

    listing = ga.background_list()
    check("it is listed as running", "running for" in listing, True)
    check("with the command that started it", "loop.py" in listing, True)

    read = ga.background_read(f"{name} | 5")
    check("reading shows recent output", "frame" in read, True)
    check("only as many lines as asked for", len(read.splitlines()) - 1, 5)
    check("with the status on the first line", "running for" in read.splitlines()[0], True)

    time.sleep(0.4)     # long enough for the loop to print more frames
    later = ga.background_read(f"{name} | 5")
    check("and it keeps producing output", later != read, True)

    print("\n-- output is kept even after it exits --")
    done = started("quick.py")
    quick = done.split()[1].rstrip(":")
    time.sleep(0.6)
    listing = ga.background_list()
    check("a finished process says so", "exited with code 0" in listing, True)
    check("and its output can still be read", "done" in ga.background_read(quick), True)

    print("\n-- stopping --")
    stopped = ga.background_stop(name)
    check("it says it stopped", "stopped" in stopped, True)
    check("and it really is not running",
          "running for" in ga.background_list().split("\n")[0], False)
    check("stopping it twice is not an error",
          "already" in ga.background_stop(name), True)
    check("an unknown name says so, and how to find the right one",
          "BG: LIST" in ga.background_read("bg999"), True)
    check("the same for stopping", "BG: LIST" in ga.background_stop("bg999"), True)

    print("\n-- a process that prints forever costs a fixed amount of memory --")
    out = started("noisy.py")
    noisy = out.split()[1].rstrip(":")
    for _ in range(40):
        time.sleep(0.1)
        if "exited" in ga.background_list().split(noisy + ":")[1].split("\n")[0]:
            break
    with ga._background_lock:
        buffered = len(ga._background[noisy].lines)
        dropped = ga._background[noisy].dropped
    check("the buffer is capped", buffered <= ga.MAX_BACKGROUND_LINES, True)
    check("5000 lines of output did not all get kept", dropped > 0, True)
    check("and the read says lines were lost rather than pretending otherwise",
          "scrolled out of the buffer" in ga.background_read(noisy), True)

    print("\n-- there is a limit on how many can run at once --")
    names = [started("loop.py") for _ in range(ga.MAX_BACKGROUND + 2)]
    check("the ones past the limit are refused",
          any(n.startswith("[Refused:") for n in names), True)
    refusal = next(n for n in names if n.startswith("[Refused:"))
    check("and it says what to do about it", "BG: STOP" in refusal, True)
    running = sum(1 for p in ga._background.values() if p.alive())
    check("never more than the limit are alive", running <= ga.MAX_BACKGROUND, True)

    print("\n-- closing the window takes them with it --")
    # A game or a dev server left running after the app is gone is not a background
    # task, it is a leak nobody can see to stop.
    survivors = [p for p in ga._background.values() if p.alive()]
    check("there is something running to clean up", len(survivors) > 0, True)
    cleaned = ga.stop_all_background()
    check("every live one was named", len(cleaned), len(survivors))
    time.sleep(0.3)
    check("and none is still alive", [p.name for p in survivors if p.alive()], [])
    check("the registry is emptied", ga.background_list(), "(no background processes)")

    print("\n-- the tool form the model actually uses --")
    check("START maps to the text form",
          ga.native_call_to_tool("bg", {"action": "START", "command": "godot --headless",
                                        "working_directory": str(work)}),
          ("BG", f"START godot --headless | {work}"))
    check("READ carries the line count",
          ga.native_call_to_tool("bg", {"action": "READ", "name": "bg1", "lines": 40}),
          ("BG", "READ bg1 | 40"))
    check("READ without one is still valid",
          ga.native_call_to_tool("bg", {"action": "READ", "name": "bg1"}),
          ("BG", "READ bg1"))
    check("STOP names the process",
          ga.native_call_to_tool("bg", {"action": "STOP", "name": "bg2"}),
          ("BG", "STOP bg2"))
    check("LIST needs nothing",
          ga.native_call_to_tool("bg", {"action": "LIST"}), ("BG", "LIST"))
    check("it is offered as a tool",
          "bg" in [s["function"]["name"] for s in ga.build_tool_schemas()], True)
    print("\n-- and the real window, closing, really does stop them --")
    ga.Bonsai.start_docker = lambda self: None
    app = ga.QApplication(sys.argv)
    window = ga.Bonsai()
    window.stop_docker = lambda *a, **k: None       # docker is not what is under test
    started("loop.py")
    time.sleep(0.4)
    check("a process is running before the window closes",
          any(p.alive() for p in ga._background.values()), True)
    window.close()
    time.sleep(0.4)
    check("closing the window left nothing running",
          [p.name for p in ga._background.values() if p.alive()], [])
finally:
    ga.stop_all_background()

print("\n-- a windowed program asked not to open a window is an ordinary command --")
# This was the whole reason asset import never happened: RUN refused "godot" outright
# as a desktop application, even with --headless, and told it to LAUNCH instead - which
# opens a window and captures nothing. Importing a Godot project, running a headless
# test or printing a version were all unreachable.
desktop = next((p for p in ga.desktop_programs() if p not in ga.ALLOWED_COMMANDS), None)
if desktop:
    _, _, plain = ga.classify_command(f"{desktop} | {work}")
    check(f"a bare {desktop!r} is still refused", "desktop application" in str(plain), True)
    _, _, quiet = ga.classify_command(f"{desktop} --headless --quit | {work}")
    check("but --headless is allowed through", "desktop application" in str(quiet), False)
    _, _, version = ga.classify_command(f"{desktop} --version | {work}")
    check("and so is --version", "desktop application" in str(version), False)
else:
    for _ in range(3):
        check("no desktop program installed to test with - skipped", True, True)

check("--headless counts", ga.headless_invocation(["godot", "--headless", "--path", "."]), True)
check("--background counts, for blender and friends",
      ga.headless_invocation(["blender", "--background", "x.blend"]), True)
check("an export counts", ga.headless_invocation(["godot", "--export-release", "linux"]), True)
check("but a bare invocation does not", ga.headless_invocation(["godot"]), False)
check("nor one that only names a project",
      ga.headless_invocation(["godot", "--path", "."]), False)

print("\n-- a path with spaces that was not quoted --")
# Watched for twenty minutes of real use: every attempt on a folder called
# "Sprout Lands - Sprites - Basic pack" came apart into separate arguments and failed
# somewhere far from the cause. Quoting fixes it - and the RUN description had said
# quoting does not work, which is what sent it looking for other ways round.
spaced = work / "Sprout Lands - Sprites"
(spaced / "Characters").mkdir(parents=True, exist_ok=True)
(spaced / "Characters" / "Tools.png").write_bytes(b"\x89PNG")

_, _, verdict = ga.classify_command(
    f"aseprite --batch --script-param out={spaced}/Characters/new --script /tmp/a.lua | {work}")
check("an unquoted output path is caught", "has spaces in it" in str(verdict), True)
check("and it shows the whole path put back together",
      "Sprout Lands - Sprites/Characters/new" in str(verdict), True)
check("with the fix spelled out", '"' in str(verdict), True)

_, _, verdict = ga.classify_command(
    f'cp "{spaced}/Characters/Tools.png" /tmp/t.png | {work}')
check("quoted, it is allowed through", verdict in ("ask", "allowed"), True)
argv, _, _ = ga.classify_command(f'cp "{spaced}/Characters/Tools.png" /tmp/t.png | {work}')
check("and the path survives as one argument",
      argv[1], f"{spaced}/Characters/Tools.png")

print("\n-- and it does not cry wolf --")
for label, cmd in [
    ("a plain path", f"ls {work}"),
    ("words that are not paths", "echo hello world"),
    ("two files, neither with spaces", "cat /tmp/a.txt /tmp/b.txt"),
    ("a missing file then a word", "cat /tmp/definitely-not-here.txt twice"),
    ("a pattern then a real path", f"grep -rn pattern {work}"),
]:
    _, _, verdict = ga.classify_command(f"{cmd} | {work}")
    check(f"quiet: {label}", "has spaces in it" in str(verdict), False)

check("the helper finds nothing in a clean argv",
      ga.unquoted_path_with_spaces(["ls", "-la", "/tmp"]), None)

print("\n-- an asset tool does not ask permission for every frame --")
# Making an animation means running the same tool over and over, and a prompt per run
# teaches clicking yes without reading. These are allowed on the same terms python3
# already was - trusted folder, sandboxed - and python3 is the wider capability.
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": True})
for program, flag in [("aseprite", "--batch"), ("blender", "--background"),
                      ("godot", "--headless")]:
    _, _, verdict = ga.classify_command(f"{program} {flag} {work}/x | {work}")
    check(f"{program} {flag} runs without asking", verdict, "allowed")

print("\n-- but only on those terms --")
_, _, verdict = ga.classify_command(f"aseprite --batch {box}/elsewhere | {box}")
check("outside a trusted folder it still asks", verdict, "ask")
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": False})
_, _, verdict = ga.classify_command(f"aseprite --batch {work}/x | {work}")
check("with the sandbox off it still asks", verdict, "ask")
ga.save_settings({**ga.DEFAULTS, "sandbox_commands": True})
_, _, verdict = ga.classify_command(f"rm -rf {work} | {work}")
check("a forbidden command is still never allowed",
      "never allowed" in str(verdict), True)
check("and a windowed invocation is still sent to LAUNCH",
      "desktop application" in str(ga.classify_command(f"aseprite {work}/a.aseprite | {work}")[2])
      if "aseprite" in ga.desktop_programs() else True, True)
check("a tool that is not an asset tool still asks",
      ga.classify_command(f"ffprobe --version | {work}")[2], "ask")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
