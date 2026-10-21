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

import json
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


if __name__ == "__main__":
    server.run()  # stdio transport by default
