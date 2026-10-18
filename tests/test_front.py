"""Mounts the instant-listing layer for real (FUSE) over a plain folder standing in for rclone."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from drivux import index as idx

try:
    import pyfuse3  # noqa: F401

    HAVE_FUSE = os.path.exists("/dev/fuse") and shutil.which("fusermount3") is not None
except ImportError:
    HAVE_FUSE = False

SRC = str(Path(__file__).resolve().parent.parent / "src")


def entry(path, id_, is_dir=False, size=0):
    return {
        "Path": path, "ID": id_, "IsDir": is_dir, "Size": -1 if is_dir else size,
        "MimeType": "application/vnd.google-apps.folder" if is_dir else "text/plain",
        "ModTime": "2026-01-01T00:00:00.000Z",
    }


@unittest.skipUnless(HAVE_FUSE, "needs FUSE 3 and python-pyfuse3")
class FrontTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="drivux-front-"))
        self.back, self.mnt, self.db = self.tmp / "back", self.tmp / "mnt", self.tmp / "index.sqlite3"
        (self.back / "Docs").mkdir(parents=True)
        (self.back / "Docs" / "a.txt").write_text("hello")
        (self.back / "c.txt").write_text("cc")
        index = idx.Index(self.db)
        index.apply_scan(
            [entry("Docs", "d1", True), entry("Docs/a.txt", "f1", size=5), entry("c.txt", "f2", size=2)],
            time.monotonic(),
        )
        index.close()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "drivux", "front", "--folder", str(self.mnt), "--back", str(self.back),
             "--index", str(self.db), "--remote", "x", "--no-scan", "--no-mount-check"],
            env={**os.environ, "PYTHONPATH": SRC}, stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if os.path.ismount(self.mnt):
                return
            time.sleep(0.1)
        self.fail("did not mount: " + self.proc.stderr.read().decode())

    def tearDown(self):
        subprocess.run(["fusermount3", "-uz", str(self.mnt)], capture_output=True)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stderr.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_listing_comes_from_the_index_not_from_the_back_folder(self):
        (self.back / "only-in-back.txt").write_text("x")  # not in the index: not shown
        self.assertEqual(sorted(os.listdir(self.mnt)), ["Docs", "c.txt"])
        self.assertEqual(os.listdir(self.mnt / "Docs"), ["a.txt"])
        self.assertEqual(os.stat(self.mnt / "Docs" / "a.txt").st_size, 5)
        self.assertTrue(os.path.isdir(self.mnt / "Docs"))

    def test_read_goes_to_the_back_folder(self):
        self.assertEqual((self.mnt / "Docs" / "a.txt").read_text(), "hello")

    def test_create_write_and_size(self):
        with open(self.mnt / "new.txt", "w") as f:
            f.write("12345678")
            f.flush()
            self.assertEqual(os.stat(self.mnt / "new.txt").st_size, 8)  # while still open
        self.assertEqual((self.back / "new.txt").read_text(), "12345678")
        self.assertEqual(os.stat(self.mnt / "new.txt").st_size, 8)
        self.assertIn("new.txt", os.listdir(self.mnt))

    def test_overwrite_and_truncate(self):
        (self.mnt / "c.txt").write_text("a longer text")
        self.assertEqual((self.back / "c.txt").read_text(), "a longer text")
        os.truncate(self.mnt / "c.txt", 3)
        self.assertEqual((self.back / "c.txt").read_text(), "a l")
        self.assertEqual(os.stat(self.mnt / "c.txt").st_size, 3)

    def test_mkdir_rename_unlink_rmdir(self):
        os.mkdir(self.mnt / "New")
        self.assertTrue((self.back / "New").is_dir())
        os.rename(self.mnt / "c.txt", self.mnt / "New" / "moved.txt")
        self.assertEqual((self.back / "New" / "moved.txt").read_text(), "cc")
        self.assertNotIn("c.txt", os.listdir(self.mnt))
        self.assertEqual(os.listdir(self.mnt / "New"), ["moved.txt"])
        os.unlink(self.mnt / "New" / "moved.txt")
        self.assertFalse((self.back / "New" / "moved.txt").exists())
        os.rmdir(self.mnt / "New")
        self.assertFalse((self.back / "New").exists())
        self.assertNotIn("New", os.listdir(self.mnt))

    def test_missing_file_and_errors(self):
        with self.assertRaises(FileNotFoundError):
            os.stat(self.mnt / "nope.txt")
        with self.assertRaises(OSError):
            os.rmdir(self.mnt / "Docs")  # not empty
        self.assertTrue((self.back / "Docs" / "a.txt").exists())

    def test_listing_does_not_wait_for_a_slow_back_folder(self):
        # A name in the index whose file the back folder cannot open still lists and stats instantly.
        (self.back / "c.txt").unlink()
        t0 = time.monotonic()
        self.assertIn("c.txt", os.listdir(self.mnt))
        self.assertEqual(os.stat(self.mnt / "c.txt").st_size, 2)
        self.assertLess(time.monotonic() - t0, 1)
        with self.assertRaises(FileNotFoundError):
            open(self.mnt / "c.txt").read()

    def test_large_file_roundtrip(self):
        data = os.urandom(5 * 1024 * 1024 + 123)
        (self.mnt / "big.bin").write_bytes(data)
        self.assertEqual((self.mnt / "big.bin").read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
