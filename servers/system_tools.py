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

import os
import platform
import time
from datetime import timedelta

import psutil
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# WARNING, not the default INFO: this process's stderr goes to a log file,
# and per-request INFO lines would bury anything useful.
server = MCPServer("system_tools", log_level="WARNING")


def _gb(n_bytes):
    return f"{n_bytes / 1_073_741_824:.1f} GB"


@server.tool()
def get_system_info() -> str:
    """Get information about this computer: operating system, CPU, memory use, disk space on the system drive, battery level, and how long it has been running. Use this when the user asks how their machine is doing, whether it is low on memory or disk space, or what it is running."""
    lines = [
        f"OS: {platform.system()} {platform.release()} ({platform.version()})",
        f"CPU: {psutil.cpu_count(logical=False) or '?'} physical cores, "
        f"{psutil.cpu_count(logical=True) or '?'} logical, "
        f"{psutil.cpu_percent(interval=0.3):.0f}% in use",
    ]

    memory = psutil.virtual_memory()
    lines.append(
        f"Memory: {_gb(memory.used)} used of {_gb(memory.total)} "
        f"({memory.percent:.0f}%), {_gb(memory.available)} available"
    )

    # The system drive, not wherever Hestia happens to be running from.
    system_drive = os.environ.get("SystemDrive", "") + os.sep or os.path.abspath(os.sep)
    try:
        disk = psutil.disk_usage(system_drive)
        lines.append(
            f"Disk ({system_drive.rstrip(os.sep) or os.sep}): {_gb(disk.used)} used of "
            f"{_gb(disk.total)} ({disk.percent:.0f}%), {_gb(disk.free)} free"
        )
    except OSError as e:
        lines.append(f"Disk: couldn't be read ({e.__class__.__name__})")

    # Desktops have no battery; psutil returns None rather than raising.
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        lines.append("Battery: none (desktop or not reported)")
    else:
        state = "charging" if battery.power_plugged else "on battery"
        lines.append(f"Battery: {battery.percent:.0f}% ({state})")

    uptime = timedelta(seconds=int(time.time() - psutil.boot_time()))
    lines.append(f"Running since last boot: {uptime}")

    return "\n".join(lines)


if __name__ == "__main__":
    server.run()  # stdio transport by default
