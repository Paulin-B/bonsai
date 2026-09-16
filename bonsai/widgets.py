"""The custom Qt widgets the window is built from."""

import re
from PIL import Image
from PyQt6.QtCore import (
    QPoint, QTimer, QUrl, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFontMetrics, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument,
)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSpinBox, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)
from .config import (
    DEFAULTS, DOCKER_SERVICES, _spell,
)
from .store import (
    all_skills, match_skills, settings, skill_query,
)
from .files import (
    resolve_guarded,
)
from .shell import (
    opener_argv, start_program,
)
from .media import (
    LOOK_RASTER, fit_images, render_pdf_pages,
)
from .theme import (
    THEMES, style_rendered_document,
)


WORD_RE = re.compile(r"[A-Za-z']+")


class SpellHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.format = QTextCharFormat()
        self.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
        self.format.setUnderlineColor(QColor("red"))

    def highlightBlock(self, text):
        if _spell is None:
            return
        for match in WORD_RE.finditer(text):
            word = match.group()
            if len(word) > 1 and _spell.unknown([word.lower()]):
                self.setFormat(match.start(), len(word), self.format)


class PreviewPane(QWidget):
    """The Preview tab: what a file actually looks like, beside the chat.

    The counterpart to LOOK - the model renders a thing, the same thing appears here,
    and "that chart's labels are overlapping" becomes something you can both see
    instead of something one of you has to take on trust."""

    PAGE_LIMIT = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Path to a file…")
        self.field.returnPressed.connect(self.open_typed)
        row.addWidget(self.field, stretch=1)
        show = QPushButton("Show")
        show.setObjectName("ghost")
        show.clicked.connect(self.open_typed)
        row.addWidget(show)
        layout.addLayout(row)

        self.view = QTextBrowser()
        self.view.setObjectName("previewView")
        self.view.setOpenExternalLinks(True)
        layout.addWidget(self.view, stretch=1)

        self.note = QLabel()
        self.note.setObjectName("note")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.browser = QPushButton("Open in browser")
        self.browser.setObjectName("ghost")
        self.browser.clicked.connect(self.open_externally)
        self.browser.hide()
        layout.addWidget(self.browser)
        # Re-laying out on every resize event would re-render a whole PDF per pixel
        # dragged, so settle first.
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.setInterval(150)
        self._relayout.timeout.connect(self._reshow)
        self.clear("Nothing to preview yet. Ask Bonsai to show you something, or type "
                   "a path above.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.path is not None:
            self._relayout.start()

    def _reshow(self):
        if self.path is not None:
            self.show_path(self.path)

    def clear(self, message):
        self.path = None
        self.view.setPlainText("")
        self.note.setText(message)
        self.browser.hide()

    def open_typed(self):
        typed = self.field.text().strip()
        if typed:
            self.show_path(typed)

    def open_externally(self):
        if not self.path:
            return
        argv = opener_argv(str(self.path))
        if argv:
            start_program(argv)

    def _width(self):
        """Room for content, with the vertical scrollbar's width always reserved.

        Measuring the viewport as it stands means anything laid out before the
        scrollbar appears ends up exactly one scrollbar too wide, and the panel then
        scrolls sideways as well as down. Reserving it costs a few pixels and is never
        wrong in the direction that shows."""
        reserved = self.view.verticalScrollBar().sizeHint().width()
        return max(self.view.viewport().width() - reserved - 14, 200)

    def show_path(self, target):
        path, err = resolve_guarded(str(target))
        if err:
            self.clear(err)
            return
        if not path.exists():
            self.clear(f"Nothing to preview: {path} does not exist.")
            return
        if path.is_dir():
            self.clear(f"{path} is a folder, not a file.")
            return
        self.path = path
        self.field.setText(str(path))
        suffix = path.suffix.lower()
        document = self.view.document()
        document.setBaseUrl(QUrl.fromLocalFile(f"{path.parent}/"))
        self.browser.setVisible(suffix in (".html", ".htm", ".pdf"))
        try:
            if suffix in LOOK_RASTER:
                with Image.open(path) as opened:
                    natural = opened.width, opened.height
                # The width has to go in the tag. fit_images cannot help here: it reads
                # the image resource, which QTextBrowser has not fetched at the moment
                # setHtml returns, so every picture measured zero and stayed full size.
                shown = min(natural[0], self._width())
                self.view.setHtml(
                    f'<img src="{QUrl.fromLocalFile(str(path)).toString()}" '
                    f'width="{shown}">')
                self.note.setText(f"{path.name} — image, {natural[0]}x{natural[1]}")
            elif suffix == ".pdf":
                pages, count = render_pdf_pages(path, self.PAGE_LIMIT, self._width() * 2)
                blocks = []
                for index, page in enumerate(pages):
                    document.addResource(QTextDocument.ResourceType.ImageResource,
                                         QUrl(f"page{index}"), page)
                    blocks.append(f'<img src="page{index}" width="{self._width()}">')
                self.view.setHtml("<br><br>".join(blocks))
                extra = f" (first {len(pages)} of {count})" if count > len(pages) else ""
                self.note.setText(f"{path.name} — {count} page PDF{extra}")
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text) > 400_000:
                    text = text[:400_000] + "\n...[truncated]"
                if suffix in (".md", ".markdown"):
                    document.setMarkdown(
                        text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
                    self.note.setText(f"{path.name} — markdown")
                elif suffix in (".html", ".htm"):
                    document.setHtml(text)
                    self.note.setText(
                        f"{path.name} — static HTML. Scripts and modern CSS do not run "
                        "in this view, so open it in a browser to see the real thing.")
                else:
                    document.setPlainText(text)
                    self.note.setText(f"{path.name} — {len(text):,} characters")
                fit_images(document, self._width(), 20000)
            style_rendered_document(document, THEMES["dark" if settings().get(
                "dark_mode", True) else "light"])
        except Exception as exc:
            self.clear(f"Could not preview {path.name}: {exc}")


class AttachmentBar(QWidget):
    """The row of files waiting to go out with the next message.

    Attaching used to be a one-way door: the only record of a dropped file was a line
    in the transcript, so a wrong file left you sending it anyway or throwing the
    message away. Anything queued has to be visible before it can be taken back."""

    removed = pyqtSignal(int)     # index to drop, or -1 for all of them

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("attachBar")
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(2, 0, 2, 0)
        self.row.setSpacing(6)
        self.hide()

    def _chip(self, index, attachment):
        chip = QFrame()
        chip.setObjectName("attachChip")
        inner = QHBoxLayout(chip)
        inner.setContentsMargins(9, 3, 4, 3)
        inner.setSpacing(6)
        name = QLabel(attachment["name"])
        name.setObjectName("attachName")
        metrics = QFontMetrics(name.font())
        name.setText(metrics.elidedText(attachment["name"], Qt.TextElideMode.ElideMiddle, 150))
        inner.addWidget(name)
        drop = QPushButton("×")
        drop.setObjectName("attachDrop")
        drop.setFixedSize(18, 18)
        drop.setCursor(Qt.CursorShape.PointingHandCursor)
        drop.setToolTip(f"Remove {attachment['name']}")
        drop.clicked.connect(lambda _checked=False, i=index: self.removed.emit(i))
        inner.addWidget(drop)
        chip.setToolTip(f"{attachment['path']}\n{attachment['chars']:,} characters")
        return chip

    def show_files(self, attachments):
        while self.row.count():
            taken = self.row.takeAt(0).widget()
            if taken is not None:
                taken.setParent(None)
                taken.deleteLater()
        for index, attachment in enumerate(attachments):
            self.row.addWidget(self._chip(index, attachment))
        if len(attachments) > 1:
            clear = QPushButton("Clear all")
            clear.setObjectName("ghost")
            clear.clicked.connect(lambda: self.removed.emit(-1))
            self.row.addWidget(clear)
        self.row.addStretch(1)
        self.setVisible(bool(attachments))


class SkillDrawer(QFrame):
    """The list that rises out of the composer when you type '/'.

    A child of the window rather than a Qt popup: a popup grabs the keyboard, and the
    whole point is that you keep typing into the input to narrow the list down."""

    chosen = pyqtSignal(str)
    VISIBLE_ROWS = 6

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("skillDrawer")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)
        self.heading = QLabel("Skills")
        self.heading.setObjectName("sectionLabel")
        layout.addWidget(self.heading)
        self.list = QListWidget()
        self.list.setObjectName("skillList")
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemClicked.connect(self._clicked)
        layout.addWidget(self.list)
        self.hide()

    def _clicked(self, item):
        self.chosen.emit(item.data(Qt.ItemDataRole.UserRole))

    def show_matches(self, matches, query=""):
        """Fill the drawer; returns whether there is anything to show."""
        self.list.clear()
        for name, description in matches[:40]:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            summary = " ".join(str(description or "").split())
            item.setText(f"/{name}\n{summary[:90]}" if summary else f"/{name}")
            self.list.addItem(item)
        if not matches:
            # Saying nothing matched beats vanishing: the drawer disappearing looks
            # like the feature is broken rather than like the name being wrong.
            item = QListWidgetItem(f"No skill matches “{query}”" if query
                                   else "No skills saved yet")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        self.list.setCurrentRow(0 if matches else -1)
        # Measured rather than assumed: a guessed row height leaves a dead strip under
        # the last skill, or clips the description off the one after it.
        rows = min(max(len(matches), 1), self.VISIBLE_ROWS)
        self.list.setFixedHeight(rows * max(self.list.sizeHintForRow(0), 20) + 6)
        self.adjustSize()
        return True

    def move_selection(self, delta):
        count = self.list.count()
        if count:
            self.list.setCurrentRow((self.list.currentRow() + delta) % count)

    def current(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def place_above(self, anchor):
        """Sit the drawer directly on top of the composer, matching its width."""
        window = self.parentWidget()
        if window is None or anchor is None:
            return
        self.setFixedWidth(anchor.width())
        self.adjustSize()
        corner = anchor.mapTo(window, QPoint(0, 0))
        self.move(corner.x(), max(0, corner.y() - self.height() - 6))
        self.raise_()


class InputBox(QTextEdit):
    """Single-line-behaving input with live spellcheck underlines. QLineEdit can't
    render per-word formatting, hence QTextEdit constrained to one line."""

    returnPressed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setAcceptRichText(False)
        self.setFixedHeight(36)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.highlighter = SpellHighlighter(self.document())
        self.drawer = None
        self.anchor = None
        self.skills_source = all_skills
        # Fires on typing and on clicking elsewhere in the text alike, so the drawer
        # tracks which word the cursor is actually in rather than just the last key.
        self.cursorPositionChanged.connect(self.refresh_drawer)

    # -- skill drawer --

    def drawer_open(self):
        return self.drawer is not None and self.drawer.isVisible()

    def refresh_drawer(self):
        if self.drawer is None:
            return
        found = skill_query(self.toPlainText(), self.textCursor().position())
        if found is None:
            self.drawer.hide()
            return
        try:
            skills = self.skills_source()
        except Exception:
            skills = {}
        query = found[1]
        self.drawer.show_matches(match_skills(query, skills), query)
        self.drawer.show()
        self.drawer.place_above(self.anchor or self.parentWidget() or self)

    def insert_skill(self, name):
        """Replace the /token under the cursor with the chosen skill name."""
        if not name:
            return False
        end = self.textCursor().position()
        found = skill_query(self.toPlainText(), end)
        if found is None:
            return False
        cursor = self.textCursor()
        cursor.setPosition(found[0])
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(f"/{name} ")
        self.setTextCursor(cursor)
        if self.drawer:
            self.drawer.hide()
        return True

    def keyPressEvent(self, event):
        key = event.key()
        if self.drawer_open():
            if key == Qt.Key.Key_Escape:
                self.drawer.hide()
                return
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.drawer.move_selection(1 if key == Qt.Key.Key_Down else -1)
                return
            # Enter completes the name rather than sending, so a half-typed skill is
            # never sent as prose by someone reaching for the obvious key.
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                if self.insert_skill(self.drawer.current()):
                    return
                self.drawer.hide()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.returnPressed.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        if self.drawer is not None:
            self.drawer.hide()
        super().focusOutEvent(event)

    def text(self):
        return self.toPlainText()

    def setText(self, value):
        self.setPlainText(value)

    def clear(self):
        self.setPlainText("")
        if self.drawer is not None:
            self.drawer.hide()

    def contextMenuEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        word = cursor.selectedText()
        menu = QMenu(self)
        if _spell and word and _spell.unknown([word.lower()]):
            for suggestion in list(_spell.candidates(word.lower()) or [])[:5]:
                action = menu.addAction(suggestion)
                action.triggered.connect(
                    lambda _, s=suggestion, c=QTextCursor(cursor): c.insertText(s))
            menu.addSeparator()
        for action in self.createStandardContextMenu().actions():
            menu.addAction(action)
        menu.exec(event.globalPos())


class SettingsDialog(QDialog):
    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(460)
        self.result_settings = None

        layout = QVBoxLayout()
        note = QLabel("These are app-side settings. The model's loaded context window is set by "
                      "-c in docker-compose.yml and needs a container restart to change.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(note)

        form = QFormLayout()
        self.fields = {}

        def line(key, label):
            widget = QLineEdit(str(current.get(key, DEFAULTS.get(key, ""))))
            form.addRow(label, widget)
            self.fields[key] = widget

        def spin(key, label, low, high, step=1):
            widget = QSpinBox()
            widget.setRange(low, high)
            widget.setSingleStep(step)
            widget.setValue(int(current.get(key, DEFAULTS.get(key, low))))
            form.addRow(label, widget)
            self.fields[key] = widget

        line("server_url", "Server URL:")
        line("searxng_url", "SearXNG URL:")
        line("compose_path", "Docker compose file:")
        line("monitor_script", "Monitor script:")
        spin("max_tool_steps", "Max tool steps per message:", 1, 40)
        spin("max_history_messages", "Conversation memory (messages):", 2, 200, 2)
        spin("max_tokens", "Max response length (tokens):", 32, 16384, 64)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.1)
        self.temperature.setValue(float(current.get("temperature", 1.0)))
        self.temperature.setToolTip("Voice: used for the reply you read.")
        form.addRow("Temperature (voice):", self.temperature)

        self.tool_temperature = QDoubleSpinBox()
        self.tool_temperature.setRange(0.0, 2.0)
        self.tool_temperature.setSingleStep(0.1)
        self.tool_temperature.setValue(float(current.get("tool_temperature", 0.5)))
        self.tool_temperature.setToolTip(
            "Used while choosing and running tools. Lower is steadier; at high values "
            "the model varies its choice of tool between identical requests.")
        form.addRow("Temperature (tools):", self.tool_temperature)

        self.top_p = QDoubleSpinBox()
        self.top_p.setRange(0.1, 1.0)
        self.top_p.setSingleStep(0.05)
        self.top_p.setValue(float(current.get("top_p", 0.8)))
        form.addRow("top_p:", self.top_p)

        self.presence_penalty = QDoubleSpinBox()
        self.presence_penalty.setRange(0.0, 2.0)
        self.presence_penalty.setSingleStep(0.1)
        self.presence_penalty.setValue(float(current.get("presence_penalty", 0.0)))
        self.presence_penalty.setToolTip(
            "Discourages reusing tokens already emitted. Keep near 0: EDIT has to "
            "reproduce existing text character for character.")
        form.addRow("Presence penalty:", self.presence_penalty)

        spin("top_k", "top_k:", 1, 200)

        spin("max_read_chars", "Max characters read from files:", 500, 200000, 500)
        spin("max_fetch_chars", "Max characters fetched from pages:", 500, 200000, 500)
        spin("max_learned_entries", "Character entries kept per category:", 5, 500)
        spin("core_memory_cap", "Core memory facts before consolidating:", 3, 100)
        line("vault_path", "Vault folder for project notes:")
        self.capture_mode = QComboBox()
        self.capture_mode.addItems(["active-monitor", "active-window", "all"])
        current_mode = current.get("capture_mode", "active-monitor")
        if current_mode in ("active-monitor", "active-window", "all"):
            self.capture_mode.setCurrentText(current_mode)
        self.capture_mode.setToolTip(
            "active-monitor: the screen you are working on.\n"
            "active-window: just the focused window - the most detail per token.\n"
            "all: every monitor at once, which loses detail on a wide desktop.")
        form.addRow("Screen capture area:", self.capture_mode)
        spin("capture_max_width", "Capture max width (px):", 640, 3840, 160)
        spin("capture_max_height", "Capture max height (px):", 480, 2160, 120)
        spin("capture_quality", "Capture JPEG quality:", 50, 100, 5)
        line("observer_url", "Observer server URL (blank = main):")
        spin("proactive_interval", "Proactive check interval (seconds):", 20, 3600, 10)
        spin("proactive_cooldown", "Quiet period after speaking (seconds):", 0, 7200, 30)
        spin("auto_max_rounds", "Auto mode: max continuation rounds:", 1, 100)
        spin("max_continuations", "Resume itself after running out (times):", 0, 20)
        spin("request_timeout", "Model request timeout (seconds):", 30, 1800, 30)

        self.service_boxes = {}
        running = current.get("services", DEFAULTS["services"])
        for index, (name, (what, required)) in enumerate(DOCKER_SERVICES.items()):
            box = QCheckBox(f"{name} \u2014 {what}")
            box.setChecked(True if required else bool(running.get(name, False)))
            box.setEnabled(not required)
            if required:
                box.setToolTip("Required - this is the model server itself.")
            form.addRow("Docker services:" if index == 0 else "", box)
            self.service_boxes[name] = box

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def save(self):
        result = {"temperature": round(self.temperature.value(), 2),
                  "tool_temperature": round(self.tool_temperature.value(), 2),
                  "top_p": round(self.top_p.value(), 2),
                  "presence_penalty": round(self.presence_penalty.value(), 2),
                  "capture_mode": self.capture_mode.currentText(),
                  "services": {name: box.isChecked()
                               for name, box in self.service_boxes.items()}}
        for key, widget in self.fields.items():
            result[key] = widget.text().strip() if isinstance(widget, QLineEdit) else widget.value()
        self.result_settings = result
        self.accept()
