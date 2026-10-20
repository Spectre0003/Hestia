import os
import unittest
from unittest import mock

import support
from support import git, make_repo, use_repos, write

import dev_tools  # noqa: E402  (support puts servers/ on the path)
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402


class LoadReposTest(unittest.TestCase):
    def test_keeps_git_repos_and_skips_missing_or_plain_folders(self):
        repo = make_repo(self)
        plain = support.temp_folder(self)
        config = write(support.temp_folder(self), "repos.yaml",
                       f"repos:\n  My Project: '{repo}'\n  plain: '{plain}'\n  gone: 'C:\\no\\such\\folder'\n")
        self.assertEqual(dev_tools._load_repos(config), {"myproject": ("My Project", repo)})

    def test_missing_or_broken_file_means_no_repos(self):
        folder = support.temp_folder(self)
        self.assertEqual(dev_tools._load_repos(os.path.join(folder, "nope.yaml")), {})
        self.assertEqual(dev_tools._load_repos(write(folder, "bad.yaml", "repos: [unclosed")), {})


class GitStatusTest(unittest.TestCase):
    def test_shows_branch_changes_and_recent_commits(self):
        repo = make_repo(self)
        use_repos(self, dev_tools, hestia=repo)
        write(repo, "readme.txt", "changed\n")
        write(repo, "new.txt", "new\n")
        status = dev_tools.git_status("hestia")
        for expected in ("branch main", "readme.txt", "new.txt", "First commit"):
            self.assertIn(expected, status)

    def test_repo_with_no_commits(self):
        use_repos(self, dev_tools, fresh=make_repo(self, commit=False))
        self.assertIn("No commits yet", dev_tools.git_status("fresh"))

    def test_names_match_loosely(self):
        use_repos(self, dev_tools, **{"My Project": make_repo(self)})
        self.assertIn("First commit", dev_tools.git_status("my-project"))

    def test_unlisted_repo_is_refused_with_the_list(self):
        use_repos(self, dev_tools, hestia=make_repo(self))
        with self.assertRaises(ToolError) as caught:
            dev_tools.git_status("someone-elses-repo")
        self.assertIn("hestia", str(caught.exception))

    def test_status_does_not_run_fsmonitor(self):
        repo = make_repo(self)
        hook_dir = support.temp_folder(self)
        marker = os.path.join(hook_dir, "MARKER")
        hook = write(hook_dir, "hook.sh", '#!/bin/sh\ntouch "$(dirname "$0")/MARKER"\nexit 1\n')
        git(repo, "config", "core.fsmonitor", hook.replace("\\", "/"))
        git(repo, "status", "--short")
        self.assertTrue(os.path.exists(marker), "plain git should run the hook, or this test proves nothing")
        os.remove(marker)
        use_repos(self, dev_tools, hestia=repo)
        dev_tools.git_status("hestia")
        self.assertFalse(os.path.exists(marker))


class GitDiffTest(unittest.TestCase):
    def test_shows_staged_and_unstaged_changes(self):
        repo = make_repo(self)
        use_repos(self, dev_tools, hestia=repo)
        write(repo, "staged.txt", "staged line\n")
        git(repo, "add", "staged.txt")
        write(repo, "readme.txt", "unstaged line\n")
        diff = dev_tools.git_diff("hestia")
        self.assertIn("Staged:", diff)
        self.assertIn("+staged line", diff)
        self.assertIn("Not staged:", diff)
        self.assertIn("+unstaged line", diff)

    def test_no_changes_is_said_plainly(self):
        use_repos(self, dev_tools, hestia=make_repo(self))
        self.assertIn("No changes", dev_tools.git_diff("hestia"))


class GitCommitTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        use_repos(self, dev_tools, hestia=self.repo)

    def commit_count(self):
        return int(git(self.repo, "rev-list", "--count", "HEAD").strip())

    def files_in_last_commit(self):
        return git(self.repo, "show", "--name-only", "--format=", "HEAD").split()

    def test_commits_the_named_file_with_the_exact_message(self):
        write(self.repo, "notes.txt", "remember milk\n")
        result = dev_tools.git_commit("hestia", ["notes.txt"], "Add notes")
        self.assertIn("Committed", result)
        self.assertEqual(self.files_in_last_commit(), ["notes.txt"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%B").strip(), "Add notes")
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an").strip(), "Test User")

    def test_commit_only_includes_named_files(self):
        write(self.repo, "other.txt", "staged by the user\n")
        git(self.repo, "add", "other.txt")
        write(self.repo, "notes.txt", "notes\n")
        dev_tools.git_commit("hestia", ["notes.txt"], "Add notes")
        self.assertEqual(self.files_in_last_commit(), ["notes.txt"])
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only").split(), ["other.txt"])

    def test_files_in_subfolders(self):
        write(self.repo, "docs/plan.md", "plan\n")
        dev_tools.git_commit("hestia", ["docs/plan.md"], "Add plan")
        self.assertEqual(self.files_in_last_commit(), ["docs/plan.md"])

    def test_commit_refuses_paths_outside_repo(self):
        outside = write(support.temp_folder(self), "x.txt", "x\n")
        for bad in ("../x.txt", outside, ".git/config", ".", ""):
            with self.subTest(path=bad), self.assertRaises(ToolError):
                dev_tools.git_commit("hestia", [bad], "Sneaky")
        self.assertEqual(self.commit_count(), 1)

    def test_commit_treats_option_like_names_as_paths(self):
        write(self.repo, "--amend", "odd name\n")
        dev_tools.git_commit("hestia", ["--amend"], "Add oddly named file")
        self.assertEqual(self.commit_count(), 2)
        self.assertEqual(self.files_in_last_commit(), ["--amend"])

    def test_nothing_to_commit_is_said_plainly(self):
        with self.assertRaises(ToolError) as caught:
            dev_tools.git_commit("hestia", ["readme.txt"], "No change")
        self.assertIn("Nothing to commit", str(caught.exception))
        self.assertEqual(self.commit_count(), 1)

    def test_needs_a_message_and_files(self):
        write(self.repo, "notes.txt", "notes\n")
        with self.assertRaises(ToolError):
            dev_tools.git_commit("hestia", ["notes.txt"], "   ")
        with self.assertRaises(ToolError):
            dev_tools.git_commit("hestia", [], "Add notes")
        self.assertEqual(self.commit_count(), 1)


class OpenInVSCodeTest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo(self)
        use_repos(self, dev_tools, hestia=self.repo)
        self.launched = []
        self.addCleanup(setattr, dev_tools, "_launch", dev_tools._launch)
        dev_tools._launch = self.launched.append

    def fake_install(self):
        root = support.temp_folder(self)
        exe = write(root, "Code.exe", "")
        shim = write(root, os.path.join("bin", "code.cmd"), "")
        return exe, shim

    def test_finds_code_exe_next_to_the_code_command(self):
        exe, shim = self.fake_install()
        with mock.patch.object(dev_tools.shutil, "which", return_value=shim):
            self.assertEqual(dev_tools._code_exe(), exe)

    def test_falls_back_to_the_default_install_folder(self):
        local = support.temp_folder(self)
        exe = write(local, os.path.join("Programs", "Microsoft VS Code", "Code.exe"), "")
        with mock.patch.object(dev_tools.shutil, "which", return_value=None), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": local}):
            self.assertEqual(dev_tools._code_exe(), exe)

    def test_opens_a_listed_repo_with_code_exe_not_the_cmd_wrapper(self):
        exe, _ = self.fake_install()
        with mock.patch.object(dev_tools, "_code_exe", return_value=exe):
            result = dev_tools.open_in_vscode("hestia")
        self.assertEqual(self.launched, [[exe, self.repo]])
        self.assertIn("hestia", result)

    def test_refuses_an_unlisted_repo(self):
        with self.assertRaises(ToolError):
            dev_tools.open_in_vscode("somewhere-else")
        self.assertEqual(self.launched, [])

    def test_says_so_when_vscode_is_missing(self):
        with mock.patch.object(dev_tools, "_code_exe", return_value=None):
            with self.assertRaises(ToolError) as caught:
                dev_tools.open_in_vscode("hestia")
        self.assertIn("VS Code", str(caught.exception))

    def test_launch_environment_is_the_users_full_one(self):
        # The server itself only gets ~12 variables from the MCP client;
        # VS Code's terminals would inherit that if it were passed on.
        env = dev_tools._user_environment()
        names = {name.lower() for name in env}
        for needed in ("comspec", "windir", "programfiles", "path", "userprofile"):
            self.assertIn(needed, names)


class RegistrationTest(unittest.TestCase):
    def test_open_in_vscode_is_write_tier(self):
        import tools
        self.assertEqual(tools.TOOL_TIERS["dev_tools"]["open_in_vscode"], tools.TIER_WRITE)

    def test_git_commit_is_write_tier(self):
        import tools
        self.assertEqual(tools.TOOL_TIERS["dev_tools"]["git_commit"], tools.TIER_WRITE)

    def test_git_read_tools_are_read_tier(self):
        import tools
        self.assertEqual(tools.TOOL_TIERS["dev_tools"]["git_status"], tools.TIER_READ)
        self.assertEqual(tools.TOOL_TIERS["dev_tools"]["git_diff"], tools.TIER_READ)
        self.assertIn("dev_tools", tools.SERVERS)


if __name__ == "__main__":
    unittest.main()
