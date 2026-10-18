import tempfile
import unittest
from pathlib import Path
from unittest import mock

from drivux import updater

RELEASE = updater.Release("0.2.0", "page", {
    "drivux-0.2.0-1-any.pkg.tar.zst": "http://x/arch", "drivux_0.2.0_all.deb": "http://x/deb",
    "drivux-0.2.0-1.noarch.rpm": "http://x/rpm", "SHA256SUMS": "http://x/sums",
})


class UpdaterTest(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(updater.is_newer("1.0.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("0.1.0-dev.3", "0.1.0"))

    def test_package_per_manager(self):
        self.assertEqual(updater.asset_for("apt-get", RELEASE), "drivux_0.2.0_all.deb")
        self.assertEqual(updater.asset_for("pacman", RELEASE), "drivux-0.2.0-1-any.pkg.tar.zst")
        self.assertEqual(updater.asset_for("dnf", RELEASE), "drivux-0.2.0-1.noarch.rpm")

    def test_expected_checksum(self):
        sums = "abc123  drivux_0.2.0_all.deb\nff00 *other.rpm\n"
        self.assertEqual(updater.expected_checksum(sums, "drivux_0.2.0_all.deb"), "abc123")
        self.assertEqual(updater.expected_checksum(sums, "other.rpm"), "ff00")
        self.assertIsNone(updater.expected_checksum(sums, "missing"))

    def test_download_checks_the_hash(self):
        import hashlib

        data = b"package"
        good = f"{hashlib.sha256(data).hexdigest()}  drivux_0.2.0_all.deb\n".encode()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(updater, "update_dir", return_value=Path(tmp)):
            with mock.patch.object(updater, "_get", side_effect=lambda url: good if url.endswith("sums") else data):
                path = updater.download(RELEASE, "apt-get")
                self.assertEqual(path.read_bytes(), data)
            with mock.patch.object(updater, "_get", side_effect=lambda url: good if url.endswith("sums") else b"tampered"):
                with self.assertRaises(RuntimeError):
                    updater.download(RELEASE, "apt-get")
                self.assertFalse((Path(tmp) / "0.2.0" / "drivux_0.2.0_all.deb.part").exists())

    def test_no_sums_no_install(self):
        bare = updater.Release("0.2.0", "", {"drivux_0.2.0_all.deb": "u"})
        with self.assertRaises(RuntimeError):
            updater.download(bare, "apt-get")


if __name__ == "__main__":
    unittest.main()
