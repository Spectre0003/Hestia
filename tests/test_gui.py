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
        if text in ("long", gui.SUMMARY_REQUEST):
            sentence = "This sentence has exactly ten words in it, you see. "
            for _ in range(3 if text != "long" else 12):  # 120 words for "long"
                for word in sentence.split(" "):
                    self.ui.text(word + " ")
            return "", False
        if text == "save it":
            self.approved = self.ui.approve("write_file", {"path": "a.txt"}, False)
        self.ui.text("Hello ")
        self.ui.text("there.")
        return "Hello there.", False

    def close_if_idle(self, now=None):
        return False

    def wake(self):
        self.woken = getattr(self, "woken", 0) + 1
        return []

    def rest(self):
        self.rested = getattr(self, "rested", 0) + 1

    def close(self):
        self.closed = True


class BrokenSession(FakeSession):
    def handle(self, text):
        raise RuntimeError("the database is locked")


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

    def test_html_in_a_reply_is_shown_not_rendered(self):
        html = gui._markdown_html('**bold** <b onclick="x">tag</b> <img src="file:///C:/x.png">')
        self.assertIn("&lt;b onclick", html)
        self.assertIn("&lt;img", html)
        self.assertNotIn("<img", html)
        self.assertIn("font-weight", html)  # Markdown itself still works

    def test_links_only_open_when_they_are_web_links(self):
        opened = []
        original = gui.QDesktopServices.openUrl
        self.addCleanup(setattr, gui.QDesktopServices, "openUrl", original)
        gui.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toString()))
        from PySide6.QtCore import QUrl
        for link in ("https://en.wikipedia.org/wiki/Hestia", "file:///C:/Windows/win.ini", "javascript:alert(1)"):
            self.window.open_link(QUrl(link))
        self.assertEqual(opened, ["https://en.wikipedia.org/wiki/Hestia"])


class FailureTest(unittest.TestCase):
    def test_an_unexpected_error_doesnt_leave_the_window_stuck(self):
        window = gui.ChatWindow(session_factory=BrokenSession)
        self.addCleanup(window.shutdown)
        self.assertTrue(wait_until(lambda: window.ready))
        window.input.setText("hi")
        window.send()
        self.assertTrue(wait_until(lambda: window.input.isEnabled()), "stuck on Thinking")
        self.assertIn("the database is locked", window.transcript_view.toPlainText())


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
    speaking = False

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

    def warm_up(self):
        self.warmed = getattr(self, "warmed", 0) + 1

    def rest(self):
        self.rested = getattr(self, "rested", 0) + 1


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


class ListeningTest(unittest.TestCase):
    def setUp(self):
        self.speaker = RecordingSpeaker()
        self.window = gui.ChatWindow(session_factory=FakeSession, speaker=self.speaker)
        self.addCleanup(self.window.shutdown)
        self.assertTrue(wait_until(lambda: self.window.ready))

    def test_rise_hestia_cuts_her_off_and_starts_a_conversation(self):
        self.window.heard_wake()
        self.assertGreaterEqual(self.speaker.stopped, 1)
        self.assertTrue(self.window.in_conversation)
        self.assertTrue(self.window.awake())
        self.assertEqual(self.window.orb.state, "listening")

    def test_she_isnt_listening_for_turns_while_thinking_or_talking(self):
        self.window.heard_wake()
        self.window.busy = True
        self.assertFalse(self.window.awake())
        self.window.busy = False
        self.speaker.speaking = True
        self.assertFalse(self.window.awake())
        self.window._refresh_state()
        self.assertEqual(self.window.orb.state, "speaking")
        self.speaker.speaking = False
        self.assertTrue(self.window.awake())

    def test_fall_hestia_ends_the_conversation_with_a_word(self):
        self.window.heard_wake()
        self.window.heard_command("hush")
        self.assertFalse(self.window.in_conversation)
        self.assertFalse(self.window.awake())
        self.assertEqual(len(self.speaker.said), 1)
        self.assertEqual(self.window.orb.state, "ready")

    def test_what_was_heard_becomes_a_spoken_turn(self):
        self.window.heard_text("hi there")
        self.assertTrue(wait_until(lambda: not self.window.busy and self.speaker.flushed))
        self.assertIn("hi there", self.window.transcript_view.toPlainText())

    def test_speech_during_a_busy_turn_is_ignored(self):
        # So a spoken "yes" can never answer an approval dialog.
        self.window.busy = True
        before = list(self.window.transcript)
        self.window.heard_text("yes")
        self.assertEqual(self.window.transcript, before)
        self.window.busy = False

    def test_mute_hestia_ends_the_conversation_and_asks_to_mute(self):
        muted = []
        self.window.mute_requested.connect(lambda: muted.append(True))
        self.window.heard_wake()
        self.window.heard_command("mute")
        self.assertEqual(muted, [True])
        self.assertFalse(self.window.in_conversation)

    def test_an_approval_doesnt_bring_up_the_window(self):
        original = gui.ApprovalDialog.exec
        self.addCleanup(setattr, gui.ApprovalDialog, "exec", original)
        gui.ApprovalDialog.exec = lambda dialog: QDialog.Rejected
        self.window.hide()
        self.window.send_spoken("save it")
        self.assertTrue(wait_until(lambda: not self.window.busy))
        self.assertFalse(self.window.isVisible())

    def long_answer(self):
        self.window.send_spoken("long")
        self.assertTrue(wait_until(lambda: not self.window.busy))
        return " ".join(self.speaker.fed)

    def test_a_long_answer_is_read_up_to_the_limit_then_she_asks(self):
        spoken = self.long_answer()
        words = len(spoken.split())
        self.assertGreaterEqual(words, gui.SPOKEN_WORD_LIMIT)
        self.assertLess(words, gui.SPOKEN_WORD_LIMIT + 12)  # finishes the sentence, then stops
        self.assertTrue(spoken.rstrip().endswith("."))
        self.assertEqual(self.speaker.said, [gui.MORE_QUESTION])
        self.assertEqual(len(self.window.transcript[-1][1].split()), 120)  # the window shows it all

    def test_read_the_rest(self):
        self.long_answer()
        before = len(" ".join(self.speaker.fed).split())
        self.window.heard_text("Yeah, read the rest.")
        self.assertGreater(len(" ".join(self.speaker.fed).split()), before + 30)
        self.assertIsNone(self.window._rest)

    def test_sum_it_up_asks_her_for_a_summary(self):
        self.long_answer()
        self.window.heard_text("Just sum it up")
        self.assertTrue(wait_until(lambda: not self.window.busy))
        self.assertEqual(self.window.transcript[-2], ("you", "Just sum it up"))  # what you said is shown

    def test_no_thanks_and_a_new_question(self):
        self.long_answer()
        self.window.heard_text("No, that's fine.")
        self.assertEqual(self.speaker.said[-1], "Okay.")
        self.long_answer()
        self.window.heard_text("what time is it")
        self.assertTrue(wait_until(lambda: not self.window.busy))
        self.assertEqual(self.window.transcript[-2], ("you", "what time is it"))

    def test_a_short_answer_is_just_spoken(self):
        self.window.send_spoken("hi")
        self.assertTrue(wait_until(lambda: not self.window.busy))
        self.assertEqual(self.speaker.said, [])
        self.assertIsNone(self.window._rest)

    def test_rise_hestia_wakes_everything_and_quiet_puts_it_back_to_rest(self):
        self.assertFalse(self.window.loaded)
        self.window.heard_wake()
        self.assertTrue(self.window.loaded)
        self.assertEqual(self.speaker.warmed, 1)
        session = self.window.worker.session
        self.assertTrue(wait_until(lambda: getattr(session, "woken", 0) == 1))
        self.window._maybe_rest()                       # not quiet long enough yet
        self.assertTrue(self.window.loaded)
        self.window._last_activity -= gui.REST_AFTER_SECONDS
        self.window._maybe_rest()
        self.assertFalse(self.window.loaded)
        self.assertEqual(self.speaker.rested, 1)
        self.assertTrue(wait_until(lambda: getattr(session, "rested", 0) == 1))
        self.window.heard_wake()                        # and wakes again
        self.assertEqual(self.speaker.warmed, 2)

    def test_she_doesnt_rest_while_talking(self):
        self.window.heard_wake()
        self.window._last_activity -= gui.REST_AFTER_SECONDS
        self.speaker.speaking = True
        self.window._maybe_rest()
        self.assertTrue(self.window.loaded)
        self.speaker.speaking = False

    def test_a_typed_message_wakes_her_too(self):
        self.window.send("hi")
        self.assertTrue(self.window.loaded)

    def test_stop_words_just_hush_her(self):
        for words in ("stop", "Never mind.", "be quiet"):
            before = list(self.window.transcript)
            self.window.heard_text(words)
            self.assertEqual(self.window.transcript, before)
        self.assertGreaterEqual(self.speaker.stopped, 3)


class StartWithWindowsTest(unittest.TestCase):
    def setUp(self):
        folder = support.temp_folder(self)
        self.addCleanup(setattr, gui, "STARTUP_SHORTCUT", gui.STARTUP_SHORTCUT)
        gui.STARTUP_SHORTCUT = os.path.join(folder, "Hestia.lnk")
        self.addCleanup(setattr, gui, "ICON_PATH", gui.ICON_PATH)
        gui.ICON_PATH = os.path.join(folder, "hestia.ico")

    def test_shortcuts_carry_the_hestia_icon(self):
        gui.set_start_with_windows(True)
        import win32com.client
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(gui.STARTUP_SHORTCUT)
        self.assertTrue(link.IconLocation.lower().startswith(gui.ICON_PATH.lower()))
        self.assertTrue(os.path.getsize(gui.ICON_PATH) > 0)

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
