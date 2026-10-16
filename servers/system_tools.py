"""
Hestia — system tool server (Stage 6 / v0.6)

An MCP server for reading and acting on the machine itself, kept separate
from hestia_core.py (which owns the workspace folder and the clock). The
split is deliberate: these tools have no sandbox to be confined to, so
keeping them in their own process and their own tier block makes the
blast radius obvious at a glance.

As with hestia_core, this file only describes and runs tools. Whether a
tool is *allowed* to run is decided in tools.py (TOOL_TIERS), never here.
What a tool is allowed to reach — which applications may be launched —
is scoped here, the same way the workspace root is scoped in hestia_core.

Run directly (`python servers/system_tools.py`) it just waits for an MCP
client on stdin; chat.py starts it itself.
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# WARNING, not the default INFO: this process's stderr goes to a log file,
# and per-request INFO lines would bury anything useful.
server = MCPServer("system_tools", log_level="WARNING")


if __name__ == "__main__":
    server.run()  # stdio transport by default
