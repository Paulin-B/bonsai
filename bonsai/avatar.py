"""A face for it.

A PNGTuber is a handful of pictures swapped on two signals: is it talking, and how
does it feel. That is the whole idea, and it is why this is worth doing before
anything involving a 3D runtime - both signals already exist here. The speaker emits
the loudness of what it is saying twenty times a second, and the mood is a number on
disk that survives the session.

It lives in its own frameless window rather than in the layout, because that is what
a PNGTuber is for: something to put in a corner and point OBS at. It also means the
main window is untouched by it.

With no art it draws itself - a potted tree with a face, which is on the nose but
means the feature works the moment it is switched on. Drop PNGs in the avatar folder
and they are used instead.
"""
import json
import os
import random
from pathlib import Path

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget,
)

from .store import load_mood, save_settings, settings

AVATAR_DIR = Path.home() / ".local/share/bonsai_avatar"

# What a folder of art may provide. Anything missing falls back to the drawing, so a
# folder with one file in it is a perfectly good start.
STATES = ("idle", "talk", "think")


def art_for(state, mood_name, openness):
    """The picture for this moment, or None to draw it.

    Looked up from most specific to least: a mouth shape for this mood, a mouth shape,
    this state in this mood, this state. Whoever is drawing can provide as much or as
    little of that as they like."""
    folder = Path((settings().get("avatar_folder") or "").strip() or AVATAR_DIR)
    if not folder.is_dir():
        return None
    mouth = min(3, int(openness * 4)) if state == "talk" else 0
    names = []
    if state == "talk":
        names += [f"talk-{mouth}-{mood_name}.png", f"talk-{mouth}.png"]
    names += [f"{state}-{mood_name}.png", f"{state}.png", "idle.png"]
    for name in names:
        candidate = folder / name.replace(" ", "-")
        if candidate.is_file():
            return str(candidate)
    return None


class Avatar(QWidget):
    """Draws the face. Knows nothing about where the numbers come from."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.openness = 0.0
        self.mood_value, self.mood_name = 0.0, "steady"
        self.blink = 0.0
        self.think_tick = 0
        self._cache = {}
        self.setMinimumSize(160, 200)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        # Blinking is the cheapest thing that makes a static drawing look alive, and
        # it costs one timer.
        self.blinker = QTimer(self)
        self.blinker.timeout.connect(self._blink)
        self.blinker.start(200)
        self._until_blink = 12

    def _blink(self):
        if self.state == "think":
            self.think_tick += 1
        if self.blink > 0:
            self.blink = max(0.0, self.blink - 0.5)
        else:
            self._until_blink -= 1
            if self._until_blink <= 0:
                self.blink = 1.0
                self._until_blink = random.randint(10, 30)
        self.update()

    def set_state(self, state):
        if state != self.state:
            self.state = state
            self.update()

    def set_level(self, level):
        # Smoothed towards the new value: at twenty frames a second the raw envelope
        # chatters, and a mouth that chatters reads as a glitch rather than as speech.
        self.openness += (max(0.0, min(1.0, level)) - self.openness) * 0.6
        self.update()

    def refresh_mood(self):
        self.mood_value, self.mood_name, _ = load_mood()
        self.update()

    # -- drawing -------------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        picture = art_for(self.state, self.mood_name, self.openness)
        if picture:
            pixmap = self._cache.get(picture)
            if pixmap is None:
                pixmap = QPixmap(picture)
                self._cache[picture] = pixmap
            if not pixmap.isNull():
                scaled = pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                painter.drawPixmap((self.width() - scaled.width()) // 2,
                                   (self.height() - scaled.height()) // 2, scaled)
                return
        self._draw_tree(painter)

    def _draw_tree(self, painter):
        width, height = self.width(), self.height()
        unit = min(width / 160, height / 200)
        middle = width / 2

        # Mood tints the leaves: greener when it is going well, browner when it is not.
        leaf = QColor("#4c9f68") if self.mood_value >= 0 else QColor("#8a7f4a")
        leaf = leaf.lighter(100 + int(abs(self.mood_value) * 18))

        pot = QPainterPath()
        pot.moveTo(middle - 34 * unit, height - 16 * unit)
        pot.lineTo(middle + 34 * unit, height - 16 * unit)
        pot.lineTo(middle + 26 * unit, height - 2 * unit)
        pot.lineTo(middle - 26 * unit, height - 2 * unit)
        pot.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor("#6b4630")))
        painter.drawPath(pot)
        painter.setBrush(QBrush(QColor("#7d523a")))
        painter.drawRect(QRectF(middle - 38 * unit, height - 22 * unit,
                                76 * unit, 8 * unit))

        painter.setBrush(QBrush(QColor("#5a4632")))
        painter.drawRect(QRectF(middle - 5 * unit, height - 74 * unit,
                                10 * unit, 54 * unit))

        painter.setBrush(QBrush(leaf))
        for dx, dy, r in ((-26, -92, 26), (24, -96, 24), (0, -112, 30), (-4, -78, 24)):
            painter.drawEllipse(QRectF(middle + (dx - r) * unit,
                                       height + (dy - r) * unit,
                                       r * 2 * unit, r * 2 * unit))

        eye_y = height - 104 * unit
        open_eye = 1.0 - self.blink
        if self.state == "think":
            eye_y -= 3 * unit          # looking up, which is what thinking looks like
        for side in (-1, 1):
            x = middle + side * 13 * unit
            painter.setBrush(QBrush(QColor("#f4f6f2")))
            painter.drawEllipse(QRectF(x - 9 * unit, eye_y - 9 * unit * open_eye,
                                       18 * unit, 18 * unit * max(0.08, open_eye)))
            if open_eye > 0.3:
                painter.setBrush(QBrush(QColor("#22302a")))
                look = -2 * unit if self.state == "think" else 0
                painter.drawEllipse(QRectF(x - 4 * unit, eye_y - 4 * unit + look,
                                           8 * unit, 8 * unit))

        # Brows carry the mood, because eyes alone cannot: the same eyes read as
        # pleased or fed up depending only on what is above them.
        painter.setPen(QPen(QColor("#22302a"), 2.2 * unit, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        tilt = -3 * unit if self.mood_value >= 0.2 else (3 * unit
                                                        if self.mood_value <= -0.2 else 0)
        for side in (-1, 1):
            x = middle + side * 13 * unit
            painter.drawLine(QPoint(int(x - 8 * unit), int(eye_y - 15 * unit + side * tilt)),
                             QPoint(int(x + 8 * unit), int(eye_y - 15 * unit - side * tilt)))

        if self.state == "think":
            # Eyes looking up is too subtle on its own - side by side with idle you
            # cannot tell them apart. Dots that fill in say it plainly.
            painter.setPen(Qt.PenStyle.NoPen)
            for index in range(3):
                lit = (self.think_tick // 4) % 3 >= index
                painter.setBrush(QBrush(QColor("#cfd8d2") if lit else QColor("#6d7a72")))
                size = (6 if lit else 4) * unit
                painter.drawEllipse(QRectF(middle + (14 + index * 11) * unit - size / 2,
                                           height - 150 * unit - size / 2, size, size))

        mouth_y = height - 86 * unit
        painter.setBrush(QBrush(QColor("#22302a")))
        painter.setPen(Qt.PenStyle.NoPen)
        if self.state == "talk" and self.openness > 0.05:
            tall = 3 * unit + self.openness * 13 * unit
            painter.drawEllipse(QRectF(middle - 7 * unit, mouth_y - tall / 2,
                                       14 * unit, tall))
        else:
            painter.setPen(QPen(QColor("#22302a"), 2.2 * unit, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            curve = QPainterPath()
            curve.moveTo(middle - 8 * unit, mouth_y)
            bend = 5 * unit if self.mood_value >= 0 else -5 * unit
            curve.quadTo(middle, mouth_y + bend, middle + 8 * unit, mouth_y)
            painter.drawPath(curve)


AVATAR_TITLE = "Bonsai Avatar"


def hypr_geometry():
    """Where the compositor actually has this window, or None.

    Under Wayland a client is not told where it is and cannot move itself, so Qt's
    x() and y() are its own fiction - it reported 60,60 for a window Hyprland had at
    3946,37. The compositor is the only one who knows."""
    import shutil
    import subprocess
    if not shutil.which("hyprctl"):
        return None
    try:
        out = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True,
                             text=True, timeout=4).stdout
        mine = os.getpid()
        for client in json.loads(out or "[]"):
            # By pid, not by title. A second Bonsai, or a window left over from a
            # crash, has the same title - and taking the first match meant a window
            # adopted the size and position of somebody else's.
            if client.get("title") == AVATAR_TITLE and client.get("pid") == mine:
                return (*client["at"], *client["size"])
    except Exception:
        return None
    return None


def float_it(width, height, x=None, y=None):
    """Ask Hyprland to leave this window alone.

    Frameless and always-on-top mean nothing to a tiling compositor: Hyprland took
    the 200x250 window, tiled it, and stretched it to 1163x1394. Qt asking for its
    size back is ignored once that has happened, so the compositor has to be told -
    float, then size exactly, then pin so it follows you between workspaces.

    Only Hyprland is handled here. Everywhere else the Qt flags are enough, and a
    missing hyprctl is simply not a tiling compositor's problem."""
    import shutil
    import subprocess
    if not shutil.which("hyprctl"):
        return False
    # Hyprland selects by title here, which is as specific as its selectors get for
    # a window this process just opened; the pid check above is what keeps the
    # geometry we read back our own.
    target = f'window="title:{AVATAR_TITLE}"'
    calls = [
        # float is a toggle, so it is only ever sent to a freshly opened window.
        f"hl.dsp.window.float{{{target}}}",
        f"hl.dsp.window.resize{{{target}, x={int(width)}, y={int(height)}, exact=true}}",
        f"hl.dsp.window.pin{{{target}}}",
    ]
    if x is not None and y is not None:
        calls.append(f"hl.dsp.window.move{{{target}, x={int(x)}, y={int(y)}}}")
    for call in calls:
        try:
            subprocess.run(["hyprctl", "dispatch", call], capture_output=True,
                           text=True, timeout=4)
        except Exception:
            return False
    return True


class Panel(QFrame):
    """The strip under the avatar: what it just said, what it is doing, and a way to
    answer without going back to the main window.

    Modelled on how Open-LLM-VTuber presents one of these, because the arrangement is
    right: the line it just said is the thing you look at, the state is one word, and
    the controls you actually reach for while it is talking are stop and the mic."""

    sent = pyqtSignal(str)
    interrupted = pyqtSignal()
    mic_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("avatarPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        self.caption = QLabel("")
        self.caption.setObjectName("avatarCaption")
        self.caption.setWordWrap(True)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.caption.hide()          # nothing said yet is nothing to show
        layout.addWidget(self.caption)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.state = QLabel("idle")
        self.state.setObjectName("avatarState")
        row.addWidget(self.state)
        row.addStretch(1)

        self.mic = QToolButton()
        self.mic.setObjectName("avatarControl")
        self.mic.setCheckable(True)
        self.mic.setText("\U0001F3A4")
        self.mic.setToolTip("Listen")
        self.mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic.toggled.connect(self.mic_toggled)
        row.addWidget(self.mic)

        self.hush = QToolButton()
        self.hush.setObjectName("avatarControl")
        self.hush.setText("\u270B")
        self.hush.setToolTip("Stop talking")
        self.hush.setCursor(Qt.CursorShape.PointingHandCursor)
        self.hush.clicked.connect(self.interrupted)
        row.addWidget(self.hush)
        layout.addLayout(row)

        self.entry = QLineEdit()
        self.entry.setObjectName("avatarEntry")
        self.entry.setPlaceholderText("Type your message...")
        self.entry.returnPressed.connect(self._send)
        layout.addWidget(self.entry)

    def _send(self):
        said = self.entry.text().strip()
        if said:
            self.entry.clear()
            self.sent.emit(said)

    def say(self, text):
        """Show a line, trimmed to something readable at this size.

        The feeling tags come out here as well as out of the speech: they are stage
        directions for the face, and leaving them in the caption is showing someone
        the markup."""
        from .vrm import take_emotions
        text, _ = take_emotions(text or "")
        text = " ".join(text.split())
        if not text:
            self.caption.hide()
            return
        self.caption.setText(text if len(text) <= 240 else text[:237] + "...")
        self.caption.show()

    def set_state(self, word):
        self.state.setText(word)


class AvatarWindow(QWidget):
    """A small frameless window to put in a corner and point OBS at.

    Transparent, always on top, and dragged by its face since it has no title bar."""

    sent = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowTitle(AVATAR_TITLE)
        self.setWindowFlags(self.wanted_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # A layout rather than a resizeEvent: resizing a window that has not been
        # shown does not deliver one, and the face stayed at its minimum size.
        self.avatar = Avatar(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # A VRM when one is set up, the drawing otherwise. Built here rather than
        # imported at the top, so a machine with no web engine never loads one.
        from .vrmview import VrmView
        self.model = VrmView.build(self)
        face = self.model if self.model is not None else self.avatar
        layout.addWidget(face, stretch=1)
        if self.model is not None:
            self.avatar.hide()

        self.panel = Panel(self)
        self.panel.sent.connect(self.sent)
        self.panel.setEnabled(not self.clicks_pass_through())
        layout.addWidget(self.panel)
        config = settings()
        # The size that is wanted, kept separately from the size it currently has:
        # by the time the compositor can be told anything, it has already tiled the
        # window, and asking it to resize to its current size achieves nothing.
        self.wanted = (max(100, int(config.get("avatar_width", 200))),
                       max(100, int(config.get("avatar_height", 250))))
        self.placed = (config.get("avatar_x", -1), config.get("avatar_y", -1))
        self.resize(*self.wanted)
        self._drag = None

    @staticmethod
    def wanted_flags():
        """Frameless, on top, and out of the way of the mouse when asked.

        WindowTransparentForInput is the platform's own idea of click-through - on
        Wayland it becomes an empty input region, so the compositor routes clicks to
        whatever is behind rather than to a window that then has to ignore them.

        It applies to the whole window, panel included. There is no masking a hole in
        the middle: a mask clips what is drawn as well as what is clicked, so masking
        to the panel would leave a panel and no avatar. So this is a mode - look at
        it, do not touch it - and the way out of it is the toggle that opened it."""
        flags = (Qt.WindowType.FramelessWindowHint
                 | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool)
        if settings().get("avatar_click_through", False):
            flags |= Qt.WindowType.WindowTransparentForInput
        return flags

    def clicks_pass_through(self):
        return bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    def apply_click_through(self):
        """Re-apply the setting to a window that is already open.

        Changing flags un-maps and re-maps the window, which loses the floating and
        pinned state Hyprland was told about - so it is told again."""
        wanted = self.wanted_flags()
        if wanted == self.windowFlags():
            return
        visible = self.isVisible()
        self.setWindowFlags(wanted)
        self.panel.setEnabled(not self.clicks_pass_through())
        if visible:
            self.show()
            QTimer.singleShot(150, self.claim_space)

    def showEvent(self, event):
        super().showEvent(event)
        # After it is mapped, or the compositor has no window to be told about yet.
        QTimer.singleShot(150, self.claim_space)

    def claim_space(self):
        x, y = self.placed
        float_it(*self.wanted, x=x if x >= 0 else None, y=y if y >= 0 else None)

    def remember(self):
        """Where you put it, so it opens there next time.

        Asked of the compositor when there is one, because the window does not know
        where it is and would otherwise save a position it invented."""
        where = hypr_geometry()
        x, y, width, height = where if where else (self.x(), self.y(),
                                                   self.width(), self.height())
        self.wanted = (width, height)
        self.placed = (x, y)
        saved = settings()
        saved.update({"avatar_x": int(x), "avatar_y": int(y),
                      "avatar_width": int(width), "avatar_height": int(height)})
        save_settings(saved)

    def closeEvent(self, event):
        self.remember()
        super().closeEvent(event)

    def show_face(self, state, level, blink=0.0, tick=0, feeling=None):
        """Whatever is drawing the face, tell it what the face is doing."""
        if self.model is None:
            return False
        from .vrm import expression_weights
        from .vrmview import VrmView
        VrmView.apply(self.model,
                      expression_weights(state, level, blink, tick, feeling=feeling))
        return True

    def mousePressEvent(self, event):
        # Only from the face. Dragging from the panel would move the window every
        # time you went to click in the text box.
        if (event.button() == Qt.MouseButton.LeftButton
                and not self.panel.geometry().contains(event.position().toPoint())):
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event):
        if self._drag is not None:
            self.remember()
        self._drag = None

    def wheelEvent(self, event):
        """Scroll on the face to resize it - there is no frame to drag."""
        step = 20 if event.angleDelta().y() > 0 else -20
        width = max(100, min(900, self.wanted[0] + step))
        self.wanted = (width, int(width * 1.25))
        self.resize(*self.wanted)
        float_it(*self.wanted)
        self.remember()
