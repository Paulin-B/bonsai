"""A face, driven by things that already existed.

A PNGTuber is pictures swapped on two signals: is it talking, and how does it feel.
Both were already here - the speaker emits the loudness of what it is saying twenty
times a second, and the mood is a number on disk that outlives the session. This is
mostly the wiring between them, so that is mostly what is checked.
"""
import sys
from pathlib import Path
from bonsai_under_test import load
ga = load()
ok = fail = 0
def check(label, got, want):
    global ok, fail
    if got == want: ok += 1; print(f"  pass  {label}")
    else: fail += 1; print(f"  FAIL  {label}\n        got:  {got!r}\n        want: {want!r}")

app = ga.QApplication(sys.argv)

print("\n-- the mouth follows the sound, not a timer --")
S = Path(__file__).resolve().parent.parent
face = ga.Avatar(); face.resize(160, 200)
face.set_state("talk")
face.set_level(1.0)
loud = face.openness
face.set_level(0.0)
check("a loud moment opens it", loud > 0.4, True)
check("  ...and silence closes it again", face.openness < loud, True)
for _ in range(12):
    face.set_level(0.0)
check("it settles shut rather than hovering", face.openness < 0.02, True)
face.set_level(5.0)
check("a level out of range does not open it past the stop", face.openness <= 1.0, True)

print("\n-- an envelope comes out of real speech --")
# Read from the file rather than tapped from the speakers: same information, no
# extra dependency.
import wave, math, struct
wav = Path("/tmp/bonsai-envelope-test.wav")
with wave.open(str(wav), "wb") as out:
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
    frames = []
    for i in range(16000):                     # a second: loud, silent, loud
        amp = 12000 if (i < 5000 or i > 11000) else 5
        frames.append(struct.pack("<h", int(amp * math.sin(i * 0.2))))
    out.writeframes(b"".join(frames))
levels, seconds = ga.envelope(wav)
check("it is about a second long", 0.9 <= seconds <= 1.1, True)
check("  ...sampled at the drawing rate", len(levels) >= 15, True)
check("the loud part is loud", max(levels), 1.0)
check("  ...and the quiet part is not", min(levels) < 0.1, True)
check("a missing file gives nothing rather than raising", ga.envelope("/nope.wav"), ([], 0.0))
wav.unlink(missing_ok=True)

print("\n-- expression follows the mood that already exists --")
for value, mood in ((0.8, "on a roll"), (0.0, "steady"), (-0.8, "fed up")):
    ga.shift_mood(value - ga.load_mood()[0], "testing")
    face.refresh_mood()
    check(f"mood {value:+.1f} reaches the face", face.mood_name, ga.mood_name(ga.load_mood()[0]))

print("\n-- every state draws something, and something different --")
from PyQt6.QtGui import QPixmap, QColor
def render(state, mood, level, tick=0):
    a = ga.Avatar(); a.resize(160, 200)
    a.state, a.openness, a.mood_value, a.think_tick = state, level, mood, tick
    a.mood_name = ga.mood_name(mood)
    shot = QPixmap(160, 200); shot.fill(QColor("#101012"))
    a.render(shot)
    return shot.toImage()

drawings = {
    "idle": render("idle", 0.0, 0.0),
    "talking": render("talk", 0.0, 0.9),
    "thinking": render("think", 0.0, 0.0),
    "pleased": render("idle", 0.8, 0.0),
    "fed up": render("idle", -0.8, 0.0),
}
for name, image in drawings.items():
    check(f"{name} draws", image.isNull(), False)
def pixels(image):
    return bytes(image.bits().asarray(image.sizeInBytes()))
seen = {}
for name, image in drawings.items():
    seen.setdefault(pixels(image), []).append(name)
check("no two states look identical", [n for n in seen.values() if len(n) > 1], [])

print("\n-- your own art is used when it is there --")
import shutil, tempfile
# A fresh folder each run: the last run's idle.png made "nothing there" find something.
folder = Path(tempfile.mkdtemp(prefix="bonsai-avatar-"))
shutil.rmtree(folder, ignore_errors=True)
folder.mkdir(parents=True)
ga.save_settings({**ga.DEFAULTS, "avatar_folder": str(folder)})
check("nothing there means draw it", ga.art_for("idle", "steady", 0.0), None)
(folder / "idle.png").write_bytes(QPixmap(8, 8).toImage().save(str(folder / "idle.png"))
                                  and b"" or (folder / "idle.png").read_bytes())
check("an idle picture is picked up", ga.art_for("idle", "steady", 0.0),
      str(folder / "idle.png"))
check("  ...and stands in for states you did not draw",
      ga.art_for("talk", "steady", 0.5), str(folder / "idle.png"))
(folder / "talk-3.png").write_bytes((folder / "idle.png").read_bytes())
check("a mouth shape is preferred when the mouth is open",
      ga.art_for("talk", "steady", 0.95), str(folder / "talk-3.png"))
check("  ...and not when it is shut", ga.art_for("talk", "steady", 0.0),
      str(folder / "idle.png"))
(folder / "idle-fed-up.png").write_bytes((folder / "idle.png").read_bytes())
check("a mood variant wins over the plain one",
      ga.art_for("idle", "fed up", 0.0), str(folder / "idle-fed-up.png"))
ga.save_settings(dict(ga.DEFAULTS))

print("\n-- and the window it lives in --")
window = ga.AvatarWindow()
flags = window.windowFlags()
check("frameless", bool(flags & ga.Qt.WindowType.FramelessWindowHint), True)
check("always on top", bool(flags & ga.Qt.WindowType.WindowStaysOnTopHint), True)
check("transparent, so it can sit over anything",
      window.testAttribute(ga.Qt.WidgetAttribute.WA_TranslucentBackground), True)
window.resize(300, 380)
window.show()
for _ in range(4):
    app.processEvents()
check("the face fills it", window.avatar.size(), window.size())
window.close()

print("\n-- wired to the app, not to a demo --")
w = ga.Bonsai()
w.avatar_box.setChecked(True)
check("the toggle opens it", w.face is not None, True)
w.set_busy("Working")
check("it thinks while the app works", w.face.avatar.state, "think")
w.clear_busy()
check("  ...and stops when the app does", w.face.avatar.state, "idle")
w._face_talking(True)
check("it talks when the speaker talks", w.face.avatar.state, "talk")
w.set_busy("Working")
check("  ...and talking is not interrupted by thinking", w.face.avatar.state, "talk")
w._face_talking(False)
w.avatar_box.setChecked(False)
check("the toggle closes it", w.face, None)
w.face_state("think")
check("and nothing breaks when it is not there", w.face, None)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
