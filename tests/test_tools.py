import contextlib
import io
import unittest
from types import SimpleNamespace as NS

import support
from support import fake_tool, temp_db

import chat  # noqa: E402
import tools  # noqa: E402


class NetworkTierTest(unittest.TestCase):
    def setUp(self):
        self.conn, self.session = temp_db(self)
        self.searched = fake_tool(self, "web_tools", "web_search", "results")
        self.read = fake_tool(self, "hestia_core", "read_file", "secret notes")
        self.wrote = fake_tool(self, "hestia_core", "write_file", "saved")
        self.asked = []

    def confirm(self, name, arguments, after_web=False):
        self.asked.append((name, after_web))
        return True

    def run_tool(self, name, turn):
        return tools.execute(self.conn, self.session, name, {}, self.confirm, turn)

    def last_decision(self):
        return self.conn.execute("SELECT decision FROM tool_calls ORDER BY id DESC LIMIT 1").fetchone()[0]

    def test_tier_values(self):
        self.assertEqual(tools.TIER_NETWORK, 3)  # 0-2 are already stored in tool_calls rows
        self.assertEqual(tools.TOOL_TIERS["web_tools"]["web_search"], tools.TIER_NETWORK)

    def test_network_tool_runs_without_asking(self):
        self.run_tool("web_search", {})
        self.assertEqual(len(self.searched), 1)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.last_decision(), "auto")

    def test_network_tool_refused_after_local_data_this_turn(self):
        turn = {}
        self.run_tool("read_file", turn)
        result = self.run_tool("web_search", turn)
        self.assertEqual(self.searched, [])
        self.assertIn("same turn", result)
        self.assertEqual(self.last_decision(), "blocked")

    def test_network_tool_allowed_again_in_a_later_turn(self):
        self.run_tool("read_file", {})
        self.run_tool("web_search", {})
        self.assertEqual(len(self.searched), 1)

    def test_reading_local_data_after_a_search_is_fine(self):
        turn = {}
        self.run_tool("web_search", turn)
        self.run_tool("read_file", turn)
        self.assertEqual((len(self.searched), len(self.read)), (1, 1))

    def test_write_after_web_content_carries_a_warning(self):
        turn = {}
        self.run_tool("write_file", turn)
        self.run_tool("web_search", turn)
        self.run_tool("write_file", turn)
        self.assertEqual(self.asked, [("write_file", False), ("write_file", True)])

    def test_the_prompt_shows_the_warning(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), \
                support.mock_input("y"):
            allowed = chat.confirm_tool("Hestia", "write_file", {"path": "a.txt"}, after_web=True)
        self.assertTrue(allowed)
        self.assertIn("web content", printed.getvalue())


class RunTurnWiringTest(unittest.TestCase):
    def setUp(self):
        self.conn, self.session = temp_db(self)
        self.searched = fake_tool(self, "web_tools", "web_search", "results")
        fake_tool(self, "hestia_core", "read_file", "secret notes")

    def run_turn(self, *rounds):
        script = list(rounds)

        def stream_reply(messages, offer_tools=True):
            if script and offer_tools:
                return "", [NS(function=NS(name=n, arguments={})) for n in script.pop(0)]
            return "Done.", []

        self.addCleanup(setattr, chat, "stream_reply", chat.stream_reply)
        chat.stream_reply = stream_reply
        with contextlib.redirect_stdout(io.StringIO()):
            chat.run_turn(self.conn, self.session, [{"role": "user", "content": "hi"}],
                          lambda name, arguments, after_web=False: True)

    def test_search_after_a_file_read_in_the_same_turn_is_refused(self):
        self.run_turn(["read_file"], ["web_search"])
        self.assertEqual(self.searched, [])

    def test_each_turn_starts_fresh(self):
        self.run_turn(["read_file"])
        self.run_turn(["web_search"])
        self.assertEqual(len(self.searched), 1)


if __name__ == "__main__":
    unittest.main()
