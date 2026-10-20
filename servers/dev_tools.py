"""
Hestia — dev environment tool server (Stage 7 / v0.7)

Git, VS Code and Docker, each of which can run arbitrary programs in
ways that aren't obvious: a repo's own config can make `git status`
execute something (core.fsmonitor), diff drivers can run on a diff,
and `docker run` / `wsl <command>` are shells by another name. So the
same rule as system_tools: narrow tools with fixed subcommands, and
the model supplies only names, paths and messages — never command text.

Everything here is scoped by repos.yaml, a user-written allowlist read
once at startup, like apps.yaml. Hestia can't edit it: write_file is
confined to workspace/ and this file lives in the project root.

As with the other servers, whether a tool may run at all is decided in
tools.py (TOOL_TIERS), never here.
"""

import os
import re
import shutil
import subprocess

import pythoncom
import win32com.client
import yaml
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

server = MCPServer("dev_tools", log_level="WARNING")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPOS_PATH = os.path.join(_ROOT, "repos.yaml")

GIT_TIMEOUT_SECONDS = 15
RECENT_COMMITS = 5

# Settings a repo's own .git/config could use to run a program during an
# ordinary read. Forced off for every call, so even a listed repo's config
# can't turn `git status` into code execution.
_SAFE_GIT = ["--no-pager", "-c", "core.fsmonitor=false", "-c", "core.pager=cat"]


def _normalise(name):
    """Fold case, spaces and punctuation so 'My Project' and 'my-project' match."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _load_repos(path=REPOS_PATH):
    """
    Read the repo allowlist into {normalised: (display name, folder)}.
    Entries that don't point at a git repo are skipped. Fails soft: a
    missing or broken file costs the git tools, not startup.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return {}
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, dict):
        return {}
    found = {}
    for name, folder in repos.items():
        if not name or not folder:
            continue
        folder = os.path.realpath(os.path.expanduser(str(folder)))
        if os.path.isdir(os.path.join(folder, ".git")):
            found[_normalise(str(name))] = (str(name), folder)
    return found


# Read once at startup, so the list can't change mid-conversation.
REPOS = _load_repos()
_REPO_NAMES = ", ".join(sorted(display for display, _ in REPOS.values())) or "(none configured)"
_REPO_HINT = (
    f"Repositories available: {_REPO_NAMES}. Names match loosely, so pass "
    "whatever the user called it; if unsure, call this anyway — the result "
    "lists what is available. Don't refuse on your own without calling it."
)


def _repo(name):
    """(display name, folder) for a listed repo, or a ToolError naming the list."""
    if not REPOS:
        raise ToolError("No repositories are listed in repos.yaml, so no git tools can be used.")
    entry = REPOS.get(_normalise(name))
    if entry is None:
        names = ", ".join(sorted(display for display, _ in REPOS.values()))
        raise ToolError(f"'{name}' isn't one of the listed repositories: {names}.")
    return entry


def _git(folder, *args):
    """Run one fixed git command in a listed repo. Returns (exit code, stdout, stderr)."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        done = subprocess.run(
            ["git", *_SAFE_GIT, "-C", folder, *args],
            stdin=subprocess.DEVNULL, capture_output=True, env=env,
            timeout=GIT_TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        raise ToolError("git isn't installed, or isn't on PATH.")
    except subprocess.TimeoutExpired:
        raise ToolError(f"git took longer than {GIT_TIMEOUT_SECONDS} seconds and was stopped.")
    decode = lambda b: b.decode("utf-8", errors="replace").strip()  # noqa: E731
    return done.returncode, decode(done.stdout), decode(done.stderr)


@server.tool(description=(
    "Show a git repository's current branch, uncommitted changes (including "
    "new untracked files) and its most recent commits. " + _REPO_HINT
))
def git_status(repo: str) -> str:
    display, folder = _repo(repo)
    code, out, err = _git(folder, "status", "--short", "--branch")
    if code != 0:
        raise ToolError(err or out or f"git status failed in {display}.")
    lines = out.splitlines()
    branch = lines[0][3:] if lines and lines[0].startswith("## ") else "unknown"
    changes = lines[1:]

    result = [f"Repository: {display} — branch {branch}"]
    result.append("Uncommitted changes:" if changes else "No uncommitted changes.")
    result += [f"  {line}" for line in changes]
    code, log, _ = _git(folder, "log", f"-{RECENT_COMMITS}", "--format=%h %ad %an: %s", "--date=short")
    if code == 0 and log:
        result.append("Recent commits:")
        result += [f"  {line}" for line in log.splitlines()]
    else:
        result.append("No commits yet.")
    return "\n".join(result)


@server.tool(description=(
    "Show the line-by-line changes in a git repository that haven't been "
    "committed yet, staged and not staged. " + _REPO_HINT
))
def git_diff(repo: str) -> str:
    display, folder = _repo(repo)
    parts = []
    # --no-ext-diff / --no-textconv: a repo can configure diff drivers that
    # run programs; neither should run on a read.
    for label, extra in (("Staged", ["--cached"]), ("Not staged", [])):
        code, out, err = _git(folder, "diff", "--no-ext-diff", "--no-textconv", *extra)
        if code != 0:
            raise ToolError(err or out or f"git diff failed in {display}.")
        if out:
            parts.append(f"{label}:\n{out}")
    if not parts:
        return f"No changes in {display}. (New untracked files don't show in a diff; git_status lists them.)"
    return "\n\n".join(parts)


def _path_in_repo(folder, path):
    """
    A file path from the model, as a path relative to the repo — or None
    if it resolves outside the repo, into .git, or to the repo itself
    (committing '.' would sweep in everything, not just named files).
    """
    if not (path or "").strip():
        return None
    full = os.path.realpath(os.path.join(folder, path))
    try:
        inside = os.path.normcase(os.path.commonpath([full, folder])) == os.path.normcase(folder)
    except ValueError:  # a different drive entirely
        return None
    relative = os.path.relpath(full, folder)
    first = relative.split(os.sep)[0]
    if not inside or relative == "." or os.path.normcase(first) == ".git":
        return None
    return relative


@server.tool(description=(
    "Commit changes in a git repository. Pass the files to include, as "
    "paths inside the repository (e.g. 'notes.txt' or 'docs/plan.md'), "
    "and a commit message. Only the named files are committed — nothing "
    "else that happens to be staged. The user approves every commit "
    "before it happens. Nothing is pushed. " + _REPO_HINT
))
def git_commit(repo: str, files: list[str], message: str) -> str:
    display, folder = _repo(repo)
    message = (message or "").strip()
    if not message:
        raise ToolError("A commit needs a message; nothing was committed.")
    if not files:
        raise ToolError("Name the files to commit; nothing was committed.")

    paths = []
    for path in files:
        relative = _path_in_repo(folder, path)
        if relative is None:
            raise ToolError(f"'{path}' isn't a file inside {display}, so nothing was committed.")
        paths.append(relative)

    # "--" so a file named like an option ("--amend") is still a file.
    code, out, err = _git(folder, "add", "--", *paths)
    if code != 0:
        raise ToolError(err or out or "git add failed; nothing was committed.")
    # Pathspecs on commit mean "only these": anything else the user had
    # staged stays staged and out of this commit.
    code, out, err = _git(folder, "commit", "-m", message, "--", *paths)
    if code != 0:
        if "nothing" in out.lower() or "no changes" in out.lower():
            raise ToolError(f"Nothing to commit: {', '.join(paths)} has no changes in {display}.")
        raise ToolError(err or out or "git commit failed.")
    _, short, _ = _git(folder, "log", "-1", "--format=%h")
    return f"Committed {short} in {display}: {message.splitlines()[0]} ({len(paths)} file(s)). Not pushed."


def _code_exe():
    """
    VS Code's own Code.exe — never the `code.cmd` wrapper on PATH: passing
    arguments through a Windows batch file is a known injection class.
    """
    candidates = []
    shim = shutil.which("code")
    if shim:  # <install>\bin\code.cmd -> <install>\Code.exe
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(shim)), "Code.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "Microsoft VS Code", "Code.exe"))
    return next((path for path in candidates if os.path.isfile(path)), None)


def _launch(argv):
    """
    Start a program so it outlives this server. The MCP client runs each
    server inside a Windows job that kills everything in it when Hestia
    exits, so an ordinary child process (subprocess, os.startfile) would
    die with it. WMI creates the process itself, outside that job, and
    with the user's normal environment rather than the ~12 variables this
    server was given. COM is set up per call: tools run on worker threads.
    """
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


@server.tool(description=(
    "Open a git repository's folder in VS Code. The user approves it "
    "before it happens. " + _REPO_HINT
))
def open_in_vscode(repo: str) -> str:
    display, folder = _repo(repo)
    exe = _code_exe()
    if exe is None:
        raise ToolError("VS Code isn't installed, or couldn't be found.")
    _launch([exe, folder])
    return f"Opened {display} in VS Code."


DOCKER_TIMEOUT_SECONDS = 30

# What the docker CLI says when it's installed but Docker Desktop's engine
# isn't up: a named-pipe connect failure on Windows, a daemon message elsewhere.
_DOCKER_DOWN = re.compile(r"error during connect|cannot connect to the docker daemon|docker desktop is (not running|stopped)",
                          re.IGNORECASE)


def _docker(*args):
    """Run one fixed docker command. Returns (exit code, stdout, stderr)."""
    try:
        done = subprocess.run(
            ["docker", *args],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=DOCKER_TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        raise ToolError("Docker isn't installed, or isn't on PATH.")
    except subprocess.TimeoutExpired:
        raise ToolError(f"Docker took longer than {DOCKER_TIMEOUT_SECONDS} seconds and was stopped.")
    out = done.stdout.decode("utf-8", errors="replace").strip()
    err = done.stderr.decode("utf-8", errors="replace").strip()
    if done.returncode != 0 and _DOCKER_DOWN.search(err):
        raise ToolError("Docker Desktop isn't running. Start it, wait for it to finish starting, then try again.")
    return done.returncode, out, err


@server.tool()
def list_containers() -> str:
    """List the Docker containers on this computer, running or stopped, with their status and image. Use this when the user asks what containers they have or whether one is running."""
    code, out, err = _docker("ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}")
    if code != 0:
        raise ToolError(err or out or "docker ps failed.")
    if not out:
        return "No Docker containers exist yet."
    lines = ["Docker containers:"]
    for row in out.splitlines():
        name, status, image = (row.split("\t") + ["", ""])[:3]
        lines.append(f"  {name} — {status} (image {image})")
    return "\n".join(lines)


def _container(name):
    """The real name of an existing container matching `name` loosely, or a ToolError with the list."""
    code, out, err = _docker("ps", "-a", "--format", "{{.Names}}")
    if code != 0:
        raise ToolError(err or out or "docker ps failed.")
    existing = {_normalise(real): real for real in out.splitlines() if real}
    match = existing.get(_normalise(name))
    if match is None:
        names = ", ".join(sorted(existing.values())) or "(none)"
        raise ToolError(f"There's no container called '{name}'. Existing containers: {names}.")
    # The name passed to docker comes from docker's own list, never from the model.
    return match


@server.tool()
def start_container(name: str) -> str:
    """Start an existing, stopped Docker container by name. It can't create new containers or run images. The user approves it before it happens."""
    real = _container(name)
    code, out, err = _docker("start", real)
    if code != 0:
        raise ToolError(err or out or f"Couldn't start {real}.")
    return f"Started {real}."


@server.tool()
def stop_container(name: str) -> str:
    """Stop a running Docker container by name. The user approves it before it happens."""
    real = _container(name)
    code, out, err = _docker("stop", real)
    if code != 0:
        raise ToolError(err or out or f"Couldn't stop {real}.")
    return f"Stopped {real}."


if __name__ == "__main__":
    server.run()  # stdio transport by default
