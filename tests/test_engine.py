import datetime
import unittest
from types import SimpleNamespace as NS

import support
from support import fake_tool, temp_db

import embeddings  # noqa: E402
import engine  # noqa: E402
import memory  # noqa: E402
import storage  # noqa: E402
import tools  # noqa: E402


class RecordingUI:
    """A front end that records everything the engine sends it."""

    def __init__(self, approve=True, confirm=True):
        self.texts, self.tools, self.notices, self.approvals, self.questions = [], [], [], [], []
        self._approve, self._confirm = approve, confirm

    def text(self, token):
        self.texts.append(token)

    def tool(self, name):
        self.tools.append(name)

    def notice(self, message):
        self.notices.append(message)

    def approve(self, name, arguments, after_web=False):
        self.approvals.append((name, arguments, after_web))
        return self._approve

    def confirm(self, question):
        self.questions.append(question)
        return self._confirm


def model(test, *rounds, answer="Hello there.", fail=False):
    """Replace the model with a script: each round asks for tool calls, then `answer`."""
    script = list(rounds)
    calls = []

    def stream_reply(messages, ui, offer_tools=True):
        calls.append(messages)
        if fail:
            raise ConnectionError("Ollama isn't running")
        if script and offer_tools:
            return "", [NS(function=NS(name=n, arguments=a)) for n, a in script.pop(0)]
        ui.text(answer)
        return answer, []

    test.addCleanup(setattr, engine, "stream_reply", engine.stream_reply)
    engine.stream_reply = stream_reply
    return calls


def new_session(test, ui):
    """A Session with no Ollama and no tool servers: those are tested elsewhere."""
    temp_db(test)
    for module, name, value in ((engine, "create", lambda **kw: None), (tools, "start", lambda: []),
                                (engine, "start_services", lambda: None),
                                (memory, "extract_auto_memory", lambda *a, **kw: None)):
        test.addCleanup(setattr, module, name, getattr(module, name))
        setattr(module, name, value)
    indexed = []
    test.addCleanup(setattr, embeddings, "index_pending", embeddings.index_pending)
    embeddings.index_pending = lambda conn, *a, **kw: (indexed.append(True), (0, 0))[1]
    session = engine.Session(ui)
    test.addCleanup(session.close)
    return session, indexed


class TurnTest(unittest.TestCase):
    def setUp(self):
        self.ui = RecordingUI()
        self.session, self.indexed = new_session(self, self.ui)

    def saved(self):
        return [tuple(r) for r in self.session.conn.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id", (self.session.session_id,))]

    def test_a_turn_streams_the_reply_and_saves_both_sides(self):
        model(self)
        reply, was_command = self.session.handle("hi")
        self.assertEqual((reply, was_command), ("Hello there.", False))
        self.assertEqual("".join(self.ui.texts), "Hello there.")
        self.assertEqual(self.saved(), [("user", "hi"), ("assistant", "Hello there.")])

    def test_commands_are_answered_without_the_model(self):
        calls = model(self)
        for command in ("memories", "tools", "MEMORIES"):
            text, was_command = self.session.handle(command)
            self.assertTrue(was_command)
            self.assertTrue(text)
        self.assertEqual(calls, [])

    def test_empty_input_does_nothing(self):
        calls = model(self)
        self.assertEqual(self.session.handle("   "), ("", True))
        self.assertEqual(calls, [])

    def test_tool_use_is_reported_and_approval_goes_to_the_ui(self):
        wrote = fake_tool(self, "hestia_core", "write_file", "saved")
        model(self, [("write_file", {})])
        self.session.handle("save a note")
        self.assertEqual(self.ui.tools, ["write_file"])
        self.assertEqual(self.ui.approvals, [("write_file", {}, False)])
        self.assertEqual(wrote, [{}])

    def test_model_failure_drops_the_unanswered_message(self):
        model(self, fail=True)
        before = list(self.session.history)
        self.session.handle("hi")
        self.assertTrue(any("Error talking to Ollama" in n for n in self.ui.notices))
        self.assertEqual(self.session.history, before)
        self.assertEqual(self.saved(), [])

    def test_links_with_no_lookup_get_a_note(self):
        model(self, answer="See http://example.com/result1")
        self.session.handle("search something")
        self.assertTrue(any("weren't looked up" in n for n in self.ui.notices))

    def test_forget_everything_asks_the_ui(self):
        storage.upsert_memory(self.session.conn, "User likes tea.", ["tea"], "explicit")
        model(self)
        self.session.handle("forget everything")
        self.assertEqual(len(self.ui.questions), 1)
        self.assertEqual(storage.get_all_memories(self.session.conn), [])

    def test_new_starts_a_fresh_session(self):
        model(self)
        self.session.handle("hi")
        old = self.session.session_id
        self.session.handle("new")
        self.assertNotEqual(self.session.session_id, old)
        self.assertEqual([m["role"] for m in self.session.history], ["system"])


class IdleTest(unittest.TestCase):
    def setUp(self):
        self.ui = RecordingUI()
        self.session, self.indexed = new_session(self, self.ui)
        model(self)

    def test_idle_session_is_closed_and_replaced(self):
        # Long enough to be indexed: embeddings skips one-word messages like "hi".
        self.session.handle("tell me about the goddess of the hearth")
        old = self.session.session_id
        later = self.session.last_activity + datetime.timedelta(minutes=engine.IDLE_MINUTES + 1)
        self.assertTrue(self.session.close_if_idle(now=later))
        self.assertNotEqual(self.session.session_id, old)
        self.assertEqual(self.indexed, [True])
        self.assertEqual([m["role"] for m in self.session.history], ["system"])

    def test_a_recent_or_empty_session_is_left_alone(self):
        soon = self.session.last_activity + datetime.timedelta(minutes=engine.IDLE_MINUTES + 1)
        self.assertFalse(self.session.close_if_idle(now=soon))  # nothing said yet
        self.session.handle("hi")
        self.assertFalse(self.session.close_if_idle(now=self.session.last_activity))


class StartupTest(unittest.TestCase):
    def test_missing_personality_is_an_error_not_an_exit(self):
        with self.assertRaises(engine.StartupError):
            engine.load_personality("no-such-personality.yaml")


class StandbyTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.session, _ = new_session(self, RecordingUI())
        for module, name, value in (
                (tools, "start", lambda: self.calls.append("tools up") or []),
                (tools, "stop", lambda: self.calls.append("tools down")),
                (engine, "_load_model", lambda keep_alive: self.calls.append(f"model {keep_alive}"))):
            self.addCleanup(setattr, module, name, getattr(module, name))
            setattr(module, name, value)

    def test_nothing_heavy_is_loaded_until_she_wakes(self):
        self.assertEqual(self.calls, [])
        self.assertFalse(self.session.awake)

    def test_wake_then_rest(self):
        self.session.wake()
        self.session.wake()  # once is enough
        self.assertTrue(self.session.awake)
        self.assertEqual(sorted(self.calls), ["model 30m", "tools up"])
        self.session.rest()
        self.assertFalse(self.session.awake)
        self.assertEqual(self.calls[-2:], ["tools down", "model 0"])

    def test_a_turn_wakes_her(self):
        model(self)
        self.session.handle("hello there")
        self.assertTrue(self.session.awake)


class StartServicesTest(unittest.TestCase):
    def patch(self, name, value, module=engine):
        self.addCleanup(setattr, module, name, getattr(module, name))
        setattr(module, name, value)

    def test_ollama_is_started_and_waited_for_when_its_not_running(self):
        started, checks = [], []
        self.patch("_ollama_up", lambda: checks.append(1) or len(started) > 0 and len(checks) > 3)
        self.patch("Popen", lambda command, **kw: started.append(command), engine.subprocess)
        self.patch("start_search", lambda: None, engine.launcher)
        engine.start_services(wait_seconds=5)
        self.assertEqual(len(started), 1)
        self.assertTrue(engine._ollama_up())

    def test_nothing_is_started_when_ollama_is_up(self):
        started = []
        self.patch("_ollama_up", lambda: True)
        self.patch("Popen", lambda command, **kw: started.append(command), engine.subprocess)
        self.patch("start_search", lambda: None, engine.launcher)
        engine.start_services()
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
