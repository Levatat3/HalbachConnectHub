"""The collaborator's area window: only its own routes exist; every owner route 404s (Task 2.2)."""
import http.client
import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import server


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


if __name__ == "__main__":
    unittest.main()
