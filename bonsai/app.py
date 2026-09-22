"""The main window, and the entry point."""

import html
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests
from PyQt6.QtCore import (
    QProcess, QTimer, QUrl, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QDesktopServices, QFont, QFontMetrics, QKeySequence, QShortcut, QTextCursor, QTextDocument,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTabWidget, QTextBrowser, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)
from .config import (
    APP_VERSION, DEFAULTS, DOCKER_SERVICES, MEMORY_FILE, PROTECTED_PATHS, SKILLS_FILE,
)
from .store import (
    available_models, briefing_is_stale, compose_for, compose_services, endpoints, expand_skill_shortcut, load_briefing, load_character, load_interests, load_memory, load_mood, load_skills, load_trusted, looks_english, memory_to_text, project_note_path, project_summary, read_project_note, save_interests, save_json, save_project_note, save_settings, save_trusted, search_backend_up, settings, suggested_interests, text_to_memory,
)
from .text import (
    ACTIONS_ECHO_RE, CLAIMED_ACTION_RE, CLAIMED_TASK_RE, DENIES_TOOL_RE, RECORD_HEADER, TASK_MUTATION_RE, invented_files, invented_recall, is_record_echo, narrate_trace, plain_text, same_as_last_time, unsearched_memory, unverified_files,
)
from .files import (
    read_file,
)
from .turns import (
    handle_task, handoff_next, load_tasks, pattern_ready, record_pattern, save_tasks, skills_as_text, skills_from_text,
)
from .chats import (
    delete_chat, fork_chat, load_chat, load_chat_index, new_chat, save_chat, title_chat, touch_chat,
)
from .shell import (
    stop_all_background,
)
from .stage import (
    handle_stage, stop_stage,
)
from .neuro import (
    NeuroServer,
)
from .speech import (
    Speaker, available_engine, speakable, why_silent,
)
from .listen import (
    Listener, why_deaf,
)
from .hotkey import (
    PushToTalk,
)
from .worker import (
    BriefingWorker, ConsolidationWorker, ObserverWorker, PlayWorker, SkillLearner,
    Worker,
)
from .theme import (
    DEFAULT_FONT, DEFAULT_THEME, NEUTRAL_DARK, NEUTRAL_LIGHT, NOTE_COLOURS, PALETTES,
    THEMES, is_dark, markdown_css, mood_colour, palette, style_rendered_document, stylesheet,
)
from .widgets import (
    AttachmentBar, InputBox, PreviewPane, SettingsDialog, SkillDrawer,
)


#!/usr/bin/env python3
"""Bonsai - a local vision/tool assistant front-end for a llama.cpp server.

Storage (all JSON, all under ~/.local/share/, all safe to hand-edit):
    bonsai_settings.json       app settings
    bonsai_character.json      who Bonsai is; edit core_traits to change personality
    bonsai_memory.json         facts about the user
    bonsai_skills.json         saved reusable procedures
    bonsai_trusted_paths.json  folders where file ops skip the permission prompt
    bonsai_projects.json       per-project file ledger and notes, kept across chats
    bonsai_chats/              one file per conversation
    bonsai_debug.log           raw model responses, for debugging tag parsing
"""


class BodyLabel(QLabel):
    """Message text that reports the height it actually needs.

    A word-wrapped QLabel reports the height its text would take laid out on a single
    line, and heightForWidth does not survive the trip up through the bubble frames.
    Every message then claims more room than it uses, which pushes the real content off
    the top of the transcript and leaves a dead gap under the last line. Pinning the
    minimum height once the label knows its true width fixes it at the source."""

    def __init__(self, text, rich=True):
        super().__init__(text)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        needed = self.heightForWidth(self.width())
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)


def _body_label(text, rich=True):
    return BodyLabel(text, rich)


BONSAI_SHAPES = [
    r"""
     &&&&&&
   &&&&&&&&&&
      \\|//
  &&&&  |
 &&&&&&&|
        \|
    \___|___/
""",
    r"""
    &&&&&
  &&&&&&&&&    &&&
     \\|//   &&&&&&
       |    &&&&&/
       |\\_____//
       |
   \___|____/
""",
    r"""
      &&&&&&&
   &&&&&&&&&&&&
   &&&  \|/  &&
       \\|
         |//
         |
     \___|___/
""",
]


BONSAI_FONT_PX = 9


def bonsai_cells(art):
    """A tree as cells ordered from the roots up and outward from the trunk, so
    revealing them one at a time reads as growth rather than a wipe."""
    rows = art.strip("\n").split("\n")
    width = max(len(row) for row in rows)
    trunk = width // 2
    cells = [(r, c, char)
             for r, row in enumerate(rows)
             for c, char in enumerate(row) if char != " "]
    cells.sort(key=lambda cell: (-cell[0], abs(cell[1] - trunk)))
    return cells, width, len(rows)


class BonsaiGrowth(QLabel):
    """A small tree that grows while Bonsai is working.

    Purely an activity signal - it says something is happening, not how far along it
    is, since the model gives no progress to report until tokens start arriving."""

    def __init__(self):
        super().__init__()
        self.setObjectName("bonsaiGrowth")
        font = QFont("DejaVu Sans Mono")
        font.setPixelSize(BONSAI_FONT_PX)      # matches the style sheet rule
        self.setFont(font)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.setFixedWidth(130)
        # Sized for the tallest shape, so a growing tree never clips its own roots.
        tallest = max(len(shape.strip("\n").split("\n")) for shape in BONSAI_SHAPES)
        self.setFixedHeight(QFontMetrics(font).lineSpacing() * tallest + 4)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.cells, self.width_cells, self.height_cells = bonsai_cells(BONSAI_SHAPES[0])
        self.shown = len(self.cells)
        self.repaint_tree()
        self.show_mood()

    def show_mood(self):
        """Wear the mood. Read from disk rather than passed in, because the mood
        outlives the window and is the same one the prompt was built from."""
        value, name, why = load_mood()
        self.setStyleSheet(f"#bonsaiGrowth {{ color: {mood_colour(value)}; "
                           "background: transparent; }")
        self.setToolTip(f"{name.capitalize()}{f' - {why}' if why else ''}")

    def repaint_tree(self):
        grid = [[" "] * self.width_cells for _ in range(self.height_cells)]
        for row, col, char in self.cells[:self.shown]:
            grid[row][col] = char
        self.setText("\n".join("".join(line).rstrip() for line in grid))

    def start(self):
        self.cells, self.width_cells, self.height_cells = bonsai_cells(
            random.choice(BONSAI_SHAPES))
        self.shown = 0
        self.repaint_tree()
        self.timer.start(70)

    def advance(self):
        if self.shown < len(self.cells):
            self.shown += 1
            self.repaint_tree()
        else:
            self.start()        # a fresh sapling, so the motion continues while busy

    def stop(self):
        self.timer.stop()
        self.shown = len(self.cells)
        self.repaint_tree()
        # The turn that just ended is what moved the mood, so this is the moment it
        # is worth looking at the tree again.
        self.show_mood()


class MarkdownView(QTextBrowser):
    """A reply rendered as markdown rather than shown as its source.

    The model writes markdown - headings, tables, fenced code - and it was being pasted
    in with newlines swapped for <br>, so readers saw literal ** and | characters. Qt's
    own document renders CommonMark with GitHub tables, so no parser dependency is
    needed. The work here is sizing to content: this lives inside a scrolling column
    and must not scroll independently."""

    def __init__(self, text, palette):
        super().__init__()
        self.setObjectName("markdownView")
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.palette_colours = palette
        document = self.document()
        document.setDefaultStyleSheet(markdown_css(palette))
        document.setDocumentMargin(0)
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        style_rendered_document(document, palette)
        document.contentsChanged.connect(self.fit_to_content)
        self.fit_to_content()

    def set_markdown(self, text):
        document = self.document()
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        style_rendered_document(document, self.palette_colours)
        self.fit_to_content()

    def fit_to_content(self):
        document = self.document()
        document.setTextWidth(max(self.viewport().width(), 1))
        self.setFixedHeight(int(document.size().height()) + 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_to_content()


class UserMessage(QWidget):
    """One thing you said, with the option to say it differently.

    Editing forks rather than overwrites. The reply the original wording got is
    usually the thing being compared against, and a conversation that quietly rewrites
    its own history cannot be checked against what actually happened - which is the
    one thing this app is careful about everywhere else."""

    edited = pyqtSignal(int, str)
    branch = pyqtSignal(int, str)

    def __init__(self, text, index=None, retry=False, parent=None):
        super().__init__(parent)
        self.said = text
        self.index = index
        self.editor = None
        self.actions = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.addSpacing(52)              # inset, so it reads as the other speaker
        self.bubble = QFrame()
        self.bubble.setObjectName("userBubble")
        self.inner = QVBoxLayout(self.bubble)
        self.inner.setContentsMargins(15, 11, 15, 11)
        self.inner.setSpacing(3)
        if retry:
            tag = QLabel("RETRY")
            tag.setObjectName("speaker")
            self.inner.addWidget(tag)
        self.body = _body_label(text)
        self.inner.addWidget(self.body)
        row.addWidget(self.bubble, stretch=1)

        self.tools = []
        for name, glyph, tip, slot in (
            ("editButton", "\u270e",
             "Change what you said and ask again from here. The conversation as it "
             "stands is kept in the sidebar.", self.begin_edit),
            ("branchButton", "\u2387",
             "Branch the conversation here - everything after this message is left "
             "behind in a copy you can go back to", self._branch),
        ):
            button = QPushButton(glyph)
            button.setObjectName(name)
            button.setFixedSize(22, 22)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            button.setVisible(index is not None)
            row.addWidget(button, alignment=Qt.AlignmentFlag.AlignTop)
            self.tools.append(button)

    def _branch(self):
        self.branch.emit(self.index, self.said)

    def begin_edit(self):
        """Turn the bubble into a box you can type in."""
        if self.editor is not None or self.index is None:
            return
        self.body.hide()
        for button in self.tools:
            button.hide()
        self.editor = QTextEdit()
        self.editor.setObjectName("messageEditor")
        self.editor.setAcceptRichText(False)
        # What is stored carries <br> where the newlines were, because the transcript
        # renders as rich text. Put them back so it is edited as it was typed.
        self.editor.setPlainText(self.said.replace("<br>", "\n"))
        self.editor.setFixedHeight(96)
        self.inner.addWidget(self.editor)

        self.actions = QWidget()
        buttons = QHBoxLayout(self.actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.end_edit)
        buttons.addWidget(cancel)
        send = QPushButton("Ask again")
        send.setObjectName("primary")
        send.clicked.connect(self._commit)
        buttons.addWidget(send)
        self.inner.addWidget(self.actions)
        self.editor.setFocus()
        self.editor.moveCursor(QTextCursor.MoveOperation.End)

    def end_edit(self):
        if self.editor is None:
            return
        for widget in (self.editor, self.actions):
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.editor = self.actions = None
        self.body.show()
        for button in self.tools:
            button.show()

    def _commit(self):
        reworded = self.editor.toPlainText().strip() if self.editor else ""
        index = self.index
        self.end_edit()
        if reworded and reworded != self.said.replace("<br>", "\n").strip():
            self.edited.emit(index, reworded)


class MessageList(QScrollArea):
    """The transcript, as a column of per-message widgets rather than one rich-text
    document. Qt's rich text has no border-radius, so rounded bubbles have to be real
    widgets - and this also keeps each message individually styleable."""

    branch_here = pyqtSignal(int, str)   # message index, what was said there
    edit_here = pyqtSignal(int, str)     # message index, the reworded message

    def __init__(self, palette=None):
        super().__init__()
        self.palette_colours = palette or THEMES["dark"]
        self.setObjectName("messageArea")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("messageBody")
        self.column = QVBoxLayout(body)
        self.column.setContentsMargins(26, 20, 26, 20)
        self.column.setSpacing(12)
        self.column.addStretch(1)
        self.setWidget(body)

        # Following the end is a state, not an action. Scrolling once on append landed
        # on the maximum as it was BEFORE the new block had been measured, so the view
        # sat one message behind and a long reply arrived mostly below the fold. The
        # scrollbar says when its range has actually changed; that is the moment to move.
        self.following = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._range_changed)
        bar.valueChanged.connect(self._value_changed)

    # Within this many pixels of the bottom still counts as being at the bottom: a
    # rounding difference should not be read as "they have scrolled away to read".
    FOLLOW_SLACK = 24

    def _value_changed(self, value):
        bar = self.verticalScrollBar()
        self.following = value >= bar.maximum() - self.FOLLOW_SLACK

    def _range_changed(self, _minimum, maximum):
        if self.following:
            self.verticalScrollBar().setValue(maximum)

    def _append(self, widget, follow=False):
        if follow:
            self.following = True
        self.column.insertWidget(self.column.count() - 1, widget)
        # The range change does the scrolling; this catches the case where the new
        # widget fits without changing the range at all.
        QTimer.singleShot(0, self.keep_up)

    def keep_up(self):
        """Move to the end if we were already there. What streaming and new messages
        use, so reading back through the transcript is not interrupted."""
        if self.following:
            self.scroll_to_end()

    def scroll_to_end(self):
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.following = True

    def clear(self):
        while self.column.count() > 1:
            item = self.column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Unparent before deleting: takeAt only removes it from the layout, and
                # a still-parented widget keeps its geometry and stays on screen until
                # deleteLater is processed - leaving ghosts of the previous chat behind.
                widget.setParent(None)
                widget.deleteLater()

    def add_user(self, text, retry=False, index=None):
        row = UserMessage(text, index=index, retry=retry)
        row.branch.connect(self.branch_here)
        row.edited.connect(self.edit_here)
        # Your own message always brings the view back down: you just sent it.
        self._append(row, follow=True)

    def add_assistant(self, text, name="Bonsai", unprompted=False):
        block = QFrame()
        block.setObjectName("assistantBlock")
        inner = QVBoxLayout(block)
        inner.setContentsMargins(2, 2, 2, 2)
        inner.setSpacing(5)
        speaker = QLabel(f"{name.upper()} · UNPROMPTED" if unprompted else name.upper())
        speaker.setObjectName("speaker")
        inner.addWidget(speaker)
        inner.addWidget(MarkdownView(text, self.palette_colours))
        self._append(block)

    def begin_stream(self, name="Bonsai"):
        """Start an empty reply that fills in as the model writes it."""
        block = QFrame()
        block.setObjectName("assistantBlock")
        inner = QVBoxLayout(block)
        inner.setContentsMargins(2, 2, 2, 2)
        inner.setSpacing(5)
        speaker = QLabel(name.upper())
        speaker.setObjectName("speaker")
        inner.addWidget(speaker)
        view = MarkdownView("", self.palette_colours)
        inner.addWidget(view)
        self._append(block)
        return view

    def add_note(self, html, colour=None):
        label = _body_label(html)
        label.setObjectName("note")
        if colour:
            label.setStyleSheet(f"color: {NOTE_COLOURS.get(colour, colour)};")
        self._append(label)


class Bonsai(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = settings()
        self.chat_id = None
        self.history = []
        self.sent_prompt = None
        self.last_prompt = None
        self.last_vision = self.settings.get("vision_default", True)
        self.attachments = []
        self.worker = None
        self.monitor = None
        self.docker = None
        self.docker_up = False
        self.health_timer = None
        self.health_elapsed = 0
        self.observer = None
        self.observer_timer = None
        self.player = None
        self.gamelink = None
        self.speaker = None
        self.listener = None
        self.observer_recent = []   # last few things it said, to avoid repeating itself
        self.observer_muted_until = 0.0
        self.briefing_worker = None
        self.stream_view = None       # the reply currently being written, if any
        self.stream_text = ""
        self.stream_timer = None
        self.busy_timer = None
        self.busy_since = 0.0
        self.busy_label = ""
        self.auto_running = False
        self.auto_round = 0
        self.auto_last_trace = None
        self.pending_handoff = None   # (reason, summary) from a turn that ran out
        self.continuations = 0
        self.last_handoff = ""
        self.last_next = ""
        self.origin_prompt = ""       # the human request a continuation is serving
        self.context_size = 0
        self._context_warned = False

        self.build_ui()
        self.setAcceptDrops(True)
        self.sidebar.setVisible(self.settings.get("sidebar_visible", True))
        self.apply_theme()
        # Asked for after startup, so a slow or absent server never delays the window.
        # Parented to self deliberately: a bare QTimer.singleShot outlives a window
        # that has been closed, and then fires into a deleted C++ object.
        self._model_timer = QTimer(self)
        self._model_timer.setSingleShot(True)
        self._model_timer.timeout.connect(self.refresh_models)
        self._model_timer.start(0)
        self.refresh_trusted()
        self.open_last_chat()
        self.refresh_side_panel()
        self.open_briefing_if_wanted()
        self.log(f"Bonsai build {APP_VERSION} - {Path(__file__).resolve()}")
        self.start_docker()

    # -- ui construction --

    def build_ui(self):
        self.setWindowTitle("Bonsai")
        self.resize(1100, 720)
        if self.settings.get("always_on_top", False):
            self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = self.build_sidebar()
        root.addWidget(self.sidebar)
        root.addWidget(self.build_main(), stretch=1)
        root.addWidget(self.build_notes())
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("Ctrl+B"), self, self.toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+J"), self, self.toggle_notes)

    def build_sidebar(self):
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(236)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(6)

        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Bonsai")
        title.setObjectName("appName")
        heading_row.addWidget(title, stretch=1)
        collapse = QPushButton("\u2039")
        collapse.setObjectName("ghost")
        collapse.setFixedWidth(24)
        collapse.setToolTip("Hide the sidebar (Ctrl+B)")
        collapse.clicked.connect(self.toggle_sidebar)
        heading_row.addWidget(collapse)
        layout.addLayout(heading_row)
        layout.addSpacing(6)

        chats_label = QLabel("CHATS")
        chats_label.setObjectName("sectionLabel")
        layout.addWidget(chats_label)

        self.chat_list = QListWidget()
        self.chat_list.setObjectName("chatList")
        self.chat_list.currentItemChanged.connect(self.on_chat_picked)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self.on_chat_menu)
        layout.addWidget(self.chat_list, stretch=1)

        chat_buttons = QHBoxLayout()
        chat_buttons.setSpacing(4)
        new_button = QPushButton("＋  New chat")
        new_button.setObjectName("ghost")
        new_button.clicked.connect(self.start_new_chat)
        chat_buttons.addWidget(new_button, stretch=1)
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("ghost")
        delete_button.setToolTip("Delete the selected chat (also on right-click)")
        delete_button.clicked.connect(self.remove_chat)
        chat_buttons.addWidget(delete_button)
        layout.addLayout(chat_buttons)

        layout.addSpacing(10)
        trusted_label = QLabel("TRUSTED FOLDERS")
        trusted_label.setObjectName("sectionLabel")
        trusted_label.setToolTip("File operations inside these folders run without asking.")
        layout.addWidget(trusted_label)

        self.trust_input = QLineEdit()
        self.trust_input.setPlaceholderText("Add a folder path…")
        self.trust_input.returnPressed.connect(self.add_trusted)
        layout.addWidget(self.trust_input)

        trust_row = QHBoxLayout()
        trust_row.setSpacing(4)
        self.trust_picker = QComboBox()
        self.trust_picker.setObjectName("trustPicker")
        trust_row.addWidget(self.trust_picker, stretch=1)
        remove_trust = QPushButton("−")
        remove_trust.setObjectName("ghost")
        remove_trust.setFixedWidth(26)
        remove_trust.setToolTip("Stop trusting the selected folder")
        remove_trust.clicked.connect(self.remove_trusted)
        trust_row.addWidget(remove_trust)
        layout.addLayout(trust_row)

        layout.addSpacing(10)
        self.docker_button = QPushButton("Start Docker Services")
        self.docker_button.setObjectName("ghost")
        self.docker_button.setEnabled(False)
        self.docker_button.clicked.connect(self.toggle_docker)
        layout.addWidget(self.docker_button)

        settings_button = QPushButton("⚙  Settings")
        settings_button.setObjectName("ghost")
        settings_button.clicked.connect(self.open_settings)
        layout.addWidget(settings_button)
        return bar

    def build_main(self):
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top = QFrame()
        top.setObjectName("topBar")
        top_row = QHBoxLayout(top)
        top_row.setContentsMargins(14, 12, 20, 12)
        top_row.setSpacing(10)
        self.sidebar_button = QPushButton("\u2630")
        self.sidebar_button.setObjectName("ghost")
        self.sidebar_button.setFixedWidth(28)
        self.sidebar_button.setToolTip("Show or hide the chat sidebar (Ctrl+B)")
        self.sidebar_button.clicked.connect(self.toggle_sidebar)
        top_row.addWidget(self.sidebar_button)
        self.chat_title = QLabel("New Chat")
        self.chat_title.setObjectName("chatTitle")
        top_row.addWidget(self.chat_title, stretch=1)

        self.model_picker = QComboBox()
        self.model_picker.setObjectName("modelPicker")
        self.model_picker.setMinimumWidth(190)
        self.model_picker.setToolTip(
            "Which model to use. The list comes from the server; choosing one takes "
            "effect on your next message. Click Refresh after starting a server.")
        self.model_picker.activated.connect(self.on_model_chosen)
        top_row.addWidget(self.model_picker)
        refresh_models = QPushButton("\u21bb")
        refresh_models.setObjectName("ghost")
        refresh_models.setFixedWidth(26)
        refresh_models.setToolTip("Ask the server what it can serve")
        refresh_models.clicked.connect(lambda: self.refresh_models(announce=True))
        top_row.addWidget(refresh_models)
        self.briefing_toggle = QPushButton("Briefing")
        self.briefing_toggle.setObjectName("ghost")
        self.briefing_toggle.setToolTip("What's new in the things you follow")
        self.briefing_toggle.clicked.connect(self.toggle_briefing)
        top_row.addWidget(self.briefing_toggle)
        notes_toggle = QPushButton("Notes")
        notes_toggle.setObjectName("ghost")
        notes_toggle.setToolTip("Show or hide the project notes pane")
        notes_toggle.clicked.connect(self.toggle_notes)
        top_row.addWidget(notes_toggle)
        layout.addWidget(top)

        self.pages = QStackedWidget()
        chat_page = QWidget()
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        self.messages = MessageList(self.palette_now())
        self.messages.branch_here.connect(self.on_branch)
        self.messages.edit_here.connect(self.on_edit_message)
        chat_layout.addWidget(self.messages, stretch=1)
        self.pages.addWidget(chat_page)
        self.pages.addWidget(self.build_briefing())
        layout.addWidget(self.pages, stretch=1)
        layout = chat_layout        # the strip and composer belong to the chat page

        # Seven named checkboxes in a row across the window was a lot of furniture for
        # settings that are glanced at rather than read. As icons they take a tenth of
        # the space, and they move down beside the tree - which is tall, so the strip
        # under the composer had an empty band across it that this now fills.
        def toggle(icon, name, tip, checked=False, on_change=None):
            button = QToolButton()
            button.setObjectName("iconToggle")
            button.setText(icon)
            button.setCheckable(True)
            button.setChecked(checked)
            button.setToolTip(f"{name}\n\n{tip}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            if on_change is not None:
                button.toggled.connect(lambda _=False: on_change())
            return button

        self.vision_box = toggle(
            "\U0001F4F7", "Screen capture",
            "Attach a picture of your screen to what you send.",
            self.settings.get("vision_default", True),
            lambda: self.update_setting("vision_default", self.vision_box.isChecked()))
        self.search_box = toggle(
            "\U0001F310", "Web search",
            "Let it search the web. Turn off to avoid tripping rate limits.",
            self.settings.get("web_search_enabled", True),
            lambda: self.update_setting("web_search_enabled",
                                        self.search_box.isChecked()))
        self.monitor_box = toggle(
            "\U0001F4E1", "Live monitor",
            "Run your monitor script and watch what it prints.",
            False, self.on_monitor_toggled)
        self.proactive_box = toggle(
            "\U0001F440", "Proactive",
            "Look at your screen every so often and speak up when something is worth "
            "saying. Interval, quiet period and how good a remark has to be are in "
            "Settings.", False, self.on_proactive_toggled)
        self.play_box = toggle(
            "\U0001F3AE", "Play",
            "Play whatever is running on the stage - looking, pressing something, "
            "seeing what happened, and talking about it. Ask it to start a game on the "
            "stage first.", False, self.on_play_toggled)
        self.gamelink_box = toggle(
            "\U0001F50C", "Game link",
            "Listen for games that speak the Neuro API, so it plays through the game "
            "itself rather than by photographing the screen.",
            False, self.on_gamelink_toggled)
        self.listen_box = toggle(
            "\U0001F3A4", "Microphone",
            "Hear what you say and put it in the box. One click off, so a call is not "
            "overheard. On push-to-talk it only listens while you hold this down. "
            "Which microphone - or which monitor, to hear what you are playing - is on "
            "the Voice page in Settings.",
            False, self.on_listen_toggled)
        self.speak_box = toggle(
            "\U0001F50A", "Speak",
            "Say answers out loud. Code, tables and paths are not read out - the prose "
            "is. The voice and how much is spoken are on the Voice page in Settings.",
            bool(self.settings.get("speak_replies", False)), self.on_speak_toggled)

        self.toggle_buttons = [self.vision_box, self.search_box, self.monitor_box,
                               self.proactive_box, self.play_box, self.gamelink_box,
                               self.listen_box, self.speak_box]

        # Push-to-talk by key, not by holding the icon: the point of it is that you
        # are talking in a game or a call, where Bonsai does not have focus and cannot
        # be clicked. Wayland will not hand a key to an unfocused window, so the
        # compositor holds the key and pokes a socket - see bonsai/ptt.py.
        self.ptt = PushToTalk()
        self.ptt.held.connect(self.hold_to_talk)
        self.ptt.toggled.connect(
            lambda: self.listen_box.setChecked(not self.listen_box.isChecked()))
        self.ptt.failed.connect(lambda why: self.log(why, "orange"))
        self.ptt.start()

        composer_wrap = QWidget()
        wrap_layout = QVBoxLayout(composer_wrap)
        wrap_layout.setContentsMargins(26, 10, 20, 6)
        wrap_layout.setSpacing(2)

        self.attach_bar = AttachmentBar()
        self.attach_bar.removed.connect(self.remove_attachment)
        wrap_layout.addWidget(self.attach_bar)

        composer = QFrame()
        composer.setObjectName("composer")
        inner = QVBoxLayout(composer)
        inner.setContentsMargins(14, 10, 10, 10)
        inner.setSpacing(6)

        self.input = InputBox()
        self.input.setPlaceholderText("Ask Bonsai to do something…")
        self.input.setFixedHeight(52)
        self.input.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.input.returnPressed.connect(self.send)
        inner.addWidget(self.input)

        # Parented to the window so it can sit over the transcript, but anchored to
        # the composer so it lines up with the box being typed into.
        self.skill_drawer = SkillDrawer(self)
        self.skill_drawer.chosen.connect(self.input.insert_skill)
        self.input.drawer = self.skill_drawer
        self.input.anchor = composer

        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch(1)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("ghost")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self.retry)
        actions.addWidget(self.retry_button)
        self.auto_button = QPushButton("Auto")
        self.auto_button.setObjectName("ghost")
        self.auto_button.setToolTip(
            "Keep working across turns until the task list is empty. Bonsai must have "
            "added tasks with the TASK tool first.")
        self.auto_button.clicked.connect(self.on_auto_clicked)
        actions.addWidget(self.auto_button)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("ghost")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip("Cancel the current request and stop auto mode")
        self.stop_button.clicked.connect(self.on_stop_clicked)
        actions.addWidget(self.stop_button)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send)
        actions.addWidget(self.send_button)
        inner.addLayout(actions)

        wrap_layout.addWidget(composer)

        # Under the composer, where you are already looking when you press Send.
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        footer.setSpacing(10)
        self.bonsai = BonsaiGrowth()
        footer.addWidget(self.bonsai, alignment=Qt.AlignmentFlag.AlignBottom)

        switches = QHBoxLayout()
        switches.setSpacing(2)
        switches.setContentsMargins(0, 0, 0, 0)
        for button in self.toggle_buttons:
            switches.addWidget(button)
        footer.addLayout(switches)

        self.status = QLabel("Ready")
        self.status.setObjectName("statusText")
        footer.addWidget(self.status, stretch=1,
                         alignment=Qt.AlignmentFlag.AlignBottom)
        self.context_label = QLabel("context: -")
        self.context_label.setObjectName("contextText")
        self.context_label.setToolTip(
            "Prompt tokens used on the last request, against the model's loaded context "
            "window. When this fills up llama.cpp silently drops the oldest messages.")
        footer.addWidget(self.context_label, alignment=Qt.AlignmentFlag.AlignBottom)
        wrap_layout.addLayout(footer)

        layout.addWidget(composer_wrap)
        return pane

    def build_briefing(self):
        """A page of what is new in the things you follow, gathered before you ask."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 16, 20, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        heading = QLabel("BRIEFING")
        heading.setObjectName("sectionLabel")
        head.addWidget(heading)
        self.briefing_status = QLabel("")
        self.briefing_status.setObjectName("briefMeta")
        head.addWidget(self.briefing_status, stretch=1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(lambda: self.start_briefing(force=True))
        head.addWidget(refresh)
        layout.addLayout(head)

        interests_row = QHBoxLayout()
        interests_row.setSpacing(4)
        self.interests_input = QLineEdit()
        self.interests_input.setPlaceholderText(
            "Things you follow, comma separated \u2014 e.g. Godot engine, factory sims, Rust")
        self.interests_input.setToolTip("These are the searches the briefing runs.")
        self.interests_input.returnPressed.connect(self.save_interests_from_input)
        interests_row.addWidget(self.interests_input, stretch=1)
        save_topics = QPushButton("Save")
        save_topics.setObjectName("ghost")
        save_topics.clicked.connect(self.save_interests_from_input)
        interests_row.addWidget(save_topics)
        layout.addLayout(interests_row)

        self.briefing_area = QScrollArea()
        self.briefing_area.setObjectName("briefingArea")
        self.briefing_area.setWidgetResizable(True)
        self.briefing_area.setFrameShape(QFrame.Shape.NoFrame)
        self.briefing_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("briefingBody")
        self.briefing_column = QVBoxLayout(body)
        self.briefing_column.setContentsMargins(0, 4, 8, 4)
        self.briefing_column.setSpacing(8)
        self.briefing_column.addStretch(1)
        self.briefing_area.setWidget(body)
        layout.addWidget(self.briefing_area, stretch=1)
        return page

    def theme_now(self):
        """The palette in force, healing a settings file that names nothing.

        Light and dark are themes here like any other, so there is no separate switch
        to disagree with the picker. A settings file written before that carries
        `dark_mode`, and it decides which neutral palette to land on."""
        name = self.settings.get("theme")
        if name in PALETTES:
            return name
        return NEUTRAL_DARK if self.settings.get("dark_mode", True) else NEUTRAL_LIGHT

    def palette_now(self):
        return palette(self.theme_now())

    def briefing_card(self, item):
        card = QFrame()
        card.setObjectName("briefCard")
        inner = QVBoxLayout(card)
        inner.setContentsMargins(13, 10, 13, 11)
        inner.setSpacing(3)

        link = self.palette_now()["text"]
        title = BodyLabel(
            f'<a href="{html.escape(item["url"], quote=True)}" '
            f'style="color:{link}; text-decoration:none;">'
            f'{html.escape(item["title"])}</a>')
        title.setObjectName("briefTitle")
        # BodyLabel sets TextSelectableByMouse, which does NOT include link clicks -
        # so the anchors rendered but were inert. Both flags are needed.
        title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                      | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.linkActivated.connect(self.open_link)
        inner.addWidget(title)

        meta = QLabel(" \u00b7 ".join(part for part in (
            item["kind"].upper(), item.get("source") or "", item.get("published") or "")
            if part))
        meta.setObjectName("briefMeta")
        inner.addWidget(meta)

        if item.get("summary"):
            summary = BodyLabel(html.escape(item["summary"]))
            summary.setObjectName("briefSummary")
            inner.addWidget(summary)
        return card

    def open_link(self, url):
        """Open a briefing link in the desktop browser, and say so when it fails -
        a link that silently does nothing is indistinguishable from a broken app."""
        if QDesktopServices.openUrl(QUrl(url)):
            return
        try:
            subprocess.Popen(["xdg-open", url], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.log(f"Could not open {url}: {exc}", "orange")

    def render_briefing(self, briefing=None):
        briefing = briefing if briefing is not None else load_briefing()
        while self.briefing_column.count() > 1:
            item = self.briefing_column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hold the reference: after setParent(None) the layout item no longer
                # reports a widget, and asking twice returns None the second time.
                widget.setParent(None)
                widget.deleteLater()

        interests = load_interests()
        if not self.interests_input.text().strip():
            self.interests_input.setText(", ".join(interests))
        # Filtered again on the way out, not only on the way in: a cache gathered before
        # a filter change would otherwise keep showing what the filter now rejects,
        # until it happened to go stale.
        english_only = self.settings.get("briefing_english_only", True)
        topics = []
        for topic in briefing.get("topics", []):
            items = [i for i in topic.get("items", [])
                     if not english_only or looks_english(i.get("title", ""),
                                                          i.get("summary", ""))]
            if items:
                topics.append({"topic": topic["topic"], "items": items})
        if not topics:
            hint = BodyLabel(
                "Nothing gathered yet. Put a few things you follow in the box above and "
                "press Refresh." if not interests else
                "No results for those topics. Try broader wording, or a longer window "
                "via \u2699 Settings \u2192 Briefing."
                if not briefing.get("repeats_skipped") else
                f"Nothing new - all {briefing['repeats_skipped']} result(s) were "
                "things a previous briefing already showed you. Widen the window, add "
                "an interest, or turn off 'Skip anything a previous briefing showed' "
                "in \u2699 Settings \u2192 Briefing.")
            hint.setObjectName("briefSummary")
            self.briefing_column.insertWidget(0, hint)
        for index, topic in enumerate(topics):
            label = QLabel(topic["topic"].upper())
            label.setObjectName("briefTopic")
            self.briefing_column.insertWidget(self.briefing_column.count() - 1, label)
            for item in topic["items"]:
                self.briefing_column.insertWidget(self.briefing_column.count() - 1,
                                                  self.briefing_card(item))
        fetched = briefing.get("fetched") or ""
        count = sum(len(t["items"]) for t in topics)
        # Say when things were held back, so a short briefing reads as "nothing new"
        # rather than as the gathering having failed.
        skipped = briefing.get("repeats_skipped") or 0
        held = f", {skipped} already seen" if skipped else ""
        self.briefing_status.setText(
            f"{count} item(s){held}, gathered {fetched.replace('T', ' ')}" if fetched
            else "not gathered yet")

    def save_interests_from_input(self):
        interests = save_interests(
            [t for t in self.interests_input.text().split(",")])
        self.interests_input.setText(", ".join(interests))
        self.log(f"\U0001F4F0 Following {len(interests)} topic(s): {', '.join(interests)}")
        self.start_briefing(force=True)

    def open_briefing_if_wanted(self):
        """On launch, show what is new rather than an empty chat - the point of a
        briefing is that you did not have to ask for it."""
        if not self.settings.get("briefing_enabled", True):
            return
        if not load_interests() and not load_briefing().get("topics"):
            # Prefill the box from what is already known, but save nothing: the list
            # of things you follow should be yours, not inferred behind your back.
            self.interests_input.setText(", ".join(suggested_interests()))
        self.render_briefing()
        if self.settings.get("briefing_on_open", True) and load_interests():
            self.pages.setCurrentIndex(1)
            self.briefing_toggle.setText("Chat")
        self.start_briefing()

    def toggle_briefing(self):
        showing = self.pages.currentIndex() == 1
        self.pages.setCurrentIndex(0 if showing else 1)
        self.briefing_toggle.setText("Chat" if not showing else "Briefing")
        if not showing:
            self.render_briefing()

    def start_briefing(self, force=False):
        """Gather in the background. Never blocks opening the window, and never
        refetches a cache that is still fresh."""
        if not self.settings.get("briefing_enabled", True):
            return
        if self.briefing_worker and self.briefing_worker.isRunning():
            return
        interests = load_interests()
        if not interests:
            self.render_briefing()
            return
        if not force and not briefing_is_stale():
            self.render_briefing()
            return
        self.briefing_status.setText("gathering\u2026")
        self.briefing_worker = BriefingWorker(interests)
        self.briefing_worker.status.connect(
            lambda where: self.briefing_status.setText(f"gathering \u2014 {where}"))
        self.briefing_worker.done.connect(self.on_briefing_ready)
        self.briefing_worker.failed.connect(lambda msg: self.log(msg, "orange"))
        self.briefing_worker.start()

    def on_briefing_ready(self, briefing):
        self.render_briefing(briefing)
        count = sum(len(t["items"]) for t in briefing.get("topics", []))
        self.log(f"\U0001F4F0 Briefing updated: {count} item(s) across "
                 f"{len(briefing.get('topics', []))} topic(s).")

    def build_notes(self):
        """The right-hand panel: everything Bonsai remembers, visible and editable.

        Project notes, memory and the task list were all model-managed files you had to
        leave the app to correct. They are the three things most worth fixing by hand
        when the model gets them wrong, so they each get a tab."""
        pane = QFrame()
        pane.setObjectName("notesPane")
        pane.setFixedWidth(340)
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)

        tabs = QTabWidget()
        tabs.setObjectName("sideTabs")
        tabs.setDocumentMode(True)
        self.side_tabs = tabs
        tabs.addTab(self.build_notes_tab(), "Notes")
        tabs.addTab(self.build_memory_tab(), "Memory")
        tabs.addTab(self.build_tasks_tab(), "Tasks")
        tabs.addTab(self.build_skills_tab(), "Skills")
        # Last, so opening the app still lands on Notes rather than on an empty pane.
        self.preview_pane = PreviewPane()
        tabs.addTab(self.preview_pane, "Preview")
        tabs.currentChanged.connect(lambda _index: self.refresh_side_panel())

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(4)
        tab_row.addWidget(tabs, stretch=1)
        hide_notes = QPushButton("\u203a")
        hide_notes.setObjectName("ghost")
        hide_notes.setFixedWidth(24)
        hide_notes.setToolTip("Hide this panel (Ctrl+J)")
        hide_notes.clicked.connect(self.toggle_notes)
        tab_row.addWidget(hide_notes, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(tab_row)
        self.side_tabs = tabs

        self.notes_pane = pane
        pane.setVisible(self.settings.get("notes_visible", True))
        return pane

    def build_notes_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        self.notes_path_label = QLabel("no vault configured")
        self.notes_path_label.setObjectName("notesPath")
        self.notes_path_label.setWordWrap(True)
        layout.addWidget(self.notes_path_label)

        self.notes_editor = QTextEdit()
        self.notes_editor.setObjectName("notesEditor")
        self.notes_editor.setAcceptRichText(False)
        self.notes_editor.setPlaceholderText(
            "Design decisions and project knowledge land here, and survive across "
            "chats.\n\nPlain markdown - the same file opens in Obsidian.")
        layout.addWidget(self.notes_editor, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        reload_button = QPushButton("Reload")
        reload_button.setObjectName("ghost")
        reload_button.setToolTip("Discard edits here and re-read the file from disk")
        reload_button.clicked.connect(self.refresh_notes)
        buttons.addWidget(reload_button)
        buttons.addStretch(1)
        self.notes_save_button = QPushButton("Save")
        self.notes_save_button.setObjectName("primary")
        self.notes_save_button.clicked.connect(self.save_notes)
        buttons.addWidget(self.notes_save_button)
        layout.addLayout(buttons)
        return tab

    def build_memory_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        note = QLabel("What Bonsai believes about you. Move a line across the ARCHIVAL "
                      "header to promote or demote it.")
        note.setObjectName("notesPath")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.memory_editor = QTextEdit()
        self.memory_editor.setObjectName("notesEditor")
        self.memory_editor.setAcceptRichText(False)
        layout.addWidget(self.memory_editor, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        reload_button = QPushButton("Reload")
        reload_button.setObjectName("ghost")
        reload_button.clicked.connect(self.refresh_memory)
        buttons.addWidget(reload_button)
        buttons.addStretch(1)
        save_button = QPushButton("Save")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.save_memory)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        return tab

    def build_skills_tab(self):
        """Learned procedures, visible and editable. A skill the app wrote by itself
        that you cannot read or delete is not a feature, it is a liability."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        note = QLabel("Procedures Bonsai has saved, including ones it wrote itself "
                      "after doing the same job three times. Edit freely; delete a "
                      "block to remove the skill.")
        note.setObjectName("notesPath")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.skills_editor = QTextEdit()
        self.skills_editor.setObjectName("notesEditor")
        self.skills_editor.setAcceptRichText(False)
        layout.addWidget(self.skills_editor, stretch=1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        reload_button = QPushButton("Reload")
        reload_button.setObjectName("ghost")
        reload_button.clicked.connect(self.refresh_skills)
        buttons.addWidget(reload_button)
        buttons.addStretch(1)
        save_button = QPushButton("Save")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self.save_skills_text)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)
        return tab

    def refresh_skills(self):
        self.skills_editor.setPlainText(skills_as_text(load_skills()["skills"]))

    def save_skills_text(self):
        skills, bad = skills_from_text(self.skills_editor.toPlainText())
        if bad:
            self.log(f"Skills not saved: {bad}", "orange")
            return
        data = load_skills()
        data["skills"] = skills
        save_json(SKILLS_FILE, data)
        self.log(f"Saved {len(skills)} skill(s).")
        self.refresh_skills()

    def build_tasks_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)

        note = QLabel("Bonsai must name a file to close a task. You don't - ticking one "
                      "here is your call.")
        note.setObjectName("notesPath")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.task_list = QListWidget()
        self.task_list.setObjectName("taskList")
        # Several at once: with twenty tasks on a list, removing them one at a time is
        # the kind of tedium that stops people pruning it at all.
        self.task_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.task_list.itemChanged.connect(self.on_task_toggled)
        layout.addWidget(self.task_list, stretch=1)

        adding = QHBoxLayout()
        adding.setSpacing(4)
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Add a task\u2026")
        self.task_input.returnPressed.connect(self.add_task)
        adding.addWidget(self.task_input, stretch=1)
        add_button = QPushButton("\uff0b")
        add_button.setObjectName("ghost")
        add_button.setFixedWidth(28)
        add_button.clicked.connect(self.add_task)
        adding.addWidget(add_button)
        layout.addLayout(adding)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        remove_button = QPushButton("Remove selected")
        remove_button.setObjectName("ghost")
        remove_button.clicked.connect(self.remove_task)
        buttons.addWidget(remove_button)
        clear_done_button = QPushButton("Clear completed")
        clear_done_button.setObjectName("ghost")
        clear_done_button.setToolTip("Remove every ticked task")
        clear_done_button.clicked.connect(self.clear_completed_tasks)
        buttons.addWidget(clear_done_button)
        buttons.addStretch(1)
        reload_button = QPushButton("Reload")
        reload_button.setObjectName("ghost")
        reload_button.clicked.connect(self.refresh_tasks)
        buttons.addWidget(reload_button)
        layout.addLayout(buttons)
        return tab

    def toggle_sidebar(self):
        visible = not self.sidebar.isVisible()
        self.sidebar.setVisible(visible)
        self.update_setting("sidebar_visible", visible)

    def toggle_notes(self):
        visible = not self.notes_pane.isVisible()
        self.notes_pane.setVisible(visible)
        self.update_setting("notes_visible", visible)
        if visible:
            self.refresh_side_panel()

    def refresh_side_panel(self):
        """Re-read whichever tab is showing. Only the visible one, so a half-typed edit
        in another tab is never thrown away underneath you."""
        which = self.side_tabs.currentIndex() if hasattr(self, "side_tabs") else 0
        if which == 0:
            self.refresh_notes()
        elif which == 1:
            self.refresh_memory()
        else:
            self.refresh_tasks()

    # -- memory tab --

    def refresh_memory(self):
        self.memory_editor.setPlainText(memory_to_text(load_memory()))
        self.memory_editor.document().setModified(False)

    def save_memory(self):
        memory = text_to_memory(self.memory_editor.toPlainText())
        save_json(MEMORY_FILE, memory)
        self.log(f"\U0001F9E0 Memory saved: {len(memory['core'])} core, "
                 f"{len(memory['archival'])} archival.")
        self.refresh_memory()

    # -- tasks tab --

    def refresh_tasks(self):
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for task in load_tasks():
            item = QListWidgetItem(f"[{task['id']}] {task['text']}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if task["done"]
                               else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            if task.get("evidence"):
                item.setToolTip(f"closed by: {task['evidence']}")
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)

    def on_task_toggled(self, item):
        """You are the authority on your own list, so no evidence is demanded here -
        this is also the way to close a task that produces no file at all."""
        task_id = item.data(Qt.ItemDataRole.UserRole)
        done = item.checkState() == Qt.CheckState.Checked
        tasks = load_tasks()
        for task in tasks:
            if task["id"] == task_id:
                if task["done"] == done:
                    return
                task["done"] = done
                task["evidence"] = "closed in the app by you" if done else ""
                save_tasks(tasks)
                self.log(f"{'\u2713' if done else '\u21ba'} Task {task_id} "
                         f"{'closed' if done else 'reopened'} by you: {task['text'][:50]}")
                return

    def add_task(self):
        text = self.task_input.text().strip()
        if not text:
            return
        self.log(handle_task(f"ADD {text}").splitlines()[0])
        self.task_input.clear()
        self.refresh_tasks()

    def clear_completed_tasks(self):
        self.log(handle_task("CLEAR_DONE").splitlines()[0])
        self.refresh_tasks()

    def remove_task(self):
        """Remove whichever rows are highlighted.

        It used to read currentItem(), which is unset until a row has actually been
        clicked - so the button did nothing at all, and said nothing about why. The
        tick box is a different gesture entirely: that marks a task done, and Clear
        completed is what removes those."""
        chosen = [item.data(Qt.ItemDataRole.UserRole)
                  for item in self.task_list.selectedItems()]
        if not chosen:
            self.log("Nothing selected. Click a task's text to highlight it, then press "
                     "Remove selected. Ticking its box marks it done instead - use "
                     "Clear completed to remove the ticked ones.", "orange")
            return
        ids = " ".join(str(task_id) for task_id in chosen)
        self.log(handle_task(f"REMOVE {ids}").splitlines()[0])
        self.refresh_tasks()

    def refresh_notes(self):
        """Show the active project's note. Regenerated first so the file list in it
        reflects what is actually on disk right now, not what was there last time."""
        path = project_note_path()
        if not path:
            self.notes_path_label.setText(
                "No vault configured - set Vault folder in \u2699 Settings.")
            self.notes_editor.setPlainText("")
            self.notes_editor.setEnabled(False)
            return
        project_summary()                      # rewrites the generated file block
        self.notes_editor.setEnabled(True)
        self.notes_path_label.setText(str(path))
        self.notes_editor.setPlainText(read_project_note())

    def save_notes(self):
        if save_project_note(self.notes_editor.toPlainText()):
            self.log(f"\U0001F4DD Notes saved to {project_note_path()}")
        else:
            self.log("Could not write the project note - check the vault path in "
                     "\u2699 Settings.", "red")

    def on_chat_menu(self, point):
        item = self.chat_list.itemAt(point)
        if item is None:
            return
        menu = QMenu(self)
        delete = menu.addAction("Delete chat")
        if menu.exec(self.chat_list.mapToGlobal(point)) is delete:
            self.chat_list.setCurrentItem(item)
            self.remove_chat()

    # -- helpers --

    # -- models --

    def refresh_models(self, announce=False):
        """Fill the picker with the configured servers, and the models the live one
        offers.

        Every endpoint is listed without touching the network, so a server that is
        switched off never delays startup or hides the entry that would let you pick
        a different one. Only the endpoint in use is asked what it can serve, which is
        what matters for LM Studio and Ollama; llama.cpp serves one model and ignores
        the request entirely, which is exactly why endpoints are the unit here."""
        here = self.settings.get("server_url", DEFAULTS["server_url"])
        chosen = (self.settings.get("model") or "").strip()
        servers = endpoints()
        self.model_picker.blockSignals(True)
        self.model_picker.clear()
        for server in servers:
            self.model_picker.addItem(server["name"], (server["url"], server["model"]))
        offered = available_models(here)
        if len(offered) > 1:
            # Somewhere that loads on demand. Offer each model under the live server.
            live = next((s["name"] for s in servers if s["url"] == here), "Server")
            for name in offered:
                self.model_picker.addItem(f"{live} \u00b7 {Path(name).name}",
                                          (here, name))
        index = self.model_picker.findData((here, chosen))
        if index < 0:
            index = next((i for i in range(self.model_picker.count())
                          if (self.model_picker.itemData(i) or ("", ""))[0] == here), 0)
        self.model_picker.setCurrentIndex(max(index, 0))
        self.model_picker.blockSignals(False)
        if announce:
            self.log(f"{len(servers)} server(s) configured; the one in use offers "
                     f"{len(offered)} model(s)." if offered else
                     "The server in use did not answer. The list is unchanged, and you "
                     "can still pick another server.", None if offered else "orange")
        return offered

    def on_model_chosen(self, index):
        url, name = self.model_picker.itemData(index) or ("", "")
        if not url:
            return
        if (url, name) == (self.settings.get("server_url"),
                           self.settings.get("model") or ""):
            return
        was_at = self.settings.get("server_url")
        moved = url != was_at
        self.settings["server_url"] = url
        self.settings["model"] = name
        save_settings(self.settings)
        if self.auto_running:
            self.stop_auto("you changed the model")
        if moved:
            # The window came from the old server, so it means nothing for the new one
            # until that one has answered once.
            self.context_size = 0
            self.context_label.setText("context: -")
        self.log(f"Now using <b>{self.model_picker.itemText(index)}</b>"
                 + (f" at {url}" if moved else "")
                 + ". It applies from your next message; this conversation carries on "
                   "as it is.")
        if moved:
            # Before refreshing the list, because the handover is what decides whether
            # the new server is going to answer the question the refresh asks it.
            self.switch_servers(was_at)
            QTimer.singleShot(0, self.refresh_models)

    def restyle_open_views(self):
        """Redraw the parts that hold their own copy of the palette.

        The stylesheet reaches ordinary widgets on its own, but rendered documents and
        the briefing's inline link colour are baked in at build time."""
        colours = self.palette_now()
        self.messages.palette_colours = colours
        if hasattr(self, "preview_pane") and self.preview_pane.path:
            self.preview_pane.show_path(self.preview_pane.path)
        if hasattr(self, "briefing_column"):
            self.render_briefing()

    def log(self, message, colour=None, italic=True):
        """A muted line in the transcript: tool activity, warnings, app notices."""
        self.messages.add_note(message, colour)

    def update_setting(self, key, value):
        self.settings[key] = value
        save_settings(self.settings)

    def apply_theme(self, theme=None):
        """Repaint in `theme`, or in whatever the settings currently name."""
        if isinstance(theme, bool):     # the toolbar switch still passes a bool
            theme = NEUTRAL_DARK if theme else NEUTRAL_LIGHT
        if theme:
            self.settings["theme"] = theme
        sheet = stylesheet(self.theme_now(),
                           self.settings.get("font", DEFAULT_FONT),
                           self.settings.get("font_size", 13))
        # Applied to the application, not to this window. A combo box's dropdown is a
        # separate top-level window, and a window-scoped stylesheet does not reach it:
        # its rows fell back to the system palette and came out white, with the theme's
        # light text on them. Everything else looked correct, which is what made it
        # look like only the hovered row was themed - that was the one row whose
        # ::item:hover rule did apply.
        app = QApplication.instance()
        if app is not None:
            self.setStyleSheet("")
            app.setStyleSheet(sheet)
        else:
            self.setStyleSheet(sheet)
        # The tree carries its own colour, so it has to be told the palette moved
        # under it or it keeps the old theme's grey.
        if getattr(self, "bonsai", None) is not None:
            self.bonsai.show_mood()

    # -- chats --

    def open_last_chat(self):
        index = load_chat_index()
        if not index["chats"]:
            chat_id = new_chat()
        else:
            last = self.settings.get("last_chat_id")
            chat_id = last if any(c["id"] == last for c in index["chats"]) else index["chats"][0]["id"]
        self.switch_chat(chat_id)

    def switch_chat(self, chat_id):
        self.chat_id = chat_id
        self.history = load_chat(chat_id)
        self.sent_prompt = None
        self.messages.clear()
        for position, message in enumerate(self.history):
            if message["role"] == "user":
                self.messages.add_user(message["content"].replace("\n", "<br>"),
                                       index=position)
            else:
                # History keeps a [SYSTEM RECORD ...] preamble so the next turn cannot
                # misremember what happened. That is scaffolding for the model, and it
                # was being shown verbatim whenever a chat was reopened.
                self.messages.add_assistant(
                    ACTIONS_ECHO_RE.sub("", message["content"]).strip())
        self.update_setting("last_chat_id", chat_id)
        self.refresh_chats()

    def refresh_chats(self):
        self.chat_list.blockSignals(True)
        self.chat_list.clear()
        for chat in load_chat_index()["chats"]:
            # A branch is marked rather than indented: the list is sorted by when each
            # chat was last used, so a child rarely sits under its parent.
            item = QListWidgetItem(("\u2387 " if chat.get("parent") else "")
                                   + chat["title"])
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            self.chat_list.addItem(item)
            if chat["id"] == self.chat_id:
                self.chat_list.setCurrentItem(item)
                self.chat_title.setText(chat["title"])
        self.chat_list.blockSignals(False)

    def on_chat_picked(self, current, _previous=None):
        if current is None:
            return
        chat_id = current.data(Qt.ItemDataRole.UserRole)
        if chat_id and chat_id != self.chat_id:
            self.switch_chat(chat_id)

    def start_new_chat(self):
        self.switch_chat(new_chat())

    def remove_chat(self):
        if not self.chat_id:
            return
        confirm = QMessageBox.question(self, "Delete Chat", "Delete this chat permanently?",
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                       QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        delete_chat(self.chat_id)
        remaining = load_chat_index()["chats"]
        self.switch_chat(remaining[0]["id"] if remaining else new_chat())

    # -- trusted folders --

    def refresh_trusted(self):
        paths = load_trusted()
        self.trust_picker.clear()
        self.trust_picker.addItems(paths or ["(no trusted folders yet)"])

    def add_trusted(self):
        raw = self.trust_input.text().strip()
        if not raw:
            return
        try:
            path = Path(raw).expanduser().resolve()
        except Exception as exc:
            self.log(f"Could not add trusted path: {exc}", "red")
            return
        if path in PROTECTED_PATHS:
            self.log("Refused: that's a protected system directory.", "red")
            return
        paths = load_trusted()
        if str(path) not in paths:
            paths.append(str(path))
            save_trusted(paths)
            self.log(f"\u2705 Trusted folder added: {path}")
        self.trust_input.clear()
        self.refresh_trusted()

    def remove_trusted(self):
        current = self.trust_picker.currentText()
        if not current or current.startswith("(no trusted"):
            return
        paths = load_trusted()
        if current in paths:
            paths.remove(current)
            save_trusted(paths)
            self.log(f"Trusted folder removed: {current}")
        self.refresh_trusted()

    # -- settings --

    def open_settings(self):
        was_running = dict(self.settings.get("services", DEFAULTS["services"]))
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result_settings:
            self.settings.update(dialog.result_settings)
            save_settings(self.settings)
            self.apply_theme()
            self.restyle_open_views()
            self.refresh_models()
            self.log("\u2699 Settings updated.")
            now = self.settings.get("services", {})
            self.stop_services([name for name, on in was_running.items()
                                if on and not now.get(name, False)])
            if self.settings.get("web_search_enabled") and not search_backend_up():
                self.log("\u26a0 Web search is on but nothing is answering at "
                         f"{self.settings.get('searxng_url', DEFAULTS['searxng_url'])}, "
                         "so SEARCH will fail. Start it, or turn web search off in "
                         "\u2699 Settings \u2192 Screen.", "orange")

    # -- drag and drop --

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        room = 5 - len(self.attachments)
        if room <= 0:
            self.log("Already have 5 files queued - remove one, or send your message "
                     "first.", "orange")
            return
        names = []
        for path in paths[:room]:
            text = read_file(path, numbered=False)
            self.attachments.append({"path": path, "name": Path(path).name,
                                     "text": text, "chars": len(text)})
            names.append(Path(path).name)
        skipped = len(paths) - len(names)
        note = f" ({skipped} skipped, queue full)" if skipped else ""
        self.attach_bar.show_files(self.attachments)
        self.log(f"\U0001F4CE Attached: {', '.join(names)}{note} "
                 f"(queued: {len(self.attachments)}/5) - click \u00d7 on a file to remove it")
        event.acceptProposedAction()

    def on_edit_message(self, index, reworded):
        """Ask again with different wording, from the point that message was sent.

        A fork, not a rewrite: the conversation as it stands - and the answer the
        original wording got - stays in the sidebar to compare against."""
        if not reworded.strip():
            return
        if self.worker and self.worker.isRunning():
            self.log("Finish or stop the current turn before editing a message.",
                     "orange")
            return
        if self.auto_running:
            self.stop_auto("you edited a message")
        forked = fork_chat(self.chat_id, index)
        self.switch_chat(forked)
        self.continuations = 0
        self.last_handoff = self.last_next = ""
        self.pending_handoff = None
        self.origin_prompt = reworded
        self.log("\u270e Asked again with different wording. The version you had, and "
                 "the reply it got, are still in the sidebar.")
        self.dispatch(reworded, self.vision_box.isChecked(), retry=False)

    def on_branch(self, index, said):
        """Fork the conversation just before this message and reopen it there.

        What was said is put back in the input rather than sent: branching is for
        trying a different phrasing, and re-sending the same words would just walk
        into the same answer."""
        if self.auto_running:
            self.stop_auto("you branched the conversation")
        if self.worker and self.worker.isRunning():
            self.log("Finish or stop the current turn before branching.", "orange")
            return
        forked = fork_chat(self.chat_id, index)
        self.switch_chat(forked)
        self.input.setText(said.replace("<br>", "\n"))
        self.input.setFocus()
        self.continuations = 0
        self.pending_handoff = None
        self.log("\u2387 Branched here. The original conversation is untouched in the "
                 "sidebar; this copy keeps everything above this point.")

    def on_preview(self, path):
        """Show a file in the side panel, and bring the panel to it - a preview behind
        a tab nobody switched to is the same as no preview."""
        # Raise the tab BEFORE rendering: a page of a QTabWidget that has never been
        # shown still reports its pre-layout width, and everything laid out against
        # that comes out too wide to fit the panel it is in.
        self.side_tabs.setCurrentWidget(self.preview_pane)
        self.preview_pane.show_path(path)

    def remove_attachment(self, index):
        """Drop one queued file, or all of them when index is -1."""
        if index < 0:
            count = len(self.attachments)
            self.attachments = []
            self.attach_bar.show_files([])
            if count:
                self.log(f"Removed all {count} queued files.")
            return
        if not 0 <= index < len(self.attachments):
            return
        gone = self.attachments.pop(index)
        self.attach_bar.show_files(self.attachments)
        self.log(f"Removed {gone['name']} - {len(self.attachments)}/5 still queued."
                 if self.attachments else f"Removed {gone['name']} - nothing queued now.")

    # -- sending --

    def send(self):
        prompt = self.input.text().strip()
        if not prompt:
            return  # empty input shouldn't invent a question
        if self.auto_running:
            self.stop_auto("you sent a message")  # your input always takes precedence
        self.continuations = 0
        self.last_handoff = ""
        self.last_next = ""
        self.pending_handoff = None
        self.origin_prompt = prompt
        self.input.clear()
        self.dispatch(prompt, self.vision_box.isChecked(), retry=False)

    def retry(self):
        if self.last_prompt:
            self.dispatch(self.last_prompt, self.last_vision, retry=True)

    def dispatch(self, prompt, vision, retry, unattended=False):
        self.messages.add_user(prompt.replace("\n", "<br>"), retry=retry,
                               index=len(self.history))

        full_prompt = expand_skill_shortcut(prompt)
        if self.attachments and not retry:
            blocks = [f"--- FILE: {a['path']} ---\n{a['text']}" for a in self.attachments]
            full_prompt = ("\n\n".join(blocks)
                           + f"\n\n--- USER MESSAGE ---\n{full_prompt}")
            self.attachments = []
            self.attach_bar.show_files([])

        self.last_prompt = prompt
        self.last_vision = vision
        self.sent_prompt = prompt
        self.status.setText("Capturing screen..." if vision else "Sending...")
        self.send_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.stop_button.setEnabled(True)

        config = dict(self.settings)
        config["unattended"] = unattended
        config["context_size"] = self.context_size
        config["chat_id"] = self.chat_id      # so HISTORY can skip this conversation
        self.worker = Worker(full_prompt, vision, list(self.history), config)
        self.worker.finished.connect(self.on_reply)
        self.worker.failed.connect(self.on_error)
        self.worker.status.connect(self.set_busy)
        self.worker.tool_ran.connect(lambda line: self.log(f"\U0001F527 {line}"))
        self.worker.permission_needed.connect(self.on_permission)
        self.worker.tokens_used.connect(self.on_tokens_used)
        self.worker.chunk.connect(self.on_stream_chunk)
        self.worker.preview.connect(self.on_preview)
        self.worker.handoff.connect(self.on_handoff)
        self.worker.thinking.connect(
            lambda seen: self.set_busy(f"Thinking\u2026 {seen * 8} tokens,"))
        self.worker.start()

    def set_busy(self, label):
        """Show what it is doing, with the seconds ticking.

        Only the closing reply streams; the steps before it are one blocking call each,
        and the first can run 18 seconds with nothing on screen. A counter is not
        progress, but it is the difference between waiting and wondering whether it has
        hung."""
        self.busy_label = label
        if self.busy_timer is None:
            self.bonsai.start()
            self.busy_since = time.time()
            self.busy_timer = QTimer(self)
            self.busy_timer.timeout.connect(self.tick_busy)
            self.busy_timer.start(1000)
        self.tick_busy()

    def tick_busy(self):
        if self.busy_timer is None:
            return
        self.status.setText(f"{self.busy_label} {int(time.time() - self.busy_since)}s")

    def clear_busy(self):
        if self.busy_timer is not None:
            self.busy_timer.stop()
            self.busy_timer = None
        self.bonsai.stop()
        self.status.setText("Ready")

    def on_stream_chunk(self, piece):
        """Collect the reply as it is written, and repaint on a timer.

        Re-rendering markdown for every token would spend more time parsing than the
        model spends generating, so the text accumulates and the view catches up a few
        times a second."""
        if self.stream_view is None:
            self.stream_text = ""
            self.stream_view = self.messages.begin_stream(
                load_character().get("name", "Bonsai"))
            self.stream_timer = QTimer(self)
            self.stream_timer.timeout.connect(self.flush_stream)
            self.stream_timer.start(120)
        self.stream_text += piece
        self.set_busy("Writing\u2026")

    def flush_stream(self):
        if self.stream_view is None:
            return
        self.stream_view.set_markdown(self.stream_text)
        self.messages.keep_up()

    def end_stream(self):
        """Drop the live view; the finished reply is added by on_reply, after the
        post-processing that strips REMEMBER and PROJECT tags out of it."""
        if self.stream_timer is not None:
            self.stream_timer.stop()
            self.stream_timer = None
        if self.stream_view is not None:
            block = self.stream_view.parentWidget()
            if block is not None:
                block.setParent(None)
                block.deleteLater()
        self.stream_view = None
        self.stream_text = ""

    def on_permission(self, description):
        name = load_character().get("name", "Bonsai")
        reply = QMessageBox.question(
            self, "Permission Requested",
            f"{name} wants to perform a file operation:\n\n{description}\n\nAllow this?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        self.worker.grant_permission(reply == QMessageBox.StandardButton.Yes)

    def on_reply(self, reply, trace, hit_step_limit=False, unfinished=""):
        self.end_stream()
        self.clear_busy()
        if is_record_echo(reply):
            # It wrote a transcript of work instead of doing the work: hundreds of
            # duplicated "ran X and got" lines, no tool calls at all, running to the
            # token limit mid-line. Showing it would be showing a fabrication, and
            # storing it would feed the next turn the thing it is copying.
            self.log("\u26a0 It replied with a copy of the turn record instead of doing "
                     "anything, so nothing happened this turn. The reply was discarded "
                     "rather than shown.", "orange")
            self.stop_auto("it copied the turn record back instead of working")
            reply = ("(Discarded: this turn produced a copy of the turn record rather "
                     "than any work, and no tool ran.)")
            trace, unfinished, hit_step_limit = "", "", False
        self.messages.add_assistant(reply)
        self.speak(reply)
        if unfinished:
            # The strongest signal available: the app itself refused or failed the
            # change, and nothing since made it happen.
            self.log("\u26a0 Attempted but did NOT happen, whatever the reply says:<br>"
                     + unfinished.replace("\n", "<br>"), "orange")
        if not trace and DENIES_TOOL_RE.search(plain_text(reply)):
            self.log("\u26a0 It says it can't, but it never tried - it has tools for "
                     "running commands, reading files and searching.", "orange")
        if unsearched_memory(reply, trace):
            self.log("\u26a0 It says it doesn't know, but never searched archival memory "
                     "- there are facts stored it hasn't looked at.", "orange")
        if invented_recall(reply, trace):
            self.log("\u26a0 It talks about an earlier conversation but never searched "
                     "for one. It cannot remember past chats - treat that as invented "
                     "unless you recognise it.", "orange")
        if (CLAIMED_TASK_RE.search(plain_text(reply))
                and not TASK_MUTATION_RE.search(trace or "")):
            self.log("\u26a0 It describes changing the task list, but no TASK call that "
                     "adds, closes or removes anything ran this turn - the list is "
                     "exactly as it was.", "orange")
        if not trace and CLAIMED_ACTION_RE.search(plain_text(reply)):
            self.log("\u26a0 It claims it did something, but no tool ran this turn - "
                     "nothing was actually changed on disk.", "orange")
        if same_as_last_time(reply, self.history):
            # The turn was already told mid-flight that it was repeating itself, and
            # said it again regardless. Nothing in the app can make it work, but it can
            # stop the repetition reading like a fresh answer.
            self.log("\u26a0 That is word for word what it said last turn, so nothing "
                     "moved. Tell it the single thing you want changed, or start a new "
                     "chat - repeating the request tends to get the same paragraph.",
                     "orange")
        made_up = invented_files(reply, trace)
        if made_up:
            self.log("\u26a0 It lists " + ", ".join(made_up[:5])
                     + (" and others" if len(made_up) > 5 else "")
                     + " as being there, but no tool this turn returned "
                     + ("that name" if len(made_up) == 1 else "those names")
                     + " - treat the list as invented until it runs LIST_DIR or FIND.",
                     "orange")
        else:
            missing = unverified_files(reply, trace)
            if missing:
                self.log("\u26a0 It mentions " + ", ".join(missing) + " as done, but no tool "
                         "touched " + ("that file" if len(missing) == 1 else "those files") +
                         " this turn.", "orange")
        self.status.setText("Ready")
        self.send_button.setEnabled(True)
        self.retry_button.setEnabled(True)
        self.stop_button.setEnabled(self.auto_running)
        if self.sent_prompt is None:
            return

        first = not self.history
        self.history += [{"role": "user", "content": self.sent_prompt},
                         {"role": "assistant", "content": reply}]
        if trace:
            # The next turn has to inherit the failures too, or it carries on from a
            # summary that treats them as done.
            failures = (f"\nThese were attempted and did NOT succeed:\n{unfinished}"
                        if unfinished else "")
            # Deliberately NOT part of the assistant's own message. It used to be, and a
            # model reading its own last turn saw that an assistant turn looks like a
            # record - so it started writing records instead of doing the work they were
            # meant to keep honest. Arriving from outside its voice, it is a report about
            # the turn rather than an example of how to answer.
            self.history.append(
                {"role": "user",
                 "content": f"{RECORD_HEADER}\n{narrate_trace(trace)}{failures}\n"
                            "Anything not listed as succeeding here did NOT happen.]"})
        self.history = self.history[-self.settings.get("max_history_messages", 20):]
        save_chat(self.chat_id, self.history)
        if first:
            title_chat(self.chat_id, self.sent_prompt)
        touch_chat(self.chat_id)
        self.refresh_chats()
        self.sent_prompt = None
        self.maybe_consolidate()
        if self.notes_pane.isVisible() and not self.notes_editor.document().isModified():
            self.refresh_side_panel()  # never clobber an edit in progress
        self.maybe_learn_skill(trace, unfinished)
        if not self.maybe_resume(trace, unfinished):
            self.maybe_continue(reply, trace, hit_step_limit, unfinished)
        if self.last_vision:
            # The turn's screenshot is gone once the reply lands; capture a note so the
            # next turn has continuity instead of seeing the workspace cold.
            self.screen_noter = ObserverWorker(dict(self.settings), [], record_only=True)
            self.screen_noter.failed.connect(lambda msg: None)  # best effort only
            self.screen_noter.start()

    def maybe_learn_skill(self, trace, unfinished):
        """Record the shape of a successful turn, and write it down once it recurs."""
        if unfinished or not trace or "[The user did not grant permission" in trace:
            return
        request = self.origin_prompt or self.last_prompt or ""
        signature, count = record_pattern(trace, request)
        if not count:
            return
        entry = pattern_ready(signature)
        if entry is None:
            return
        self.learner = SkillLearner(dict(self.settings), entry)
        self.learner.learned.connect(
            lambda name, description: self.log(
                f"\U0001F331 Learned a skill from doing this {entry['count']} times: "
                f"<b>{name}</b> - {description} (edit or delete it in the Skills tab)"))
        self.learner.rejected.connect(
            lambda why: self.log(f"Tried to write a skill for a repeated job but "
                                 f"discarded the draft: {why}.", "orange"))
        self.learner.failed.connect(lambda msg: self.log(msg, "orange"))
        self.learner.start()

    def maybe_consolidate(self):
        """Runs after the reply is delivered, so the user never waits on it."""
        cap = self.settings.get("core_memory_cap", 12)
        if len(load_memory()["core"]) <= cap:
            return
        self.consolidator = ConsolidationWorker(dict(self.settings))
        self.consolidator.done.connect(
            lambda kept, moved: self.log(f"\U0001F9E0 Memory tidied: {kept} core facts, "
                                         f"{moved} moved to archival."))
        self.consolidator.failed.connect(lambda msg: self.log(msg, "orange"))
        self.consolidator.start()

    def on_handoff(self, reason, summary):
        self.pending_handoff = (reason, summary)

    def maybe_resume(self, trace, unfinished=""):
        """Pick the work back up when a turn ended because it ran out, not because it
        finished. Returns whether a continuation was started.

        Auto mode already loops over the task list; this is the case auto mode does not
        cover - one ordinary request too big for a single turn. Every stop condition
        below exists because the alternative is a loop nobody asked for."""
        handoff, self.pending_handoff = self.pending_handoff, None
        if handoff is None or self.auto_running:
            return False
        reason, summary = handoff
        cap = self.settings.get("max_continuations", 3)
        if cap <= 0:
            return False
        # None of these reset the counter. send() does that, which means every stop
        # below stays stopped until a person says something - a budget that refilled
        # itself would let the same wall be hit again and again unattended.
        if "[The user did not grant permission" in (trace or ""):
            self.log("Not continuing: a permission request was denied.", "orange")
            return False
        if unfinished:
            self.log("Not continuing: a change was attempted this turn and never "
                     "landed, so another round would repeat it.", "orange")
            return False
        planned = handoff_next(summary)
        if summary == self.last_handoff or (planned and planned == self.last_next):
            # Heading for the same next action twice running means it is not short of
            # room, it is going round in circles re-orienting instead of acting.
            self.log("Stopped continuing: it ended two rounds running intending to do "
                     "the same thing without doing it, so it is stuck rather than "
                     "short of room.", "orange")
            return False
        if self.continuations >= cap:
            self.log(f"Stopped after {cap} continuations and it is still unfinished. "
                     "Say 'continue' to give it more, or raise 'Resume itself after "
                     "running out' in Settings.", "orange")
            return False

        self.last_handoff = summary
        self.last_next = planned
        self.continuations += 1
        self.log(f"\u21bb Ran out of {reason}; carrying on from where it stopped "
                 f"(continuation {self.continuations}/{cap})")
        # The original request goes back in every time. Without it a continuation has
        # only the note, and a note that says "finish what was asked" is worthless when
        # what was asked is no longer in the conversation - it read one file, found
        # nothing to do, and declared itself done.
        self.dispatch(
            "You are part-way through this request, which is still what you are doing:\n\n"
            f"{self.origin_prompt or self.last_prompt}\n\n"
            f"That turn ended early ({reason}), and this is the note you left "
            "yourself:\n\n"
            f"{summary}\n\n"
            "Do the NEXT action FIRST, before anything else. The note is your own "
            "record of where things stand, so do not spend steps listing folders or "
            "re-reading files just to re-orient - that is how a round ends having "
            "moved nothing. Only read a file you are about to edit. Do not start over "
            "and do not redo what the note says is already done. Then keep going until "
            "the request above is actually complete. Nobody is watching this round, so "
            "do not end it by asking a question; if something genuinely blocks you, "
            "say what it is.",
            vision=False, retry=False, unattended=True)
        return True

    def maybe_continue(self, reply, trace, hit_step_limit, unfinished=""):
        """Keep working across turns until the task list is clear. Every stop condition
        here exists because an unattended model with shell and file access can otherwise
        run for hours repeating the same failure."""
        if not self.auto_running:
            return

        open_tasks = [t for t in load_tasks() if not t["done"]]
        rounds_left = self.settings.get("auto_max_rounds", 10) - self.auto_round

        if not open_tasks:
            self.stop_auto("all tasks complete")
            return
        if rounds_left <= 0:
            self.stop_auto(f"hit the {self.settings.get('auto_max_rounds', 10)}-round limit")
            return
        if "[The user did not grant permission" in trace:
            self.stop_auto("a permission request was denied")
            return
        if unfinished:
            # It already had its correction round inside the turn and the change still
            # didn't land. Further rounds just narrate progress that isn't happening.
            self.stop_auto("a change was attempted and never succeeded")
            return
        if trace and trace == self.auto_last_trace:
            self.stop_auto("the last round repeated the previous one exactly")
            return
        if not trace and not hit_step_limit:
            # Answered in plain text with no tools and didn't run out of budget: it
            # believes it's finished, even though tasks remain.
            self.stop_auto("it stopped using tools while tasks are still open")
            return

        self.auto_last_trace = trace
        self.auto_round += 1
        remaining = "\n".join(f"[{t['id']}] {t['text']}" for t in open_tasks)
        self.log(f"\u21bb Continuing automatically (round {self.auto_round}"
                 f"/{self.settings.get('auto_max_rounds', 10)}) - {len(open_tasks)} task(s) left")
        self.dispatch(
            "Continue the work already in progress. For reference, these tasks are "
            "ALREADY on your list - do not add them again:\n" + remaining +
            "\nPick the first one that isn't blocked, do it with your tools, and mark it "
            "DONE by its id. If you finished one last turn without marking it, mark it "
            "now.\nYou are running unattended: nobody is there to answer a question, so "
            "do not end your turn by asking one. Choose the sensible path yourself and "
            "make the call. A task counts as done only when a tool result in this turn "
            "shows it happened - if a call was refused or failed, fix it and retry "
            "before moving on. If something genuinely blocks you, say what it is "
            "instead of retrying.",
            vision=False, retry=False, unattended=True)

    def fetch_context_size(self):
        """Ask llama.cpp what context window it actually loaded with, rather than
        assuming the -c value in docker-compose.yml is what's running."""
        url = self.settings.get("server_url", DEFAULTS["server_url"])
        try:
            response = requests.get(url.replace("/v1/chat/completions", "/props"), timeout=3)
            if response.status_code == 200:
                data = response.json()
                size = (data.get("default_generation_settings", {}).get("n_ctx")
                        or data.get("n_ctx"))
                if size:
                    self.context_size = int(size)
        except Exception:
            pass

    def on_tokens_used(self, prompt_tokens, completion_tokens):
        total = prompt_tokens + completion_tokens
        if not self.context_size:
            self.fetch_context_size()
        if not self.context_size:
            self.context_label.setText(f"context: {total / 1000:.1f}k")
            return

        share = total / self.context_size
        colour = "#888888"
        if share >= 0.9:
            colour = "#e06c5f"
        elif share >= 0.75:
            colour = "#d9a441"
        self.context_label.setStyleSheet(f"color: {colour};")
        self.context_label.setText(
            f"context: {total / 1000:.1f}k / {self.context_size / 1000:.0f}k "
            f"({share * 100:.0f}%)")
        if share >= 0.9 and not self._context_warned:
            self._context_warned = True
            self.log("\u26a0 Context is nearly full. Older messages will start being "
                     "dropped silently - start a new chat, lower Conversation memory in "
                     "Settings, or attach fewer files.", "orange")
        elif share < 0.75:
            self._context_warned = False

    def on_stop_clicked(self):
        # Stop means stop. A voice carrying on for another twenty seconds after the
        # button is the most annoying thing this feature could do.
        if self.speaker is not None:
            self.speaker.silence()
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.status.setText("Cancelling...")
            self.log("\u23f9 Cancelling - the current model call has to finish first.")
        self.stop_auto("you pressed Stop")

    def on_auto_clicked(self):
        open_tasks = [t for t in load_tasks() if not t["done"]]
        if not open_tasks:
            self.log("Nothing to do - ask Bonsai to plan the job with the TASK tool first, "
                     "then press Auto.", "orange")
            return
        self.start_auto()
        self.dispatch("Work through your open tasks now, using your tools. Mark each one "
                      "DONE as you complete it. You are running unattended, so don't ask "
                      "questions - decide and act, and treat a task as done only once a "
                      "tool result shows it actually happened.",
                      vision=False, retry=False, unattended=True)

    def start_auto(self):
        self.auto_running = True
        self.auto_round = 0
        self.auto_last_trace = None
        self.stop_button.setEnabled(True)
        self.log("\u25b6 Auto mode on - will keep working until the task list is clear.")

    def stop_auto(self, reason):
        if self.auto_running:
            self.log(f"\u23f9 Auto mode stopped: {reason}.")
        self.auto_running = False
        self.auto_round = 0
        self.stop_button.setEnabled(False)

    def on_error(self, message):
        self.end_stream()
        self.clear_busy()
        self.stop_button.setEnabled(False)
        self.stop_auto("an error occurred")
        self.log(f"<b>{message}</b>", "red", italic=False)
        self.status.setText("Error")
        self.send_button.setEnabled(True)
        self.retry_button.setEnabled(True)

    # -- screen monitor --

    def on_proactive_toggled(self):
        if self.proactive_box.isChecked():
            interval = self.settings.get("proactive_interval", 90)
            self.observer_timer = QTimer(self)
            self.observer_timer.timeout.connect(self.observer_tick)
            self.observer_timer.start(interval * 1000)
            self.log(f"--- proactive mode on (checking every {interval}s) ---")
        else:
            if self.observer_timer:
                self.observer_timer.stop()
            self.observer_timer = None
            self.log("--- proactive mode off ---")

    def on_play_toggled(self):
        if not self.play_box.isChecked():
            if self.player and self.player.isRunning():
                self.player.cancel()
            return
        # Playing needs something to play. Saying so beats starting a loop that
        # photographs an empty display sixty times.
        if handle_stage("STATUS").startswith("(no stage"):
            self.log("Nothing is on the stage - ask me to start a game there first, "
                     "then switch Play on.", "orange")
            self.play_box.blockSignals(True)
            self.play_box.setChecked(False)
            self.play_box.blockSignals(False)
            return
        self.player = PlayWorker(dict(self.settings))
        self.player.comment.connect(
            lambda text: (self.messages.add_assistant(text, unprompted=True),
                          self.speak(text, unprompted=True)))
        self.player.acted.connect(lambda line: self.log(f"\U0001F3AE {line}"))
        self.player.stopped.connect(self.on_play_stopped)
        self.player.failed.connect(self.on_play_stopped)
        self.player.start()
        self.log(f"--- playing (up to {self.settings.get('play_max_steps', 60)} steps) ---")

    def on_play_stopped(self, why):
        self.log(f"--- play stopped: {why} ---")
        self.play_box.blockSignals(True)
        self.play_box.setChecked(False)
        self.play_box.blockSignals(False)

    def on_speak_toggled(self):
        self.settings["speak_replies"] = self.speak_box.isChecked()
        save_settings(self.settings)
        if not self.speak_box.isChecked():
            if self.speaker is not None:
                self.speaker.silence()
            self.log("--- not speaking ---")
            return
        engine = available_engine()
        if engine is None:
            # Saying why beats a toggle that looks on and makes no sound.
            self.log(why_silent(), "orange")
            self.speak_box.blockSignals(True)
            self.speak_box.setChecked(False)
            self.speak_box.blockSignals(False)
            self.settings["speak_replies"] = False
            save_settings(self.settings)
            return
        self.log(f"--- speaking, using {engine} ---")

    def speak(self, text, unprompted=False):
        """Say something, if speaking is on and this kind of line is wanted."""
        if not self.settings.get("speak_replies", False):
            return
        if unprompted and not self.settings.get("speak_unprompted", True):
            return
        if self.speaker is None:
            self.speaker = Speaker()
            self.speaker.failed.connect(lambda why: self.log(why, "orange"))
        self.speaker.say(text)

    def on_listen_toggled(self):
        if not self.listen_box.isChecked():
            if self.listener is not None:
                self.listener.stop()
                self.listener.wait(2000)
                self.listener = None
            self.log("--- not listening ---")
            return
        problem = why_deaf()
        if problem:
            self.log(problem, "orange")
            self.listen_box.blockSignals(True)
            self.listen_box.setChecked(False)
            self.listen_box.blockSignals(False)
            return
        push = self.settings.get("listen_mode", "always") == "push"
        self.listener = Listener(dict(self.settings))
        self.listener.heard.connect(self.on_heard)
        self.listener.failed.connect(self.on_listen_failed)
        self.listener.ready.connect(
            lambda: self.log("\U0001F3A4 listening" + (" while held" if push else "")))
        self.listener.set_open(not push)
        self.listener.start()
        self.log("--- starting to listen (the transcriber takes a moment) ---")

    def hold_to_talk(self, down):
        """The key went down or came up. Only meaningful on push-to-talk.

        Holding the key with the microphone switched off turns it on for the duration:
        pressing talk should talk, not silently do nothing because a toggle elsewhere
        was off."""
        if self.settings.get("listen_mode", "always") != "push":
            return
        if down and not self.listen_box.isChecked():
            self.listen_box.setChecked(True)     # starts the listener, closed
        if self.listener is not None:
            self.listener.set_open(down)
            self.status.setText("Listening..." if down else "Ready")

    def on_heard(self, text):
        self.log(f"\U0001F3A4 heard: {text}")
        if self.settings.get("listen_sends", False):
            self.input.setPlainText(text)
            self.send()
            return
        # Appended, not replacing: half a typed message should survive being spoken to.
        existing = self.input.text().strip()
        self.input.setPlainText(f"{existing} {text}".strip())
        self.input.moveCursor(QTextCursor.MoveOperation.End)

    def on_listen_failed(self, why):
        self.log(why, "orange")
        self.listen_box.blockSignals(True)
        self.listen_box.setChecked(False)
        self.listen_box.blockSignals(False)
        self.listener = None

    def on_gamelink_toggled(self):
        if not self.gamelink_box.isChecked():
            if self.gamelink:
                self.gamelink.stop()
                self.gamelink = None
            self.log("--- game link off ---")
            return
        self.gamelink = NeuroServer(dict(self.settings))
        self.gamelink.listening.connect(
            lambda port: self.log(f"--- game link listening on ws://127.0.0.1:{port} ---"))
        self.gamelink.game_connected.connect(
            lambda name: self.log(f"\U0001F3AE {name} connected", "green"))
        self.gamelink.game_gone.connect(lambda name: self.log(f"\U0001F3AE {name} left"))
        self.gamelink.acted.connect(lambda line: self.log(f"\U0001F3AE {line}"))
        self.gamelink.said.connect(
            lambda text: (self.messages.add_assistant(text, unprompted=True),
                          self.speak(text, unprompted=True)))
        self.gamelink.failed.connect(self.on_gamelink_failed)
        self.gamelink.start()

    def on_gamelink_failed(self, why):
        self.log(why, "red")
        self.gamelink_box.blockSignals(True)
        self.gamelink_box.setChecked(False)
        self.gamelink_box.blockSignals(False)
        self.gamelink = None

    def observer_tick(self):
        # Never interrupt an in-flight request, a permission dialog, or the quiet period.
        if self.worker and self.worker.isRunning():
            return
        if self.observer and self.observer.isRunning():
            return
        if time.time() < self.observer_muted_until:
            return
        self.observer = ObserverWorker(dict(self.settings), list(self.observer_recent))
        self.observer.comment.connect(self.on_observer_comment)
        self.observer.quiet.connect(self.on_observer_quiet)
        self.observer.failed.connect(lambda msg: self.log(msg, "orange"))
        self.observer.start()

    def on_observer_quiet(self):
        """Silence is the expected outcome, but it needs to be distinguishable from a
        broken observer - otherwise 'nothing happened' is ambiguous."""
        if self.settings.get("proactive_verbose", True):
            self.log(f"\U0001F441 checked screen at {datetime.now():%H:%M:%S} - nothing to flag")

    def on_observer_comment(self, text):
        self.messages.add_assistant(text, unprompted=True)
        self.speak(text, unprompted=True)
        self.observer_recent = (self.observer_recent + [text])[-5:]
        self.observer_muted_until = time.time() + self.settings.get("proactive_cooldown", 600)

    def on_monitor_toggled(self):
        if self.monitor_box.isChecked():
            script = Path(self.settings.get("monitor_script", "")).expanduser()
            if not script.exists():
                self.log(f"Monitor script not found at {script} - set it in Settings.", "red")
                self.monitor_box.blockSignals(True)
                self.monitor_box.setChecked(False)
                self.monitor_box.blockSignals(False)
                return
            self.monitor = QProcess(self)
            self.monitor.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            # Drain output so the pipe can't fill; it is intentionally not displayed.
            self.monitor.readyReadStandardOutput.connect(
                lambda: self.monitor and self.monitor.readAllStandardOutput())
            self.monitor.start("fish", [str(script)])
            self.log("--- live screen monitor started ---")
        else:
            self.stop_monitor()

    def stop_monitor(self):
        if self.monitor and self.monitor.state() != QProcess.ProcessState.NotRunning:
            self.monitor.terminate()
            if not self.monitor.waitForFinished(2000):
                self.monitor.kill()
            self.log("--- live screen monitor stopped ---")
        self.monitor = None

    # -- docker --

    def compose_file(self, url=None):
        """The compose file that starts a server, or None if it has none to run.

        An endpoint with nothing configured reads as Path(""), which is the current
        directory - and a directory passes exists(), so asking "is it there" would have
        run compose against a folder. The question is whether it is a file."""
        name = (compose_for(self.settings.get("server_url") if url is None else url)
                or "").strip()
        if not name:
            return None
        path = Path(name).expanduser()
        return path if path.is_file() else None

    def compose_args(self, *extra, path=None):
        path = Path(path) if path else Path(compose_for(self.settings.get("server_url"))).expanduser()
        # --project-directory is pinned so Compose identifies the project the same way
        # regardless of the shell's working directory.
        return ["compose", "-f", str(path), "--project-directory", str(path.parent), *extra]

    def switch_servers(self, old_url):
        """Move the GPU from the server just left to the one moved to.

        Two local servers cannot both hold a card, so switching model used to mean
        stopping one by hand, switching, and starting the other. Bonsai does it in that
        order itself, and in that order specifically: starting first would meet a card
        that is still full and fail somewhere inside the allocator, which says nothing
        about what actually went wrong.

        It acts only where a compose file is configured and present, so a remote API,
        or a server run by hand, is left exactly as it is."""
        leaving = self.compose_file(old_url)
        arriving = self.compose_file()
        if leaving and arriving and leaving == arriving:
            return False        # the same server, serving a different model
        if leaving:
            self.log(f"Handing the GPU over: stopping {leaving.name}, then starting "
                     f"{arriving.name}." if arriving else
                     f"Stopping {leaving.name}, since nothing here needs the card now.")
            self.stop_docker(path=leaving, then=self.start_docker if arriving else None)
            return True
        if arriving:
            self.log(f"Starting {arriving.name} for the server you moved to.")
            self.start_docker()
            return True
        return False

    def active_services(self):
        """Compose services to run: the required ones, plus whatever is switched on,
        minus anything the chosen compose file does not define.

        Naming a service the file does not have fails the whole command with "no such
        service", so a compose file that only brings up a model server must not be
        asked for a search backend as well."""
        wanted = self.settings.get("services", DEFAULTS["services"])
        asked = [name for name, (_what, required) in DOCKER_SERVICES.items()
                 if required or wanted.get(name, False)]
        defined = compose_services(self.compose_file())
        return [name for name in asked if name in defined] if defined else asked

    def toggle_docker(self):
        self.stop_docker() if self.docker_up else self.start_docker()

    def stop_services(self, names):
        """Stop specific services without touching the rest - used when one is switched
        off in Settings, so it doesn't keep running until the next full restart."""
        names = [n for n in names if n in DOCKER_SERVICES]
        path = self.compose_file()
        if not names or path is None:
            return
        process = QProcess(self)
        self._service_stopper = process       # keep a reference past this scope
        process.start("docker", self.compose_args("stop", *names))
        self.log(f"Stopping {', '.join(names)} - switched off in Settings.")

    def start_docker(self):
        path = self.compose_file()
        if path is None:
            named = (compose_for(self.settings.get("server_url")) or "").strip()
            self.log(f"No compose file at {named or '(none set)'}, so the services for the server you are using "
         "cannot be started. Set one on that server's line in \u2699 Settings "
         "\u2192 Model, or set Docker compose file under Services.", "red")
            self.docker_button.setEnabled(True)
            return
        self.status.setText("Starting Docker services...")
        self.docker_button.setEnabled(False)
        self.docker = QProcess(self)
        self.docker.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.docker.finished.connect(self.on_docker_started)
        self.docker.errorOccurred.connect(
            lambda e: self.log(f"Docker error: {e}. Is docker installed and in PATH?", "red"))
        services = self.active_services()

        skipped = [n for n in DOCKER_SERVICES if n not in services]
        if skipped:
            self.log(f"Not starting {', '.join(skipped)} - switched off in \u2699 Settings.")
        self.docker.start("docker", self.compose_args("up", "-d", *services))

    def on_docker_started(self, code, _status):
        output = self.docker.readAllStandardOutput().data().decode(errors="replace")
        self.docker_button.setEnabled(True)
        if code != 0:
            self.log(f"Failed to start Docker services (exit {code}):<br>{output}", "red")
            self.status.setText("Docker start failed")
            return
        self.docker_up = True
        self.docker_button.setText("Stop Docker Services")
        self.log("--- docker services starting, waiting for model ---")
        self.health_elapsed = 0
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.poll_health)
        self.health_timer.start(3000)
        self.poll_health()

    def poll_health(self):
        url = self.settings.get("server_url", DEFAULTS["server_url"])
        try:
            if requests.get(url.replace("/v1/chat/completions", "/health"), timeout=2).status_code == 200:
                self.health_timer.stop()
                self.status.setText("Ready")
                self.log("--- model server is ready ---")
                self.fetch_context_size()
                return
        except Exception:
            pass
        self.health_elapsed += 3
        self.status.setText(f"Waiting for model to load... ({self.health_elapsed}s)")
        if self.health_elapsed >= 180:
            self.health_timer.stop()
            self.status.setText("Model server not responding")
            self.log("Model server didn't respond in 3 minutes. Check "
                     "`docker compose logs bonsai-api`.", "red")

    def stop_docker(self, blocking=False, path=None, then=None):
        """Stop a server's services. `path` names one other than the server in use,
        which is how a handover stops the server being left rather than the new one;
        `then` is what to do once it has actually let go."""
        path = Path(path) if path else self.compose_file()
        if path is None:
            return
        if self.health_timer:
            self.health_timer.stop()
        self.status.setText("Stopping Docker services...")
        process = QProcess(self)
        # Every service this file defines, not just the enabled ones, so one switched
        # off while running does not get orphaned - but only the ones it defines.
        # Naming an absent service fails the whole command, which is how Stop came to
        # report an error and leave everything running.
        known = compose_services(path) or list(DOCKER_SERVICES)
        process.start("docker", self.compose_args("stop", *known, path=path))
        if blocking:
            process.waitForFinished(15000)
            self.docker_up = False
            return
        self._stopper = process  # keep a reference so it isn't garbage collected
        process.finished.connect(
            lambda code, status: self.on_docker_stopped(code, status, then))
        self.docker_button.setEnabled(False)

    def on_docker_stopped(self, code, _status, then=None):
        self.docker_button.setEnabled(True)
        self.docker_up = False
        self.docker_button.setText("Start Docker Services")
        self.status.setText("Docker services stopped" if code == 0 else "Docker stop had errors")
        if code == 0:
            self.log("--- docker services stopped ---")
        if then and code == 0:
            QTimer.singleShot(0, then)
        elif then:
            self.status.setText("Handover stopped")
            self.log("The server you left did not stop, so it is still holding the card. "
                     "The new one has NOT been started - it would fail on memory. Stop "
                     "the old one by hand, then press Start Docker Services.", "red")

    def closeEvent(self, event):
        if self.observer_timer:
            self.observer_timer.stop()
        self.stop_monitor()
        # A process started from here is ours to clean up. Leaving a game or a dev
        # server running after the window is gone is not a background task, it is a leak
        # nobody can see to stop.
        for name in stop_all_background():
            self.log(f"Stopped background process {name}.")
        if self.speaker is not None:
            self.speaker.stop()
            self.speaker.wait(1500)
        if self.listener is not None:
            self.listener.stop()
            self.listener.wait(2000)
        if getattr(self, "ptt", None) is not None:
            self.ptt.stop()
            self.ptt.wait(1000)
        closed = stop_stage()
        if closed:
            self.log(f"Closed the stage on {closed}.")
        self.stop_docker(blocking=True)
        event.accept()


def main():
    """Entry point for `bonsai`, `python -m bonsai`, and running this file."""
    app = QApplication(sys.argv)
    app.setApplicationName("Bonsai")
    window = Bonsai()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())