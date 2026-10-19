import http.client
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from halbach_connecthub import publisher as pub
from halbach_connecthub import server, sitebuilder
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.store = st.Store(self.root / "hub")
        self.svc = ss.SiteService(self.store)
        self.svc.apply("template.load", {"id": "personal"})

    def tearDown(self):
        self._t.cleanup()


class TestEdits(Base):
    def test_every_edit_is_saved_with_a_history_snapshot(self):
        n = len(self.svc.model.history())
        self.svc.apply("page.add", {"label": "Funk Workshop", "slug": "funkworkshop"})
        self.assertEqual(self.svc.model.page("funk-workshop")["slug"], "funkworkshop")
        self.assertGreater(len(self.svc.model.history()), n)
        again = ss.SiteService(self.store)                 # survives a restart
        self.assertEqual(again.model.page("funk-workshop")["label"], "Funk Workshop")

    def test_a_refused_edit_leaves_everything_as_it_was(self):
        before = json.dumps(self.svc.model.data, sort_keys=True)
        with self.assertRaises(sm.ModelError):
            self.svc.apply("page.add", {"label": "Dupe", "slug": "projects"})
        with self.assertRaises(sm.ModelError):
            self.svc.apply("block.add", {"page": "home", "type": "cards", "props": {"items": [{"title": ""}]}})
        with self.assertRaises(sm.ModelError):
            self.svc.apply("nonsense", {})
        self.assertEqual(json.dumps(self.svc.model.data, sort_keys=True), before)

    def test_removing_a_tab_is_one_edit_no_assistant_needed(self):
        self.svc.apply("page.delete", {"id": "projects"})
        self.assertNotIn("projects", [p["id"] for p in self.svc.model.data["pages"]])
        res = self.svc.build(self.root / "stage")
        self.assertTrue(res.ok, res.errors)
        self.assertFalse((self.root / "stage" / "projects").exists())
        self.assertNotIn("/projects/", (self.root / "stage" / "index.html").read_text(encoding="utf-8"))

    def test_deleting_a_page_a_card_points_at_is_caught_before_publishing(self):
        self.svc.apply("page.delete", {"id": "about"})
        res = self.svc.build(self.root / "stage")
        self.assertFalse(res.ok)
        self.assertIn("page that does not exist", self.svc.explain(res))

    def test_block_edits_use_the_current_modules(self):
        src = self.root / "g.swf"
        src.write_bytes(b"FWS")
        mod = self.store.add_module("static", "Game", str(src))
        with self.assertRaises(sm.ModelError):                   # still switched off
            self.svc.apply("block.add", {"page": "home", "type": "module-view", "props": {"module": mod["id"]}})
        self.store.set_on(mod["id"], True)
        self.svc.apply("block.add", {"page": "home", "type": "module-view", "props": {"module": mod["id"]}})
        self.assertEqual(self.svc.model.page("home")["blocks"][-1]["type"], "module-view")

    def test_restore_brings_back_an_older_site(self):
        first = self.svc.model.history()[0]["n"]
        self.svc.apply("page.delete", {"id": "about"})
        self.svc.apply("restore", {"n": first})
        self.assertIn("about", [p["id"] for p in self.svc.model.data["pages"]])

    def test_view_lists_catalog_and_problems(self):
        v = self.svc.view()
        self.assertEqual({c["id"] for c in v["catalog"]["theme"]},
                         {"retro-terminal", "neutral-dark", "neutral-light", "paper", "solarized-dark", "high-contrast", "funk"})
        self.assertIn("hero", {b["id"] for b in v["catalog"]["block"]})
        self.assertIsInstance(v["errors"], list)


class TestPublishAndPending(Base):
    def make(self):
        calls = []

        def dep(stage, project, branch, log):
            calls.append(Path(stage))
            self.assertTrue((Path(stage) / "index.html").is_file())
            self.assertTrue((Path(stage) / "_headers").is_file())
            return "https://x.pages.dev"
        return pub.Publisher(self.store, dep, lambda host, build: True, site=self.svc), calls

    def wait(self, p):
        for _ in range(100):
            s = p.status()
            if s["state"] != "running":
                return s
            time.sleep(0.05)
        self.fail("publish did not finish")

    def test_modular_publish_builds_pages_and_marks_the_model_published(self):
        self.store.set_settings(project="my-site", site_mode="modular")
        self.assertEqual([x["id"] for x in self.store.pending()], ["__model__"])
        p, calls = self.make()
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        self.assertEqual(self.wait(p)["state"], "done")
        self.assertEqual(self.store.pending(), [])
        self.svc.apply("page.add", {"label": "New"})
        self.assertEqual([x["id"] for x in self.store.pending()], ["__model__"])

    def test_problems_stop_a_publish_with_a_plain_message(self):
        self.store.set_settings(project="my-site", site_mode="modular")
        self.svc.model.data["pages"][0]["blocks"].append({"id": "zz", "type": "module-view", "props": {"module": "nope"}, "enabled": True})
        p, calls = self.make()
        with mock.patch.object(pub, "find_npx", return_value="npx"):
            p.start()
        s = self.wait(p)
        self.assertEqual(s["state"], "error")
        self.assertIn("fix first", s["message"])
        self.assertEqual(calls, [])

    def test_classic_mode_is_untouched_by_the_modular_code(self):
        self.assertEqual(self.store.state["settings"].get("site_mode", "classic"), "classic")
        with self.assertRaises(st.ValidationError):
            self.store.set_settings(site_mode="bogus")
        p, _ = self.make()
        ok, why = p.ready()
        self.assertFalse(ok)                                        # classic still needs the site folder
        self.assertIn("site folder", why)


class TestPreviewRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        tmp = Path(cls._t.name)
        cls.srv, cls.hub = server.serve(tmp / "hub", 0, token="tok")
        cls.hub.site.apply("template.load", {"id": "streamer"})
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if token:
            h["X-Halbach-Token"] = "tok"
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        hdrs = dict((k.lower(), v) for k, v in r.getheaders())
        c.close()
        return r.status, raw, hdrs

    def test_preview_needs_a_build_then_serves_it_with_real_headers(self):
        import shutil
        shutil.rmtree(self.hub.site.preview_dir, ignore_errors=True)
        s, raw, _ = self.req("GET", "/preview/", token=False)
        self.assertEqual(s, 404)
        s, raw, _ = self.req("POST", "/api/site/preview", {})
        self.assertEqual(s, 200)
        self.assertTrue(json.loads(raw)["ok"], raw)
        s, raw, h = self.req("GET", "/preview/", token=False)
        self.assertEqual(s, 200)
        self.assertIn(b"hs-page", raw)
        self.assertIn("player.twitch.tv", h.get("content-security-policy-report-only", "") + h.get("content-security-policy", ""))
        self.assertEqual(h["cache-control"], "no-store")
        s, raw, h = self.req("GET", "/preview/live", token=False)
        self.assertEqual(s, 308)
        s, raw, h = self.req("GET", "/preview/live/", token=False)
        self.assertEqual(s, 200)
        s, raw, h = self.req("GET", "/preview/nothing-here/", token=False)
        self.assertEqual(s, 404)
        self.assertIn(b"Not found", raw)

    def test_preview_cannot_be_used_to_read_other_files(self):
        self.req("POST", "/api/site/preview", {})
        for bad in ("/preview/../state.json", "/preview/%2e%2e/state.json", "/preview/..%5cstate.json", "/preview//etc/passwd"):
            s, raw, _ = self.req("GET", bad, token=False)
            self.assertNotEqual(s, 200, bad)
            self.assertNotIn(b"modules", raw[:30] if b"{" in raw[:5] else b"")
        s, _, _ = self.req("GET", "/preview/", token=False, host="evil.example")
        self.assertEqual(s, 403)

    def test_publish_plan_in_modular_mode_lists_the_real_upload_and_the_changes(self):
        self.req("POST", "/api/settings", {"site_mode": "modular"})
        s, raw, _ = self.req("GET", "/api/publish/plan")
        plan = json.loads(raw)
        self.assertTrue(plan["modular"])
        paths = [f["path"] for f in plan["files"]]
        self.assertIn("index.html", paths)
        self.assertIn("_headers", paths)
        self.assertTrue(plan["site_changes"])
        self.assertEqual(plan["site_problems"], [])
        self.req("POST", "/api/settings", {"site_mode": "classic"})

    def test_site_api_requires_the_token_and_reports_refusals(self):
        self.assertEqual(self.req("GET", "/api/site", token=False)[0], 401)
        s, raw, _ = self.req("GET", "/api/site")
        self.assertEqual(s, 200)
        self.assertIn("catalog", json.loads(raw))
        s, raw, _ = self.req("POST", "/api/site/page.add", {"label": "X", "slug": "modules"})
        self.assertEqual(s, 400)
        self.assertIn("error", json.loads(raw))

    def test_headers_parser_matches_what_the_builder_writes(self):
        text = sitebuilder.build_headers("default-src 'self'", "enforce")
        rules = sitebuilder.parse_headers(text)
        root = sitebuilder.headers_for(rules, "/")
        self.assertEqual(root["Content-Security-Policy"], "default-src 'self'")
        asset = sitebuilder.headers_for(rules, "/_halbach/x.js")
        self.assertEqual(asset["Cache-Control"], "public, max-age=31536000, immutable")
        mod = sitebuilder.headers_for(rules, "/modules/a/index.html")
        self.assertIn("unsafe-inline", mod["Content-Security-Policy"])
        self.assertNotIn("default-src 'self'; ", mod["Content-Security-Policy"].replace("default-src 'self' https:", ""))


if __name__ == "__main__":
    unittest.main()
