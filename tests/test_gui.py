import os
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # no windows appear during tests

import support  # noqa: F401,E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

import gui  # noqa: E402

app = QApplication.instance() or QApplication([])


def wait_until(condition, seconds=5):
    """Let Qt process events until `condition()` is true."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeSession:
    """Stands in for engine.Session: no model, no tools, no database."""
    name = "Hestia"
    problems = []

    def __init__(self, ui):
        self.ui = ui
        self.approved = None
        self.closed = False

    def handle(self, text):
        if text == "memories":
            return "[No memories stored yet.]", True
        if text == "save it":
            self.approved = self.ui.approve("write_file", {"path": "a.txt"}, False)
        self.ui.text("Hello ")
        self.ui.text("there.")
        return "Hello there.", False

    def close_if_idle(self, now=None):
        return False

    def close(self):
        self.closed = True


class ApprovalDialogTest(unittest.TestCase):
    def test_approval_dialog_defaults_to_deny(self):
        dialog = gui.ApprovalDialog("Hestia", "write_file", {"path": "a.txt"}, after_web=False)
        self.assertTrue(dialog.deny_button.isDefault())
        self.assertFalse(dialog.allow_button.isDefault())

    def test_closing_dialog_denies(self):
        dialog = gui.ApprovalDialog("Hestia", "write_file", {"path": "a.txt"}, after_web=False)
        dialog.show()
        dialog.reject()  # what Esc and the window's X do
        self.assertEqual(dialog.result(), QDialog.Rejected)

    def test_shows_every_argument_whole_with_control_characters_escaped(self):
        long_text = "x" * 2000 + "END"
        dialog = gui.ApprovalDialog("Hestia", "write_file",
                                    {"path": "a.txt", "content": long_text + "\x1b[2K"}, after_web=False)
        shown = dialog.arguments_view.toPlainText()
        self.assertIn("END", shown)
        self.assertIn("\\u001b", shown)
        self.assertNotIn("\x1b", shown)

    def test_web_content_warning(self):
        warned = gui.ApprovalDialog("Hestia", "git_commit", {}, after_web=True)
        plain = gui.ApprovalDialog("Hestia", "git_commit", {}, after_web=False)
        self.assertIn("web content", warned.warning.text())
        self.assertTrue(warned.warning.isVisibleTo(warned))
        self.assertFalse(plain.warning.isVisibleTo(plain))


class BridgeTest(unittest.TestCase):
    def test_approve_waits_for_the_answer(self):
        bridge = gui.Bridge()
        bridge.approval_requested.connect(lambda *a: bridge.answer(True), Qt.DirectConnection)
        self.assertTrue(bridge.approve("write_file", {}, False))

    def test_shutting_down_while_waiting_denies_instead_of_hanging(self):
        bridge = gui.Bridge()
        result = []
        worker = threading.Thread(target=lambda: result.append(bridge.approve("write_file", {}, False)))
        worker.start()
        time.sleep(0.2)
        bridge.shutdown()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [False])

    def test_after_shutdown_nothing_is_approved(self):
        bridge = gui.Bridge()
        bridge.shutdown()
        self.assertFalse(bridge.approve("write_file", {}, False))


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.window = gui.ChatWindow(session_factory=FakeSession)
        self.addCleanup(self.window.shutdown)
        self.assertTrue(wait_until(lambda: self.window.ready), "the engine never started")

    def send(self, text):
        self.window.input.setText(text)
        self.window.send()
        self.assertFalse(self.window.input.isEnabled(), "input stays open during a turn")
        self.assertTrue(wait_until(lambda: self.window.input.isEnabled()), "the turn never finished")

    def test_a_reply_streams_into_the_transcript(self):
        self.send("hi")
        transcript = self.window.transcript_view.toPlainText()
        self.assertIn("hi", transcript)
        self.assertIn("Hello there.", transcript)

    def test_command_output_is_shown(self):
        self.send("memories")
        self.assertIn("No memories stored yet.", self.window.transcript_view.toPlainText())

    def test_approval_goes_through_the_dialog(self):
        asked = []

        def fake_exec(dialog):
            asked.append(dialog.tool_name)
            return QDialog.Rejected

        original = gui.ApprovalDialog.exec
        self.addCleanup(setattr, gui.ApprovalDialog, "exec", original)
        gui.ApprovalDialog.exec = fake_exec
        self.send("save it")
        self.assertEqual(asked, ["write_file"])
        self.assertIs(self.window.worker.session.approved, False)

    def test_links_only_open_when_they_are_web_links(self):
        opened = []
        original = gui.QDesktopServices.openUrl
        self.addCleanup(setattr, gui.QDesktopServices, "openUrl", original)
        gui.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))
        from PySide6.QtCore import QUrl
        for link in ("https://en.wikipedia.org/wiki/Hestia", "file:///C:/Windows/win.ini", "javascript:alert(1)"):
            self.window.open_link(QUrl(link))
        self.assertEqual(opened, ["https://en.wikipedia.org/wiki/Hestia"])


if __name__ == "__main__":
    unittest.main()
