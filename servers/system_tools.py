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
import re
import time
from datetime import timedelta

import psutil
import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

# WARNING, not the default INFO: this process's stderr goes to a log file,
# and per-request INFO lines would bury anything useful.
server = MCPServer("system_tools", log_level="WARNING")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_PATH = os.path.join(_ROOT, "apps.yaml")


def _normalise(name):
    """Fold case, spaces and punctuation so 'VS Code' and 'vscode' match."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _load_apps():
    """
    Read the launch allowlist into {normalised: (display name, target)}.

    Fails soft, unlike personality.yaml: a missing or malformed list
    should cost one tool, not stop Hestia starting. launch_application
    then refuses everything and says why.
    """
    try:
        with open(APPS_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    apps = data.get("apps")
    if not isinstance(apps, dict):
        return {}
    return {
        _normalise(name): (str(name), str(target))
        for name, target in apps.items()
        if name and target
    }


# Read once at startup. Editing apps.yaml takes effect on the next run,
# which also means the list can't change midway through a conversation.
APPS = _load_apps()
_APP_NAMES = sorted(display for display, _ in APPS.values())


def _gb(n_bytes):
    """For system totals, which are always gigabyte-scale."""
    return f"{n_bytes / 1_073_741_824:.1f} GB"


def _size(n_bytes):
    """Adaptive, for per-process figures: most are megabytes, a few aren't."""
    if n_bytes >= 1_073_741_824:
        return f"{n_bytes / 1_073_741_824:.1f} GB"
    return f"{n_bytes / 1_048_576:.0f} MB"


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


# Processes are grouped by name before being capped: a browser alone can
# account for twenty entries, which would crowd out everything else and
# tell the user nothing they didn't know.
MAX_PROCESS_GROUPS = 25


@server.tool()
def list_processes(name_filter: str = "") -> str:
    """List programs currently running on this computer, grouped by name and ordered by how much memory they are using. Use this when the user asks what is running, what is using their memory, or whether a particular program is open. Pass name_filter to look for one program, e.g. 'chrome'."""
    wanted = (name_filter or "").strip().lower()

    groups = {}
    unreadable = 0
    for process in psutil.process_iter(["name", "memory_info"]):
        try:
            name = process.info["name"] or "(unknown)"
            memory = process.info["memory_info"]
            rss = memory.rss if memory else 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Normal on Windows: plenty of system processes refuse to be read.
            unreadable += 1
            continue
        if wanted and wanted not in name.lower():
            continue
        count, total = groups.get(name, (0, 0))
        groups[name] = (count + 1, total + rss)

    if not groups:
        if wanted:
            return f"Nothing matching '{name_filter}' is running."
        return "No processes could be read."

    ordered = sorted(groups.items(), key=lambda item: item[1][1], reverse=True)
    shown = ordered[:MAX_PROCESS_GROUPS]
    width = max(len(name) for name, _ in shown)

    total_processes = sum(count for _, (count, _) in ordered)
    if wanted:
        header = f"Matching '{name_filter}': {total_processes} process(es) in {len(ordered)} program(s)."
    else:
        header = f"{total_processes} processes running, in {len(ordered)} programs, by memory use:"

    lines = [header]
    for name, (count, total) in shown:
        plural = "process" if count == 1 else "processes"
        lines.append(f"  {name:<{width}}  {_size(total):>9}  ({count} {plural})")
    if len(ordered) > len(shown):
        lines.append(f"  ...and {len(ordered) - len(shown)} more programs")
    if unreadable:
        lines.append(f"({unreadable} process(es) couldn't be read \u2014 normal for system processes.)")
    return "\n".join(lines)


@server.tool(
    description=(
        "Open an application on this computer. The applications available are: "
        + ", ".join(_APP_NAMES) + ". Names are matched loosely, so spacing, "
        "capitalisation and punctuation do not matter — pass whatever the user "
        "called it. If you are not sure whether something is available, call this "
        "anyway: the result will say so and list what is. Do not refuse on your "
        "own without calling it."
        if _APP_NAMES else
        "Open an application on this computer. No applications are currently "
        "configured, so this tool cannot open anything; say so if asked."
    )
)
def launch_application(name: str) -> str:
    """Open an application on this computer."""
    if not APPS:
        raise ToolError(
            "No applications are configured in apps.yaml, so nothing can be opened."
        )

    entry = APPS.get(_normalise(name))
    if entry is None:
        raise ToolError(
            f"'{name}' isn't on the list of applications that can be opened. "
            f"The list is: {', '.join(_APP_NAMES)}."
        )

    display, target = entry
    try:
        # Windows handles .exe, .lnk and URI protocols alike here. The
        # target comes from apps.yaml, never from the model, and nothing
        # is appended to it — no arguments reach the launched program.
        os.startfile(target)
    except AttributeError:
        raise ToolError("Opening applications is only supported on Windows.")
    except OSError as e:
        raise ToolError(
            f"Couldn't open {display}: {e.strerror or e}. "
            f"Its entry in apps.yaml may point somewhere that no longer exists."
        )
    return f"Opened {display}."


if __name__ == "__main__":
    server.run()  # stdio transport by default
