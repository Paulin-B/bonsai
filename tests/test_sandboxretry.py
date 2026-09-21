"""When the sandbox is the reason, ask about that instead of giving up.

The Factorio launcher writes to ~/.factorio-bonsai and starts a server that has to be
reachable on a port. The sandbox denies both by design, so the script cannot work
inside it and no amount of fixing the command will change that. The old answer was to
tell the user to turn the sandbox off in Settings - a global switch, flipped for one
command, and easily left off afterwards.
"""
import sys
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

print("\n-- telling the sandbox apart from a broken command --")
for blocked in ["mkdir: cannot create directory '/home/p/.factorio-bonsai': Read-only file system",
                "curl: (6) Could not resolve host: auth.factorio.com",
                "bind: Address family not supported by protocol",
                "touch: /home/p/x: Operation not permitted"]:
    check(f"sandbox: {blocked[:46]}", ga.sandbox_blocked(blocked), True)
for real in ["factorio-start.sh: line 12: syntax error near unexpected token",
             "No Factorio at /media/D/... - set FACTORIO=...",
             "exit code 1", ""]:
    check(f"not the sandbox: {real[:40]!r}", ga.sandbox_blocked(real), False)

print("\n-- it asks, and says what is being given up --")
def run_once(result, answer):
    """One RUN whose sandboxed attempt returns `result`; the user answers `answer`."""
    worker = ga.Worker("start it", False, [], {"native_tools": False})
    asked = {}
    worker.ask_permission = lambda description: (asked.update(text=description), answer)[1]
    calls = []
    def fake(argv, workdir, sandbox=None):
        calls.append(sandbox)
        return result if sandbox is None else "exit code 0 in /w (NOT sandboxed)\nup"
    return worker.offer_without_sandbox(result, ["start.sh"], "/w", fake), asked, calls

BLOCKED = ("exit code 1 in /w (sandboxed)\nmkdir: cannot create directory "
           "'/home/p/.factorio-bonsai': Read-only file system")

out, asked, calls = run_once(BLOCKED, True)
check("the user is asked", "OUTSIDE the sandbox" in asked.get("text", ""), True)
check("  ...shown the command", "start.sh" in asked["text"], True)
check("  ...and told what it gains", "can write anywhere you can" in asked["text"], True)
check("  ...and warned", "Only allow this if you know" in asked["text"], True)
check("on yes it runs again unsandboxed", calls, [False])
check("  ...and the new result is what comes back", "NOT sandboxed" in out, True)

out, asked, calls = run_once(BLOCKED, False)
check("on no it is not run again", calls, [])
check("  ...the original failure is kept", "Read-only file system" in out, True)
check("  ...and it is told not to keep trying", "Do not try again this turn" in out, True)

print("\n-- and it only asks when the sandbox was really the reason --")
for result in ["exit code 1 in /w (sandboxed)\nsyntax error near unexpected token",
               "exit code 0 in /w (sandboxed)\nall good",
               "exit code 1 in /w (NOT sandboxed)\nRead-only file system"]:
    _, asked, calls = run_once(result, True)
    check(f"quiet for: {result.splitlines()[1][:38]}", (asked, calls), ({}, []))

print("\n-- a background job says it too, where it actually shows --")
class FakeProcess:
    name, argv, dropped, sandboxed = "server", ["start.sh"], 0, True
    lines = ["Creating a new map", "mkdir: cannot create directory: Read-only file system"]
    def status(self): return "running"
ga.shell._background["server"] = FakeProcess()
read = ga.background_read("server")
check("the output still comes back", "Read-only file system" in read, True)
check("with the sandbox named as the reason", "started sandboxed" in read, True)
check("  ...and what to offer the user",
      "start it again outside the sandbox" in read, True)

FakeProcess.lines = ["Creating a new map", "Done."]
check("a healthy job gets no such note", "started sandboxed" in ga.background_read("server"), False)
FakeProcess.sandboxed = False
FakeProcess.lines = ["Read-only file system"]
check("nor does an unsandboxed one that failed for its own reasons",
      "started sandboxed" in ga.background_read("server"), False)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
