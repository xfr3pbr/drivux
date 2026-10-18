import os
import tempfile
import unittest
from pathlib import Path

from drivux import offline, service


class RelativeTest(unittest.TestCase):
    folder = Path("/home/u/GoogleDrive")

    def test_path_inside_folder(self):
        self.assertEqual(offline.relative(self.folder, "/home/u/GoogleDrive/Docs/a.txt"), "Docs/a.txt")

    def test_path_outside_folder_is_rejected(self):
        with self.assertRaises(ValueError):
            offline.relative(self.folder, "/home/u/Downloads/a.txt")

    def test_the_folder_itself_is_rejected(self):
        with self.assertRaises(ValueError):
            offline.relative(self.folder, "/home/u/GoogleDrive")

    def test_sibling_with_same_prefix_is_outside(self):
        with self.assertRaises(ValueError):
            offline.relative(self.folder, "/home/u/GoogleDrive-old/a.txt")


class ListTest(unittest.TestCase):
    def test_add_skips_what_a_folder_already_keeps(self):
        self.assertEqual(offline.add(["Docs"], ["Docs/a.txt"]), ["Docs"])

    def test_adding_a_folder_absorbs_its_files(self):
        self.assertEqual(sorted(offline.add(["Docs/a.txt", "Other"], ["Docs"])), ["Docs", "Other"])

    def test_similar_names_are_not_nested(self):
        self.assertEqual(sorted(offline.add(["Docs"], ["Docs2"])), ["Docs", "Docs2"])

    def test_remove_entry(self):
        self.assertEqual(offline.remove(["Docs", "Other"], ["Docs"]), ["Other"])

    def test_remove_file_kept_through_its_folder_is_rejected(self):
        with self.assertRaises(ValueError):
            offline.remove(["Docs"], ["Docs/a.txt"])

    def test_remove_unknown_is_ignored(self):
        self.assertEqual(offline.remove(["Docs"], ["Nope"]), ["Docs"])

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CONFIG_HOME"] = tmp
            try:
                self.assertEqual(offline.load(), [])
                offline.save(["b", "a"])
                self.assertEqual(offline.load(), ["a", "b"])
            finally:
                del os.environ["XDG_CONFIG_HOME"]


class FetchTest(unittest.TestCase):
    def test_reads_files_of_a_folder_and_a_single_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "Docs" / "sub").mkdir(parents=True)
            (folder / "Docs" / "a.txt").write_text("abc")
            (folder / "Docs" / "sub" / "b.txt").write_text("de")
            (folder / "c.txt").write_text("f")
            result = offline.fetch(folder, ["Docs", "c.txt"])
            self.assertEqual((result.files, result.size, result.failed), (3, 6, []))

    def test_missing_path_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = offline.fetch(Path(tmp), ["gone.txt"])
            self.assertEqual(result.files, 0)
            self.assertEqual(result.failed, ["gone.txt: not found"])


class UnitTest(unittest.TestCase):
    def test_offline_unit_runs_sync(self):
        unit = service.render_offline_unit(drivux_bin="/usr/bin/drivux")
        self.assertIn('ExecStart="/usr/bin/drivux" offline sync', unit)
        self.assertIn("Type=oneshot", unit)

    def test_timer_is_daily_and_persistent(self):
        timer = service.render_offline_timer()
        self.assertIn("OnCalendar=daily", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
