import json
import os
import tempfile
import unittest
from pathlib import Path

from drivux import config, offline, service
from drivux.tray import Snapshot, summarize


class InTempConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["XDG_CONFIG_HOME"] = self._tmp.name
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        del os.environ["XDG_CONFIG_HOME"]
        self._tmp.cleanup()


class ConfigAccountsTest(InTempConfig):
    def test_file_from_before_accounts_is_the_default_account(self):
        path = config.config_path()
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"remote": "drivux", "folder": "/home/u/GoogleDrive", "fast_listing": True}))
        accounts = config.load_all()
        self.assertEqual([a.name for a in accounts], ["default"])
        self.assertTrue(accounts[0].fast_listing)

    def test_saving_keeps_every_account_and_the_default_first(self):
        config.save(config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), name="work"))
        config.save(config.Config("drivux", Path("/home/u/GoogleDrive")))
        self.assertEqual([a.name for a in config.load_all()], ["default", "work"])
        self.assertEqual(config.load("work").remote, "drivux-work")
        self.assertEqual(config.load().name, "default")

    def test_saving_again_replaces_the_account(self):
        config.save(config.Config("drivux", Path("/home/u/GoogleDrive")))
        config.save(config.Config("drivux", Path("/home/u/GoogleDrive"), fast_listing=True))
        self.assertEqual(len(config.load_all()), 1)
        self.assertTrue(config.load().fast_listing)

    def test_removing_one_account_keeps_the_other(self):
        config.save(config.Config("drivux", Path("/home/u/GoogleDrive")))
        config.save(config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), name="work"))
        config.remove("work")
        self.assertEqual([a.name for a in config.load_all()], ["default"])
        config.remove("default")
        self.assertFalse(config.config_path().exists())

    def test_account_of_finds_the_folder_a_path_is_in(self):
        config.save(config.Config("drivux", Path("/home/u/GoogleDrive")))
        config.save(config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), name="work"))
        self.assertEqual(config.account_of("/home/u/GoogleDrive-work/a/b.txt").name, "work")
        self.assertEqual(config.account_of("/home/u/GoogleDrive/a").name, "default")
        self.assertIsNone(config.account_of("/home/u/Documents"))

    def test_names_cannot_clash_with_unit_or_file_names(self):
        for bad in ("", "Work", "a-b", "a@b", "a b", "x" * 21):
            with self.assertRaises(ValueError):
                config.check_name(bad)
        self.assertEqual(config.check_name("work2"), "work2")

    def test_names_decide_folder_remote_and_cache(self):
        self.assertEqual(config.default_remote("work"), "drivux-work")
        self.assertEqual(config.default_folder("work").name, "GoogleDrive-work")
        self.assertEqual(config.default_remote(), "drivux")
        self.assertNotEqual(config.cache_dir("work"), config.cache_dir())
        self.assertNotIn(config.cache_dir(), config.cache_dir("work").parents)  # removing one never removes the other


class AccountUnitsTest(unittest.TestCase):
    def test_default_account_keeps_the_original_unit_names(self):
        self.assertEqual(service.unit_name("folder"), "drivux.service")
        self.assertEqual(service.unit_name("front"), "drivux-front.service")
        self.assertEqual(service.unit_name("offline", suffix="timer"), "drivux-offline.timer")

    def test_other_accounts_have_their_own_units(self):
        self.assertEqual(service.unit_name("folder", "work"), "drivux@work.service")
        self.assertEqual(service.unit_name("front", "work"), "drivux-front@work.service")
        self.assertEqual(service.unit_name("offline", "work", "timer"), "drivux-offline@work.timer")

    def test_an_account_unit_never_equals_another_kind_of_unit(self):
        names = {service.unit_name(kind, account) for kind in ("folder", "front", "notify", "offline")
                 for account in ("default", "front", "notify", "work")}
        self.assertEqual(len(names), 16)

    def test_second_account_mounts_its_own_remote_with_its_own_socket_and_cache(self):
        cfg = config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), name="work")
        unit = service.render_unit(cfg)
        self.assertIn('mount drivux-work: "/home/u/GoogleDrive-work"', unit)
        self.assertIn("drivux-work.sock", unit)
        self.assertIn(str(config.cache_dir("work")), unit)
        self.assertNotIn("%t/drivux.sock", unit)

    def test_fast_listing_hides_each_account_in_its_own_folder(self):
        work = config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), fast_listing=True, name="work")
        main = config.Config("drivux", Path("/home/u/GoogleDrive"), fast_listing=True)
        self.assertNotEqual(service.mount_folder(work), service.mount_folder(main))

    def test_services_of_an_account_name_it(self):
        cfg = config.Config("drivux-work", Path("/home/u/GoogleDrive-work"), fast_listing=True, name="work")
        front = service.render_front_unit(cfg, drivux_bin="/usr/bin/drivux")
        self.assertIn("Requires=drivux@work.service", front)
        self.assertIn('ExecStart="/usr/bin/drivux" front --account work', front)
        self.assertIn("--account work", service.render_notify_unit("/usr/bin/drivux", name="work"))
        self.assertIn("offline sync --account work", service.render_offline_unit("/usr/bin/drivux", name="work"))
        self.assertNotIn("--account", service.render_notify_unit("/usr/bin/drivux"))

    def test_tray_unit_runs_drivux_tray(self):
        self.assertIn('ExecStart="/usr/bin/drivux" tray', service.render_tray_unit("/usr/bin/drivux"))


class OfflinePerAccountTest(InTempConfig):
    def test_each_account_has_its_own_list(self):
        offline.save(["a"], "default")
        offline.save(["b"], "work")
        self.assertEqual(offline.load("default"), ["a"])
        self.assertEqual(offline.load("work"), ["b"])
        self.assertTrue(offline.list_path("default").name == "offline.json")


class TraySummaryTest(unittest.TestCase):
    def test_synced(self):
        summary = summarize([Snapshot("default", "/d", True)])
        self.assertEqual(summary.headline, "Everything is synced")
        self.assertEqual(summary.lines, ["Everything is synced"])

    def test_syncing_lists_the_files(self):
        summary = summarize([Snapshot("default", "/d", True, uploads=["a/report.pdf"], downloads=["b.txt"])])
        self.assertEqual(summary.icon, "emblem-synchronizing-symbolic")
        self.assertEqual(summary.lines, ["Syncing 2 files", "↑ report.pdf", "↓ b.txt"])

    def test_long_lists_are_cut(self):
        summary = summarize([Snapshot("default", "/d", True, uploads=[f"f{i}" for i in range(9)])])
        self.assertEqual(summary.lines[-1], "and 4 more")

    def test_paused_only_when_every_account_is_paused(self):
        paused = Snapshot("a", "/a", False)
        self.assertEqual(summarize([paused]).headline, "Paused")
        self.assertEqual(summarize([paused, Snapshot("b", "/b", True)]).headline, "Everything is synced")

    def test_error_needs_attention(self):
        summary = summarize([Snapshot("a", "/a", True, error="quota exceeded")])
        self.assertEqual(summary.status, "NeedsAttention")
        self.assertEqual(summary.lines, ["Error: quota exceeded"])

    def test_several_accounts_are_named(self):
        summary = summarize([Snapshot("default", "/d", True), Snapshot("work", "/w", True, pending=1)])
        self.assertEqual(summary.headline, "Syncing")
        self.assertEqual(summary.lines, ["default: Everything is synced", "work: Syncing 1 file"])


class RcloneTimeoutTest(unittest.TestCase):
    def test_a_busy_rclone_is_an_rclone_error(self):
        from unittest import mock
        import subprocess
        from drivux import rclone

        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("rclone", 10)):
            with self.assertRaises(rclone.RcloneError):
                rclone.rc(Path("/nonexistent.sock"), "vfs/stats")


if __name__ == "__main__":
    unittest.main()
