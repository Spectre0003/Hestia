"""
Hestia — core tool server (Stage 5 / v0.5)

An MCP server holding Hestia's own built-in tools. It runs as a separate
process that tools.py starts and talks to over stdio — no network port
is opened. This file only describes and runs tools; whether a tool is
*allowed* to run is decided in tools.py (TOOL_TIERS), never here.

File tools can only reach the workspace folder: `workspace/` in the
directory Hestia is run from, next to `data/`. Every path the model
gives is fully resolved — "..", links, and junctions included — and has
to still land inside the workspace, or the call is refused.

Each tool's docstring is the description the model sees, so it's
written for the model: what the tool does, and when to use it. A
ToolError's message reaches the model too, so those are written for it
as well.

Run directly (`python servers/hestia_core.py`) it just waits for an
MCP client on stdin — there's nothing to see; chat.py starts it itself.
"""

import os
import re
from datetime import datetime

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# WARNING, not the default INFO: this process's stderr goes to a log
# file, and per-request INFO lines would just bury anything useful.
server = MCPServer("hestia_core", log_level="WARNING")

# Resolved once at startup, links and all, so every check below compares
# against the real location rather than whatever the name points to.
WORKSPACE = os.path.realpath("workspace")
os.makedirs(WORKSPACE, exist_ok=True)

MAX_READ_BYTES = 200_000  # tools.py trims what actually reaches the model further
MAX_WRITE_CHARS = 100_000
MAX_LIST_ENTRIES = 200

# Windows device names: opening "nul" or "con.txt" reaches a device, not a file.
_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def _resolve(path):
    """
    Turn a path from the model into a real path inside WORKSPACE, or
    raise ToolError. The containment check at the end is what actually
    enforces the sandbox: it runs on the fully resolved path, so "..",
    absolute paths, and links or junctions pointing outside all fail it.
    The checks before it only give the model a clearer reason.
    """
    path = (path or "").strip() or "."
    drive, rest = os.path.splitdrive(path)
    if drive or rest.startswith(("/", "\\")):
        raise ToolError("Use a path relative to the workspace folder, like 'notes.txt' or 'projects/plan.md'.")
    if ":" in path:
        # "notes.txt:hidden" would address an NTFS alternate data stream.
        raise ToolError("File paths can't contain ':'.")
    for part in re.split(r"[\\/]", path):
        if part.split(".")[0].strip().lower() in _RESERVED_NAMES:
            raise ToolError(f"'{part}' is a reserved device name on Windows, not a usable file name.")

    full = os.path.realpath(os.path.join(WORKSPACE, path))
    try:
        inside = os.path.normcase(os.path.commonpath([full, WORKSPACE])) == os.path.normcase(WORKSPACE)
    except ValueError:  # resolved onto a different drive entirely
        inside = False
    if not inside:
        raise ToolError("That path is outside the workspace folder, which is the only place file tools can reach.")
    return full


def _display(full):
    """A resolved path as the model should see it: relative to the workspace."""
    return os.path.relpath(full, WORKSPACE).replace("\\", "/")


@server.tool()
def get_current_time() -> str:
    """Get the current local date and time. Use this when the user asks what time it is, today's date, or the day of the week."""
    return datetime.now().astimezone().strftime("%A, %d %B %Y, %H:%M (%Z)")


@server.tool()
def list_files(path: str = ".") -> str:
    """List the files and folders in the user's workspace folder, or in a folder inside it. The path is relative to the workspace: use '.' for the workspace itself, or e.g. 'projects'."""
    folder = _resolve(path)
    if not os.path.isdir(folder):
        raise ToolError(f"'{path}' isn't a folder in the workspace.")
    with os.scandir(folder) as scan:
        entries = sorted(scan, key=lambda e: (not e.is_dir(), e.name.lower()))
    if not entries:
        return f"'{_display(folder)}' is empty."
    lines = [
        f"{entry.name}/" if entry.is_dir() else f"{entry.name}  ({entry.stat().st_size} bytes)"
        for entry in entries[:MAX_LIST_ENTRIES]
    ]
    if len(entries) > MAX_LIST_ENTRIES:
        lines.append(f"...and {len(entries) - MAX_LIST_ENTRIES} more")
    return "\n".join(lines)


@server.tool()
def read_file(path: str) -> str:
    """Read a text file from the user's workspace folder. The path is relative to the workspace, e.g. 'notes.txt' or 'projects/plan.md'."""
    full = _resolve(path)
    if not os.path.isfile(full):
        raise ToolError(f"There's no file called '{path}' in the workspace.")
    with open(full, "rb") as f:
        data = f.read(MAX_READ_BYTES + 1)
    if b"\x00" in data:
        raise ToolError(f"'{path}' looks like a binary file, not text.")
    if not data:
        return f"'{path}' is empty."
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    if len(data) > MAX_READ_BYTES:
        text += f"\n[...file continues past {MAX_READ_BYTES} bytes]"
    return text


@server.tool()
def write_file(path: str, content: str, overwrite: bool = False) -> str:
    """Save text to a file in the user's workspace folder, creating the file and any folders in its path. The path is relative to the workspace, e.g. 'notes.txt'. Won't replace an existing file unless overwrite is true. The user approves every write before it happens."""
    full = _resolve(path)
    if os.path.isdir(full):
        raise ToolError(f"'{path}' is a folder, not a file.")
    if os.path.exists(full) and not overwrite:
        raise ToolError(f"'{path}' already exists. Ask the user before replacing it, and only then pass overwrite=true.")
    if len(content) > MAX_WRITE_CHARS:
        raise ToolError(f"That's {len(content)} characters; the limit for one file is {MAX_WRITE_CHARS}.")
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Saved {len(content)} characters to '{_display(full)}'."


if __name__ == "__main__":
    server.run()  # stdio transport by default
