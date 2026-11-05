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
import os
import sys
import threading
import unicodedata

from PySide6.QtCore import QObject, QSettings, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPixmap, QTextDocument
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QMenu, QPlainTextEdit, QPushButton, QSystemTrayIcon, QTextBrowser, QVBoxLayout, QWidget,
)

import engine
import voice

_HERE = os.path.dirname(os.path.abspath(__file__))

# One Hestia per Windows user: a second launch finds the first through this
# local socket name, asks it to show its window, and exits.
INSTANCE_NAME = f"hestia-{getpass.getuser()}"

# "Start with Windows" is a shortcut in the user's own Startup folder —
# per-user, no admin, and visible/removable in File Explorer.
STARTUP_SHORTCUT = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                                "Start Menu", "Programs", "Startup", "Hestia.lnk")

# Said on their own after the wake word, these just stop her talking.
STOP_WORDS = {"stop", "cancel", "never mind", "nevermind", "be quiet", "quiet", "shush", "that's enough"}

IDLE_CHECK_MS = 60_000
RENDER_EVERY_MS = 50  # streaming re-renders are batched, not one per token

# Applied to what the transcript's Markdown renders to. Colours read on both
# Windows light and dark mode.
TRANSCRIPT_STYLE = """
p { margin-top: 2px; margin-bottom: 12px; line-height: 135%; }
strong { color: #4f8fd6; }
em { color: #8a8f98; }
code { font-family: Consolas, monospace; color: #8a8f98; }
pre { font-family: Consolas, monospace; background-color: rgba(128, 128, 128, 30); padding: 8px; }
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
    nothing = Signal()


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
        output, was_command = self.session.handle(text)
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


class ChatWindow(QMainWindow):
    """The chat window. The engine is started on a worker thread as soon as it's built."""
    _start_engine = Signal()
    _submit = Signal(str)
    _check_idle = Signal()
    _stop_engine = Signal()
    hidden_to_tray = Signal()

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
        self.name = "Hestia"
        self.ready = False
        self.busy = False
        self.transcript = []  # (kind, text): you / hestia / notice / output
        self.setWindowTitle("Hestia")
        self.resize(760, 640)

        self.transcript_view = QTextBrowser()
        self.transcript_view.setOpenLinks(False)
        # Styles what the Markdown renders to; the model's text never gets to add markup.
        self.transcript_view.document().setDefaultStyleSheet(TRANSCRIPT_STYLE)
        self.transcript_view.document().setDocumentMargin(14)
        font = self.transcript_view.font()
        font.setPointSizeF(font.pointSizeF() + 1.5)
        self.transcript_view.setFont(font)
        self.transcript_view.anchorClicked.connect(self.open_link)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Message Hestia — or type memories, tools, new, remember …, forget …")
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self.send)
        row = QHBoxLayout()
        row.addWidget(self.input)
        row.addWidget(self.send_button)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.transcript_view)
        layout.addLayout(row)
        self.setCentralWidget(central)

        toolbar = self.addToolBar("Hestia")
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

        self._idle_timer = QTimer(self, interval=IDLE_CHECK_MS)
        self._idle_timer.timeout.connect(self._check_idle.emit)
        self._idle_timer.start()

    # ---- sending -------------------------------------------------------
    def send(self, text=None, spoken=False):
        text = (self.input.text() if text is None else text).strip()
        if not text or not self.ready or self.busy:
            return
        self._hush()  # a new message cuts off whatever she was saying
        self.speaking_turn = bool(self.speaker) and (spoken or self.speak_all.isChecked())
        self.input.clear()
        if text.lower() not in ("memories", "tools", "new"):
            self.transcript.append(("you", text))
        self._render()
        self._set_busy(True, "Thinking…")
        self._submit.emit(text)

    def send_spoken(self, text):
        """A turn that came from the microphone: its reply is spoken."""
        self.send(text, spoken=True)

    # ---- what the microphone hears (main thread) -------------------------
    @Slot()
    def heard_wake(self):
        self._hush()
        if self.chime:
            self.chime()
        self.statusBar().showMessage("Listening…")

    @Slot(str)
    def heard_text(self, text):
        if text.lower().strip(" .!?,") in STOP_WORDS:
            self._hush()
            self.statusBar().showMessage("Okay.", 3000)
            return
        if self.busy or not self.ready:
            # Never queued: a spoken "yes" must not answer an open approval dialog.
            self.statusBar().showMessage("Still on the last request — ask again in a moment.", 4000)
            return
        self.send_spoken(text)

    @Slot()
    def heard_nothing(self):
        self.statusBar().showMessage("Didn't catch that.", 3000)

    def _hush(self):
        if self.speaker:
            self.speaker.stop()

    def _set_busy(self, busy, status=""):
        self.busy = busy
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.statusBar().showMessage(status or ("Ready" if not busy else ""))
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
            self.speaker.feed(token)
        self._render_timer.start()

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
        self.show_window()
        dialog = ApprovalDialog(self.name, tool_name, arguments, after_web, parent=self)
        self.bridge.answer(dialog.exec() == QDialog.Accepted)

    @Slot(str)
    def _on_question(self, question):
        self.show_window()
        answer = QMessageBox.question(self, self.name, question,
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        self.bridge.answer(answer == QMessageBox.Yes)

    @Slot(str, list)
    def _on_ready(self, name, problems):
        self.name = name
        self.setWindowTitle(name)
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
            self.speaker.flush()
        self._render()
        self._set_busy(False)

    # ---- display --------------------------------------------------------
    def _render(self):
        self._render_timer.stop()
        blocks = []
        for kind, text in self.transcript:
            if kind == "you":
                blocks.append(f"**You:** {text}")
            elif kind == "hestia":
                blocks.append(f"**{self.name}:** {text}")
            elif kind == "notice":
                blocks.append(f"*{text}*")
            else:
                blocks.append(f"```\n{text}\n```")
        # NoHTML: replies and web content are shown as text, never as markup.
        self.transcript_view.document().setMarkdown(
            "\n\n".join(blocks),
            QTextDocument.MarkdownFeature(QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML))
        bar = self.transcript_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @Slot(QUrl)
    def open_link(self, url):
        """Only web links open, in the browser; file:, javascript: and the rest do nothing."""
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

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
    import win32com.client
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(STARTUP_SHORTCUT)
    link.TargetPath = pythonw if os.path.exists(pythonw) else sys.executable
    link.Arguments = f'"{os.path.join(_HERE, "gui.py")}" --background'
    link.WorkingDirectory = _HERE
    link.Description = "Hestia, your local assistant"
    link.Save()


def hestia_icon():
    """A warm circle with an H, drawn here so there's no image file to ship."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#e07a3f"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, 60, 60)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 30, QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "H")
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
            self.mute = menu.addAction(f"Mute microphone (wake word: “{voice.WAKE_PHRASE}”)")
            self.mute.setCheckable(True)
            self.mute.setChecked(window.listener.muted)
            self.mute.toggled.connect(self._set_muted)
        menu.addSeparator()
        menu.addAction("Quit Hestia", window.quit)
        self.setContextMenu(menu)
        self.menu = menu
        self.activated.connect(self._on_activated)
        window.hidden_to_tray.connect(self._first_hide)
        self._told = False

    def _set_muted(self, muted):
        self.window.listener.set_muted(muted)
        if self.window.settings:
            self.window.settings.setValue("muted", muted)
        self.setToolTip("Hestia (microphone muted)" if muted else "Hestia")

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
    app.setQuitOnLastWindowClosed(False)  # closing the window hides it to the tray
    if hand_off():
        return
    app.setWindowIcon(hestia_icon())
    settings = QSettings("Hestia", "Hestia")
    speaker = voice.Speaker() if voice.voice_models_present() else None
    ears = Ears()
    listener = None
    if voice.listening_models_present():
        listener = voice.Listener(on_wake=ears.wake.emit, on_text=ears.text.emit, on_nothing=ears.nothing.emit)
        listener.muted = settings.value("muted", False, type=bool)
    window = ChatWindow(speaker=speaker, settings=settings, listener=listener, chime=voice.chime)
    ears.wake.connect(window.heard_wake)
    ears.text.connect(window.heard_text)
    ears.nothing.connect(window.heard_nothing)
    if listener:
        try:
            listener.start()
        except Exception as e:  # no microphone, or Windows privacy settings blocking it
            window.transcript.append(("notice", f"[Microphone unavailable, so the wake word is off: {e}]"))
    server = InstanceServer(INSTANCE_NAME, window.show_window)  # noqa: F841 (kept alive for the app's life)
    tray = Tray(window)
    tray.show()
    if "--background" not in sys.argv:  # started at login: just the tray icon
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
