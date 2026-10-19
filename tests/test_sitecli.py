import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from halbach_connecthub import __main__ as entry
from halbach_connecthub import publisher as pub
from halbach_connecthub import quality
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st
from halbach_connecthub.single import InstanceLock


def run(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = entry.main(list(args))
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.home = self.root / "hub"
        svc = ss.SiteService(st.Store(self.home))
        svc.apply("template.load", {"id": "personal"})

    def tearDown(self):
        self._t.cleanup()

    def test_check_reports_advice_and_exits_zero_when_nothing_blocks(self):
        code, out = run("site", "--home", str(self.home), "check")
        self.assertEqual(code, 0, out)
        self.assertIn("placeholder", out)              # the template's example text is called out

    def test_build_writes_the_site_and_refuses_a_broken_one(self):
        out_dir = self.root / "out"
        code, out = run("site", "--home", str(self.home), "build", "--out", str(out_dir))
        self.assertEqual(code, 0, out)
        self.assertTrue((out_dir / "index.html").is_file())
        svc = ss.SiteService(st.Store(self.home))
        svc.model.data["pages"][0]["enabled"] = False
        svc.model.save("break")
        code, out = run("site", "--home", str(self.home), "build", "--out", str(self.root / "out2"))
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "out2" / "index.html").exists())

    def test_export_then_import_round_trips(self):
        f = self.root / "site.json"
        self.assertEqual(run("site", "--home", str(self.home), "export", str(f))[0], 0)
        pkg = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(pkg["format"], "halbach-site")
        pkg["model"]["brand"]["name"] = "CHANGED"
        f.write_text(json.dumps(pkg), encoding="utf-8")
        code, out = run("site", "--home", str(self.home), "import", str(f))
        self.assertEqual(code, 0, out)
        self.assertEqual(ss.SiteService(st.Store(self.home)).model.data["brand"]["name"], "CHANGED")

    def test_import_refuses_junk_and_newer_versions_and_leaves_the_site_alone(self):
        bad = self.root / "bad.json"
        for content in ('{"nope": 1}', "not json", json.dumps({"format": "halbach-site", "version": 99, "model": {"pages": []}}),
                        json.dumps({"format": "halbach-site", "version": 1, "model": {"pages": [{"blocks": [0] * 500}]}})):
            bad.write_text(content, encoding="utf-8")
            self.assertEqual(run("site", "--home", str(self.home), "import", str(bad))[0], 1, content[:30])
        self.assertEqual(ss.SiteService(st.Store(self.home)).model.data["brand"]["name"], "PERSONAL SITE")


class TestPublish(unittest.TestCase):
    """`halbach site publish`: the window's Publish button, run headless. Never deploys for real in tests."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.home = self.root / "hub"
        self.store = st.Store(self.home)
        self.store.set_settings(site_mode="modular", project="my-site", domain="example.com")
        ss.SiteService(self.store).apply("template.load", {"id": "personal"})
        self.deployed = []

    def tearDown(self):
        self._t.cleanup()

    def run_publish(self, *args, deployer=None):
        def dep(stage, project, branch, log):
            self.deployed.append(branch)
            self.assertTrue((Path(stage) / "manifest.json").is_file())
            return "https://abc123.my-site.pages.dev"
        with mock.patch.object(pub, "find_npx", return_value="npx"), \
                mock.patch.object(pub, "wrangler_deploy", deployer or dep), \
                mock.patch.object(pub, "fetch_manifest_build", lambda host, build: True):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = entry.main(["site", "--home", str(self.home), "publish"] + list(args))
        return code, buf.getvalue()

    def test_publishes_prints_the_url_and_exits_zero(self):
        code, out = self.run_publish()
        self.assertEqual(code, 0, out)
        self.assertIn("https://abc123.my-site.pages.dev", out)
        self.assertEqual(self.deployed, ["main"])
        self.assertIsNotNone(st.Store(self.home).state["last_deploy"])
        lock = InstanceLock(self.home)                             # the state folder is free again afterwards
        self.assertTrue(lock.acquire())
        lock.release()

    def test_preview_deploys_the_preview_branch_and_keeps_the_live_site(self):
        code, out = self.run_publish("--preview")
        self.assertEqual(code, 0, out)
        self.assertIn("Preview ready", out)
        self.assertEqual(self.deployed, ["preview"])
        self.assertIsNone(st.Store(self.home).state.get("last_deploy"))

    def test_refuses_a_classic_site(self):
        self.store.set_settings(site_mode="classic")
        code, out = self.run_publish()
        self.assertEqual(code, 1)
        self.assertIn("classic", out)
        self.assertEqual(self.deployed, [])

    def test_refuses_while_the_window_is_open(self):
        held = InstanceLock(self.home)                             # what the hub window holds while it runs
        self.assertTrue(held.acquire())
        try:
            code, out = self.run_publish()
        finally:
            held.release()
        self.assertEqual(code, 1)
        self.assertIn("Halbach is open", out)
        self.assertEqual(self.deployed, [])                        # nothing was uploaded over the window

    def test_a_failed_deploy_prints_the_reason_and_exits_one(self):
        def boom(stage, project, branch, log):
            raise pub.PublishError("Not signed in to Cloudflare.")
        code, out = self.run_publish(deployer=boom)
        self.assertEqual(code, 1)
        self.assertIn("Not signed in to Cloudflare.", out)
        self.assertIsNone(st.Store(self.home).state.get("last_deploy"))

    def test_not_ready_without_a_project_name(self):
        self.store.set_settings(project="")
        code, out = self.run_publish()
        self.assertEqual(code, 1)
        self.assertIn("Pages project", out)
        self.assertEqual(self.deployed, [])


class TestQuality(unittest.TestCase):
    def test_contrast_maths(self):
        self.assertAlmostEqual(quality.contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertLess(quality.contrast("#777777", "#808080"), 1.5)
        self.assertIsNone(quality.contrast("red", "#fff"))

    def test_low_contrast_theme_is_flagged_and_good_one_is_not(self):
        data = {"pages": [], "settings": {"csp": "enforce", "indexable": True, "base_url": "https://x.y"}}
        bad = quality.check_site(data, {"--text": "#777777", "--bg": "#808080", "--muted": "#888888", "--accent": "#999999"})
        self.assertTrue(any("hard to read" in w["message"] for w in bad))
        good = quality.check_site(data, {"--text": "#ffffff", "--bg": "#000000", "--muted": "#cccccc", "--accent": "#7cc4ff"})
        self.assertEqual(good, [])


if __name__ == "__main__":
    unittest.main()
