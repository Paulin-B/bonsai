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
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)
from .config import (
    DEFAULTS, DOCKER_SERVICES, _spell,
)
from .store import (
    BRIEFING_ALL_KINDS, BRIEFING_RANGE_LABELS, BRIEFING_SEEN_DAYS, all_skills, endpoints_as_text, endpoints_from_text, forget_briefing_seen, match_skills, settings, skill_query,
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
    FONT_STACKS, PALETTES, THEMES, palette, style_rendered_document,
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
            style_rendered_document(document, palette(settings().get("theme")))
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
    """Settings, grouped. One flat list of forty fields meant that finding anything
    required reading all of it, and related options sat nowhere near each other."""

    PAGES = ["Model", "Appearance", "Briefing", "Screen", "Memory", "Autonomy",
             "Services"]

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(560, 560)
        self.result_settings = None
        self.current = current
        self.fields = {}
        self.labelled = {}      # key -> {shown: stored}, for dropdowns that differ

        self.tabs = QTabWidget()
        self.tabs.setObjectName("sideTabs")
        self.tabs.setDocumentMode(True)
        self.forms = {}
        for name in self.PAGES:
            inner = QWidget()
            wrap = QVBoxLayout(inner)
            wrap.setContentsMargins(4, 10, 4, 4)
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            wrap.addLayout(form)
            wrap.addStretch(1)
            # A page taller than the dialog has to scroll. Without this the form layout
            # squeezes rows past their own minimum heights and they draw on top of each
            # other, which is what a long page looked like on a laptop screen.
            page = QScrollArea()
            page.setWidget(inner)
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.Shape.NoFrame)
            page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page.viewport().setAutoFillBackground(False)
            inner.setAutoFillBackground(False)
            self.forms[name] = form
            self.tabs.addTab(page, name)

        self._build()

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

    # -- field helpers; each one registers how to read its value back --

    def _value(self, key, fallback=None):
        return self.current.get(key, DEFAULTS.get(key, fallback))

    def _add(self, page, label, widget, key=None, tip=""):
        if tip:
            widget.setToolTip(tip)
        self.forms[page].addRow(label, widget)
        if key:
            self.fields[key] = widget
        return widget

    def line(self, page, key, label, tip=""):
        return self._add(page, label, QLineEdit(str(self._value(key, ""))), key, tip)

    def spin(self, page, key, label, low, high, step=1, tip=""):
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setValue(int(self._value(key, low)))
        return self._add(page, label, widget, key, tip)

    def decimal(self, page, key, label, low, high, step=0.1, tip=""):
        widget = QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setSingleStep(step)
        widget.setValue(float(self._value(key, low)))
        return self._add(page, label, widget, key, tip)

    def choice(self, page, key, label, options, tip="", values=None):
        """A dropdown. `values` maps what is shown to what is stored, for settings
        whose stored form is not what you would want to read - "month" against "Past
        month", or "" against "Any time"."""
        widget = QComboBox()
        widget.addItems(list(options))
        stored = str(self._value(key, ""))
        if values:
            self.labelled[key] = dict(values)
            shown = next((label_ for label_, value in values.items()
                          if value == stored), None)
            if shown:
                widget.setCurrentText(shown)
        elif stored in list(options):
            widget.setCurrentText(stored)
        return self._add(page, label, widget, key, tip)

    def check(self, page, key, label, tip=""):
        widget = QCheckBox(label)
        widget.setChecked(bool(self._value(key, False)))
        return self._add(page, "", widget, key, tip)

    def _forget_seen(self):
        forget_briefing_seen()
        self.forms["Briefing"].addRow("", QLabel("Cleared - the next briefing may "
                                                 "repeat older stories."))

    def hint(self, page, text):
        label = QLabel(text)
        label.setObjectName("notesPath")
        label.setWordWrap(True)
        # A wrapped label reports the height of a single line unless the layout is told
        # to ask how tall it needs to be at the width it is given. Without this the form
        # hands it one line and the rest of the sentence is drawn over the row below.
        policy = label.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
        label.setSizePolicy(policy)
        self.forms[page].addRow(label)

    # -- the pages --

    def _build(self):
        self.hint("Model", "Any OpenAI-compatible endpoint: llama.cpp, LM Studio, "
                           "Ollama, or a remote API.")
        self.line("Model", "server_url", "Server URL:",
                  "The server in use. Picking a different one in the header changes "
                  "this for you.")
        self.line("Model", "model", "Model name:",
                  "Sent with each request. Blank uses whatever the server has loaded - "
                  "llama.cpp serves one model and ignores this; LM Studio and Ollama "
                  "will load the one you name.")
        self.hint("Model", "Servers you can switch between from the header, one per "
                          "line as 'Name | url', optionally followed by a model name "
                          "and the compose file that starts it. llama.cpp serves one "
                          "model per process and ignores the model name, so a second "
                          "server is how you swap models there: another container on "
                          "another GPU, or a bigger model across both. The Docker "
                          "button uses the compose file of whichever server you are "
                          "on.")
        self.endpoints_editor = QTextEdit()
        self.endpoints_editor.setObjectName("notesEditor")
        self.endpoints_editor.setAcceptRichText(False)
        # One server per line, as the note above says: wrapping turned a three-server
        # list into nine lines and only the first one was in view.
        self.endpoints_editor.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.endpoints_editor.setFixedHeight(84)
        self.endpoints_editor.setPlainText(
            endpoints_as_text(self.current.get("endpoints") or []))
        self.forms["Model"].addRow("Servers:", self.endpoints_editor)
        self.spin("Model", "max_tokens", "Max response length (tokens):", 32, 32768, 64,
                  "Roughly 100 tokens per 10 lines of code. It has to fit in the context "
                  "window alongside the prompt, and the timeout below has to outlast it.")
        self.spin("Model", "request_timeout", "Request timeout (seconds):", 30, 3600, 30)
        self.spin("Model", "max_tool_steps", "Max tool steps per message:", 1, 40)
        self.spin("Model", "max_history_messages", "Conversation memory (messages):",
                  2, 200, 2)
        self.decimal("Model", "temperature", "Temperature (voice):", 0.0, 2.0, 0.1,
                     "Used for the reply you read.")
        self.decimal("Model", "tool_temperature", "Temperature (tools):", 0.0, 2.0, 0.1,
                     "Used while choosing and running tools. Lower is steadier; high "
                     "values make it pick different tools for identical requests.")
        self.decimal("Model", "top_p", "top_p:", 0.1, 1.0, 0.05)
        self.spin("Model", "top_k", "top_k:", 1, 200)
        self.decimal("Model", "presence_penalty", "Presence penalty:", 0.0, 2.0, 0.1,
                     "Discourages reusing tokens already emitted. Keep near 0: EDIT has "
                     "to reproduce existing text character for character.")

        self.hint("Appearance", "Applied as soon as you save.")
        self.choice("Appearance", "theme", "Theme:", list(PALETTES))
        self.choice("Appearance", "font", "Font:", list(FONT_STACKS),
                    "Each option is a stack, so a font you do not have falls back to "
                    "something sensible for your system.")
        self.spin("Appearance", "font_size", "Font size (px):", 10, 22, 1)

        self.hint("Briefing", "What's new in the things you follow, gathered before "
                              "you ask. Interests are edited on the Briefing page "
                              "itself.")
        self.check("Briefing", "briefing_enabled", "Gather a briefing")
        self.check("Briefing", "briefing_on_open", "Show it when Bonsai opens")
        self.choice("Briefing", "briefing_time_range", "Look back:",
                    list(BRIEFING_RANGE_LABELS),
                    "How far back results may come from. A narrow window on a quiet "
                    "topic returns nothing at all, which is why month is the default.",
                    values=BRIEFING_RANGE_LABELS)
        self.kind_boxes = {}
        for index, kind in enumerate(BRIEFING_ALL_KINDS):
            box = QCheckBox({"news": "News", "videos": "Videos", "images": "Images",
                             "general": "Articles and web pages"}[kind])
            box.setChecked(kind in (self._value("briefing_kinds") or []))
            self.forms["Briefing"].addRow("Include:" if index == 0 else "", box)
            self.kind_boxes[kind] = box
        self.check("Briefing", "briefing_no_repeats",
                   "Skip anything a previous briefing showed",
                   f"Stories are remembered for {BRIEFING_SEEN_DAYS} days, then may "
                   "appear again. Turn this off and every refresh re-shows whatever "
                   "is still current.")
        self.check("Briefing", "briefing_english_only", "English results only")
        self.spin("Briefing", "briefing_per_topic", "Results per interest, per kind:",
                  1, 20)
        self.spin("Briefing", "briefing_max_age_hours", "Refetch after (hours):", 1, 168)
        forget = QPushButton("Forget what has already been shown")
        forget.setObjectName("ghost")
        forget.setToolTip("Clears the record of seen stories, so the next briefing may "
                          "repeat things you have already read.")
        forget.clicked.connect(self._forget_seen)
        self.forms["Briefing"].addRow("", forget)

        self.hint("Screen", "What Bonsai sees when you attach the screen to a message, "
                            "and how often it looks on its own.")
        self.choice("Screen", "capture_mode", "Capture area:",
                    ["active-monitor", "active-window", "all"],
                    "active-monitor: the screen you are working on.\n"
                    "active-window: just the focused window - most detail per token.\n"
                    "all: every monitor, which loses detail on a wide desktop.")
        self.choice("Screen", "play_scope", "Input reaches:",
                    ["Only programs Bonsai opened", "Any window you approve",
                     "Any window, no questions"],
                    "How far PLAY's keys and clicks can go. Input is injected at the "
                    "keyboard, so it lands in whatever window has focus - the first "
                    "setting is the only one where a mistake cannot reach your editor, "
                    "browser or terminal.",
                    values={"Only programs Bonsai opened": "own",
                            "Any window you approve": "approved",
                            "Any window, no questions": "any"})
        self.spin("Screen", "capture_max_width", "Max width (px):", 640, 3840, 160)
        self.spin("Screen", "capture_max_height", "Max height (px):", 480, 2160, 120)
        self.spin("Screen", "capture_quality", "JPEG quality:", 50, 100, 5)
        self.line("Screen", "observer_url", "Observer server URL:",
                  "Blank uses the main server. Point it at a second, smaller model to "
                  "keep periodic screen checks off the main queue.")
        self.spin("Screen", "proactive_interval", "Proactive check every (seconds):",
                  20, 3600, 10)
        self.spin("Screen", "proactive_cooldown", "Quiet after speaking (seconds):",
                  0, 7200, 30)
        self.spin("Screen", "proactive_worth", "Only speak when a remark rates at least:",
                  1, 5, 1,
                  "1-5. It rates every remark it thinks of and only ones at or above "
                  "this get said. 3 lets through opinions and asides, and speaks about "
                  "as often as the cooldown allows; 4 is errors and things you would "
                  "act on, which is a few times a day; 5 is almost never.")

        self.spin("Screen", "play_max_steps", "Steps before a play run stops:", 5, 500, 5,
                  "How long Bonsai plays for after you switch Play on, in actions. It "
                  "stops on its own at this point so a loop cannot run all night.")
        self.spin("Screen", "play_step_seconds", "Pause after each play action (s):",
                  0, 30, 1,
                  "Time to let the game respond before looking again. Too short and it "
                  "photographs the screen mid-transition and thinks nothing happened.")

        self.hint("Memory", "Notes become markdown files if you point this at a vault; "
                            "leave it blank and they stay in JSON.")
        self.line("Memory", "vault_path", "Vault folder:")
        self.spin("Memory", "core_memory_cap", "Core facts before consolidating:", 3, 100)
        self.spin("Memory", "max_learned_entries", "Character entries per category:",
                  5, 500)
        self.spin("Memory", "max_read_chars", "Max characters read from files:",
                  500, 200000, 500)
        self.spin("Memory", "max_fetch_chars", "Max characters fetched from pages:",
                  500, 200000, 500)

        self.hint("Autonomy", "How far it may carry on without you.")
        self.spin("Autonomy", "auto_max_rounds", "Auto mode: max rounds:", 1, 100)
        self.spin("Autonomy", "max_continuations",
                  "Resume itself after running out (times):", 0, 20, 1,
                  "0 turns self-resumption off.")

        self.hint("Services", "Optional. Bonsai works without Docker; these only matter "
                              "if you use a compose file. A service your file does not "
                              "define is skipped rather than failing the command, so "
                              "search is happiest in a compose file of its own - it has "
                              "nothing to do with which model is loaded.")
        self.line("Services", "searxng_url", "SearXNG URL:")
        self.line("Services", "compose_path", "Docker compose file:")
        self.line("Services", "monitor_script", "Monitor script:")
        self.service_boxes = {}
        running = self.current.get("services", DEFAULTS["services"])
        for index, (name, (what, required)) in enumerate(DOCKER_SERVICES.items()):
            box = QCheckBox(f"{name} — {what}")
            box.setChecked(True if required else bool(running.get(name, False)))
            box.setEnabled(not required)
            if required:
                box.setToolTip("Required - this is the model server itself.")
            self.forms["Services"].addRow("Start on launch:" if index == 0 else "", box)
            self.service_boxes[name] = box

    def save(self):
        parsed, bad = endpoints_from_text(self.endpoints_editor.toPlainText())
        if bad:
            self.endpoints_editor.setToolTip(f"Not saved: {bad}")
            self.tabs.setCurrentIndex(self.PAGES.index("Model"))
            self.endpoints_editor.setFocus()
            return                      # refuse rather than drop a server they typed
        result = {"services": {name: box.isChecked()
                               for name, box in self.service_boxes.items()},
                  "endpoints": parsed,
                  "briefing_kinds": [kind for kind, box in self.kind_boxes.items()
                                     if box.isChecked()]}
        for key, widget in self.fields.items():
            if isinstance(widget, QLineEdit):
                result[key] = widget.text().strip()
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
            elif isinstance(widget, QComboBox):
                chosen = widget.currentText()
                result[key] = self.labelled.get(key, {}).get(chosen, chosen)
            elif isinstance(widget, QDoubleSpinBox):
                result[key] = round(widget.value(), 2)
            else:
                result[key] = widget.value()
        self.result_settings = result
        self.accept()
