"""A face, driven by things that already existed.

A PNGTuber is pictures swapped on two signals: is it talking, and how does it feel.
Both were already here - the speaker emits the loudness of what it is saying twenty
times a second, and the mood is a number on disk that outlives the session. This is
mostly the wiring between them, so that is mostly what is checked.
"""
import json
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
# Not the whole window any more: the panel underneath takes its share.
check("the face fills the width", window.avatar.width(), window.width())
check("  ...and the panel has the rest",
      window.avatar.height() + window.panel.height() <= window.height(), True)
check("  ...with the face getting most of it",
      window.avatar.height() > window.panel.height(), True)
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

print("\n-- a tiling compositor has to be told --")
# Frameless and always-on-top mean nothing to Hyprland: it tiled the 200x250 window
# and stretched it to 1163x1394, and Qt asking for its size back was ignored.
import subprocess as sp
sent = []
real_run, real_which = ga.avatar.__dict__.get("_run"), None
import shutil as sh
class FakeRun:
    stdout = "[]"
def fake(argv, **kw):
    sent.append(" ".join(argv))
    return FakeRun()
orig_run, orig_which = sp.run, sh.which
sp.run, sh.which = fake, (lambda name: "/usr/bin/hyprctl")
try:
    ga.float_it(200, 250)
    plain = list(sent)
    sent.clear()
    ga.float_it(200, 250, x=40, y=60)
    placed = list(sent)
finally:
    sp.run, sh.which = orig_run, orig_which

check("it floats the window", any("window.float" in c for c in plain), True)
check("  ...sizes it exactly, or it keeps the tiled size",
      any("exact=true" in c and "x=200" in c and "y=250" in c for c in plain), True)
check("  ...and pins it, so it follows you between workspaces",
      any("window.pin" in c for c in plain), True)
check("float is sent once, since it is a toggle",
      sum("window.float" in c for c in plain), 1)
check("it targets itself by title, not whatever is focused",
      all(ga.AVATAR_TITLE in c for c in plain), True)
check("a remembered position is moved to", any("window.move" in c for c in placed), True)
check("  ...and is not sent when there is none",
      any("window.move" in c for c in plain), False)

sh_which = sh.which
sh.which = lambda name: None
try:
    check("no hyprctl means no compositor to argue with", ga.float_it(200, 250), False)
    check("  ...and nothing to ask where it is", ga.hypr_geometry(), None)
finally:
    sh.which = sh_which

print("\n-- where it is, asked of the one who knows --")
# Under Wayland a client is not told where it is: Qt reported 60,60 for a window the
# compositor had at 3946,37.
import os
# Two windows with the same title: one belonging to another Bonsai, one to this
# process. A leftover from a crash looks exactly like the first, and taking it meant
# a window adopted somebody else's size and position.
CLIENTS = json.dumps([
    {"title": "something else", "at": [0, 0], "size": [10, 10], "pid": os.getpid()},
    {"title": ga.AVATAR_TITLE, "at": [1, 2], "size": [900, 189], "pid": os.getpid() + 1},
    {"title": ga.AVATAR_TITLE, "at": [3946, 37], "size": [200, 250], "pid": os.getpid()},
])
class Reply:
    stdout = CLIENTS
sp.run, sh.which = (lambda *a, **k: Reply()), (lambda name: "/usr/bin/hyprctl")
try:
    check("it finds itself among the windows", ga.hypr_geometry(), (3946, 37, 200, 250))
    check("  ...and not another Bonsai's window with the same title",
          ga.hypr_geometry()[2:], (200, 250))
    window = ga.AvatarWindow()
    window.remember()
    check("and saves what the compositor says, not what Qt guessed",
          (ga.settings()["avatar_x"], ga.settings()["avatar_y"]), (3946, 37))
    check("  ...including the size", ga.settings()["avatar_width"], 200)
finally:
    sp.run, sh.which = orig_run, orig_which

print("\n-- resizing it, since there is no frame to drag --")
ga.save_settings({**ga.DEFAULTS, "avatar_width": 200, "avatar_height": 250})
window = ga.AvatarWindow()
check("it opens at the remembered size", window.wanted, (200, 250))
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtCore import QPointF
def scroll(widget, up):
    widget.wheelEvent(QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), ga.QPoint(0, 0),
        ga.QPoint(0, 120 if up else -120), ga.Qt.MouseButton.NoButton,
        ga.Qt.KeyboardModifier.NoModifier, ga.Qt.ScrollPhase.NoScrollPhase, False))
scroll(window, True)
check("scrolling up makes it bigger", window.wanted[0], 220)
check("  ...keeping its shape", window.wanted[1], 275)
for _ in range(40): scroll(window, False)
check("it will not shrink to nothing", window.wanted[0], 100)
for _ in range(60): scroll(window, True)
check("nor grow past the screen", window.wanted[0], 900)
ga.save_settings(dict(ga.DEFAULTS))

print("\n-- the panel under the face --")
# Laid out the way Open-LLM-VTuber does it, because the arrangement is right: the
# line it just said is what you look at, the state is one word, and the controls you
# reach for while it is talking are stop and the mic.
win = ga.AvatarWindow()
check("there is a caption, hidden until there is something to caption",
      win.panel.caption.isVisibleTo(win), False)
win.panel.say("Right, that was never going to work.")
check("a line shows", win.panel.caption.text(), "Right, that was never going to work.")
check("  ...and the caption appears", win.panel.caption.isVisibleTo(win), True)
win.panel.say("")
check("an empty line hides it again", win.panel.caption.isVisibleTo(win), False)
win.panel.say("word " * 200)
check("a very long line is trimmed rather than filling the window",
      len(win.panel.caption.text()) <= 240, True)
check("  ...and says it was", win.panel.caption.text().endswith("..."), True)

win.panel.say("[happy] That worked.")
check("feeling tags are stage directions, not caption text",
      win.panel.caption.text(), "That worked.")

typed = []
win.sent.connect(typed.append)
win.panel.entry.setText("  start the server  ")
win.panel._send()
check("typing in the panel sends it", typed, ["start the server"])
check("  ...and clears the box", win.panel.entry.text(), "")
win.panel.entry.setText("   ")
win.panel._send()
check("whitespace sends nothing", typed, ["start the server"])

stopped = []
win.panel.interrupted.connect(lambda: stopped.append(1))
win.panel.hush.click()
check("there is a way to shut it up", stopped, [1])
flips = []
win.panel.mic_toggled.connect(flips.append)
win.panel.mic.setChecked(True)
check("and a mic that reports itself", flips, [True])
win.panel.set_state("thinking")
check("the state is one word", win.panel.state.text(), "thinking")

print("\n-- dragging moves the window, typing does not --")
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtCore import QPointF, QEvent
win.resize(300, 600)
win.show()
for _ in range(8): app.processEvents()
def press(at):
    win._drag = None
    win.mousePressEvent(QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(*at), QPointF(*at),
        ga.Qt.MouseButton.LeftButton, ga.Qt.MouseButton.LeftButton,
        ga.Qt.KeyboardModifier.NoModifier))
    return win._drag is not None
press_face = press((150, 50))
press_panel = press((150, win.panel.geometry().center().y()))
check("pressing the face starts a drag", press_face, True)
check("pressing the panel does not", press_panel, False)

print("\n-- feelings a line asks for --")
check("a tag is found", ga.emotion_face("[angry] no it did not"), "angry")
check("the last one wins, since a line can turn",
      ga.emotion_face("[angry] no - [happy] oh, actually yes"), "happy")
check("words map onto what a model can do", ga.emotion_face("[sigh] fine"), "sad")
check("an unknown one is not invented", ga.emotion_face("[bemused] hm"), None)
check("no tag is no feeling", ga.emotion_face("just a sentence"), None)
check("the tag never reaches the ear", "[happy]" in ga.speakable("[happy] done"), False)
check("what it asked for beats the mood it was in",
      ga.expression_weights("idle", 0.0, mood=-0.9, feeling="happy").get("happy"), 0.8)
check("  ...and without one, the mood still shows",
      "angry" in ga.expression_weights("idle", 0.0, mood=-0.9), True)

print("\n-- clicks passing through --")
# The platform's own click-through, so the compositor routes the click to whatever is
# behind rather than to a window that then ignores it. There is no masking a hole in
# the middle: a mask clips what is drawn as well as what is clicked, so masking to
# the panel would leave a panel and no avatar.
ga.save_settings(dict(ga.DEFAULTS))
solid = ga.AvatarWindow()
check("off by default", solid.clicks_pass_through(), False)
check("  ...so the panel works", solid.panel.isEnabled(), True)

ga.save_settings({**ga.DEFAULTS, "avatar_click_through": True})
ghost = ga.AvatarWindow()
check("turned on, the window is transparent to input", ghost.clicks_pass_through(), True)
check("  ...and it says so by disabling the panel", ghost.panel.isEnabled(), False)
check("it is still frameless and on top",
      bool(ghost.windowFlags() & ga.Qt.WindowType.FramelessWindowHint)
      and bool(ghost.windowFlags() & ga.Qt.WindowType.WindowStaysOnTopHint), True)

print("\n-- and it can be changed without reopening --")
ga.save_settings(dict(ga.DEFAULTS))
ghost.apply_click_through()
check("turning it off restores the panel", ghost.panel.isEnabled(), True)
check("  ...and the flag", ghost.clicks_pass_through(), False)
before = ghost.windowFlags()
ghost.apply_click_through()
check("applying the same setting twice changes nothing",
      ghost.windowFlags(), before)
ga.save_settings(dict(ga.DEFAULTS))

print("\n-- the panel does not cost the full body --")
window = ga.AvatarWindow()
window.resize(300, 380)
window.show()
for _ in range(8): app.processEvents()
face = window.model if window.model is not None else window.avatar
check("the face still gets most of a short window",
      face.height() > window.height() * 0.6, True)
check("  ...and all of the width", face.width(), window.width())
window.close()

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
