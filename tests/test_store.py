import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from halbach_connecthub import store as st
from halbach_connecthub import __version__


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.home = self.tmp / "hub"
        self.site = self.tmp / "stuff"
        self.site.mkdir()
        (self.site / "a.txt").write_text("hello")
        self.s = st.Store(self.home)

    def tearDown(self):
        self._t.cleanup()


class TestLiveSources(unittest.TestCase):
    def test_accepts_local_forms(self):
        self.assertEqual(st.normalize_live_source("127.0.0.1:8787"), "127.0.0.1:8787")
        self.assertEqual(st.normalize_live_source("http://localhost:8787/overlay"), "127.0.0.1:8787/overlay")

    def test_rejects_remote_hosts(self):
        for bad in ("example.com:80", "192.168.1.5:8080", "10.0.0.1:22", "http://evil.test:80/x"):
            with self.assertRaises(st.ValidationError, msg=bad):
                st.normalize_live_source(bad)

    def test_rejects_bad_ports_and_shapes(self):
        for bad in ("127.0.0.1:0", "127.0.0.1:99999", "127.0.0.1", "", "127.0.0.1:80 /x"):
            with self.assertRaises(st.ValidationError, msg=bad):
                st.normalize_live_source(bad)


class TestFields(unittest.TestCase):
    def test_locked_names(self):
        for n in ("chat", "notes", "loot_log", "account_id", "password", "api_key", "session_token", "Email"):
            self.assertTrue(st.is_locked_field(n), n)
        for n in ("level", "hp", "class", "position"):
            self.assertFalse(st.is_locked_field(n), n)


class TestFiles(Base):
    def test_never_publish_list(self):
        for n in (".env", ".dev.vars", "id_rsa", "cert.pem", "x.key", "INGEST_KEY.txt", "TIMELINE.md",
                  "notes.bak-old", "debug.log", ".gitignore", "Thumbs.db"):
            (self.site / n).write_text("secret")
        (self.site / ".git").mkdir()
        (self.site / ".git" / "config").write_text("x")
        files, _ = st.collect_files(self.site)
        self.assertEqual([f[1] for f in files], ["a.txt"])

    def test_oversize_file_is_reported_not_included(self):
        big = self.site / "big.bin"
        with open(big, "wb") as f:
            f.truncate(st.MAX_FILE_BYTES + 1)
        files, problems = st.collect_files(self.site)
        self.assertEqual([f[1] for f in files], ["a.txt"])
        self.assertTrue(any("big.bin" in p for p in problems))

    def test_fingerprint_changes_with_content(self):
        a = st.fingerprint(self.site)
        time.sleep(0.01)
        (self.site / "b.txt").write_text("new")
        self.assertNotEqual(a, st.fingerprint(self.site))


class TestStaticSources(Base):
    def test_rejects_missing_drive_root_and_home(self):
        for bad in (str(self.tmp / "nope"), Path(self.tmp.anchor).as_posix(), str(Path.home()), ""):
            with self.assertRaises(st.ValidationError, msg=bad):
                st.validate_static_source(bad)

    def test_rejects_own_data_folder(self):
        os.environ["CONNECTHUB_HOME"] = str(self.home)
        try:
            with self.assertRaises(st.ValidationError):
                st.validate_static_source(str(self.home))
        finally:
            del os.environ["CONNECTHUB_HOME"]

    def test_rejects_folder_with_nothing_publishable(self):
        d = self.tmp / "onlysecrets"
        d.mkdir()
        (d / ".env").write_text("x")
        with self.assertRaises(st.ValidationError):
            st.validate_static_source(str(d))


class TestModules(Base):
    def test_new_module_is_off_and_ids_are_unique(self):
        a = self.s.add_module("static", "My Stuff", str(self.site))
        b = self.s.add_module("static", "My Stuff", str(self.site))
        self.assertFalse(a["on"])
        self.assertNotEqual(a["id"], b["id"])

    def test_live_module_starts_with_no_fields(self):
        m = self.s.add_module("live", "Overlay", "127.0.0.1:8787")
        self.assertEqual(m["fields"], [])
        self.assertEqual(m["viewer_cap"], 40)

    def test_locked_fields_cannot_be_allowed(self):
        m = self.s.add_module("live", "Overlay", "127.0.0.1:8787")
        out = self.s.set_fields(m["id"], [{"name": "level", "allowed": True}, {"name": "chat", "allowed": True},
                                          {"name": "password", "allowed": True}])
        by = {f["name"]: f for f in out["fields"]}
        self.assertTrue(by["level"]["allowed"])
        self.assertFalse(by["chat"]["allowed"])
        self.assertTrue(by["chat"]["locked"])
        self.assertFalse(by["password"]["allowed"])

    def test_cap_is_clamped(self):
        m = self.s.add_module("live", "Overlay", "127.0.0.1:8787")
        self.assertEqual(self.s.set_cap(m["id"], 10 ** 6)["viewer_cap"], 500)
        self.assertEqual(self.s.set_cap(m["id"], 0)["viewer_cap"], 1)

    def test_pending_lifecycle(self):
        m = self.s.add_module("static", "Stuff", str(self.site))
        self.assertEqual(self.s.pending(), [])                 # off and never published
        self.s.set_on(m["id"], True)
        self.assertEqual([p["change"] for p in self.s.pending()], ["add"])
        self.s.mark_published()
        self.assertEqual(self.s.pending(), [])
        time.sleep(1.1)                                        # mtime is whole seconds
        (self.site / "a.txt").write_text("edited and longer")
        self.assertEqual([p["change"] for p in self.s.pending()], ["update"])
        self.s.mark_published()
        self.s.set_on(m["id"], False)
        self.assertEqual([p["change"] for p in self.s.pending()], ["remove"])
        self.s.mark_published()
        self.assertEqual(self.s.pending(), [])

    def test_state_survives_restart(self):
        m = self.s.add_module("static", "Stuff", str(self.site))
        self.s.set_on(m["id"], True)
        again = st.Store(self.home)
        self.assertTrue(again.state["modules"][0]["on"])

    def test_corrupt_state_file_starts_clean(self):
        (self.home / "state.json").write_text("{not json")
        self.assertEqual(st.Store(self.home).state["modules"], [])

    def test_remove(self):
        m = self.s.add_module("static", "Stuff", str(self.site))
        self.s.remove(m["id"])
        self.assertEqual(self.s.state["modules"], [])
        with self.assertRaises(st.ValidationError):
            self.s.remove(m["id"])

    def test_settings_validation(self):
        with self.assertRaises(st.ValidationError):
            self.s.set_settings(site_dir=str(self.tmp / "missing"))
        with self.assertRaises(st.ValidationError):
            self.s.set_settings(project="Bad Name!")
        out = self.s.set_settings(site_dir=str(self.site), project="my-site", domain="example.com")
        self.assertEqual(out["project"], "my-site")

    def test_activity_log_newest_first(self):
        self.s.log("info", "one")
        self.s.log("ok", "two")
        self.assertEqual([r["msg"] for r in self.s.activity()][:2], ["two", "one"])


class TestUpgradeBackup(unittest.TestCase):
    """Task 0.2: back up state.json before new code opens an older one."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.home = Path(self._t.name) / "hub"
        self.home.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def _write_state(self, version, marker="keep-me"):
        state = {"version": 1, "settings": {}, "modules": [{"id": "m", "note": marker}]}
        if version is not None:
            state["app_version"] = version
        path = self.home / "state.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        return path.read_bytes()

    def _backups(self):
        return sorted(self.home.glob("state.json.bak-*"))

    def test_upgrade_creates_one_backup_with_identical_bytes(self):
        original = self._write_state("0.0.1")
        st.Store(self.home)
        baks = self._backups()
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), original)
        self.assertEqual(st.Store(self.home).state["app_version"], __version__)

    def test_missing_version_is_backed_up(self):
        self._write_state(None)
        st.Store(self.home)
        self.assertEqual(len(self._backups()), 1)

    def test_second_open_creates_no_second_backup(self):
        self._write_state("0.0.1")
        st.Store(self.home)
        st.Store(self.home)
        st.Store(self.home)
        self.assertEqual(len(self._backups()), 1)

    def test_six_upgrades_leave_five_backups(self):
        for i in range(6):
            self._write_state("0.0.%d" % i)
            st.Store(self.home)
        self.assertEqual(len(self._backups()), st.BACKUP_KEEP)

    def test_no_state_file_no_backup(self):
        store = st.Store(self.home)
        self.assertEqual(self._backups(), [])
        self.assertEqual(store.state["app_version"], __version__)

    def test_corrupt_state_file_does_not_crash_or_back_up(self):
        self.home.joinpath("state.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(st.Store(self.home).state["modules"], [])
        self.assertEqual(self._backups(), [])


if __name__ == "__main__":
    unittest.main()
