import unittest

import support  # noqa: F401  (puts servers/ on the path)

import system_tools  # noqa: E402


def utf16(text):
    """wsl.exe writes UTF-16 LE, not the console code page."""
    return text.encode("utf-16-le")


class WslSummaryTest(unittest.TestCase):
    def test_lists_each_distro_with_state_and_version(self):
        raw = utf16("  NAME              STATE           VERSION\r\n"
                    "* Ubuntu            Running         2\r\n"
                    "  docker-desktop    Stopped         2\r\n")
        self.assertEqual(system_tools._wsl_summary(raw),
                         "WSL: Ubuntu (running, WSL2), docker-desktop (stopped, WSL2)")

    def test_not_installed(self):
        raw = utf16("The Windows Subsystem for Linux is not installed. "
                    "You can install by running 'wsl.exe --install'.\r\n")
        self.assertEqual(system_tools._wsl_summary(raw), "WSL: not installed")

    def test_installed_without_distros(self):
        raw = utf16("Windows Subsystem for Linux has no installed distributions.\r\n")
        self.assertEqual(system_tools._wsl_summary(raw), "WSL: installed, no Linux distributions")

    def test_wsl_exe_missing(self):
        self.assertEqual(system_tools._wsl_summary(None), "WSL: not available")

    def test_description_tells_the_model_wsl_is_covered(self):
        # Without this, "is WSL set up?" got no tool call 3 times out of 3.
        self.assertIn("WSL", system_tools.get_system_info.__doc__)

    def test_system_info_includes_a_wsl_line(self):
        self.assertIn("WSL: ", system_tools.get_system_info())


# Like dev_tools' launch test: a helper inside a kill-on-close Windows job
# (what the MCP client puts every server in), with the server's stripped
# environment, opening a .lnk entry from apps.yaml on a worker thread, then
# closing the job. The shortcut points at a harmless sleeper that reports
# its pid; the test checks the sleeper outlived the job.
_APP_HELPER = r'''
import os, sys, threading, time
import win32api, win32job
repo, shortcut = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, "servers"))
job = win32job.CreateJobObject(None, "")
info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
win32job.AssignProcessToJobObject(job, win32api.GetCurrentProcess())
import system_tools
system_tools.APPS = {"sleeper": ("sleeper", shortcut)}
worker = threading.Thread(target=system_tools.launch_application, args=("sleeper",))
worker.start(); worker.join()
time.sleep(5)
win32api.CloseHandle(job)
'''


class LaunchApplicationTest(unittest.TestCase):
    def test_an_opened_app_outlives_hestia(self):
        import os
        import psutil
        import win32com.client
        folder = support.temp_folder(self)
        report = os.path.join(folder, "report.txt")
        sleeper = support.write(folder, "sleeper.py",
                                "import os, sys, time\n"
                                "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
                                "time.sleep(60)\n")
        shortcut = os.path.join(folder, "sleeper.lnk")
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(shortcut)
        link.TargetPath = support.sys.executable
        link.Arguments = f'"{sleeper}" "{report}"'
        link.WindowStyle = 7  # minimised
        link.Save()
        helper = support.write(folder, "helper.py", _APP_HELPER)
        keep = ("APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT",
                "PROCESSOR_ARCHITECTURE", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "USERNAME", "USERPROFILE")
        support.subprocess.run([support.sys.executable, helper, support.REPO, shortcut],
                               env={k: os.environ[k] for k in keep if k in os.environ},
                               timeout=60, capture_output=True)
        for _ in range(20):
            if os.path.exists(report) and os.path.getsize(report):
                break
            support.time.sleep(0.5)
        self.assertTrue(os.path.exists(report), "the app never started")
        with open(report) as f:
            pid = int(f.read())
        self.addCleanup(lambda: psutil.pid_exists(pid) and psutil.Process(pid).kill())
        self.assertTrue(psutil.pid_exists(pid), "the app was closed when Hestia's server exited")

    def test_a_missing_file_target_is_reported_not_opened(self):
        from mcp.server.mcpserver.exceptions import ToolError
        self.addCleanup(setattr, system_tools, "APPS", system_tools.APPS)
        system_tools.APPS = {"gone": ("gone", r"C:\no\such\program.exe")}
        with self.assertRaises(ToolError) as caught:
            system_tools.launch_application("gone")
        self.assertIn("no longer exists", str(caught.exception))

    def test_names_are_matched_loosely(self):
        self.addCleanup(setattr, system_tools, "APPS", system_tools.APPS)
        system_tools.APPS = {"googlechrome": ("Google Chrome", "c"), "steam": ("Steam", "s"),
                             "steamsupportcenter": ("Steam Support Center", "x")}
        self.assertEqual(system_tools._find_app("STEAM"), ("Steam", "s"))
        self.assertEqual(system_tools._find_app("chrome"), ("Google Chrome", "c"))
        self.assertIsNone(system_tools._find_app("firefox"))
        self.assertIsNone(system_tools._find_app(" "))

    def test_the_start_menu_is_found(self):
        self.assertIn("notepad", system_tools._start_menu_apps())


if __name__ == "__main__":
    unittest.main()
