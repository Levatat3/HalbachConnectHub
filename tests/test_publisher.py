import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from connecthub import publisher as pub
from connecthub import store as st


def wait_done(p, secs=5):
    end = time.time() + secs
    while time.time() < end:
        if p.status()["state"] != "running":
            return p.status()
        time.sleep(0.02)
    raise AssertionError("publish did not finish")


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.site = self.tmp / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text("<h1>site</h1>")
        (self.site / "README.md").write_text("private notes")
        (self.site / ".env").write_text("SECRET=1")
        (self.site / "css").mkdir()
        (self.site / "css" / "a.css").write_text("body{}")
        self.s = st.Store(self.tmp / "hub")
        self.s.set_settings(site_dir=str(self.site), project="my-site", domain="example.com", live_host="live.example.com")
        self.stage = self.tmp / "stage"

    def tearDown(self):
        self._t.cleanup()

    def mod(self, name, files, on=True):
        d = self.tmp / name.replace(" ", "_")
        d.mkdir()
        for rel, text in files.items():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text(text)
        src = d if len(files) > 1 or "/" in next(iter(files)) else d / next(iter(files))
        m = self.s.add_module("static", name, str(src))
        self.s.set_on(m["id"], on)
        return m


class TestClassify(unittest.TestCase):
    def c(self, *rels):
        return pub.classify([("x", r, 1) for r in rels])

    def test_single_files(self):
        self.assertEqual(self.c("g.swf"), ("game", "g.swf"))
        self.assertEqual(self.c("p.html"), ("page", "p.html"))
        self.assertEqual(self.c("a.PNG"), ("image", "a.PNG"))
        self.assertEqual(self.c("doc.pdf"), ("file", "doc.pdf"))

    def test_folders(self):
        self.assertEqual(self.c("index.html", "a.js"), ("page", "index.html"))
        self.assertEqual(self.c("x/index.html", "index.html"), ("page", "index.html"))
        self.assertEqual(self.c("game.swf", "readme.txt"), ("game", "game.swf"))
        self.assertEqual(self.c("1.png", "2.jpg", "3.jpg")[0], "gallery")
        self.assertEqual(self.c("a.txt", "b.txt")[0], "file")


class TestBuildStage(Base):
    def test_site_copied_without_secrets_or_notes(self):
        pub.build_stage(self.s, self.stage)
        got = sorted(p.relative_to(self.stage).as_posix() for p in self.stage.rglob("*") if p.is_file())
        self.assertEqual(got, ["css/a.css", "index.html", "manifest.json"])

    def test_enabled_modules_only_and_manifest(self):
        self.mod("Demo Page", {"index.html": "x", "a.js": "y"})
        self.mod("Pics", {"1.png": "a", "2.png": "b"})
        self.mod("Hidden", {"z.html": "q"}, on=False)
        live = self.s.add_module("live", "Overlay", "127.0.0.1:8787", "desc")
        self.s.set_on(live["id"], True)
        man = pub.build_stage(self.s, self.stage)
        ids = [m["id"] for m in man["modules"]]
        self.assertEqual(ids, ["demo-page", "pics", "overlay"])
        demo, pics, ov = man["modules"]
        self.assertEqual((demo["type"], demo["entry"]), ("page", "modules/demo-page/index.html"))
        self.assertEqual(pics["type"], "gallery")
        self.assertEqual(pics["files"], ["modules/pics/1.png", "modules/pics/2.png"])
        self.assertEqual(ov["kind"], "live")
        self.assertEqual(man["live_base"], "https://live.example.com")
        self.assertTrue((self.stage / "modules" / "demo-page" / "index.html").exists())
        self.assertFalse((self.stage / "modules" / "hidden").exists())
        on_disk = json.loads((self.stage / "manifest.json").read_text())
        self.assertEqual(on_disk["build"], man["build"])

    def test_no_live_base_without_live_modules(self):
        self.assertIsNone(pub.build_stage(self.s, self.stage)["live_base"])

    def test_single_file_module(self):
        self.mod("Game", {"g.swf": "FWS"})
        man = pub.build_stage(self.s, self.stage)
        self.assertEqual((man["modules"][0]["type"], man["modules"][0]["entry"]), ("game", "modules/game/g.swf"))

    def test_site_using_reserved_names_is_refused(self):
        (self.site / "manifest.json").write_text("{}")
        with self.assertRaises(pub.PublishError):
            pub.build_stage(self.s, self.stage)
        (self.site / "manifest.json").unlink()
        (self.site / "modules").mkdir()
        (self.site / "modules" / "x.txt").write_text("x")
        with self.assertRaises(pub.PublishError):
            pub.build_stage(self.s, self.stage)

    def test_missing_site_dir(self):
        self.s.state["settings"]["site_dir"] = None
        with self.assertRaises(pub.PublishError):
            pub.build_stage(self.s, self.stage)

    def test_stage_is_rebuilt_clean(self):
        self.mod("Demo", {"index.html": "x", "a.js": "y"})
        pub.build_stage(self.s, self.stage)
        self.s.set_on("demo", False)
        pub.build_stage(self.s, self.stage)
        self.assertFalse((self.stage / "modules").exists())


class TestPublisher(Base):
    def make(self, deployer=None, verifier=None):
        calls = []

        def dep(stage, project, branch, log):
            calls.append((str(stage), project, branch))
            self.assertTrue((Path(stage) / "manifest.json").exists())
            return "https://abc123.my-site.pages.dev"
        return pub.Publisher(self.s, deployer or dep, verifier or (lambda host, build: True)), calls

    def test_success_marks_published_and_logs(self):
        self.mod("Demo", {"index.html": "x", "a.js": "y"})
        p, calls = self.make()
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        s = wait_done(p)
        self.assertEqual(s["state"], "done")
        self.assertIn("verified live", s["message"])
        self.assertEqual(calls[0][1:], ("my-site", "main"))
        self.assertEqual(self.s.pending(), [])
        self.assertIsNotNone(self.s.state["last_deploy"])
        self.assertTrue(any("Publish finished" in r["msg"] for r in self.s.activity()))

    def test_preview_does_not_mark_published(self):
        self.mod("Demo", {"index.html": "x", "a.js": "y"})
        p, calls = self.make()
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start(preview=True)
        self.assertEqual(wait_done(p)["state"], "done")
        self.assertEqual(calls[0][2], "preview")
        self.assertEqual([x["change"] for x in self.s.pending() if x["id"] != "__site__"], ["add"])
        self.assertTrue(any(x["id"] == "__site__" for x in self.s.pending()))

    def test_site_edits_become_pending_until_published(self):
        self.assertEqual([x["id"] for x in self.s.pending()], ["__site__"])   # never published
        p, _ = self.make()
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        wait_done(p)
        self.assertEqual(self.s.pending(), [])
        time.sleep(1.1)
        (self.site / "index.html").write_text("<h1>edited, and longer than before</h1>")
        self.assertEqual([x["id"] for x in self.s.pending()], ["__site__"])
        (self.site / "README.md").write_text("notes change; never published")
        self.s.mark_published()
        self.assertEqual(self.s.pending(), [])                     # README edits are ignored

    def test_view_returns_pending_objects(self):
        self.assertEqual(self.s.view()["pending"][0]["name"], "Site files")

    def test_failure_keeps_changes_pending(self):
        self.mod("Demo", {"index.html": "x", "a.js": "y"})

        def boom(*a):
            raise pub.PublishError("Not signed in to Cloudflare.")
        p, _ = self.make(deployer=boom)
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        s = wait_done(p)
        self.assertEqual((s["state"], s["message"]), ("error", "Not signed in to Cloudflare."))
        self.assertEqual([x["change"] for x in self.s.pending() if x["id"] != "__site__"], ["add"])

    def test_unexpected_exception_does_not_hang(self):
        def boom(*a):
            raise RuntimeError("kaboom")
        p, _ = self.make(deployer=boom)
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        s = wait_done(p)
        self.assertEqual(s["state"], "error")
        self.assertIn("kaboom", s["message"])

    def test_only_one_publish_at_a_time(self):
        gate = threading.Event()

        def slow(*a):
            gate.wait(3)
            return None
        p, _ = self.make(deployer=slow)
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
            with self.assertRaises(st.ValidationError):
                p.start()
        gate.set()
        wait_done(p)

    def test_not_ready_without_settings_or_node(self):
        p, _ = self.make()
        with mock.patch.object(pub, "find_npx", return_value=None):
            self.assertFalse(p.ready()[0])
            with self.assertRaises(st.ValidationError):
                p.start()
        self.s.state["settings"]["project"] = ""
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            self.assertFalse(p.ready()[0])

    def test_unverified_is_a_warning_not_a_failure(self):
        p, _ = self.make(verifier=lambda host, build: False)
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        s = wait_done(p)
        self.assertEqual(s["state"], "done")
        self.assertNotIn("verified", s["message"])
        self.assertTrue(any(r["level"] == "warn" for r in self.s.activity()))


class TestWranglerErrors(unittest.TestCase):
    def run_with(self, returncode, out):
        res = mock.Mock(returncode=returncode, stdout=out, stderr="")
        with mock.patch.object(pub, "find_npx", return_value="npx"), \
                mock.patch.object(pub.subprocess, "run", return_value=res):
            return pub.wrangler_deploy("stage", "proj", "main", lambda *a: None)

    def test_url_parsed(self):
        out = "Uploading...\n Deployment complete! Take a peek over at https://07991b51.levmorium.pages.dev\n"
        self.assertEqual(self.run_with(0, out), "https://07991b51.levmorium.pages.dev")

    def test_not_signed_in_is_friendly(self):
        with self.assertRaises(pub.PublishError) as cm:
            self.run_with(1, "You are not authenticated. Please run `wrangler login`.")
        self.assertIn("wrangler@4 login", str(cm.exception))

    def test_unknown_project_is_friendly(self):
        with self.assertRaises(pub.PublishError) as cm:
            self.run_with(1, "Project not found. The specified project name does not match")
        self.assertIn("Settings", str(cm.exception))

    def test_no_node(self):
        with mock.patch.object(pub, "find_npx", return_value=None):
            with self.assertRaises(pub.PublishError):
                pub.wrangler_deploy("s", "p", "main", lambda *a: None)

    def test_command_is_a_list_with_validated_project(self):
        res = mock.Mock(returncode=0, stdout="https://x.pages.dev", stderr="")
        with mock.patch.object(pub, "find_npx", return_value="npx"), \
                mock.patch.object(pub.subprocess, "run", return_value=res) as run:
            pub.wrangler_deploy("stage", "proj", "main", lambda *a: None)
        cmd = run.call_args[0][0]
        self.assertIsInstance(cmd, list)
        self.assertNotIn("shell", run.call_args[1])
        self.assertEqual(cmd[cmd.index("--project-name") + 1], "proj")


if __name__ == "__main__":
    unittest.main()
