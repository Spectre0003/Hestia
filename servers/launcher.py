"""
Hestia — shared program launcher for the tool servers (Stage 7, shared from Stage 10)

The MCP client runs each tool server inside a Windows job that kills
everything in it when Hestia exits, so an ordinary child process
(subprocess, os.startfile) dies with the server. WMI creates the process
itself, outside that job, and with the user's normal environment rather
than the ~12 variables a server is given.

Used by dev_tools (VS Code) and system_tools (launch_application).
"""

import subprocess

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
