import tempfile
import time
import unittest
from pathlib import Path

from drivux import index as idx


def entry(path, id_, is_dir=False, size=0, mime=None, mtime="2026-01-01T00:00:00.000Z"):
    return {
        "Path": path,
        "Name": path.rsplit("/", 1)[-1],
        "ID": id_,
        "IsDir": is_dir,
        "Size": -1 if is_dir else size,
        "MimeType": mime or ("application/vnd.google-apps.folder" if is_dir else "text/plain"),
        "ModTime": mtime,
    }


TREE = [
    entry("Docs", "d1", is_dir=True),
    entry("Docs/a.txt", "f1", size=10),
    entry("Docs/sub", "d2", is_dir=True),
    entry("Docs/sub/b.txt", "f2", size=20),
    entry("c.txt", "f3", size=5),
]


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.index = idx.Index(Path(self.tmp.name) / "index.sqlite3")

    def tearDown(self):
        self.index.close()
        self.tmp.cleanup()

    def scan(self, entries):
        return self.index.apply_scan(entries, time.monotonic())

    def test_scan_builds_the_tree(self):
        result = self.scan(TREE)
        self.assertEqual(result.added, 5)
        docs = self.index.lookup(idx.ROOT, "Docs")
        self.assertTrue(docs.is_dir)
        self.assertEqual(self.index.lookup(docs.inode, "a.txt").size, 10)
        self.assertEqual([n.name for n in self.index.children(idx.ROOT)], ["Docs", "c.txt"])
        b = self.index.lookup(self.index.lookup(docs.inode, "sub").inode, "b.txt")
        self.assertEqual(self.index.path_of(b.inode), "Docs/sub/b.txt")

    def test_second_scan_changes_nothing(self):
        self.scan(TREE)
        result = self.scan(TREE)
        self.assertEqual((result.added, result.updated, result.removed), (0, 0, 0))

    def test_rename_and_move_keep_the_inode(self):
        self.scan(TREE)
        before = self.index.lookup(idx.ROOT, "c.txt").inode
        moved = [e for e in TREE if e["ID"] != "f3"] + [entry("Docs/renamed.txt", "f3", size=5)]
        result = self.scan(moved)
        self.assertEqual((result.added, result.updated, result.removed), (0, 1, 0))
        self.assertIsNone(self.index.lookup(idx.ROOT, "c.txt"))
        self.assertEqual(self.index.path_of(before), "Docs/renamed.txt")

    def test_removed_items_disappear_with_their_children(self):
        self.scan(TREE)
        result = self.scan([e for e in TREE if not e["Path"].startswith("Docs")])
        self.assertEqual(result.removed, 4)
        self.assertEqual([n.name for n in self.index.children(idx.ROOT)], ["c.txt"])

    def test_google_docs_are_left_out(self):
        self.scan(TREE + [entry("Plan", "g1", mime="application/vnd.google-apps.document")])
        self.assertIsNone(self.index.lookup(idx.ROOT, "Plan"))

    def test_duplicate_names_keep_the_first(self):
        result = self.scan([entry("x.txt", "a", size=1), entry("x.txt", "b", size=2)])
        self.assertEqual(result.added, 1)
        self.assertEqual(result.skipped, 1)

    def test_local_file_is_adopted_when_it_shows_up_on_the_drive(self):
        local = self.index.add_local(idx.ROOT, "new.txt", False)
        self.scan([entry("new.txt", "n1", size=7)])
        node = self.index.get(local.inode)
        self.assertEqual(node.size, 7)
        self.assertEqual(self.index.count(), 1)

    def test_local_file_survives_a_scan_that_started_before_it(self):
        started = time.monotonic()
        local = self.index.add_local(idx.ROOT, "new.txt", False)
        self.index.apply_scan([], started)
        self.assertIsNotNone(self.index.get(local.inode))

    def test_local_delete_wins_over_a_scan_that_started_before_it(self):
        self.scan(TREE)
        started = time.monotonic()
        self.index.remove(self.index.lookup(idx.ROOT, "c.txt").inode)
        self.index.apply_scan(TREE, started)  # the listing still has c.txt
        self.assertIsNone(self.index.lookup(idx.ROOT, "c.txt"))
        self.scan(TREE)  # a later listing is the truth again
        self.assertIsNotNone(self.index.lookup(idx.ROOT, "c.txt"))

    def test_rename_over_existing_replaces_it(self):
        self.scan(TREE)
        c = self.index.lookup(idx.ROOT, "c.txt")
        docs = self.index.lookup(idx.ROOT, "Docs")
        self.index.rename(c.inode, docs.inode, "a.txt")
        self.assertEqual(self.index.path_of(c.inode), "Docs/a.txt")
        self.assertEqual(len(self.index.children(docs.inode)), 2)

    def test_index_survives_reopening(self):
        self.scan(TREE)
        path = Path(self.tmp.name) / "index.sqlite3"
        self.index.close()
        self.index = idx.Index(path)
        self.assertEqual(self.index.count(), 5)
        self.assertIsNotNone(self.index.last_scan())


if __name__ == "__main__":
    unittest.main()
