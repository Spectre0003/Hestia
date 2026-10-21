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


class PublicAddressTest(unittest.TestCase):
    def test_non_public_addresses_are_refused(self):
        for host in ("localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1", "0.0.0.0", "10.0.0.5",
                     "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "fe80::1"):
            with self.subTest(host=host):
                self.assertFalse(web_tools._is_public(host))

    def test_public_addresses_pass(self):
        self.assertTrue(web_tools._is_public("8.8.8.8"))
        self.assertTrue(web_tools._is_public("2001:4860:4860::8888"))

    def test_bad_addresses_are_refused_before_anything_is_fetched(self):
        for url in ("", "file:///C:/Windows/win.ini", "ftp://example.org/x", "javascript:alert(1)",
                    "http:///nohost", "http://localhost:11434/api/tags", "http://192.168.1.1/admin"):
            with self.subTest(url=url), self.assertRaises(ToolError):
                web_tools.fetch_page(url)


class FetchPageTest(unittest.TestCase):
    def serve_as_public(self, respond):
        """A local server standing in for the internet: only its exact host passes the public check."""
        base, seen = serve(self, respond)
        real = web_tools._is_public
        self.addCleanup(setattr, web_tools, "_is_public", real)
        web_tools._is_public = lambda host: host == "127.0.0.1" or real(host)
        return base, seen

    def test_redirect_to_private_address_is_refused(self):
        base, seen = self.serve_as_public(
            lambda path: (302, {"Location": "http://192.168.1.1/admin"}, b""))
        with self.assertRaises(ToolError) as caught:
            web_tools.fetch_page(base + "/start")
        self.assertIn("192.168.1.1", str(caught.exception))
        self.assertEqual(seen, ["/start"])

    def test_redirects_to_public_pages_are_followed(self):
        def respond(path):
            if path == "/old":
                return 301, {"Location": "/new"}, b""
            return 200, {"Content-Type": "text/plain"}, b"moved here"
        base, _ = self.serve_as_public(respond)
        self.assertIn("moved here", web_tools.fetch_page(base + "/old"))

    def test_html_becomes_readable_text(self):
        page = (b"<html><head><title>Hearth facts</title><style>p{color:red}</style>"
                b"<script>steal()</script></head><body><nav>Home | About</nav>"
                b"<h1>Hestia</h1><p>Goddess of the  hearth.</p><p>Eldest of the &amp; Olympians.</p>"
                b"<footer>Copyright</footer></body></html>")
        base, _ = self.serve_as_public(lambda path: (200, {"Content-Type": "text/html; charset=utf-8"}, page))
        text = web_tools.fetch_page(base + "/")
        for expected in ("Hearth facts", "Hestia", "Goddess of the hearth.", "Eldest of the & Olympians."):
            self.assertIn(expected, text)
        for dropped in ("steal()", "color:red", "Home | About", "Copyright"):
            self.assertNotIn(dropped, text)

    def test_huge_pages_are_cut_short_not_loaded_whole(self):
        big = b"word " * 700_000  # 3.5 MB
        base, _ = self.serve_as_public(lambda path: (200, {"Content-Type": "text/plain"}, big))
        self.assertIn("cut short", web_tools.fetch_page(base + "/big"))

    def test_an_unknown_charset_falls_back_instead_of_failing(self):
        base, _ = self.serve_as_public(
            lambda path: (200, {"Content-Type": "text/html; charset=not-a-real-charset"}, b"<p>still readable</p>"))
        self.assertIn("still readable", web_tools.fetch_page(base + "/"))

    def test_non_text_content_is_refused(self):
        base, _ = self.serve_as_public(lambda path: (200, {"Content-Type": "image/png"}, b"\x89PNG"))
        with self.assertRaises(ToolError) as caught:
            web_tools.fetch_page(base + "/cat.png")
        self.assertIn("image/png", str(caught.exception))


def _online():
    try:
        socket.create_connection(("example.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


@unittest.skipUnless(_online(), "no internet connection")
class RealFetchTest(unittest.TestCase):
    def test_a_real_public_page(self):
        self.assertIn("Example Domain", web_tools.fetch_page("https://example.com/"))


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
