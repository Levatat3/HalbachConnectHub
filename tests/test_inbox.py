import http.client
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from halbach_connecthub import publisher as pub
from halbach_connecthub import server, submit
from halbach_connecthub import store as st


def later():
    return time.time() + 30          # "now" far enough ahead that a fresh folder counts as settled


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.home = self.tmp / "hub"
        self.s = st.Store(self.home)
        self.inbox = self.s.inbox_dir
        self.inbox.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._t.cleanup()

    def drop(self, name, files, meta=None):
        d = self.inbox / name
        d.mkdir()
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text, encoding="utf-8")
        if meta is not None:
            (d / "module.json").write_text(json.dumps(meta), encoding="utf-8")
        return d


class TestScan(Base):
    def test_new_submission_becomes_a_switched_off_module(self):
        self.drop("proj", {"index.md": "# Hi"}, {"name": "My Project", "desc": "An info dump"})
        added = self.s.scan_inbox(later())
        self.assertEqual(len(added), 1)
        m = added[0]
        self.assertEqual((m["name"], m["desc"], m["on"], m["kind"]), ("My Project", "An info dump", False, "static"))
        self.assertTrue(m["from_inbox"] and m["new"])
        self.assertEqual(self.s.pending(), [])              # off and never published: nothing goes out

    def test_not_imported_twice(self):
        self.drop("proj", {"index.md": "x"})
        self.s.scan_inbox(later())
        self.assertEqual(self.s.scan_inbox(later()), [])
        self.assertEqual(len(self.s.state["modules"]), 1)

    def test_folder_still_being_written_is_left_for_later(self):
        self.drop("proj", {"index.md": "x"})
        self.assertEqual(self.s.scan_inbox(time.time()), [])        # just written: wait
        self.assertEqual(len(self.s.scan_inbox(later())), 1)        # settled: import

    def test_name_falls_back_to_folder_and_text_is_cleaned(self):
        self.drop("fly-brain_notes", {"a.md": "x"})
        self.drop("b", {"a.md": "x"}, {"name": "Line\nbreak\x00name", "desc": "d" * 500})
        names = {m["name"]: m for m in self.s.scan_inbox(later())}
        self.assertIn("Fly Brain Notes", names)
        self.assertIn("Line break name", names)
        self.assertEqual(len(names["Line break name"]["desc"]), 200)

    def test_bad_or_huge_module_json_is_ignored(self):
        d = self.drop("x", {"a.md": "x"})
        (d / "module.json").write_text("{not json")
        self.assertEqual(self.s.scan_inbox(later())[0]["name"], "X")
        d2 = self.drop("y", {"a.md": "x"})
        (d2 / "module.json").write_text(json.dumps({"name": "Z" * (st.META_MAX_BYTES + 5)}))
        self.assertEqual(self.s.scan_inbox(later())[0]["name"], "Y")

    def test_secrets_in_a_submission_never_count_and_never_publish(self):
        d = self.drop("x", {"index.md": "ok", ".env": "KEY=1", "id_rsa": "k", "cert.pem": "c", "module.json": "{}"})
        files, _ = st.collect_files(d)
        self.assertEqual([f[1] for f in files], ["index.md"])

    def test_folder_with_only_private_files_is_skipped_with_one_warning(self):
        self.drop("x", {".env": "KEY=1", "id_rsa": "k"})
        self.assertEqual(self.s.scan_inbox(later()), [])
        self.s.scan_inbox(later())
        warns = [r for r in self.s.activity() if r["level"] == "warn"]
        self.assertEqual(len(warns), 1)

    def test_loose_files_and_dot_folders_are_ignored(self):
        (self.inbox / "loose.md").write_text("x")
        (self.inbox / ".hidden").mkdir()
        (self.inbox / ".hidden" / "a.md").write_text("x")
        self.assertEqual(self.s.scan_inbox(later()), [])

    def test_symlink_pointing_outside_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("x")
        link = self.inbox / "evil"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("symlinks not permitted here")
            import subprocess        # a junction needs no special rights on Windows
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("neither symlinks nor junctions could be created")
        self.assertTrue(link.exists())
        self.assertEqual(self.s.scan_inbox(later()), [])
        self.assertEqual(self.s.state["modules"], [])

    def test_oversize_submission_skipped(self):
        self.drop("big", {"a.md": "x"})
        with mock.patch.object(st, "INBOX_MAX_TOTAL_BYTES", 0):
            self.assertEqual(self.s.scan_inbox(later()), [])

    def test_submission_cap(self):
        with mock.patch.object(st, "INBOX_MAX_MODULES", 2):
            for n in "abc":
                self.drop(n, {"a.md": "x"})
            self.assertEqual(len(self.s.scan_inbox(later())), 2)

    def test_switching_on_or_acking_clears_the_review_flag(self):
        self.drop("a", {"a.md": "x"})
        self.drop("b", {"a.md": "x"})
        a, b = self.s.scan_inbox(later())
        self.s.set_on(a["id"], True)
        self.s.ack(b["id"])
        by = {m["id"]: m for m in self.s.state["modules"]}
        self.assertFalse(by[a["id"]]["new"])
        self.assertFalse(by[b["id"]]["new"])

    def test_removing_a_submission_shelves_it_and_it_is_not_reimported(self):
        d = self.drop("a", {"a.md": "x"})
        m = self.s.scan_inbox(later())[0]
        self.s.remove(m["id"])
        self.assertFalse(d.exists())
        self.assertTrue(any((self.inbox / ".removed").iterdir()))
        self.assertEqual(self.s.scan_inbox(later()), [])

    def test_regular_modules_are_untouched_by_remove(self):
        keep = self.tmp / "mine"
        keep.mkdir()
        (keep / "a.txt").write_text("x")
        m = self.s.add_module("static", "Mine", str(keep))
        self.s.remove(m["id"])
        self.assertTrue((keep / "a.txt").exists())

    def test_submission_survives_restart_without_duplicates(self):
        self.drop("a", {"a.md": "x"})
        self.s.scan_inbox(later())
        again = st.Store(self.home)
        self.assertEqual(again.scan_inbox(later()), [])
        self.assertEqual(len(again.state["modules"]), 1)


class TestSubmit(Base):
    def make_src(self):
        d = self.tmp / "work"
        d.mkdir()
        (d / "index.md").write_text("# Notes")
        (d / ".env").write_text("SECRET=1")
        (d / "pic.png").write_bytes(b"\x89PNG")
        return d

    def test_folder_copied_without_private_files_and_with_metadata(self):
        out = submit.submit(source=self.make_src(), name="Info Dump", desc="d", home=self.home)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["index.md", "module.json", "pic.png"])
        meta = json.loads((out / "module.json").read_text())
        self.assertEqual((meta["name"], meta["desc"], meta["by"]), ("Info Dump", "d", "halbach submit"))

    def test_lone_markdown_file_becomes_index_md(self):
        f = self.tmp / "summary.md"
        f.write_text("# Summary")
        out = submit.submit(source=f, home=self.home)
        self.assertTrue((out / "index.md").exists())
        self.assertEqual(json.loads((out / "module.json").read_text())["name"], "Summary")

    def test_text_becomes_index_md(self):
        out = submit.submit(text="# From a chat\n\nhello", name="Chat note", home=self.home)
        self.assertEqual((out / "index.md").read_text(), "# From a chat\n\nhello")

    def test_name_collisions_get_a_suffix(self):
        a = submit.submit(text="x", name="Same", home=self.home)
        b = submit.submit(text="y", name="Same", home=self.home)
        self.assertNotEqual(a, b)
        self.assertTrue(b.name.endswith("-2"))

    def test_no_temp_folders_left_behind(self):
        submit.submit(text="x", name="a", home=self.home)
        self.assertEqual([p.name for p in self.inbox.iterdir() if p.name.startswith(".tmp-")], [])

    def test_refusals(self):
        for kw in ({}, {"source": self.tmp, "text": "x"}, {"text": "   "}, {"source": Path(self.tmp.anchor)},
                   {"source": Path.home()}, {"source": self.tmp / "nope"}):
            with self.assertRaises(st.ValidationError, msg=str(kw)):
                submit.submit(home=self.home, **kw)
        only_secrets = self.tmp / "s"
        only_secrets.mkdir()
        (only_secrets / ".env").write_text("x")
        with self.assertRaises(st.ValidationError):
            submit.submit(source=only_secrets, home=self.home)
        self.assertEqual([p for p in self.inbox.iterdir() if not p.name.startswith(".")], [])

    def test_oversize_text_refused(self):
        with self.assertRaises(st.ValidationError):
            submit.submit(text="x" * (submit.MAX_STDIN_BYTES + 1), home=self.home)

    def test_cli_exit_codes_and_messages(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = submit.main([str(self.make_src()), "--name", "Via CLI", "--home", str(self.home)])
        self.assertEqual(code, 0)
        self.assertIn("switched OFF", out.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = submit.main([str(self.tmp / "missing"), "--home", str(self.home)])
        self.assertEqual(code, 2)
        self.assertIn("Not submitted", err.getvalue())

    def test_dispatch_from_main(self):
        from halbach_connecthub import __main__ as entry
        out = io.StringIO()
        with redirect_stdout(out):
            code = entry.main(["submit", str(self.make_src()), "--name", "Dispatch", "--home", str(self.home)])
        self.assertEqual(code, 0)

    def test_submit_then_scan_then_publish_plan(self):
        """The whole chain: a chat drops a note, the hub imports it, switching it on puts it in the manifest."""
        submit.submit(text="# Project notes\n\nAll done.", name="Project notes", desc="Wrap-up", home=self.home)
        mod = self.s.scan_inbox(later())[0]
        self.assertFalse(mod["on"])
        site = self.tmp / "site"
        site.mkdir()
        (site / "index.html").write_text("<h1>site</h1>")
        self.s.set_settings(site_dir=str(site), project="p")
        stage = self.tmp / "stage"
        self.assertEqual(pub.build_stage(self.s, stage)["modules"], [])          # off: not in the manifest
        self.s.set_on(mod["id"], True)
        man = pub.build_stage(self.s, stage)
        item = man["modules"][0]
        self.assertEqual((item["type"], item["entry"], item["desc"]), ("doc", "modules/project-notes/index.md", "Wrap-up"))
        self.assertFalse((stage / "modules" / "project-notes" / "module.json").exists())


class TestClassifyDocs(unittest.TestCase):
    def c(self, *rels):
        return pub.classify([("x", r, 1) for r in rels])

    def test_docs(self):
        self.assertEqual(self.c("notes.md"), ("doc", "notes.md"))
        self.assertEqual(self.c("index.md", "pic.png"), ("doc", "index.md"))
        self.assertEqual(self.c("index.md", "index.html"), ("page", "index.html"))     # html wins


class TestApiAndWatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.home = Path(cls._t.name) / "hub"
        cls.srv, cls.hub = server.serve(cls.home, 0, token="tok", inbox=False)
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.hub.close()
        cls._t.cleanup()

    def api(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok", "Content-Type": "application/json"}
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        r = c.getresponse()
        j = json.loads(r.read())
        c.close()
        return r.status, j

    def test_preview_and_ack(self):
        submit.submit(text="# Hello preview\n\nbody text", name="Previewable", home=self.home)
        added = self.hub.store.scan_inbox(later())
        mid = added[0]["id"]
        s, state = self.api("GET", "/api/state")
        mod = [m for m in state["modules"] if m["id"] == mid][0]
        self.assertTrue(mod["from_inbox"] and mod["new"])
        s, pv = self.api("GET", "/api/modules/%s/preview" % mid)
        self.assertEqual((s, pv["type"], pv["entry"]), (200, "doc", "index.md"))
        self.assertIn("Hello preview", pv["text"])
        self.assertEqual([f["path"] for f in pv["files"]], ["index.md"])        # module.json is not listed
        s, m = self.api("POST", "/api/modules/%s/ack" % mid, {})
        self.assertEqual((s, m["new"]), (200, False))

    def test_preview_of_unknown_module_is_404(self):
        self.assertEqual(self.api("GET", "/api/modules/nope/preview")[0], 404)

    def test_watcher_imports_new_submissions_by_itself(self):
        with tempfile.TemporaryDirectory() as t:
            srv, hub = server.serve(Path(t) / "h", 0, token="tok")
            try:
                with mock.patch.object(st, "INBOX_SETTLE_SECONDS", 0.0):
                    hub.start_inbox_watcher(interval=0.1)
                    submit.submit(text="# auto", name="Auto", home=Path(t) / "h")
                    end = time.time() + 5
                    while time.time() < end and not hub.store.state["modules"]:
                        time.sleep(0.05)
                self.assertEqual([m["name"] for m in hub.store.state["modules"]], ["Auto"])
            finally:
                hub.close()
                srv.server_close()


if __name__ == "__main__":
    unittest.main()


class TestByteOrderMarks(Base):
    def test_bom_is_stripped_from_text_and_markdown_files(self):
        a = submit.submit(text=chr(0xFEFF) + "# Title", name="a", home=self.home)
        self.assertEqual((a / "index.md").read_bytes(), b"# Title")
        f = self.tmp / "n.md"
        f.write_bytes(b"\xef\xbb\xbf# From file")
        b = submit.submit(source=f, name="b", home=self.home)
        self.assertEqual((b / "index.md").read_bytes(), b"# From file")
        d = self.tmp / "dd"
        d.mkdir()
        (d / "index.md").write_bytes(b"\xef\xbb\xbf# In folder")
        (d / "x.png").write_bytes(b"\x89PNG\xef\xbb\xbf")                 # non-markdown files are left byte for byte
        c = submit.submit(source=d, name="c", home=self.home)
        self.assertEqual((c / "index.md").read_bytes(), b"# In folder")
        self.assertEqual((c / "x.png").read_bytes(), b"\x89PNG\xef\xbb\xbf")
