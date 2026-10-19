import http.client
import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import server


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        tmp = Path(cls._t.name)
        cls.site = tmp / "stuff"
        cls.site.mkdir()
        (cls.site / "a.txt").write_text("hi")
        cls.srv, cls.hub = server.serve(tmp / "hub", 0, token="tok")
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    def api(self, method, path, body=None):
        s, raw = self.req(method, path, body, {"X-Halbach-Token": "tok"})
        return s, json.loads(raw)

    def test_ui_served_with_token_filled_in(self):
        s, raw = self.req("GET", "/")
        self.assertEqual(s, 200)
        self.assertIn(b'content="tok"', raw)
        self.assertNotIn(b"__TOKEN__", raw)

    def test_dashboard_site_and_area_tiles_are_wired(self):
        """Task 1.5: the two extra tiles exist and each request that fills them survives a failure."""
        s, raw = self.req("GET", "/")
        self.assertEqual(s, 200)
        for needle in (b'id="tSite"', b'id="tAreas"', b'data-act="go:site"', b'data-act="go:areas"'):
            self.assertIn(needle, raw)
        s, js = self.req("GET", "/app.js")
        self.assertEqual(s, 200)
        self.assertIn(b'api("/api/site").catch', js)      # a broken site must not break the dashboard
        self.assertIn(b'api("/api/areas").catch', js)

    def test_site_and_area_summaries_are_available(self):
        s, j = self.api("GET", "/api/site")
        self.assertEqual(s, 200)
        for key in ("mode", "exists", "model", "errors"):
            self.assertIn(key, j)
        self.assertIn("pages", j["model"])
        s, j = self.api("GET", "/api/areas")
        self.assertEqual(s, 200)
        self.assertIn("areas", j)
        self.assertIn("pending", j)

    def test_api_needs_token(self):
        self.assertEqual(self.req("GET", "/api/state")[0], 401)
        self.assertEqual(self.req("GET", "/api/state", headers={"X-Halbach-Token": "nope"})[0], 401)
        self.assertEqual(self.req("POST", "/api/modules", {}, {"X-Halbach-Token": ""})[0], 401)

    def test_wrong_host_is_refused(self):
        self.assertEqual(self.req("GET", "/", host="evil.example:80")[0], 403)
        self.assertEqual(self.req("GET", "/api/state", {}, {"X-Halbach-Token": "tok"}, host="evil.example")[0], 403)

    def test_foreign_origin_is_refused(self):
        s, _ = self.req("GET", "/api/state", headers={"X-Halbach-Token": "tok", "Origin": "https://evil.example"})
        self.assertEqual(s, 403)

    def test_unknown_paths_404_and_no_file_leak(self):
        self.assertEqual(self.req("GET", "/../store.py")[0], 404)
        self.assertEqual(self.req("GET", "/connecthub/store.py")[0], 404)

    def test_add_toggle_remove_roundtrip(self):
        s, m = self.api("POST", "/api/modules", {"kind": "static", "name": "Roundtrip", "source": str(self.site)})
        self.assertEqual(s, 201)
        s, m2 = self.api("POST", "/api/modules/%s/toggle" % m["id"], {"on": True})
        self.assertTrue(m2["on"])
        s, state = self.api("GET", "/api/state")
        mod = [x for x in state["modules"] if x["id"] == m["id"]][0]
        self.assertEqual(mod["pending"], "add")
        self.assertEqual(mod["file_count"], 1)
        self.assertNotIn("published_fp", mod)
        s, plan = self.api("GET", "/api/publish/plan")
        self.assertIn("a.txt", [f["path"] for f in plan["files"]])
        self.assertEqual(self.api("POST", "/api/modules/%s/remove" % m["id"], {})[0], 200)

    def test_validation_errors_are_400_with_message(self):
        s, j = self.api("POST", "/api/modules", {"kind": "live", "name": "Bad", "source": "example.com:80"})
        self.assertEqual(s, 400)
        self.assertIn("this PC", j["error"])
        s, j = self.api("POST", "/api/modules", {"kind": "nope", "name": "x", "source": "y"})
        self.assertEqual(s, 400)

    def test_bad_json_is_400(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("POST", "/api/modules", b"{nope", {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok",
                                                       "Content-Length": "5"})
        self.assertEqual(c.getresponse().status, 400)


class TestQuit(unittest.TestCase):
    def test_quit_stops_the_server(self):
        with tempfile.TemporaryDirectory() as t:
            srv, hub = server.serve(Path(t) / "hub", 0, token="tok")
            port = srv.server_address[1]
            th = server.run_in_thread(srv)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("POST", "/api/quit", b"{}", {"Host": "127.0.0.1:%d" % port, "X-Halbach-Token": "tok",
                                                    "Content-Type": "application/json", "Content-Length": "2"})
            self.assertEqual(c.getresponse().status, 200)
            th.join(timeout=5)
            self.assertFalse(th.is_alive())
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
