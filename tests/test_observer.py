"""The proactive observer - the half of Bonsai that speaks without being spoken to.

It had said one thing in 193 checks, and the one thing was "Enter a name to continue".
The prompt was the cause and rewording it was not the cure: asked yes-or-no whether to
interrupt, this model answers no, because SILENT is one token and always defensible.
So it is not asked. It writes a remark and rates it, and the app applies the threshold.
These checks are about that split, and about the remark not being allowed to invent.
"""
import re
import sys
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

def observe(reply, config=None, record_only=False):
    """One observer run against a scripted model reply."""
    worker = ga.ObserverWorker({**ga.DEFAULTS, **(config or {})}, [], record_only)
    out = {}
    worker.comment.connect(lambda t: out.update(said=t))
    worker.quiet.connect(lambda: out.setdefault("quiet", True))
    worker.failed.connect(lambda m: out.update(error=m))
    class Response:
        status_code = 200
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": reply}}]}
    real_post, real_capture = ga.requests.post, ga.worker.capture_screen
    ga.requests.post = lambda *a, **k: Response()
    ga.worker.capture_screen = lambda *a, **k: "ZmFrZQ=="
    try:
        worker.run()
    finally:
        ga.requests.post, ga.worker.capture_screen = real_post, real_capture
    return out

GOOD = ("SEEN: Godot with flow_field.gd open and a red parse error.\n"
        "SAY: That is the tuple unpacking thing biting you again.\n"
        "WORTH: 4")

print("\n-- the rating decides, not the model --")
check("a remark worth more than the threshold is said",
      observe(GOOD, {"proactive_worth": 3}).get("said"),
      "That is the tuple unpacking thing biting you again.")
check("the same remark below the threshold is not",
      "said" in observe(GOOD, {"proactive_worth": 5}), False)
check("  ...and that counts as a quiet check, not a failure",
      observe(GOOD, {"proactive_worth": 5}).get("quiet"), True)
check("exactly at the threshold is said",
      "said" in observe(GOOD, {"proactive_worth": 4}), True)

print("\n-- the threshold is a dial the user owns --")
for level in (1, 2, 3, 4, 5):
    reply = GOOD.replace("WORTH: 4", "WORTH: 3")
    said = "said" in observe(reply, {"proactive_worth": level})
    check(f"a 3 is {'said' if level <= 3 else 'held back'} at threshold {level}",
          said, level <= 3)

print("\n-- a reply that ignored the format is not guessed at --")
# Guessing high here would swap one failure for its opposite: an unrated remark said
# every ninety seconds.
check("no WORTH line means no interruption",
      "said" in observe("SEEN: A desktop.\nSAY: Something or other."), False)
check("a bare SILENT is still silence", "said" in observe("SILENT"), False)
check("and an empty reply is reported rather than swallowed",
      "error" in observe(""), True)

print("\n-- SAY stops at WORTH, and does not swallow it --")
said = observe(GOOD, {"proactive_worth": 1}).get("said", "")
check("the rating is not read out to the user", "WORTH" in said, False)
check("nor is the digit", said.endswith("again."), True)
check("a bolded rating is still read", ga.WORTH_RE.search("WORTH: **5**").group(1), "5")
check("an unrated reply gives nothing to read", ga.WORTH_RE.search("SAY: hi"), None)

print("\n-- what it saw is recorded whatever it decides --")
observe(GOOD, {"proactive_worth": 5})
notes = [e["note"] for e in ga.load_screen_log()]
check("a screen it stayed quiet about is still remembered",
      any("flow_field" in n for n in notes), True)
observe("SILENT")
check("but a bare SILENT is not recorded as what was on screen",
      any(e["note"].strip().upper() == "SILENT" for e in ga.load_screen_log()), False)

print("\n-- record_only never speaks, however good the remark --")
out = observe(GOOD.replace("WORTH: 4", "WORTH: 5"), {"proactive_worth": 1}, record_only=True)
check("the screen-noting pass stays silent", "said" in out, False)
check("  ...and still records the screen",
      any("flow_field" in e["note"] for e in ga.load_screen_log()), True)

print("\n-- the prompt it is actually given --")
prompt = ga.OBSERVER_PROMPT.format(name="Bonsai", recent="- none", history="- none",
                                   character=ga.character_voice(), mood="", minutes=10)
check("it is told to always write a remark", "ALWAYS write one" in prompt, True)
check("and told the decision is not its own", "not your decision" in prompt, True)
check("the rating scale is spelled out", "WORTH:" in prompt and "5 -" in prompt, True)
check("it is told to rate low, so the scale does not drift up",
      "honestly and low" in prompt, True)
check("captioning the screen is ruled out", "is a caption" in prompt, True)
check("so is asserting a stale error is still there",
      "may all be fixed by now" in prompt, True)
check("and so is flogging one joke", "stops being a joke" in prompt, True)
check("it carries the character, which is why it sounds like anyone",
      "Curious and sarcastic" in prompt or "no character defined" in prompt, True)

print("\n-- the character reaches it --")
ga.store_growth("[EVOLVE: OPINION: GDScript tuple unpacking is a trap.]")
ga.store_growth("[EVOLVE: JOKE: MotherMachine is the hungry boi.]")
voice = ga.character_voice()
check("its opinions are offered to it", "You think: GDScript" in voice, True)
check("and its jokes", "running joke" in voice, True)
check("it is not just a name any more", len(voice.splitlines()) > 2, True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
