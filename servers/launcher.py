"""
Hestia — shared program launcher for the tool servers (Stage 7, shared from Stage 10)

The MCP client runs each tool server inside a Windows job that kills
everything in it when Hestia exits, so an ordinary child process
(subprocess, os.startfile) dies with the server. WMI creates the process
itself, outside that job, and with the user's normal environment rather
than the ~12 variables a server is given.

Used by dev_tools (VS Code) and system_tools (launch_application); start_search
by web_tools and the engine, to get Docker and SearXNG going.
"""

import os
import subprocess
import time

import pythoncom
import win32com.client
from mcp.server.mcpserver.exceptions import ToolError


def launch(argv):
    """Start argv so it outlives this server. COM is set up per call: tools run on worker threads."""
    pythoncom.CoInitialize()
    try:
        processes = win32com.client.GetObject("winmgmts:").Get("Win32_Process")
        params = processes.Methods_("Create").InParameters.SpawnInstance_()
        params.CommandLine = subprocess.list2cmdline(argv)
        result = processes.ExecMethod_("Create", params)
    finally:
        pythoncom.CoUninitialize()
    if result.ReturnValue != 0:
        raise ToolError(f"Windows refused to start it (WMI error {result.ReturnValue}).")


DOCKER_DESKTOP = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
SEARCH_CONTAINER = "hestia-searxng"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _docker(*args, timeout=30):
    try:
        return subprocess.run(["docker", *args], capture_output=True, timeout=timeout,
                              creationflags=_NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def start_search(wait_seconds=180):
    """
    Get web search running: Docker Desktop if it isn't (it takes about a
    minute), then the SearXNG container. Blocks, so run it on a thread.
    True if the container was started.
    """
    if not _docker("info"):
        if not os.path.exists(DOCKER_DESKTOP):
            return False
        launch([DOCKER_DESKTOP])
        end = time.monotonic() + wait_seconds
        while not _docker("info", timeout=10):
            if time.monotonic() > end:
                return False
            time.sleep(3)
    return _docker("start", SEARCH_CONTAINER, timeout=60)
