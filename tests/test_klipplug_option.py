"""Tests for the optional KlipPlug plugin manager.

They need no USB stick, no root and no network: they exercise the code that
finds a local copy of KlipPlug and bakes it into the installer overlay, check
the option reaches options.env, and check the shell scripts parse.

    python3 -m unittest discover -s tests
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import wyse_injector as wyse
except SystemExit:  # wyse_injector exits when tkinter is missing
    wyse = None


def make_klipplug_folder(path, with_git=True):
    os.makedirs(os.path.join(path, "tools"))
    os.makedirs(os.path.join(path, "src", "klipplug", "__pycache__"))
    for name in ("tools/printer_install.py", "src/klipplug/__main__.py", "README.md",
                 "src/klipplug/__pycache__/x.pyc"):
        with open(os.path.join(path, name), "w") as f:
            f.write("# stand-in\n")
    with open(os.path.join(path, "Install-On-Printer.sh"), "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(os.path.join(path, "Install-On-Printer.sh"), 0o755)
    if with_git:
        os.makedirs(os.path.join(path, ".git"))
        with open(os.path.join(path, ".git", "config"), "w") as f:
            f.write('[remote "origin"]\n\turl = https://user:SECRET-TOKEN@github.com/x/klipplug.git\n')


def zip_folder(folder, zip_path, top="2026-10-07_1053_klipplug"):
    with zipfile.ZipFile(zip_path, "w") as zf:
        for dirpath, _dirs, files in os.walk(folder):
            for name in files:
                full = os.path.join(dirpath, name)
                zf.write(full, os.path.join(top, os.path.relpath(full, folder)))


def all_files(folder):
    found = []
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            found.append(os.path.relpath(os.path.join(dirpath, name), folder))
    return sorted(found)


@unittest.skipIf(wyse is None, "wyse_injector needs tkinter to import")
class KlipPlugStaging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wyse-klipplug-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tooldir = os.path.join(self.tmp, "tool")
        self.overlay = os.path.join(self.tmp, "work", "overlay")
        os.makedirs(self.tooldir)
        self.said = []

    def stage(self, want=True):
        return wyse.stage_klipplug(self.overlay, want, self.said.append, local_folder=self.tooldir)

    def test_option_off_stages_nothing_and_clears_an_old_copy(self):
        make_klipplug_folder(os.path.join(self.overlay, "klipplug"))
        make_klipplug_folder(os.path.join(self.tooldir, "klipplug"))
        self.assertEqual(self.stage(want=False), "")
        self.assertFalse(os.path.exists(os.path.join(self.overlay, "klipplug")))

    def test_no_local_copy_means_units_download_it(self):
        make_klipplug_folder(os.path.join(self.overlay, "klipplug"))  # left by an earlier build
        self.assertIn("GitHub", self.stage())
        self.assertFalse(os.path.exists(os.path.join(self.overlay, "klipplug")),
                         "a copy from an earlier build must not ride along")

    def test_local_folder_is_baked_in_without_git_or_caches(self):
        make_klipplug_folder(os.path.join(self.tooldir, "klipplug"))
        self.assertIn("baked into the ISO", self.stage())
        files = all_files(os.path.join(self.overlay, "klipplug"))
        self.assertIn(os.path.join("tools", "printer_install.py"), files)
        self.assertFalse([f for f in files if ".git" in f.split(os.sep)], "a clone's .git can hold a login token")
        self.assertFalse([f for f in files if f.endswith(".pyc")])

    def test_local_zip_is_baked_in_without_git_and_keeps_run_permission(self):
        source = os.path.join(self.tmp, "src-folder")
        make_klipplug_folder(source)
        zip_folder(source, os.path.join(self.tooldir, "klipplug-0.2.1-wip.zip"))
        self.assertIn("klipplug-0.2.1-wip.zip", self.stage())
        dest = os.path.join(self.overlay, "klipplug")
        files = all_files(dest)
        self.assertIn(os.path.join("src", "klipplug", "__main__.py"), files)
        self.assertFalse([f for f in files if ".git" in f.split(os.sep)])
        self.assertTrue(os.access(os.path.join(dest, "Install-On-Printer.sh"), os.X_OK))
        self.assertFalse(os.path.exists(dest + ".unpack"))

    def test_zip_that_reaches_outside_stops_the_build(self):
        with zipfile.ZipFile(os.path.join(self.tooldir, "klipplug-bad.zip"), "w") as zf:
            zf.writestr("../../escaped.txt", "x")
            zf.writestr("tools/printer_install.py", "x")
            zf.writestr("src/klipplug/__main__.py", "x")
        with self.assertRaises(wyse.InjectError):
            self.stage()
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "work", "escaped.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.overlay, "klipplug")))

    def test_zip_that_is_not_klipplug_stops_the_build(self):
        with zipfile.ZipFile(os.path.join(self.tooldir, "klipplug-other.zip"), "w") as zf:
            zf.writestr("readme.txt", "hello")
        with self.assertRaises(wyse.InjectError):
            self.stage()

    def test_unrelated_files_are_not_mistaken_for_klipplug(self):
        os.makedirs(os.path.join(self.tooldir, "klipplug-notes"))
        with open(os.path.join(self.tooldir, "klipplug.txt"), "w") as f:
            f.write("x")
        self.assertIsNone(wyse.find_local_klipplug(self.tooldir))

    def test_option_reaches_options_env(self):
        for want, expected in ((True, 'KLIPPLUG="1"'), (False, 'KLIPPLUG="0"')):
            dest = os.path.join(self.tmp, "options-%s.env" % want)
            wyse.render_template(os.path.join(ROOT, "overlay", "options.env.tmpl"), dest, {
                "WEBUI": "mainsail", "CROWSNEST": "0", "KLIPPLUG": "1" if want else "0", "WIFI_MODE": "ap",
                "STATION_SSID": "", "STATION_PASSWORD": "", "COUNTRY": "GB", "HOSTNAME_PREFIX": "klipper"})
            with open(dest) as f:
                self.assertIn(expected, f.read())


class Scripts(unittest.TestCase):
    SCRIPTS = ("setup.sh", "overlay/late-command.sh", "overlay/klipplug-step.sh", "overlay/wyse-diag.sh")

    @unittest.skipIf(shutil.which("bash") is None, "needs bash")
    def test_shell_scripts_parse(self):
        for name in self.SCRIPTS:
            result = subprocess.run(["bash", "-n", os.path.join(ROOT, name)], stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(result.returncode, 0, "%s: %s" % (name, result.stdout))

    def test_option_is_wired_through_every_build_route(self):
        def text(name):
            with open(os.path.join(ROOT, name), encoding="utf-8") as f:
                return f.read()
        self.assertIn('KLIPPLUG="__KLIPPLUG__"', text("overlay/options.env.tmpl"))
        self.assertIn("__KLIPPLUG__", text("setup.sh"))
        self.assertIn("KLIPPLUG: ${{ secrets.KLIPPLUG }}", text(".github/workflows/build-iso.yml"))
        late = text("overlay/late-command.sh")
        self.assertIn('"${KLIPPLUG:=0}"', late, "an options.env from before this option must still work")
        self.assertIn('klipplug-step.sh" "$PRINT_USER" "$HOME_DIR" "$SETUP_DIR"', late)
        self.assertLess(late.index("klipplug-step.sh"), late.index('rm -rf "$SETUP_DIR"'),
                        "the step must run before the setup folder is removed")
        self.assertLess(late.index("moonraker.conf"), late.index("klipplug-step.sh"),
                        "KlipPlug's installer needs moonraker.conf to exist")

    @unittest.skipIf(shutil.which("bash") is None, "needs bash")
    def test_step_refuses_a_wrong_home_folder_before_touching_anything(self):
        # (The step's real work needs root and a freshly installed system; see
        # README "What has and hasn't been tried". This case exits first.)
        result = subprocess.run(["bash", os.path.join(ROOT, "overlay", "klipplug-step.sh"), "print", "/", "/nowhere"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn("NOT installed", result.stdout)


if __name__ == "__main__":
    unittest.main()
