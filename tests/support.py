"""
Shared test setup. Nothing here touches the real data/hestia.db or the
real repos.yaml: repos are throwaway folders, and each test points the
dev_tools server's allowlist at them directly.
"""
import os
import subprocess
import sys
import tempfile

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


def use_repos(test, module, **repos):
    """Point module.REPOS at the given name=path pairs for one test."""
    test.addCleanup(setattr, module, "REPOS", module.REPOS)
    module.REPOS = {module._normalise(name): (name, path) for name, path in repos.items()}
