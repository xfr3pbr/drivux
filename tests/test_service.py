import os
import tempfile
import unittest
from pathlib import Path

from drivux import config, service


class RenderUnitTest(unittest.TestCase):
    def test_unit_mounts_remote_on_folder(self):
        cfg = config.Config(remote="drivux", folder=Path("/home/u/Google Drive"))
        unit = service.render_unit(cfg)
        self.assertIn('mount drivux: "/home/u/Google Drive"', unit)
        self.assertIn("--vfs-cache-mode full", unit)
        self.assertIn("vfs/refresh recursive=true", unit)
        self.assertRegex(unit, r'ExecStop="/\S*fusermount3" -uz "/home/u/Google Drive"')

    def test_notify_unit_runs_drivux_notify(self):
        unit = service.render_notify_unit(python="/usr/bin/python3")
        self.assertIn('ExecStart="/usr/bin/python3" -m drivux notify', unit)
        self.assertIn('Environment="PYTHONPATH=', unit)

    def test_installed_notify_unit_uses_launcher(self):
        unit = service.render_notify_unit(drivux_bin="/usr/bin/drivux")
        self.assertIn('ExecStart="/usr/bin/drivux" notify', unit)
        self.assertNotIn("PYTHONPATH", unit)

    def test_percent_is_escaped_for_systemd(self):
        cfg = config.Config(remote="drivux", folder=Path("/home/u/100%"))
        self.assertIn('"/home/u/100%%"', service.render_unit(cfg))


class CheckFolderTest(unittest.TestCase):
    def test_empty_folder_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service.check_folder(Path(tmp))

    def test_missing_folder_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service.check_folder(Path(tmp) / "new")

    def test_non_empty_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "file.txt").write_text("x")
            with self.assertRaises(ValueError):
                service.check_folder(Path(tmp))


class ConfigTest(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                cfg = config.Config(remote="drivux", folder=Path("/home/u/GoogleDrive"))
                config.save(cfg)
                self.assertEqual(config.load(), cfg)
                config.remove()
                self.assertIsNone(config.load())
            finally:
                del os.environ["XDG_CONFIG_HOME"]


if __name__ == "__main__":
    unittest.main()
