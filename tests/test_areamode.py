"""The collaborator's area window: only its own routes exist; every owner route 404s (Task 2.2)."""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import areaclient, areamode, server
from halbach_connecthub.registry import Registry
from halbach_connecthub.sitemodel import SiteModel


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok", area=True)
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok"}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw


class TestAreaMode(Base):
    def test_owner_routes_are_404(self):
        for method, path in (
            ("GET", "/api/state"), ("GET", "/api/activity"), ("GET", "/api/live"),
            ("GET", "/api/publish/status"), ("GET", "/api/publish/plan"),
            ("GET", "/api/areas"), ("GET", "/api/funkthemes"), ("GET", "/api/windowtheme"),
            ("GET", "/api/site/export"),
            ("GET", "/app.js"), ("GET", "/areas.js"),
            ("POST", "/api/modules"), ("POST", "/api/tunnel/start"), ("POST", "/api/settings"),
            ("POST", "/api/publish"), ("POST", "/api/areas/create"), ("POST", "/api/quit"),
            ("POST", "/api/funkthemes/save"), ("POST", "/api/windowtheme"), ("POST", "/api/pick"),
            # site actions the collaborator must not reach
            ("POST", "/api/site/template.load"), ("POST", "/api/site/settings"), ("POST", "/api/site/theme"),
            ("POST", "/api/site/brand"), ("POST", "/api/site/import.classic"), ("POST", "/api/site/import.package"),
            ("POST", "/api/site/restore"), ("POST", "/api/site/redirects"), ("POST", "/api/site/nav_links"),
            ("POST", "/api/site/linked.add"), ("POST", "/api/site/page.add"), ("POST", "/api/site/page.delete"),
            ("POST", "/api/site/page.move"), ("POST", "/api/site/page.duplicate"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.req(method, path)[0], 404)

    def test_area_routes_are_served(self):
        s, raw = self.req("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn(b"Your area", raw)                       # the restricted window, not the owner's
        self.assertEqual(self.req("GET", "/area.js")[0], 200)
        self.assertEqual(self.req("GET", "/api/site")[0], 200)

    def test_unconnected_area_client_returns_a_clean_400(self):
        # No connection file -> hub.area_client() raises; it must become a plain 400, never a dropped connection.
        self.assertEqual(self.req("GET", "/api/area/limits")[0], 400)
        self.assertEqual(self.req("GET", "/api/area/status")[0], 400)

    def test_area_post_routes_are_not_blocked(self):
        # A block edit reaches the model; it must not be a 404 from the restriction.
        s, raw = self.req("POST", "/api/site/block.add", {"page": "page", "type": "text", "props": {"markdown": "hi"}})
        self.assertNotEqual(s, 404, raw)
        self.assertNotEqual(self.req("POST", "/api/site/preview")[0], 404)      # the preview route exists


class TestAreaModel(unittest.TestCase):
    def test_round_trip_keeps_blocks(self):
        with tempfile.TemporaryDirectory() as t:
            root, folder = Path(t), Path(t) / "page"
            areaclient.init_folder(folder)
            reg = Registry()
            m = SiteModel(root / "hub", reg)
            areamode.load_area(folder, m)
            self.assertEqual(m.data["pages"][0]["slug"], "")
            self.assertEqual(len(m.data["pages"][0]["blocks"]), 2)         # hero + text from the template
            # an edit and dump survive a reload
            m.data["pages"][0]["blocks"][0]["props"]["heading"] = "Edited"
            areamode.dump_area(m, folder)
            m2 = SiteModel(root / "hub2", reg)
            areamode.load_area(folder, m2)
            self.assertEqual(m2.data["pages"][0]["blocks"][0]["props"]["heading"], "Edited")

    def test_missing_folder_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as t:
            reg = Registry()
            m = SiteModel(Path(t) / "hub", reg)
            with self.assertRaises(Exception):
                areamode.load_area(Path(t) / "nope", m)

    def test_dump_writes_area_json(self):
        with tempfile.TemporaryDirectory() as t:
            root, folder = Path(t), Path(t) / "page"
            areaclient.init_folder(folder)
            m = SiteModel(root / "hub", Registry())
            areamode.load_area(folder, m)
            areamode.dump_area(m, folder)
            doc = json.loads((folder / "area.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["format"], 1)
            self.assertEqual(doc["pages"][0]["blocks"][0]["type"], "hero")

    def test_dump_never_drops_a_disabled_block(self):
        # The area format has no per-block on/off switch, so a block with enabled=False must still be written
        # (never silently dropped), rather than lost on the round-trip.
        with tempfile.TemporaryDirectory() as t:
            root, folder = Path(t), Path(t) / "page"
            areaclient.init_folder(folder)
            m = SiteModel(root / "hub", Registry())
            areamode.load_area(folder, m)
            m.data["pages"][0]["blocks"][0]["enabled"] = False
            areamode.dump_area(m, folder)
            doc = json.loads((folder / "area.json").read_text(encoding="utf-8"))
            self.assertEqual(len(doc["pages"][0]["blocks"]), 2)     # nothing dropped

    def test_every_page_and_block_round_trips(self):
        with tempfile.TemporaryDirectory() as t:
            root, folder = Path(t), Path(t) / "page"
            areaclient.init_folder(folder)
            # a second page, added the way the owner's builder would see it
            doc = json.loads((folder / "area.json").read_text(encoding="utf-8"))
            doc["pages"].append({"name": "about", "title": "About", "blocks": [{"type": "text", "props": {"markdown": "hi"}}]})
            (folder / "area.json").write_text(json.dumps(doc), encoding="utf-8")
            m = SiteModel(root / "hub", Registry())
            areamode.load_area(folder, m)
            self.assertEqual(len(m.data["pages"]), 2)
            self.assertEqual(m.data["pages"][1]["slug"], "about")
            areamode.dump_area(m, folder)
            doc2 = json.loads((folder / "area.json").read_text(encoding="utf-8"))
            self.assertEqual(len(doc2["pages"]), 2)
            self.assertEqual(doc2["pages"][1]["name"], "about")
            self.assertEqual(doc2["pages"][1]["blocks"][0]["type"], "text")


class TestAreaFolderLoading(unittest.TestCase):
    def test_area_folder_loads_into_the_model(self):
        with tempfile.TemporaryDirectory() as t:
            root, folder = Path(t), Path(t) / "page"
            areaclient.init_folder(folder)
            srv, hub = server.serve(Path(t) / "hub", 0, token="tok", area=True, area_folder=str(folder))
            port = srv.server_address[1]
            server.run_in_thread(srv)
            try:
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request("GET", "/api/site", headers={"Host": "127.0.0.1:%d" % port, "X-Halbach-Token": "tok"})
                r = c.getresponse()
                body = json.loads(r.read())
                c.close()
                self.assertEqual(r.status, 200)
                self.assertEqual(len(body["model"]["pages"]), 1)
                self.assertEqual(len(body["model"]["pages"][0]["blocks"]), 2)
            finally:
                srv.shutdown()
                srv.server_close()


class TestAreaWindowEndToEnd(unittest.TestCase):
    """Drive the collaborator window over HTTP the way the browser would: load, edit, add/update/delete blocks,
    preview, and persist back to area.json. No browser needed."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.folder = Path(cls._t.name) / "page"
        areaclient.init_folder(cls.folder)
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok", area=True, area_folder=str(cls.folder))
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok"}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    def model(self):
        s, d = self.req("GET", "/api/site")
        self.assertEqual(s, 200)
        return json.loads(d)["model"]

    def test_full_edit_flow(self):
        page = self.model()["pages"][0]
        self.assertEqual(page["id"], "page")
        self.assertEqual(page["slug"], "")
        self.assertEqual([b["type"] for b in page["blocks"]], ["hero", "text"])

        # title + description
        self.assertEqual(self.req("POST", "/api/site/page.update", {"id": "page", "title": "Workshop", "label": "Workshop", "description": "A page"})[0], 200)
        self.assertEqual(self.model()["pages"][0]["title"], "Workshop")

        # add a block
        s, raw = self.req("POST", "/api/site/block.add", {"page": "page", "type": "text", "props": {"markdown": "Hello **there**"}})
        self.assertEqual(s, 200, raw)
        blocks = self.model()["pages"][0]["blocks"]
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[-1]["props"]["markdown"], "Hello **there**")
        bid = blocks[-1]["id"]

        # update it
        self.assertEqual(self.req("POST", "/api/site/block.update", {"page": "page", "id": bid, "props": {"markdown": "Changed"}})[0], 200)
        self.assertEqual(self.model()["pages"][0]["blocks"][-1]["props"]["markdown"], "Changed")

        # move it up, then delete it
        self.assertEqual(self.req("POST", "/api/site/block.move", {"page": "page", "id": bid, "delta": -1})[0], 200)
        self.assertEqual(self.req("POST", "/api/site/block.delete", {"page": "page", "id": bid})[0], 200)
        self.assertEqual(len(self.model()["pages"][0]["blocks"]), 2)

        # preview builds cleanly
        s, raw = self.req("POST", "/api/site/preview", {})
        self.assertEqual(s, 200)
        summary = json.loads(raw)
        self.assertTrue(summary["ok"], summary.get("errors"))

        # the edit persists to area.json on the next dump (which send performs)
        areamode.dump_area(self.hub.site.model, self.folder)
        doc = json.loads((self.folder / "area.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["pages"][0]["title"], "Workshop")
        self.assertEqual(len(doc["pages"][0]["blocks"]), 2)


class TestAreaDesignWindow(unittest.TestCase):
    """The Design panel's API: the sender edits their site's look; Preview shows it; it is written into area.json on send."""

    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.folder = Path(cls._t.name) / "page"
        areaclient.init_folder(cls.folder)
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok", area=True, area_folder=str(cls.folder))
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok"}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    GOOD = {"brand": {"name": "Funka's Workshop", "mark": "FW", "footer": "by Funka"}, "theme": "neutral-light",
            "vars": {"--accent": "#ff8800"}, "layout": {"nav": "side"}}

    def test_offline_every_installed_theme_is_offered_for_the_preview(self):
        s, raw = self.req("GET", "/api/area/design")
        view = json.loads(raw)
        self.assertEqual(s, 200)
        self.assertTrue(view["enabled"])
        self.assertFalse(view["from_owner"])
        self.assertIn("neutral-light", [t["id"] for t in view["themes"]])

    def test_saving_a_design_shows_in_preview_and_is_written_on_send(self):
        s, raw = self.req("POST", "/api/area/design", {"design": self.GOOD})
        self.assertEqual(s, 200, raw)
        self.assertEqual(json.loads(raw)["design"]["brand"]["name"], "Funka's Workshop")
        self.assertEqual(self.req("POST", "/api/site/preview", {})[0], 200)
        s, page = self.req("GET", "/preview/")
        text = page.decode("utf-8")
        self.assertIn("Funka&#x27;s Workshop", text)
        self.assertIn('data-nav="side"', text)
        areamode.dump_area(self.hub.site.model, self.folder, self.hub.design)
        doc = json.loads((self.folder / "area.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["design"], self.GOOD)

    def test_hostile_designs_are_refused_with_a_plain_reason(self):
        for bad in ({"vars": {"--accent": "url(https://evil.example/x)"}}, {"theme": "nope"}, {"script": "alert(1)"}, {"brand": {"logo": "../x.png"}},
                    {"layout": {"nav": "bottom"}}):
            with self.subTest(design=bad):
                s, raw = self.req("POST", "/api/area/design", {"design": bad})
                self.assertEqual(s, 400, raw)
                self.assertIn("error", json.loads(raw))

    def test_when_the_owner_allowed_no_themes_the_panel_says_so_and_refuses(self):
        from unittest import mock
        with mock.patch.object(self.hub, "_design_rules", return_value=([], True)):
            view = json.loads(self.req("GET", "/api/area/design")[1])
            self.assertFalse(view["enabled"])
            s, raw = self.req("POST", "/api/area/design", {"design": self.GOOD})
            self.assertEqual(s, 400)
            self.assertIn("not enabled", json.loads(raw)["error"])

    def test_only_the_design_routes_were_added_to_the_window(self):
        self.assertEqual(self.req("GET", "/api/areas")[0], 404)
        self.assertEqual(self.req("POST", "/api/areas/create", {"name": "x"})[0], 404)


if __name__ == "__main__":
    unittest.main()
