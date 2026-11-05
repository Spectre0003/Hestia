"""
Hestia — window front end (Stage 10 / v1.0)

The desktop way to talk to Hestia: a chat window (PySide6 / Qt). The
engine (engine.py) runs on its own worker thread, which owns the Session,
the database and the tool servers, so the window never freezes while
Hestia thinks or a tool runs, and nothing touches the database across
threads.

The engine's UI object is a Bridge: it forwards everything to the window
through Qt signals. Approval is the delicate part — the worker must wait
for your click without blocking the window, and if Hestia quits while a
dialog is open, the answer is Deny, never a hang and never an approval.

    venv\\Scripts\\pythonw gui.py
"""

import getpass
import json
import math
import os
import random
import re
import sys
import threading
import unicodedata

from PySide6.QtCore import QEasingCurve, QObject, QPointF, QPropertyAnimation, QSettings, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
                           QRadialGradient, QTextDocument)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QMenu, QPlainTextEdit, QToolBar, QPushButton, QScrollArea, QSystemTrayIcon, QVBoxLayout, QWidget,
)

import engine
import voice

_HERE = os.path.dirname(os.path.abspath(__file__))

# One Hestia per Windows user: a second launch finds the first through this
# local socket name, asks it to show its window, and exits.
INSTANCE_NAME = f"hestia-{getpass.getuser()}"

# "Start with Windows" is a shortcut in the user's own Startup folder —
# per-user, no admin, and visible/removable in File Explorer.
LOGO_PATH = os.path.join(_HERE, "assets", "hestia.png")
ICON_PATH = os.path.join(_HERE, "data", "hestia.ico")  # for shortcuts, made from the logo
STARTUP_SHORTCUT = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                                "Start Menu", "Programs", "Startup", "Hestia.lnk")

# A spoken reply longer than this is read out up to the end of the sentence
# that crosses it; then she asks before going on. The window shows it all.
SPOKEN_WORD_LIMIT = 80
MORE_QUESTION = "There's more to it — want me to read the rest, or sum it up?"
SUMMARY_REQUEST = "Sum up the rest of your last answer in two or three sentences — I'm listening, not reading."
_WANTS_SUMMARY = re.compile(r"\b(sum|summar\w*|short|shorter|brief|gist|tl;?dr)\b", re.IGNORECASE)
_WANTS_NOTHING = re.compile(r"\b(no|nope|nah|skip( it)?|that's (fine|enough|ok(ay)?))\b", re.IGNORECASE)
_WANTS_REST = re.compile(r"\b(read|rest|all|full|whole|continue|go on|carry on|yes|yeah|yep|sure|ok(ay)?)\b",
                         re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")

# Said on her way out of a conversation ("Fall Hestia").
FAREWELLS = ("Okay.", "Alright, I'll be here.", "Sure. Talk later.", "Okay — I'm around if you need me.")

# Said on their own in a conversation, these just stop her talking.
STOP_WORDS = {"stop", "cancel", "never mind", "nevermind", "be quiet", "quiet", "shush", "that's enough"}

IDLE_CHECK_MS = 60_000
RENDER_EVERY_MS = 50  # streaming re-renders are batched, not one per token

FONT = "Yu Gothic UI"          # clean, and from the same Japanese design family as the logo
CODE_FONT = "Cascadia Mono"
BUBBLE_SHARE = 0.78            # a message bubble is at most this much of the transcript's width

# Her state, shown by the orb in the window and the tray icon's colour.
STATE_COLOURS = {"starting": "#6c7380", "ready": "#e07a3f", "listening": "#3ecf8e",
                 "thinking": "#f0b44c", "speaking": "#5aa7ff", "muted": "#6c7380"}
STATE_LABELS = {"starting": "Starting…", "ready": "Say “Rise Hestia”", "listening": "Listening…",
                "speaking": "Speaking…",
                "thinking": "Thinking…", "muted": "Microphone muted"}

APP_STYLE = """
QMainWindow, QWidget#central, QDialog, QMessageBox { background: #14161b; color: #e6e8ec; }
QLabel { color: #e6e8ec; }
QLabel#title { font-size: 18pt; font-weight: 600; letter-spacing: 1px; }
QLabel#state { color: #8a919e; font-size: 10pt; }
QScrollArea#transcript { background: #181b22; border: 1px solid #222731; border-radius: 18px; }
QWidget#transcriptInner { background: #181b22; }
QLabel#you { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #c8642c, stop:1 #e0843f);
             color: #fff7f0; border-radius: 16px; padding: 10px 15px; font-size: 11pt; }
QLabel#hestia { background: #242934; color: #eceef2; border: 1px solid #2e3441; border-radius: 16px;
                padding: 10px 15px; font-size: 11pt; }
QLabel#speaker { color: #7d8594; font-size: 8.5pt; padding: 0 6px; }
QLabel#notice { color: #8a919e; font-size: 9pt; font-style: italic; padding: 2px 30px; }
QLabel#output { background: #11141a; color: #c9d1dc; border: 1px solid #262c37; border-radius: 12px;
                padding: 12px 14px; font-family: "Cascadia Mono", Consolas, monospace; font-size: 9.5pt; }
QLabel#welcome { color: #8a919e; font-size: 12pt; }
QPlainTextEdit { background: #1b1f27; border: 1px solid #2c3340; border-radius: 10px; color: #e6e8ec; }
QLineEdit { background: #1f242d; border: 1px solid #2c3340; border-radius: 14px; padding: 10px 18px;
            color: #e6e8ec; font-size: 11pt; selection-background-color: #e07a3f; }
QLineEdit:focus { border: 1px solid #e07a3f; }
QLineEdit:disabled { color: #6c7380; }
QPushButton { background: #262c37; color: #e6e8ec; border: none; border-radius: 16px; padding: 8px 18px; }
QPushButton:hover { background: #303846; }
QPushButton#send { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e07a3f, stop:1 #f0a35e);
                   color: white; font-weight: 600; border-radius: 14px; padding: 11px 24px; }
QPushButton#send:hover { background: #f0a35e; }
QPushButton#send:disabled { background: #303846; color: #6c7380; }
QToolBar { background: transparent; border: none; spacing: 6px; }
QToolButton { background: #1f242d; color: #c9ccd3; border: 1px solid #2c3340; border-radius: 13px; padding: 5px 13px; }
QToolButton:hover { background: #2a303b; border-color: #e07a3f; }
QToolButton:checked { background: #e07a3f; color: white; border-color: #e07a3f; }
QStatusBar { background: #14161b; color: #7d8594; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 6px 2px; }
QScrollBar::handle:vertical { background: #343b48; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #e07a3f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical, QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical { height: 0; background: none; }
QMenu { background: #1b1f27; color: #e6e8ec; border: 1px solid #2c3340; padding: 4px; }
QMenu::item { padding: 6px 22px; border-radius: 6px; }
QMenu::item:selected { background: #e07a3f; color: white; }
"""


def _visible(value):
    """
    A tool argument as it should be shown for approval: JSON, so control
    characters come out as \\u escapes, plus invisible formatting characters
    (right-to-left marks and the like), which JSON leaves alone and which
    can make text read differently from what it is.
    """
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return "".join(f"\\u{ord(ch):04x}" if unicodedata.category(ch) == "Cf" else ch for ch in text)


class ApprovalDialog(QDialog):
    """Asks before a write-tier tool runs. Deny is the default; Esc and closing both deny."""

    def __init__(self, persona, tool_name, arguments, after_web, parent=None):
        super().__init__(parent)
        self.tool_name = tool_name
        self.setWindowTitle(f"{persona} wants to run {tool_name}")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"<b>{persona}</b> wants to run <b>{tool_name}</b> with:"))

        self.warning = QLabel("Asked for after reading web content this turn — make sure "
                              "it's what YOU asked for, not something a page told Hestia to do.")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #c0392b; font-weight: bold;")
        self.warning.setVisible(after_web)
        layout.addWidget(self.warning)

        self.arguments_view = QPlainTextEdit(
            "\n".join(f"{key}: {_visible(value)}" for key, value in arguments.items()) or "(no arguments)")
        self.arguments_view.setReadOnly(True)
        layout.addWidget(self.arguments_view)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.allow_button = QPushButton("Allow")
        self.deny_button = QPushButton("Deny")
        self.allow_button.setAutoDefault(False)
        self.deny_button.setDefault(True)
        self.allow_button.clicked.connect(self.accept)
        self.deny_button.clicked.connect(self.reject)
        buttons.addWidget(self.deny_button)
        buttons.addWidget(self.allow_button)
        layout.addLayout(buttons)
        self.deny_button.setFocus()


class Bridge(QObject):
    """
    The engine's UI object. Its methods are called on the worker thread;
    each one emits a signal the window handles on the main thread.
    approve/confirm then wait for answer(), or for shutdown().
    """
    text_received = Signal(str)
    tool_started = Signal(str)
    notice_posted = Signal(str)
    approval_requested = Signal(str, object, bool)
    question_asked = Signal(str)

    def __init__(self):
        super().__init__()
        self._answered = threading.Event()
        self._answer = False
        self._closing = False

    # The engine's UI protocol (worker thread).
    def text(self, token):
        self.text_received.emit(token)

    def tool(self, name):
        self.tool_started.emit(name)

    def notice(self, message):
        self.notice_posted.emit(message)

    def approve(self, name, arguments, after_web=False):
        return self._ask(self.approval_requested, name, arguments, after_web)

    def confirm(self, question):
        return self._ask(self.question_asked, question)

    def _ask(self, signal, *args):
        if self._closing:
            return False
        self._answer = False
        self._answered.clear()
        signal.emit(*args)
        self._answered.wait()
        return self._answer and not self._closing

    # Main thread.
    def answer(self, value):
        self._answer = bool(value)
        self._answered.set()

    def shutdown(self):
        """Hestia is quitting: whatever is still being asked is answered No."""
        self._closing = True
        self._answer = False
        self._answered.set()


class Ears(QObject):
    """Carries the listener's callbacks (its own threads) to the window (the main thread)."""
    wake = Signal()
    text = Signal(str)
    command = Signal(str)


class Orb(QWidget):
    """A glowing circle in her state's colour: it breathes while listening, spins while thinking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 46)
        self.state, self.phase = "starting", 0.0
        self._timer = QTimer(self, interval=33)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state):
        self.state = state
        # Only animates when there's something to show: idle costs no CPU.
        self._timer.start() if state in ("listening", "thinking", "speaking") else self._timer.stop()
        self.update()

    def _tick(self):
        self.phase = (self.phase + 0.05) % (2 * math.pi)
        self.update()

    def paintEvent(self, event):
        colour = QColor(STATE_COLOURS[self.state])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre, glow = self.rect().center(), 21
        if self.state == "listening":
            glow = 17 + 4 * (1 + math.sin(self.phase * 2)) / 2
        elif self.state == "speaking":
            glow = 16 + 6 * abs(math.sin(self.phase * 6))  # quicker, like a voice
        halo = QRadialGradient(centre, glow)
        halo.setColorAt(0.0, colour)
        fade = QColor(colour)
        fade.setAlpha(0)
        halo.setColorAt(1.0, fade)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(centre, glow, glow)
        painter.setBrush(colour)
        painter.drawEllipse(centre, 10, 10)
        if self.state == "thinking":
            painter.setPen(QPen(colour, 3, Qt.SolidLine, Qt.RoundCap))
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(self.rect().adjusted(5, 5, -5, -5), int(-math.degrees(self.phase * 2) * 16), 100 * 16)
        painter.end()


class Worker(QObject):
    """Owns the engine Session; every slot runs on the worker thread."""
    ready = Signal(str, list)
    failed = Signal(str)
    done = Signal(str, bool)

    def __init__(self, bridge, session_factory):
        super().__init__()
        self.bridge = bridge
        self.session_factory = session_factory
        self.session = None

    @Slot()
    def start(self):
        try:
            self.session = self.session_factory(self.bridge)
        except engine.StartupError as e:
            self.failed.emit(str(e))
            return
        self.ready.emit(self.session.name, list(self.session.problems))

    @Slot(str)
    def handle(self, text):
        try:
            output, was_command = self.session.handle(text)
        except Exception as e:
            # The window must never be left waiting: there's no console to show
            # the error when Hestia runs in the background, so it goes on screen.
            self.bridge.notice(f"[Something went wrong: {e}]")
            output, was_command = "", True
        self.done.emit(output, was_command)

    @Slot()
    def check_idle(self):
        if self.session:
            self.session.close_if_idle()

    @Slot()
    def close(self):
        if self.session:
            self.session.close()
        QThread.currentThread().quit()


def _markdown_html(text):
    """
    Markdown as HTML, converted by Qt with raw HTML switched off: whatever a
    reply or a web page put in the text comes out escaped, never as markup.
    """
    doc = QTextDocument()
    doc.setMarkdown(text, QTextDocument.MarkdownFeature(
        QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML))
    html = doc.toHtml()
    return html[html.index(">", html.index("<body")) + 1:html.rindex("</body>")]


_TOOL_MARK = re.compile(r"`\[tool: (\w+)\]` ?")


def _tool_note(text):
    """The engine's `[tool: name]` marks, as one quiet line at the top of her bubble."""
    used = _TOOL_MARK.findall(text)
    if not used:
        return text
    names = " · ".join(dict.fromkeys(name.replace("_", " ") for name in used))
    return f"*⚙ {names}*\n\n" + _TOOL_MARK.sub("", text).lstrip()


class Transcript(QScrollArea):
    """The conversation as chat bubbles: yours on the right, hers on the left."""
    link_clicked = Signal(QUrl)

    def __init__(self):
        super().__init__()
        self.setObjectName("transcript")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transcriptInner")
        self._rows = QVBoxLayout(inner)
        self._rows.setContentsMargins(18, 18, 18, 18)
        self._rows.setSpacing(8)
        self._welcome = QLabel()
        self._welcome.setObjectName("welcome")
        self._welcome.setAlignment(Qt.AlignCenter)
        self._welcome.setTextFormat(Qt.RichText)
        self._welcome.setText(f'<img src="{LOGO_PATH}" width="96" height="96"><br><br>'
                              "Say <b>“Rise Hestia”</b> to talk, or type below.")
        self._rows.addStretch(1)
        self._rows.addWidget(self._welcome)
        self._rows.addStretch(1)
        self.setWidget(inner)
        self._shown = []    # (kind, text) on screen, in order
        self._bubbles = []  # the label showing each
        # New messages land at the bottom: follow them.
        self.verticalScrollBar().rangeChanged.connect(lambda low, high: self.verticalScrollBar().setValue(high))

    def show_messages(self, entries, name):
        if len(entries) < len(self._shown) or entries[:len(self._shown)][:-1] != self._shown[:-1]:
            self._clear()
        for index, (kind, text) in enumerate(entries):
            if index < len(self._shown):
                if self._shown[index] != (kind, text):
                    self._fill(self._bubbles[index], kind, text)
                    self._shown[index] = (kind, text)
            else:
                self._add(kind, text, name)
        self._welcome.setVisible(not entries)
        self._rows.setStretch(0, 0 if entries else 1)  # messages from the top; the welcome centred

    def _clear(self):
        while self._rows.count() > 3:  # stretch, welcome, stretch stay
            item = self._rows.takeAt(2)
            if item.layout():
                while item.layout().count():
                    widget = item.layout().takeAt(0).widget()
                    if widget:
                        widget.deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        self._shown, self._bubbles = [], []

    def _add(self, kind, text, name):
        bubble = QLabel()
        bubble.setObjectName(kind)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextBrowserInteraction)
        bubble.setOpenExternalLinks(False)
        bubble.linkActivated.connect(lambda link: self.link_clicked.emit(QUrl(link)))
        row = QVBoxLayout()
        row.setSpacing(3)
        line = QHBoxLayout()
        if kind == "you":
            line.addStretch(1)
            line.addWidget(bubble)
        elif kind == "hestia":
            caption = QLabel(name)
            caption.setObjectName("speaker")
            row.addWidget(caption)
            line.addWidget(bubble)
            line.addStretch(1)
        else:
            bubble.setAlignment(Qt.AlignCenter if kind == "notice" else Qt.AlignLeft)
            line.addWidget(bubble)
        row.addLayout(line)
        self._rows.insertLayout(self._rows.count() - 1, row)
        self._shown.append((kind, text))
        self._bubbles.append(bubble)
        self._fill(bubble, kind, text)

    def _fill(self, bubble, kind, text):
        if kind in ("you", "hestia"):
            if kind == "hestia":
                text = _tool_note(text)
            bubble.setTextFormat(Qt.RichText)
            bubble.setText(_markdown_html(text))
            self._fit(bubble)
        else:  # notices and command output are shown exactly as they are
            bubble.setTextFormat(Qt.PlainText)
            bubble.setText(text)

    def _fit(self, bubble):
        """As wide as its text needs, up to BUBBLE_SHARE of the transcript."""
        widest = int(self.viewport().width() * BUBBLE_SHARE)
        bubble.ensurePolished()  # so its font is the stylesheet's, not the default
        doc = QTextDocument()
        doc.setDefaultFont(bubble.font())
        doc.setHtml(bubble.text())
        doc.setTextWidth(widest - 32)
        bubble.setFixedWidth(min(widest, int(doc.idealWidth()) + 40))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for (kind, _), bubble in zip(self._shown, self._bubbles):
            if kind in ("you", "hestia"):
                self._fit(bubble)

    def toPlainText(self):
        return "\n".join(text for _, text in self._shown)


class ChatWindow(QMainWindow):
    """The chat window. The engine is started on a worker thread as soon as it's built."""
    _start_engine = Signal()
    _submit = Signal(str)
    _check_idle = Signal()
    _stop_engine = Signal()
    hidden_to_tray = Signal()
    state_changed = Signal(str)    # a colour, for the tray icon
    mute_requested = Signal()      # "Mute Hestia" was said

    def __init__(self, session_factory=engine.Session, speaker=None, settings=None, listener=None, chime=None):
        """
        speaker: a voice.Speaker, or None for a silent window. settings: a
        QSettings to remember choices in. listener: a voice.Listener, stopped
        on quit. chime: called when the wake word is heard.
        """
        super().__init__()
        self.speaker = speaker
        self.listener = listener
        self.chime = chime
        self.settings = settings
        self.speaking_turn = False  # whether the reply now streaming is being spoken
        self._spoken_text = ""      # what of it has gone to the speaker
        self._held_back = None      # past the word limit: the part not spoken yet
        self._rest = None           # held back, waiting for "read the rest" or "sum it up"
        self.name = "Hestia"
        self.ready = False
        self.busy = False
        self.transcript = []  # (kind, text): you / hestia / notice / output
        self.state = "starting"
        self.in_conversation = False  # from "Rise Hestia" until "Fall Hestia"
        self.setWindowTitle("Hestia")
        self.resize(780, 680)

        self.transcript_view = Transcript()
        self.transcript_view.link_clicked.connect(self.open_link)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message Hestia — or type memories, tools, new, remember …, forget …")
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("send")
        self.send_button.setCursor(Qt.PointingHandCursor)
        self.send_button.clicked.connect(self.send)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self.input)
        row.addWidget(self.send_button)

        self.orb = Orb()
        self.title = QLabel("Hestia")
        self.title.setObjectName("title")
        self.state_label = QLabel()
        self.state_label.setObjectName("state")
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(self.title)
        names.addWidget(self.state_label)
        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(self.orb)
        header.addLayout(names)
        header.addStretch()
        self._header = header

        central = QWidget()
        central.setObjectName("central")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(18, 10, 18, 8)
        layout.setSpacing(12)
        layout.addLayout(header)
        layout.addWidget(self.transcript_view)
        layout.addLayout(row)
        self.setCentralWidget(central)

        toolbar = QToolBar("Hestia")  # sits in the header, beside her name
        toolbar.setMovable(False)
        for label, command in (("New session", "new"), ("Memories", "memories"), ("Tool log", "tools")):
            action = QAction(label, self)
            action.triggered.connect(lambda checked=False, c=command: self.send(c))
            toolbar.addAction(action)
        toolbar.addSeparator()
        self.speak_all = QAction("Speak all replies", self, checkable=True)
        self.speak_all.setToolTip("Replies to what you say are always spoken; this speaks typed ones too.")
        self.speak_all.setChecked(bool(settings and settings.value("speak_all", False, type=bool)))
        self.speak_all.toggled.connect(lambda on: settings and settings.setValue("speak_all", on))
        self.speak_all.setEnabled(speaker is not None)
        toolbar.addAction(self.speak_all)
        self.stop_speaking = QAction("Stop speaking", self)
        self.stop_speaking.setShortcut(QKeySequence(Qt.Key_Escape))
        self.stop_speaking.triggered.connect(self._hush)
        self.stop_speaking.setEnabled(speaker is not None)
        toolbar.addAction(self.stop_speaking)
        self.toolbar = toolbar
        header.addWidget(toolbar)

        self._render_timer = QTimer(self, singleShot=True, interval=RENDER_EVERY_MS)
        self._render_timer.timeout.connect(self._render)
        self._set_busy(True, "Starting Hestia…")

        self.bridge = Bridge()
        self.bridge.text_received.connect(self._on_text)
        self.bridge.tool_started.connect(self._on_tool)
        self.bridge.notice_posted.connect(self._on_notice)
        self.bridge.approval_requested.connect(self._on_approval)
        self.bridge.question_asked.connect(self._on_question)

        self.thread = QThread(self)
        self.worker = Worker(self.bridge, session_factory)
        self.worker.moveToThread(self.thread)
        self._start_engine.connect(self.worker.start)
        self._submit.connect(self.worker.handle)
        self._check_idle.connect(self.worker.check_idle)
        self._stop_engine.connect(self.worker.close)
        self.worker.ready.connect(self._on_ready)
        self.worker.failed.connect(self._on_failed)
        self.worker.done.connect(self._on_done)
        self.thread.start()
        self._start_engine.emit()

        # The speaker talks on its own threads; this keeps the orb in step with it.
        self._state_timer = QTimer(self, interval=200)
        self._state_timer.timeout.connect(self._refresh_state)
        self._state_timer.start()

        self._idle_timer = QTimer(self, interval=IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._check_idle.emit)
        self._idle_timer.start()

    # ---- sending -------------------------------------------------------
    def send(self, text=None, spoken=False, asking=None):
        """asking: what the engine is sent, when it isn't what's shown (a summary request)."""
        text = (self.input.text() if text is None else text).strip()
        if not text or not self.ready or self.busy:
            return
        self._hush()  # a new message cuts off whatever she was saying
        self.speaking_turn = bool(self.speaker) and (spoken or self.speak_all.isChecked())
        self._spoken_text, self._held_back, self._rest = "", None, None
        self.input.clear()
        if text.lower() not in ("memories", "tools", "new"):
            self.transcript.append(("you", text))
        self._render()
        self._set_busy(True, "Thinking…")
        self._submit.emit(asking or text)

    def send_spoken(self, text):
        """A turn that came from the microphone: its reply is spoken."""
        self.send(text, spoken=True)

    # ---- what the microphone hears (main thread) -------------------------
    def awake(self):
        """For the listener (its own thread): is what's said now a turn? Only in a
        conversation, and only while she's neither thinking nor talking."""
        return (self.in_conversation and self.ready and not self.busy
                and not (self.speaker and self.speaker.speaking))

    @Slot()
    def heard_wake(self):
        """"Rise Hestia": cut her off, and talk until "Fall Hestia"."""
        self._hush()
        if self.chime:
            self.chime()
        self.in_conversation = True
        self.statusBar().showMessage("Talk away — say “Fall Hestia” to end the conversation.", 6000)
        self._refresh_state()

    @Slot(str)
    def heard_command(self, name):
        """"Fall Hestia": end the conversation. "Mute Hestia": that, and stop listening."""
        self._hush()
        was_talking, self.in_conversation = self.in_conversation, False
        if name == "mute":
            self.mute_requested.emit()
        else:
            if was_talking and self.speaker:
                self.speaker.say(random.choice(FAREWELLS))
            self.statusBar().showMessage("Conversation ended — say “Rise Hestia” to start again.", 4000)
        self._refresh_state()

    @Slot(str)
    def heard_text(self, text):
        if text.lower().strip(" .!?,") in STOP_WORDS:
            self._hush()
            self._rest = None
            self.statusBar().showMessage("Okay.", 3000)
            return
        if self.busy or not self.ready:
            # Never queued: a spoken "yes" must not answer an open approval dialog.
            self.statusBar().showMessage("Still on the last request — ask again in a moment.", 4000)
            return
        if self._rest and self._answer_more(text):
            return
        self.send_spoken(text)

    def _answer_more(self, text):
        """The answer to MORE_QUESTION, if that's what this is. True if it was handled."""
        rest, self._rest = self._rest, None
        if _WANTS_SUMMARY.search(text):
            self.send(text, spoken=True, asking=SUMMARY_REQUEST)
        elif _WANTS_NOTHING.search(text):
            self.speaker.say("Okay.")
        elif _WANTS_REST.search(text):
            self._hush()
            self.speaker.feed(rest)
            self.speaker.flush()
        else:
            return False  # a new question: the rest is dropped
        return True

    def _idle_state(self):
        if self.listener and self.listener.muted:
            return "muted"
        if self.speaker and self.speaker.speaking:
            return "speaking"
        return "listening" if self.in_conversation else "ready"

    def _refresh_state(self):
        if self.ready and not self.busy:
            state = self._idle_state()
            if state != self.state:
                self.set_state(state)

    def set_state(self, state):
        self.state = state
        self.orb.set_state(state)
        self.state_label.setText(STATE_LABELS[state] if state != "ready" or self.listener else "Ready")
        self.state_changed.emit(STATE_COLOURS[state])

    def _hush(self):
        if self.speaker:
            self.speaker.stop()

    def _set_busy(self, busy, status=""):
        self.busy = busy
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.statusBar().showMessage(status or ("Ready" if not busy else ""))
        if hasattr(self, "orb"):
            self.set_state("thinking" if busy and self.ready else
                           "starting" if busy else self._idle_state())
        if not busy:
            self.input.setFocus()

    # ---- what the engine sends (main thread) ----------------------------
    def _hestia_entry(self):
        if not self.transcript or self.transcript[-1][0] != "hestia":
            self.transcript.append(("hestia", ""))
        return len(self.transcript) - 1

    @Slot(str)
    def _on_text(self, token):
        index = self._hestia_entry()
        self.transcript[index] = ("hestia", self.transcript[index][1] + token)
        if self.speaking_turn:
            self._speak(token)
        self._render_timer.start()

    def _speak(self, token):
        """Feed the speaker until SPOKEN_WORD_LIMIT words and the end of that sentence; hold back the rest."""
        if self._held_back is not None:
            self._held_back += token
            return
        end = None
        if len((self._spoken_text + token).split()) >= SPOKEN_WORD_LIMIT:
            end = _SENTENCE_END.search(token)
        if end:
            self.speaker.feed(token[:end.end()])
            self._held_back = token[end.end():]
        else:
            self.speaker.feed(token)
        self._spoken_text += token if not end else token[:end.end()]

    @Slot(str)
    def _on_tool(self, name):
        index = self._hestia_entry()
        self.transcript[index] = ("hestia", self.transcript[index][1] + f"`[tool: {name}]` ")
        self.statusBar().showMessage(f"Running {name}…")
        self._render_timer.start()

    @Slot(str)
    def _on_notice(self, message):
        self.transcript.append(("notice", message))
        self._render_timer.start()

    @Slot(str, object, bool)
    def _on_approval(self, tool_name, arguments, after_web):
        if self.speaking_turn:
            # Approvals are never given by voice: a misheard "yes", or one
            # from a video playing nearby, must not approve anything.
            self.speaker.say("I need you to approve that on screen.")
        # Just the dialog, on top — the main window stays in the tray.
        dialog = ApprovalDialog(self.name, tool_name, arguments, after_web)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.bridge.answer(dialog.exec() == QDialog.Accepted)

    @Slot(str)
    def _on_question(self, question):
        box = QMessageBox(QMessageBox.Question, self.name, question, QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.bridge.answer(box.exec() == QMessageBox.Yes)

    @Slot(str, list)
    def _on_ready(self, name, problems):
        self.name = name
        self.setWindowTitle(name)
        self.title.setText(name)
        for problem in problems:
            self.transcript.append(("notice", f"[Tool server problem — {problem}. Its tools are unavailable this run.]"))
        self.ready = True
        self._render()
        self._set_busy(False)

    @Slot(str)
    def _on_failed(self, error):
        self.transcript.append(("notice", f"[Hestia couldn't start: {error}]"))
        self._render()
        self.statusBar().showMessage("Couldn't start — see the message above.")

    @Slot(str, bool)
    def _on_done(self, output, was_command):
        if was_command and output:
            self.transcript.append(("output", output))
        elif self.speaking_turn:
            held, self._held_back = self._held_back, None
            if held and len(held.split()) >= 8:
                self.speaker.flush()
                self.speaker.say(MORE_QUESTION)
                self._rest = held
            else:  # only a few words more: just say them
                self.speaker.feed(held or "")
                self.speaker.flush()
        self._render()
        self._set_busy(False)

    # ---- display --------------------------------------------------------
    def _render(self):
        self._render_timer.stop()
        self.transcript_view.show_messages(list(self.transcript), self.name)

    @Slot(QUrl)
    def open_link(self, url):
        """Only web links open, in the browser; file:, javascript: and the rest do nothing."""
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def show_window(self):
        appearing = not self.isVisible()
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if appearing:  # a quick fade in
            self.setWindowOpacity(0.0)
            self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=180,
                                            startValue=0.0, endValue=1.0, easingCurve=QEasingCurve.OutCubic)
            self._fade.start()

    # ---- quitting -------------------------------------------------------
    def quit(self):
        """Really quit: end the session, stop the engine, close the app."""
        self.shutdown()
        QApplication.instance().quit()

    def shutdown(self):
        """Close the session (indexing it) and stop the worker; a pending approval is denied."""
        if not self.thread.isRunning():
            return
        self._idle_timer.stop()
        if self.listener:
            self.listener.stop()
        if self.speaker:
            self.speaker.close()
        self.bridge.shutdown()
        self._stop_engine.emit()
        self.thread.wait(30_000)

    def closeEvent(self, event):
        """Closing the window hides it: Hestia keeps running in the tray until Quit."""
        if self.thread.isRunning():
            event.ignore()
            self.hide()
            self.hidden_to_tray.emit()
        else:
            super().closeEvent(event)


def hand_off(name=None):
    """If a Hestia is already running, ask it to show itself and return True."""
    name = name or INSTANCE_NAME  # read at call time, not frozen at import
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(500):
        return False
    socket.write(b"show")
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    return True


class InstanceServer(QLocalServer):
    """Listens for later launches and shows the window when one arrives."""

    def __init__(self, name, show):
        super().__init__()
        self._show = show
        QLocalServer.removeServer(name)  # a stale name left by a crash
        self.newConnection.connect(self._on_connection)
        self.listen(name)

    def _on_connection(self):
        while self.hasPendingConnections():
            self.nextPendingConnection().close()
            self._show()


def starts_with_windows():
    return os.path.exists(STARTUP_SHORTCUT)


def set_start_with_windows(enabled):
    """Add or remove the Startup-folder shortcut that runs Hestia, windowless, at login."""
    if not enabled:
        if os.path.exists(STARTUP_SHORTCUT):
            os.remove(STARTUP_SHORTCUT)
        return
    _write_shortcut(STARTUP_SHORTCUT, "--background")


def make_desktop_shortcut():
    """A Hestia icon on the desktop: opens her window (and starts her, Ollama included, if needed)."""
    import win32com.client
    path = os.path.join(win32com.client.Dispatch("WScript.Shell").SpecialFolders("Desktop"), "Hestia.lnk")
    _write_shortcut(path)
    return path


def _write_shortcut(path, arguments=""):
    """A shortcut running gui.py with pythonw, so no console window appears."""
    import win32com.client
    icon = ICON_PATH
    if not os.path.exists(icon) or os.path.getmtime(icon) < os.path.getmtime(LOGO_PATH):
        os.makedirs(os.path.dirname(icon), exist_ok=True)
        hestia_icon().pixmap(256, 256).save(icon)
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
    link.TargetPath = pythonw if os.path.exists(pythonw) else sys.executable
    link.Arguments = f'"{os.path.join(_HERE, "gui.py")}" {arguments}'.strip()
    link.WorkingDirectory = _HERE
    link.IconLocation = icon
    link.Description = "Hestia, your local assistant"
    link.Save()


def hestia_icon(colour=None):
    """
    The brush-stroke H (assets/hestia.png, drawn by scripts/make_logo.py).
    With a colour, a dot in the corner shows her state — for the tray.
    """
    pixmap = QPixmap(LOGO_PATH).scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if colour:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#0e0e10"), 14))
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QPointF(196, 60), 50, 50)
        painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    """The always-there part: open, start with Windows, quit."""

    def __init__(self, window):
        super().__init__(hestia_icon(), window)
        self.window = window
        self.setToolTip("Hestia")
        menu = QMenu()
        menu.addAction("Open Hestia", window.show_window)
        self.start_with_windows = menu.addAction("Start with Windows")
        self.start_with_windows.setCheckable(True)
        self.start_with_windows.setChecked(starts_with_windows())
        self.start_with_windows.toggled.connect(set_start_with_windows)
        self.mute = None
        if window.listener:
            self.mute = menu.addAction(f"Mute microphone (or say “{voice.MUTE_PHRASE}”)")
            self.mute.setCheckable(True)
            self.mute.setChecked(window.listener.muted)
            self.mute.toggled.connect(self._set_muted)
        menu.addSeparator()
        menu.addAction("Quit Hestia", window.quit)
        self.setContextMenu(menu)
        self.menu = menu
        self.activated.connect(self._on_activated)
        window.hidden_to_tray.connect(self._first_hide)
        window.state_changed.connect(lambda colour: self.setIcon(hestia_icon(colour)))
        window.mute_requested.connect(self._mute_by_voice)
        self._told = False

    def _set_muted(self, muted):
        self.window.listener.set_muted(muted)
        if muted:
            self.window.in_conversation = False  # she can't hear the rest of it
        if self.window.settings:
            self.window.settings.setValue("muted", muted)
        self.setToolTip("Hestia (microphone muted)" if muted else "Hestia")
        if not self.window.busy:
            self.window.set_state(self.window._idle_state())

    def _mute_by_voice(self):
        if self.mute and not self.mute.isChecked():
            self.mute.setChecked(True)
            self.showMessage("Microphone muted", "Hestia has stopped listening. "
                             "Unmute from this icon's menu.", hestia_icon(STATE_COLOURS["muted"]), 4000)

    def _on_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.window.show_window()

    def _first_hide(self):
        if not self._told:
            self._told = True
            self.showMessage("Hestia is still here", "She keeps running in the tray. "
                             "Right-click the icon to quit.", hestia_icon(), 4000)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Hestia")
    app.setStyle("Fusion")
    app.setFont(QFont(FONT, 10))
    app.setStyleSheet(APP_STYLE)
    app.setQuitOnLastWindowClosed(False)  # closing the window hides it to the tray
    if "--desktop-shortcut" in sys.argv:
        print(f"Created {make_desktop_shortcut()}")
        return
    if hand_off():
        return
    app.setWindowIcon(hestia_icon())
    settings = QSettings("Hestia", "Hestia")
    speaker = voice.Speaker() if voice.voice_models_present() else None
    ears = Ears()
    listener = None
    if voice.listening_models_present():
        listener = voice.Listener(on_wake=ears.wake.emit, on_text=ears.text.emit,
                                  on_command=ears.command.emit, awake=lambda: window.awake())
        listener.muted = settings.value("muted", False, type=bool)
    window = ChatWindow(speaker=speaker, settings=settings, listener=listener, chime=voice.chime)
    ears.wake.connect(window.heard_wake)
    ears.text.connect(window.heard_text)
    ears.command.connect(window.heard_command)
    if listener:
        try:
            listener.start()
        except Exception as e:  # no microphone, or Windows privacy settings blocking it
            window.transcript.append(("notice", f"[Microphone unavailable, so the wake word is off: {e}]"))
    server = InstanceServer(INSTANCE_NAME, window.show_window)  # noqa: F841 (kept alive for the app's life)
    tray = Tray(window)
    tray.show()
    window.set_state("thinking" if window.busy and window.ready else
                     "starting" if window.busy else window._idle_state())
    if "--background" not in sys.argv:  # started at login: just the tray icon
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
