"""
Hestia — web tool server (Stage 8 / v0.8)

The first tools that reach the internet, and so the first place anything
can leave this computer. Two narrow tools: web_search, through a SearXNG
instance the user hosts themselves (the hestia-searxng Docker container,
bound to 127.0.0.1 — no account, no API key, no cloud dependency), and
fetch_page, which reads one public web page as text.

What may run when is decided in tools.py: these are TIER_NETWORK, which
runs without asking but is refused in any turn where local data (files,
repos, processes) has already been read, so none of it can leave in a
query or a URL. Everything that comes back is written by strangers and
is treated as data, never instructions.

Standard library only.
"""

import codecs
import html.parser
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("web_tools", log_level="WARNING")

SEARXNG_URL = "http://127.0.0.1:8888"
SEARCH_TIMEOUT_SECONDS = 15
MAX_RESULTS = 5
# Short on purpose: a search query is the one thing the model writes that
# leaves the machine, so it gets room for keywords, not for paragraphs.
MAX_QUERY_CHARS = 300

# SearXNG is on this machine; never route it through a system proxy.
_LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_NOT_RUNNING = (
    "The search engine (SearXNG) isn't running. It's the 'hestia-searxng' Docker "
    "container: Docker Desktop has to be running, then that container started."
)


@server.tool()
def web_search(query: str) -> str:
    """Search the web and return the top results, each with its title, address and a short snippet. Use this for anything current or outside what you know. When you use a result, say which address it came from. The query leaves this computer, so put only the topic in it — never the user's personal details, file contents or other private information."""
    query = (query or "").strip()
    if not query:
        raise ToolError("A search needs some words to search for.")
    if len(query) > MAX_QUERY_CHARS:
        raise ToolError(
            f"That search is too long ({len(query)} characters; the limit is "
            f"{MAX_QUERY_CHARS}). Search for a few key words instead."
        )

    url = f"{SEARXNG_URL}/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    try:
        with _LOCAL.open(url, timeout=SEARCH_TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise ToolError("The search engine refused to answer in JSON: turn on the json "
                            "format in searxng/settings.yml and restart the container.")
        raise ToolError(f"The search engine returned an error ({e.code}).")
    except (urllib.error.URLError, OSError):
        raise ToolError(_NOT_RUNNING)
    except ValueError:
        raise ToolError("The search engine sent back something that wasn't valid JSON.")

    results = data.get("results") or []
    if not results:
        return f"No results for '{query}'."
    lines = [f"Top results for '{query}':"]
    for number, result in enumerate(results[:MAX_RESULTS], 1):
        lines.append(f"{number}. {result.get('title') or '(no title)'}")
        lines.append(f"   {result.get('url') or ''}")
        snippet = " ".join((result.get("content") or "").split())
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 2_000_000
MAX_REDIRECTS = 5
_TEXT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hestia/0.8"


def _is_public(host):
    """
    True only if every address `host` resolves to is on the public
    internet. Without this, the model — or a page it read — could point
    fetch_page at Ollama on this machine, a router's admin page, or a
    cloud metadata address.
    """
    # shortcut: the name is resolved here and again by urllib when it connects, so a
    # DNS answer that changes in between (rebinding) isn't caught; pin the address if
    # Hestia ever runs anywhere that matters more than one person's PC.
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(address, "ipv4_mapped", None):  # ::ffff:127.0.0.1 is 127.0.0.1
            address = address.ipv4_mapped
        if not address.is_global:
            return False
    return bool(infos)


def _check_url(url):
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ("http", "https"):
        raise ToolError("Only http:// and https:// web addresses can be fetched.")
    if not parts.hostname:
        raise ToolError(f"'{url}' has no website name in it.")
    if not _is_public(parts.hostname):
        raise ToolError(
            f"'{parts.hostname}' isn't a public internet address, so it can't be fetched — "
            "pages on this computer or the local network are off limits."
        )


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Hand redirects back unfollowed, so each hop is checked before it's visited."""
    def redirect_request(self, *args, **kwargs):
        return None


_WEB = urllib.request.build_opener(_NoRedirects)


class _PageText(html.parser.HTMLParser):
    """Visible text and title from HTML, minus scripts, styles and site chrome."""
    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form"}
    BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "pre", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skipping = 0
        self.in_title = False
        self.title = ""
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag == "title":
            self.in_title = True
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skipping = max(0, self.skipping - 1)
        elif tag == "title":
            self.in_title = False
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skipping:
            self.parts.append(data)

    def text(self):
        lines = (" ".join(line.split()) for line in "".join(self.parts).splitlines())
        return "\n".join(line for line in lines if line)


@server.tool()
def fetch_page(url: str) -> str:
    """Read one public web page and return its title and main text. Use it to read a result from web_search more fully, or a page the user names. Only public http/https addresses work, not pages on this computer or the local network. Pages that need JavaScript to show their content come back mostly empty. The address leaves this computer, so never put private information in it."""
    for _ in range(MAX_REDIRECTS + 1):
        _check_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            response = _WEB.open(request, timeout=FETCH_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
            if location:
                url = urllib.parse.urljoin(url, location)
                continue  # checked at the top of the loop before it's visited
            raise ToolError(f"The page returned an error ({e.code} {e.reason}).")
        except (urllib.error.URLError, OSError) as e:
            raise ToolError(f"Couldn't reach {urllib.parse.urlsplit(url).hostname}: "
                            f"{getattr(e, 'reason', e)}.")
        break
    else:
        raise ToolError(f"Gave up after {MAX_REDIRECTS} redirects.")

    with response:
        kind = response.headers.get_content_type()
        if kind not in _TEXT_TYPES:
            raise ToolError(f"That address is {kind}, not a web page, so it wasn't read.")
        raw = response.read(MAX_PAGE_BYTES + 1)
        charset = response.headers.get_content_charset() or "utf-8"

    try:
        codecs.lookup(charset)
    except LookupError:  # pages do declare charsets Python has never heard of
        charset = "utf-8"
    cut = len(raw) > MAX_PAGE_BYTES
    body = raw[:MAX_PAGE_BYTES].decode(charset, errors="replace")
    if kind == "text/plain":
        title, text = "", body.strip()
    else:
        parser = _PageText()
        parser.feed(body)
        title, text = " ".join(parser.title.split()), parser.text()
    if not text:
        text = "(No readable text — the page may need JavaScript to show its content.)"
    note = "\n\n(The page was larger than 2 MB and was cut short.)" if cut else ""
    return f"{title or '(no title)'}\n{url}\n\n{text}{note}"


if __name__ == "__main__":
    server.run()  # stdio transport by default
