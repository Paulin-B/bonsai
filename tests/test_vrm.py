"""What a 3D avatar should be doing, decided without a 3D avatar.

A VRM is a glTF file carrying a set of named expressions that every conforming model
must provide - aa, ih, ou, ee, oh for the mouth, happy, angry, sad, relaxed, neutral
for the face, blink, and lookUp and friends for the gaze. That is a richer vocabulary
than the drawn face has, and it takes the same two inputs: how loud it is and how it
feels.

Deciding is kept apart from drawing on purpose. This is arithmetic and can be checked
with no renderer installed; which renderer draws it is a separate question with its
own dependencies.
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

print("\n-- the mood, in the model's own vocabulary --")
for value, face in ((0.9, "happy"), (0.6, "happy"), (0.3, "relaxed"), (0.0, "neutral"),
                    (-0.3, "sad"), (-0.9, "angry")):
    check(f"{value:+.1f} is {face}", ga.mood_face(value), face)
check("anything below the floor still has a face", ga.mood_face(-5.0), "angry")

print("\n-- and never at full strength --")
# A model holding an expression at 1.0 looks like a mask, and the mouth has to stay
# legible through whatever the face is doing.
for value in (1.0, -1.0, 0.7):
    weights = ga.expression_weights("idle", 0.0, mood=value)
    check(f"mood {value:+.1f} is held back", max(weights.values()) <= 0.85, True)
check("a steady mood asks for nothing at all",
      ga.expression_weights("idle", 0.0, mood=0.0), {})

print("\n-- the mouth runs through vowels, it does not just open --")
shapes = {ga.viseme_for(0.8, tick)[0] for tick in range(10)}
check("several shapes are used", len(shapes) > 1, True)
check("  ...all of them real ones", shapes <= set(ga.VISEMES), True)
check("silence shuts it", ga.viseme_for(0.0)[1], 0.0)
check("  ...and so does very nearly silence", ga.viseme_for(0.03)[1], 0.0)
loud = ga.viseme_for(1.0, 0)[1]
check("loud opens it wide", loud > 0.9, True)
check("quiet speech opens it a little", 0 < ga.viseme_for(0.1, 0)[1] < loud, True)

print("\n-- the whole face at once --")
talking = ga.expression_weights("talk", 0.9, tick=2, mood=0.4)
check("it talks", any(name in ga.VISEMES for name in talking), True)
check("  ...while still looking pleased", "relaxed" in talking, True)
thinking = ga.expression_weights("think", 0.0, mood=0.0)
check("thinking looks up", thinking, {"lookUp": 0.6})
blinking = ga.expression_weights("idle", 0.0, blink=1.0, mood=0.0)
check("blinking blinks", blinking, {"blink": 1.0})
check("a silent moment mid-speech closes the mouth",
      any(name in ga.VISEMES for name in ga.expression_weights("talk", 0.0, mood=0.0)),
      False)

print("\n-- nothing asked for that a model may not have --")
# A renderer that silently ignores an unknown name is how you end up wondering why
# the mouth never moves.
every = set()
for state in ("idle", "talk", "think"):
    for level in (0.0, 0.5, 1.0):
        for tick in range(6):
            for mood in (-1.0, 0.0, 1.0):
                every |= set(ga.expression_weights(state, level, 1.0, tick, mood))
check("everything reachable is a name from the spec",
      sorted(every - set(ga.VRM_EXPRESSIONS)), [])
check("and that is checkable against a given model",
      ga.unknown_expressions({"aa": 1.0, "smirk": 1.0}, ga.VRM_EXPRESSIONS), ["smirk"])
check("  ...with nothing to report when it all fits",
      ga.unknown_expressions({"aa": 1.0}, ga.VRM_EXPRESSIONS), [])

print("\n-- it reads the mood from disk when not told one --")
ga.shift_mood(0.9 - ga.load_mood()[0], "testing")
check("a good mood reaches the face",
      ga.mood_face(ga.load_mood()[0]) in ("happy", "relaxed"), True)
check("  ...without being passed in",
      bool(ga.expression_weights("idle", 0.0)), True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
