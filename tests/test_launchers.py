"""The three click-and-launch launchers exist, are the right kind of file,
and (Linux/Mac) are executable. Doesn't run them - see
"Click and Launch" testing notes in the project's own records for that.

    python3 -m unittest discover -s tests
"""

import os
import stat
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class Launchers(unittest.TestCase):
    def test_linux_launcher_exists_and_is_executable(self):
        path = os.path.join(ROOT, "WyseInjector-Linux.sh")
        self.assertTrue(os.path.isfile(path))
        mode = os.stat(path).st_mode
        self.assertTrue(mode & stat.S_IXUSR, "WyseInjector-Linux.sh must be executable")
        with open(path, "rb") as f:
            self.assertTrue(f.read(2) == b"#!", "must start with a shebang")

    def test_mac_launcher_exists_and_is_executable(self):
        path = os.path.join(ROOT, "WyseInjector-Mac.command")
        self.assertTrue(os.path.isfile(path))
        mode = os.stat(path).st_mode
        self.assertTrue(mode & stat.S_IXUSR, "WyseInjector-Mac.command must be executable")

    def test_windows_launcher_exists_with_crlf(self):
        path = os.path.join(ROOT, "WyseInjector-Windows.bat")
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            data = f.read()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n\n", data.replace(b"\r\n", b""), "mixed line endings")

    def test_linux_and_mac_launchers_use_lf_only(self):
        for name in ("WyseInjector-Linux.sh", "WyseInjector-Mac.command"):
            with open(os.path.join(ROOT, name), "rb") as f:
                data = f.read()
            self.assertNotIn(b"\r", data, "%s must use LF line endings" % name)


if __name__ == "__main__":
    unittest.main()
