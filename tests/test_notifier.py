import unittest

from drivux.notifier import Activity, Notifier, describe


class FakeSender:
    def __init__(self):
        self.sent = []

    def __call__(self, title, body, *, replaces=0, persistent=False, critical=False):
        self.sent.append({"title": title, "replaces": replaces, "persistent": persistent, "critical": critical})
        return 42


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.send = FakeSender()
        self.notifier = Notifier(self.send)

    def test_quick_saves_do_not_notify(self):
        self.notifier.update(Activity(uploads=["a.txt"]))
        self.notifier.update(Activity())
        self.assertEqual(self.send.sent, [])

    def test_long_sync_shows_progress_then_done(self):
        self.notifier.update(Activity(uploads=["a.txt"]))
        self.notifier.update(Activity(uploads=["a.txt"]))
        self.notifier.update(Activity())
        titles = [n["title"] for n in self.send.sent]
        self.assertEqual(titles, ["Syncing 1 file", "Everything is synced"])
        self.assertTrue(self.send.sent[0]["persistent"])
        self.assertEqual(self.send.sent[1]["replaces"], 42)

    def test_progress_is_updated_in_place(self):
        for act in (Activity(uploads=["a"]), Activity(uploads=["a"]), Activity(uploads=["a", "b"])):
            self.notifier.update(act)
        self.assertEqual(len(self.send.sent), 2)
        self.assertEqual(self.send.sent[1]["replaces"], 42)

    def test_same_content_is_not_resent(self):
        for _ in range(5):
            self.notifier.update(Activity(downloads=["photo.jpg"]))
        self.assertEqual(len(self.send.sent), 1)

    def test_new_errors_notify_but_old_ones_do_not(self):
        self.notifier.update(Activity(errors=3))
        self.notifier.update(Activity(errors=4, last_error="quota exceeded"))
        self.assertEqual(len(self.send.sent), 1)
        self.assertTrue(self.send.sent[0]["critical"])

    def test_unmounted_resets_quietly(self):
        self.notifier.update(None)
        self.assertEqual(self.send.sent, [])


class DescribeTest(unittest.TestCase):
    def test_lists_direction_and_truncates(self):
        title, body = describe(Activity(uploads=["folder/a.txt"], downloads=["b", "c", "d", "e"]))
        self.assertEqual(title, "Syncing 5 files")
        self.assertTrue(body.startswith("↑ a.txt\n↓ b"))
        self.assertTrue(body.endswith("and 1 more"))


if __name__ == "__main__":
    unittest.main()
