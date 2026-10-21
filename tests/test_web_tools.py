import json
import socket
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import support  # noqa: F401  (puts servers/ on the path)

import tools  # noqa: E402
import web_tools  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


def serve(test, respond):
    """
    Run a throwaway HTTP server on 127.0.0.1 for one test. `respond(path)`
    returns (status, headers dict, body bytes). Returns (base_url, requests seen).
    """
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            status, headers, body = respond(self.path)
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    test.addCleanup(httpd.server_close)
    test.addCleanup(httpd.shutdown)
    return f"http://127.0.0.1:{httpd.server_port}", seen


def json_reply(payload, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(payload).encode()


class WebSearchTest(unittest.TestCase):
    def use_engine(self, respond):
        base, seen = serve(self, respond)
        self.addCleanup(setattr, web_tools, "SEARXNG_URL", web_tools.SEARXNG_URL)
        web_tools.SEARXNG_URL = base
        return seen

    def test_returns_top_results_with_addresses(self):
        results = [{"title": f"Result {i}", "url": f"https://example.org/{i}", "content": f"snippet {i}"}
                   for i in range(1, 8)]
        seen = self.use_engine(lambda path: json_reply({"results": results}))
        text = web_tools.web_search("hestia greek goddess")
        for expected in ("Result 1", "https://example.org/1", "snippet 1", "Result 5"):
            self.assertIn(expected, text)
        self.assertNotIn("Result 6", text)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
        self.assertEqual(query["q"], ["hestia greek goddess"])
        self.assertEqual(query["format"], ["json"])

    def test_empty_and_overlong_queries_are_refused_before_anything_is_sent(self):
        seen = self.use_engine(lambda path: json_reply({"results": []}))
        for bad in ("", "   ", "x" * (web_tools.MAX_QUERY_CHARS + 1)):
            with self.subTest(query=bad[:10]), self.assertRaises(ToolError):
                web_tools.web_search(bad)
        self.assertEqual(seen, [])

    def test_no_results_is_said_plainly(self):
        self.use_engine(lambda path: json_reply({"results": []}))
        self.assertIn("No results", web_tools.web_search("zzqqxx nothing"))

    def test_json_disabled_message(self):
        self.use_engine(lambda path: (403, {}, b"Forbidden"))
        with self.assertRaises(ToolError) as caught:
            web_tools.web_search("anything")
        self.assertIn("json", str(caught.exception).lower())

    def test_search_engine_not_running_message(self):
        with socket.socket() as s:  # a port nothing is listening on
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]
        self.addCleanup(setattr, web_tools, "SEARXNG_URL", web_tools.SEARXNG_URL)
        web_tools.SEARXNG_URL = f"http://127.0.0.1:{closed}"
        with self.assertRaises(ToolError) as caught:
            web_tools.web_search("anything")
        self.assertIn("isn't running", str(caught.exception))
        self.assertIn("hestia-searxng", str(caught.exception))


def _searxng_up():
    try:
        urllib.request.urlopen(web_tools.SEARXNG_URL, timeout=3)
        return True
    except Exception:
        return False


@unittest.skipUnless(_searxng_up(), "SearXNG isn't running")
class RealSearXNGTest(unittest.TestCase):
    def test_a_real_search_returns_results(self):
        self.assertIn("http", web_tools.web_search("python programming language"))


class RegistrationTest(unittest.TestCase):
    def test_web_tools_server_is_registered(self):
        self.assertIn("web_tools", tools.SERVERS)


if __name__ == "__main__":
    unittest.main()
