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

    def test_system_info_includes_a_wsl_line(self):
        self.assertIn("WSL: ", system_tools.get_system_info())


if __name__ == "__main__":
    unittest.main()
