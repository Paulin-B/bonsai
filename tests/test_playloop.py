"""Playing a game: look, press something, see what happened, talk about it.

The loop exists in code rather than in the prompt for one reason - the model cannot
tell whether its keypress landed. xdotool reports that it sent the key, which is not
the same as the game receiving it, so the loop photographs the screen before and
after and feeds the answer back. Most of these checks are about that, and about the
loop not being allowed to do anything but play.
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

def play(replies, frames=None, config=None, goal=""):
    """One run with the model, the screen and the stage all scripted.

    `frames` is what the stage looks like at each look; repeating one means nothing
    on screen changed."""
    worker = ga.PlayWorker({**ga.DEFAULTS, "play_step_seconds": 0,
                            "play_max_steps": len(replies), **(config or {})}, goal)
    seq, shots = list(replies), list(frames or ["a", "b"] * 40)
    out = {"said": [], "did": [], "end": None}
    worker.comment.connect(lambda t: out["said"].append(t))
    worker.stopped.connect(lambda t: out.update(end=t))
    worker.failed.connect(lambda t: out.update(end=t, error=True))
    worker.ask = lambda prompt, frame: (out.setdefault("prompt", prompt), 
                                        seq.pop(0) if seq else replies[-1])[1]
    ga.worker.stage_look = lambda: ("on the stage", shots.pop(0) if shots else "z")
    ga.worker.handle_stage = lambda action: (out["did"].append(action),
                                             f"Did {action}.")[1]
    ga.worker.time = type("T", (), {"sleep": staticmethod(lambda s: None)})()
    worker.run()
    out["history"] = worker.history
    return out

STEP = "SEE: a menu\nDO: KEY Return\nSAY: Right, here we go."

print("\n-- it plays --")
out = play([STEP] * 3, frames=["a", "b", "c", "d", "e", "f", "g"])
check("it pressed what it said it would", out["did"], ["KEY Return"] * 3)
check("it said something", out["said"][0], "Right, here we go.")
check("and stopped when the budget ran out", "Played 3 steps" in out["end"], True)

print("\n-- it can only play, not run the machine --")
# START would let an unattended loop launch programs; STOP would let it close the
# game it is meant to be playing.
for forbidden in ("START xterm", "STOP", "OUTPUT", "RUN rm -rf /"):
    out = play([f"SEE: x\nDO: {forbidden}\nSAY: NOTHING"])
    check(f"{forbidden.split()[0]} never reaches the stage", out["did"], [])
    check(f"  ...and it is told why", "not something you can do here"
          in out["history"][0][1], True)
out = play(["SEE: x\nDO: HOLD Right 600\nSAY: NOTHING"])
check("but a real move does", out["did"], ["HOLD Right 600"])

print("\n-- NOTHING is a move, and does not touch the stage --")
out = play(["SEE: a cutscene\nDO: NOTHING\nSAY: I will let this play out."])
check("nothing is sent", out["did"], [])
check("and it counts as a step", len(out["history"]), 1)
check("recorded as what it was", out["history"][0][1], "watched a frame")

print("\n-- a key that did nothing is not reported as success --")
# The whole point. Same picture before and after means the game did not react,
# whatever xdotool said.
out = play([STEP] * 4, frames=["same"] * 10)
check("the loop measured no change", [round(c, 3) for c in
      [h[2] for h in out["history"]]], [0.0] * 4)
worker = ga.PlayWorker({**ga.DEFAULTS})
worker.history = [("KEY space", "Pressed space.", 0.0)] * 3
block = worker.recent_block()
check("three dead presses are called out", "NOTHING CHANGED" in block, True)
check("  ...and it is told to try something else",
      "Try a different one" in block, True)
worker.history = [("KEY space", "Pressed space.", 0.5)] * 3
check("a screen that did change is not", "Your last three actions"
      in worker.recent_block(), False)

print("\n-- it stops when there is nothing left to play --")
out = play(["SEE: the end screen\nDO: NOTHING\nSAY: NOTHING"] * 8, frames=["still"] * 20)
check("three idle steps on a still screen end the run", len(out["history"]), 3)
check("  ...and it says why", "Nothing is happening" in out["end"], True)
out = play(["SEE: x\nDO: NOTHING\nSAY: NOTHING"] * 8, frames=["a", "b"] * 20)
check("but idling while the screen moves is watching, not stopping",
      len(out["history"]), 8)

print("\n-- it does not say the same thing twice in different words --")
out = play(["SEE: x\nDO: NOTHING\nSAY: I did it! The red square is on the green one.",
            "SEE: x\nDO: NOTHING\nSAY: I did it! I totally did it, red is on green!",
            "SEE: x\nDO: NOTHING\nSAY: Right, what is next then."],
           frames=["a", "b"] * 10)
check("the rephrased boast is dropped", len(out["said"]), 2)
check("the first one stands", out["said"][0].startswith("I did it!"), True)
check("and a genuinely new line gets through",
      out["said"][1], "Right, what is next then.")
out = play(["SEE: x\nDO: KEY a\nSAY: NOTHING"], frames=["a", "b"])
check("SAY: NOTHING says nothing", out["said"], [])

print("\n-- it will not play a stage that is not there --")
worker = ga.PlayWorker({**ga.DEFAULTS, "play_step_seconds": 0})
ga.worker.stage_look = lambda: ("[No stage is open. STAGE: START <command> first.]", None)
ended = {}
worker.stopped.connect(lambda t: ended.update(why=t))
worker.run()
check("it stops rather than photographing an empty display",
      "No stage is open" in ended["why"], True)

print("\n-- and it can be stopped --")
worker = ga.PlayWorker({**ga.DEFAULTS, "play_step_seconds": 0, "play_max_steps": 500})
worker.cancel()
ga.worker.stage_look = lambda: ("on the stage", "a")
stopped = {}
worker.stopped.connect(lambda t: stopped.update(why=t))
worker.run()
check("cancelling ends the run before it looks", stopped["why"], "Stopped.")
check("and nothing was played", worker.history, [])

print("\n-- the prompt it is given --")
prompt = ga.PLAY_PROMPT.format(name="Bonsai", character=ga.character_voice(), mood="",
                               goal="", recent="- nothing yet")
check("one action at a time", "One action per turn" in prompt, True)
check("it is told not to plan a sequence", "do not plan" in prompt, True)
check("it is told to react, being watched", "being watched" in prompt, True)
check("but not to narrate its own keypresses",
      "Do not narrate your own keypresses" in prompt, True)
check("and not to invent what it cannot see",
      "Only say what you can actually see" in prompt, True)
ga.store_growth("[EVOLVE: OPINION: Peglin is a slot machine with extra steps.]")
withchar = ga.PLAY_PROMPT.format(name="Bonsai", character=ga.character_voice(),
                                 mood="", goal="", recent="-")
check("it carries the character, opinions and all",
      "Peglin is a slot machine" in withchar, True)
goal = ga.PLAY_PROMPT.format(name="B", character="-", mood="",
                             goal="You are trying to: win\n\n", recent="-")
check("a goal reaches it when there is one", "You are trying to: win" in goal, True)

print("\n-- the frame comparison the loop leans on --")
check("identical frames are no change", ga.frame_change("abc", "abc"), 0.0)
check("a missing frame is assumed to be change, not stillness",
      ga.frame_change("abc", None), 1.0)
check("unreadable frames too", ga.frame_change("not base64", "also not"), 1.0)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
