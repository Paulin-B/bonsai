"""Hearing you - and, more importantly, not hearing you.

Always on with a switch is the design: someone on a Discord call does not want every
word going to an assistant, so the switch is one click and takes effect immediately
rather than at the end of the sentence being recorded.

The part that has to be right is finding speech before transcribing it. Whisper given
a stream of silence returns "Thank you." and "you" indefinitely, so audio is only sent
once it has been loud enough for long enough - and these checks are about that, with
no microphone involved.
"""
import sys
import numpy as np
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)
FRAME = ga.listen.FRAME_BYTES

def tone(level, frames=1):
    """`frames` frames of noise at roughly `level` mean amplitude."""
    rng = np.random.default_rng(7)
    return (rng.normal(0, level * 1.3, FRAME * frames // 2)).astype("<i2").tobytes()

SILENCE = tone(10)
SPEECH = tone(900)

print("\n-- loudness --")
check("silence is quiet", ga.loudness(SILENCE) < 60, True)
check("speech is not", ga.loudness(SPEECH) > 400, True)
check("an empty frame does not divide by zero", ga.loudness(b""), 0.0)

print("\n-- silence is never an utterance --")
# This is the one that matters: whisper on silence invents words forever.
seg = ga.Segmenter()
check("fifty frames of room tone produce nothing",
      [seg.feed(SILENCE) for _ in range(50)].count(None), 50)

print("\n-- speech is found, and kept whole --")
seg = ga.Segmenter()
out = [seg.feed(SILENCE) for _ in range(5)]
out += [seg.feed(SPEECH) for _ in range(20)]
check("nothing is emitted while talking", [x for x in out if x], [])
out = [seg.feed(SILENCE) for _ in range(12)]
spoken = [x for x in out if x]
check("the utterance lands once it goes quiet", len(spoken), 1)
# The speech, plus the preroll before it and the quiet window that proved it had
# ended. Trailing silence costs nothing - whisper ignores it - but losing the start
# of the first word would change the sentence.
check("  ...holding the speech and no more than the windows around it",
      20 <= len(spoken[0]) / FRAME <= 20 + seg.preroll + seg.stop_frames, True)
check("  ...including audio from before it crossed the threshold",
      len(spoken[0]) / FRAME > 20, True)

print("\n-- a pause inside a sentence is not the end of it --")
seg = ga.Segmenter()
for _ in range(20): seg.feed(SPEECH)
short = [seg.feed(SILENCE) for _ in range(4)]     # 0.4s, less than stop_frames
check("a short gap does not end it", [x for x in short if x], [])
for _ in range(10): seg.feed(SPEECH)
ended = [seg.feed(SILENCE) for _ in range(10)]
check("a longer one does", len([x for x in ended if x]), 1)

print("\n-- someone who will not stop talking --")
seg = ga.Segmenter(longest=2.0)
out = [seg.feed(SPEECH) for _ in range(40)]
check("it is cut at the limit rather than growing forever",
      len([x for x in out if x]) >= 1, True)

print("\n-- the switch takes effect at once --")
seg = ga.Segmenter()
for _ in range(20): seg.feed(SPEECH)
check("mid-sentence, it is holding audio", seg.talking, True)
seg.drop()
check("switching off throws it away", (seg.talking, seg.speech), (False, []))
check("  ...and nothing arrives afterwards",
      [seg.feed(SILENCE) for _ in range(12)].count(None), 12)

print("\n-- what it listens to --")
check("a source is passed through",
      "mysource" in " ".join(ga.recorder_argv("mysource") or []), True)
check("blank means the default input",
      "--target" in (ga.recorder_argv("") or []), False)
check("it records what whisper wants",
      "16000" in " ".join(ga.recorder_argv("") or []), True)
check("a monitor source is just another source",
      "alsa_output.x.monitor" in " ".join(ga.recorder_argv("alsa_output.x.monitor") or []),
      True)

print("\n-- and the settings that shape it --")
check("always on by default", ga.DEFAULTS["listen_mode"], "always")
check("it types rather than sending, until asked", ga.DEFAULTS["listen_sends"], False)
check("small.en, which hears Factorio", ga.DEFAULTS["listen_model"], "small.en")

print("\n-- heard text reaches the box --")
w = ga.Bonsai()
w.input.setPlainText("half a typed")
w.on_heard("message about iron")
check("it is appended, not replacing what you typed",
      w.input.text().strip(), "half a typed message about iron")
w.input.setPlainText("")
w.on_heard("start the server")
check("and stands alone when the box is empty", w.input.text().strip(), "start the server")
check("nothing was sent", w.input.text().strip() != "", True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
