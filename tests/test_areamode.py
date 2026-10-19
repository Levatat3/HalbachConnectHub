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
            ("POST", "/api/modules"), ("POST", "/api/tunnel/start"), ("POST", "/api/settings"),
            ("POST", "/api/publish"), ("POST", "/api/areas/create"), ("POST", "/api/quit"),
            ("POST", "/api/funkthemes/save"), ("POST", "/api/windowtheme"), ("POST", "/api/pick"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.req(method, path)[0], 404)

    def test_area_routes_are_served(self):
        self.assertEqual(self.req("GET", "/")[0], 200)
        self.assertEqual(self.req("GET", "/site.js")[0], 200)
        self.assertEqual(self.req("GET", "/app.js")[0], 200)
        self.assertEqual(self.req("GET", "/api/site")[0], 200)

    def test_area_post_routes_are_not_blocked(self):
        # A page edit reaches the model; it must not be a 404 from the restriction.
        s, raw = self.req("POST", "/api/site/page.add", {"label": "x", "slug": "x"})
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


if __name__ == "__main__":
    unittest.main()
