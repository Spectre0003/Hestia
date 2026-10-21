"""
Shared test setup. Nothing here touches the real data/hestia.db or the
real repos.yaml: repos are throwaway folders, and each test points the
dev_tools server's allowlist at them directly.
"""
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "servers"))


def git(path, *args):
    """Run git directly, for setting up test repos (not through Hestia)."""
    return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, check=True).stdout


def write(folder, name, text):
    full = os.path.join(folder, name)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)
    return full


def temp_folder(test):
    folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    test.addCleanup(folder.cleanup)
    return os.path.realpath(folder.name)


def make_repo(test, commit=True):
    """A throwaway git repo with its own identity and, by default, one commit."""
    path = temp_folder(test)
    git(path, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Test User"), ("user.email", "test@example.com"),
                       ("commit.gpgsign", "false")):
        git(path, "config", key, value)
    if commit:
        write(path, "readme.txt", "hello\n")
        git(path, "add", "readme.txt")
        git(path, "commit", "-q", "-m", "First commit")
    return path


def temp_db(test):
    """Point storage (and the server log) at a throwaway folder. Returns (conn, session_id)."""
    import storage
    import tools
    folder = temp_folder(test)
    for module, attr in ((storage, "DB_DIR"), (storage, "DB_PATH"), (tools, "SERVER_LOG_PATH")):
        test.addCleanup(setattr, module, attr, getattr(module, attr))
    storage.DB_DIR = folder
    storage.DB_PATH = os.path.join(folder, "test.db")
    tools.SERVER_LOG_PATH = os.path.join(folder, "mcp_servers.log")
    conn = storage.get_connection()
    test.addCleanup(conn.close)
    return conn, storage.start_new_session(conn)


def fake_tool(test, server, name, result="ok"):
    """
    Make `name` look like a tool `server` offers, without starting any
    server: tools.execute runs a stub that records each call's arguments
    and returns ("ok", result). Returns the list of recorded arguments.
    """
    import tools
    calls = []
    tools._tools[name] = {"server": server, "description": "",
                          "parameters": {"type": "object", "properties": {}, "required": []}}
    test.addCleanup(tools._tools.pop, name, None)
    previous = tools._call

    def stub(srv, tool_name, arguments):
        if tool_name != name:
            return previous(srv, tool_name, arguments)
        calls.append(arguments)
        return "ok", result

    tools._call = stub
    test.addCleanup(setattr, tools, "_call", previous)
    return calls


def mock_input(*answers):
    """Feed input() these answers, as if typed."""
    from unittest import mock
    import io as _io
    return mock.patch("sys.stdin", _io.StringIO("".join(a + "\n" for a in answers)))


def use_repos(test, module, **repos):
    """Point module.REPOS at the given name=path pairs for one test."""
    test.addCleanup(setattr, module, "REPOS", module.REPOS)
    module.REPOS = {module._normalise(name): (name, path) for name, path in repos.items()}
