import http.client
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from halbach_connecthub import live
from halbach_connecthub import store as st


class Upstream:
    """A stand-in for a service on this PC."""

    def __init__(self):
        self.hits = 0
        self.body = json.dumps({"level": 14, "hp": 400, "chat": ["hi"], "notes": "private",
                                "pos": {"x": 1, "y": 2, "session_token": "abc"}}).encode()
        self.ctype = "application/json"
        self.status = 200
        self.redirect = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                outer.hits += 1
                if outer.redirect:
                    self.send_response(302)
                    self.send_header("Location", outer.redirect)
                    self.end_headers()
                    return
                self.send_response(outer.status)
                self.send_header("Content-Type", outer.ctype)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class TestFilter(unittest.TestCase):
    def test_only_allowed_fields_pass(self):
        fields = [{"name": "level", "allowed": True}, {"name": "hp", "allowed": False}]
        self.assertEqual(live.filter_payload({"level": 1, "hp": 2, "x": 3}, fields), {"level": 1})

    def test_locked_never_passes_even_if_marked_allowed(self):
        fields = [{"name": "chat", "allowed": True, "locked": False}, {"name": "level", "allowed": True}]
        self.assertEqual(live.filter_payload({"chat": ["x"], "level": 1}, fields), {"level": 1})

    def test_private_keys_removed_at_every_depth(self):
        fields = [{"name": "pos", "allowed": True}]
        out = live.filter_payload({"pos": {"x": 1, "token": "t", "deep": [{"password": "p", "ok": 1}]}}, fields)
        self.assertEqual(out, {"pos": {"x": 1, "deep": [{"ok": 1}]}})

    def test_non_object_is_refused(self):
        with self.assertRaises(live.UpstreamError):
            live.filter_payload([1, 2], [])

    def test_nothing_allowed_means_empty(self):
        self.assertEqual(live.filter_payload({"a": 1}, []), {})


class TestHub(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.store = st.Store(Path(self._t.name) / "hub")
        self.up = Upstream()
        self.store.set_settings(domain="example.com", live_host="live.example.com")
        m = self.store.add_module("live", "Overlay", "127.0.0.1:%d/data" % self.up.port)
        self.mid = m["id"]
        self.store.set_fields(self.mid, [{"name": "level", "allowed": True}, {"name": "hp", "allowed": True},
                                         {"name": "pos", "allowed": True}, {"name": "chat", "allowed": True}])
        self.store.set_on(self.mid, True)
        self.srv, self.state = live.start_live_hub(self.store, 0)
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.up.close()
        self._t.cleanup()

    def get(self, path, host=None, headers=None, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": host or "live.example.com"}
        h.update(headers or {})
        c.request(method, path, None, h)
        r = c.getresponse()
        raw = r.read()
        hdrs = dict(r.getheaders())
        c.close()
        try:
            return r.status, json.loads(raw) if raw else None, hdrs
        except ValueError:
            return r.status, raw, hdrs

    def test_status_lists_switched_on_modules(self):
        s, j, _ = self.get("/status")
        self.assertEqual((s, j["online"], j["modules"]), (200, True, [self.mid]))

    def test_serves_filtered_json(self):
        s, j, _ = self.get("/m/" + self.mid)
        self.assertEqual(s, 200)
        self.assertEqual(sorted(j), ["hp", "level", "pos"])
        self.assertNotIn("session_token", j["pos"])
        self.assertNotIn("chat", j)

    def test_off_or_unknown_module_is_404(self):
        self.store.set_on(self.mid, False)
        self.assertEqual(self.get("/m/" + self.mid)[0], 404)
        self.assertEqual(self.get("/m/nope")[0], 404)
        self.assertEqual(self.get("/status")[1]["modules"], [])

    def test_wrong_host_refused(self):
        self.assertEqual(self.get("/status", host="evil.example")[0], 403)
        self.assertEqual(self.get("/m/" + self.mid, host="evil.example")[0], 403)

    def test_local_host_allowed(self):
        self.assertEqual(self.get("/status", host="127.0.0.1:%d" % self.port)[0], 200)

    def test_only_get(self):
        for m in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.get("/m/" + self.mid, method=m)[0], 405)

    def test_odd_paths_rejected(self):
        for p in ("/m/", "/m/../x", "/m/%s/extra" % self.mid, "/", "/api/state"):
            self.assertEqual(self.get(p)[0], 404, p)

    def test_cors_only_for_own_origins(self):
        _, _, h = self.get("/status", headers={"Origin": "https://example.com"})
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "https://example.com")
        _, _, h = self.get("/status", headers={"Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", h)
        s, _, h = self.get("/status", headers={"Origin": "https://www.example.com"}, method="OPTIONS")
        self.assertEqual((s, h.get("Access-Control-Allow-Origin")), (204, "https://www.example.com"))

    def test_extra_origins_are_allowed_and_validated(self):
        self.store.set_settings(extra_origins="https://preview.example.pages.dev/, http://localhost:8795")
        _, _, h = self.get("/status", headers={"Origin": "https://preview.example.pages.dev"})
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "https://preview.example.pages.dev")
        _, _, h = self.get("/status", headers={"Origin": "http://localhost:8795"})
        self.assertEqual(h.get("Access-Control-Allow-Origin"), "http://localhost:8795")
        for bad in ("javascript:alert(1)", "https://x.com/path", "*", "ftp://x.com"):
            with self.assertRaises(st.ValidationError, msg=bad):
                self.store.set_settings(extra_origins=bad)

    def test_upstream_cached_for_a_second(self):
        for _ in range(5):
            self.get("/m/" + self.mid)
        self.assertEqual(self.up.hits, 1)

    def test_viewer_cap(self):
        self.store.set_cap(self.mid, 1)
        ok = self.get("/m/" + self.mid, headers={"User-Agent": "a", "CF-Connecting-IP": "1.1.1.1"})[0]
        again = self.get("/m/" + self.mid, headers={"User-Agent": "a", "CF-Connecting-IP": "1.1.1.1"})[0]
        other = self.get("/m/" + self.mid, headers={"User-Agent": "b", "CF-Connecting-IP": "2.2.2.2"})[0]
        self.assertEqual((ok, again, other), (200, 200, 503))
        self.assertEqual(self.state.stats()[self.mid]["viewers"], 1)

    def test_upstream_problems_are_502_with_no_leak(self):
        self.up.ctype = "text/html"
        s, j, _ = self.get("/m/" + self.mid)
        self.assertEqual(s, 502)
        self.assertNotIn("127.0.0.1", json.dumps(j))
        self.state.cache.clear()
        self.up.ctype = "application/json"
        self.up.body = b"{not json"
        self.assertEqual(self.get("/m/" + self.mid)[0], 502)
        self.state.cache.clear()
        self.up.body = b"[1,2]"
        self.assertEqual(self.get("/m/" + self.mid)[0], 502)

    def test_oversize_response_refused(self):
        self.up.body = b'{"level":"' + b"x" * (live.MAX_UPSTREAM_BYTES + 10) + b'"}'
        self.assertEqual(self.get("/m/" + self.mid)[0], 502)

    def test_redirects_are_not_followed(self):
        self.up.redirect = "http://example.com/"
        self.assertEqual(self.get("/m/" + self.mid)[0], 502)

    def test_service_down_is_502(self):
        self.up.close()
        self.assertEqual(self.get("/m/" + self.mid)[0], 502)
        self.up = Upstream()  # so tearDown can close something


if __name__ == "__main__":
    unittest.main()
