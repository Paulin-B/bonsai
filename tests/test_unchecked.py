"""Saying something about this machine without looking at it.

Asked to start the Factorio server, it answered "I can't start a Factorio server for
you because it's not installed on this machine" - with Factorio installed, a launcher
for it in the project, and not one tool call made. The existing guard catches a model
denying its own abilities; this is the same refusal worded as a fact about the world,
which sounds far more authoritative and is worth exactly as much.
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

REAL = ("I can't start a Factorio server for you because it's not installed on this "
        "machine. I can help you set one up if you'd like, but I'd need to know what "
        "kind of server you want (single-player, multiplayer, etc.) and if you have "
        "the game installed. Let me know!")

print("\n-- the reply that prompted this --")
check("it is caught", bool(ga.UNCHECKED_CLAIM_RE.search(REAL)), True)
check("the older guard misses it, which is why it got through",
      bool(ga.DENIES_TOOL_RE.search(REAL)), False)

print("\n-- claims about this machine that were never checked --")
for claim in ["Factorio is not installed.",
              "You don't have docker installed.",
              "There is no such script in your project.",
              "That file does not exist on this machine.",
              "Godot is not available on this system.",
              "There is no program called aseprite."]:
    check(f"caught: {claim}", bool(ga.UNCHECKED_CLAIM_RE.search(claim)), True)

print("\n-- ordinary sentences it must not fire on --")
for fine in ["I installed the package and the build went through.",
             "The server is running on port 34198.",
             "Here is how to install it if you want to.",
             "There is no rush - it can wait until tomorrow.",
             "The file exists and has 40 lines.",
             "I have not installed anything."]:
    check(f"quiet: {fine}", bool(ga.UNCHECKED_CLAIM_RE.search(fine)), False)

print("\n-- and the turn loop actually acts on it --")
# The regex being right is not the point: the loop has to notice and push back.
def drive(replies, tool_result="ok", steps=6):
    worker = ga.Worker("start the factorio server", False, [],
                       {"max_tool_steps": steps, "native_tools": False})
    seen = []
    seq = list(replies)
    def ask(system, prompt, image=None, temperature=None, with_tools=True, stream=False):
        seen.append(prompt)
        return (seq.pop(0) if seq else replies[-1], None)
    worker.ask_model = ask
    worker.run_tool = lambda name, argument: tool_result
    worker.model_name = lambda: "fake"
    out = {}
    worker.finished.connect(lambda r, t, e, u: out.update(reply=r, trace=t))
    worker.failed.connect(lambda m: out.update(error=m))
    worker.run()
    return out, seen

def nudges(prompts):
    """How many times the push-back was ADDED.

    It is appended to a prompt that keeps growing, so it shows up in every later call
    as well - counting prompts that contain it counts the same nudge twice."""
    return max((p.count("you did not look") for p in prompts), default=0)

out, prompts = drive([REAL, "Started it; the server is listening on 34198."])
check("it did not stop at the guess", out.get("reply"),
      "Started it; the server is listening on 34198.")
check("it was pushed back on, once", nudges(prompts), 1)
nudge = [p for p in prompts if "you did not look" in p][0]
check("and told it had not looked", "you did not look" in nudge, True)
check("  ...with ways to check named", "which" in nudge, True)
check("  ...and allowed to still say it is missing",
      "say how you checked" in nudge, True)

print("\n-- but only when nothing was checked --")
# A claim earned by actually running something is not a guess, and must stand.
earned = ("I ran which factorio and it printed nothing, so it is not installed.")
out, prompts = drive(["[TOOL: RUN: which factorio]", earned], tool_result="")
check("a claim backed by a tool call is left alone", out.get("reply"), earned)
check("  ...and it was never told it had not looked", nudges(prompts), 0)

print("\n-- and it only nudges once --")
out, prompts = drive([REAL, REAL, "Fine, it is there and I started it."])
check("a second guess is not nudged again", nudges(prompts), 1)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
