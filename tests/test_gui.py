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


class BackgroundTest(unittest.TestCase):
    def setUp(self):
        self.window = gui.ChatWindow(session_factory=FakeSession)
        self.addCleanup(self.window.shutdown)
        self.assertTrue(wait_until(lambda: self.window.ready))

    def test_closing_the_window_hides_it_but_hestia_keeps_running(self):
        self.window.show()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.thread.isRunning())

    def test_quit_stops_the_engine(self):
        self.window.quit()
        self.assertFalse(self.window.thread.isRunning())
        self.assertTrue(self.window.worker.session.closed)

    def test_second_instance_hands_off(self):
        name = f"hestia-test-{os.getpid()}-{time.monotonic_ns()}"
        self.assertFalse(gui.hand_off(name), "nothing is running under this name yet")
        shown = []
        server = gui.InstanceServer(name, lambda: shown.append(True))
        self.addCleanup(server.close)
        result = []
        threading.Thread(target=lambda: result.append(gui.hand_off(name))).start()
        self.assertTrue(wait_until(lambda: shown and result))
        self.assertEqual(result, [True])


    def test_hand_off_uses_the_current_instance_name(self):
        # A default argument would freeze the name at import; caught live when
        # a second instance looked under the old name and started anyway.
        name = f"hestia-test-default-{os.getpid()}-{time.monotonic_ns()}"
        self.addCleanup(setattr, gui, "INSTANCE_NAME", gui.INSTANCE_NAME)
        gui.INSTANCE_NAME = name
        shown = []
        server = gui.InstanceServer(name, lambda: shown.append(True))
        self.addCleanup(server.close)
        result = []
        threading.Thread(target=lambda: result.append(gui.hand_off())).start()
        self.assertTrue(wait_until(lambda: shown and result))
        self.assertEqual(result, [True])


class RecordingSpeaker:
    def __init__(self):
        self.fed, self.flushed, self.stopped, self.said = [], 0, 0, []

    def feed(self, token):
        self.fed.append(token)

    def flush(self):
        self.flushed += 1

    def stop(self):
        self.stopped += 1

    def say(self, text):
        self.said.append(text)

    def close(self):
        pass


class SpeakingTest(unittest.TestCase):
    def setUp(self):
        self.speaker = RecordingSpeaker()
        self.window = gui.ChatWindow(session_factory=FakeSession, speaker=self.speaker)
        self.addCleanup(self.window.shutdown)
        self.window.speak_all.setChecked(False)
        self.assertTrue(wait_until(lambda: self.window.ready))

    def finish(self):
        self.assertTrue(wait_until(lambda: not self.window.busy))

    def test_typed_replies_stay_text_by_default(self):
        self.window.input.setText("hi")
        self.window.send()
        self.finish()
        self.assertEqual(self.speaker.fed, [])

    def test_spoken_turns_are_answered_aloud(self):
        self.window.send_spoken("hi")
        self.finish()
        self.assertEqual("".join(self.speaker.fed), "Hello there.")
        self.assertEqual(self.speaker.flushed, 1)

    def test_speak_all_replies_speaks_typed_ones_too(self):
        self.window.speak_all.setChecked(True)
        self.window.input.setText("hi")
        self.window.send()
        self.finish()
        self.assertEqual("".join(self.speaker.fed), "Hello there.")

    def test_a_new_message_cuts_her_off(self):
        self.window.input.setText("hi")
        self.window.send()
        self.finish()
        self.assertGreaterEqual(self.speaker.stopped, 1)

    def test_approval_in_a_spoken_turn_is_announced_out_loud(self):
        original = gui.ApprovalDialog.exec
        self.addCleanup(setattr, gui.ApprovalDialog, "exec", original)
        gui.ApprovalDialog.exec = lambda dialog: QDialog.Rejected
        self.window.send_spoken("save it")
        self.finish()
        self.assertTrue(any("approve" in s for s in self.speaker.said))


class StartWithWindowsTest(unittest.TestCase):
    def setUp(self):
        folder = support.temp_folder(self)
        self.addCleanup(setattr, gui, "STARTUP_SHORTCUT", gui.STARTUP_SHORTCUT)
        gui.STARTUP_SHORTCUT = os.path.join(folder, "Hestia.lnk")

    def test_turning_it_on_and_off(self):
        self.assertFalse(gui.starts_with_windows())
        gui.set_start_with_windows(True)
        self.assertTrue(gui.starts_with_windows())
        import win32com.client
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(gui.STARTUP_SHORTCUT)
        self.assertTrue(link.TargetPath.lower().endswith("pythonw.exe"))
        self.assertIn("gui.py", link.Arguments)
        gui.set_start_with_windows(False)
        self.assertFalse(gui.starts_with_windows())


if __name__ == "__main__":
    unittest.main()
