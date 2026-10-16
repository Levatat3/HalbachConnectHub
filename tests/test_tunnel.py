import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from halbach_connecthub import server, tunnel
from halbach_connecthub import store as st


class FakeProc:
    def __init__(self, text="2026-10-15 INF Registered tunnel connection connIndex=0\n"):
        self.stdout = io.StringIO(text)
        self.alive = True
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.alive = False


class TestTunnel(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.s = st.Store(self.tmp / "hub")
        self.fake = self.tmp / "cloudflared.exe"
        self.fake.write_text("x")

    def tearDown(self):
        self._t.cleanup()

    def test_refuses_without_binary_or_name(self):
        t = tunnel.Tunnel(self.s, lambda: 8782)
        with mock.patch.object(tunnel.shutil, "which", return_value=None):
            with self.assertRaises(st.ValidationError):
                t.start()
        self.s.set_settings(cloudflared_path=str(self.fake))
        with self.assertRaises(st.ValidationError):        # no tunnel name yet
            t.start()

    def test_command_and_connected_detection(self):
        self.s.set_settings(cloudflared_path=str(self.fake), tunnel_name="connecthub")
        seen = {}

        def popen(cmd, **kw):
            seen["cmd"] = cmd
            return FakeProc()
        t = tunnel.Tunnel(self.s, lambda: 8782, popen=popen)
        t.start()
        time.sleep(0.2)
        self.assertEqual(seen["cmd"][1:], ["tunnel", "--no-autoupdate", "run", "--url", "http://127.0.0.1:8782", "connecthub"])
        self.assertTrue(any("Tunnel connected" in r["msg"] for r in self.s.activity()))

    def test_stop_terminates(self):
        self.s.set_settings(cloudflared_path=str(self.fake), tunnel_name="connecthub")
        p = FakeProc(text="")
        t = tunnel.Tunnel(self.s, lambda: 1, popen=lambda *a, **k: p)
        t.start()
        t.stop()
        self.assertTrue(p.terminated)
        self.assertFalse(t.running())

    def test_no_double_start(self):
        self.s.set_settings(cloudflared_path=str(self.fake), tunnel_name="connecthub")
        t = tunnel.Tunnel(self.s, lambda: 1, popen=lambda *a, **k: FakeProc(text=""))
        t.start()
        t.proc.alive = True
        with self.assertRaises(st.ValidationError):
            t.start()

    def test_status_has_setup_steps_with_names_filled_in(self):
        self.s.set_settings(live_host="live.example.com", tunnel_name="mytun")
        steps = tunnel.Tunnel(self.s, lambda: 1).status()["setup"]
        self.assertTrue(any("create mytun" in x for x in steps))
        self.assertTrue(any("route dns mytun live.example.com" in x for x in steps))

    def test_settings_validation(self):
        with self.assertRaises(st.ValidationError):
            self.s.set_settings(tunnel_name="Bad Name")
        with self.assertRaises(st.ValidationError):
            self.s.set_settings(cloudflared_path=str(self.tmp / "nope.exe"))
        other = self.tmp / "notit.exe"
        other.write_text("x")
        with self.assertRaises(st.ValidationError):
            self.s.set_settings(cloudflared_path=str(other))


class Svc(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        b = json.dumps({"level": 3, "hp": 9, "chat": ["x"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


class TestControlApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import http.client
        cls.http = http.client
        cls._t = tempfile.TemporaryDirectory()
        cls.svc = ThreadingHTTPServer(("127.0.0.1", 0), Svc)
        threading.Thread(target=cls.svc.serve_forever, daemon=True).start()
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok", live_port=0)
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.hub.close()
        cls.svc.shutdown()
        cls.svc.server_close()
        cls._t.cleanup()

    def api(self, method, path, body=None):
        c = self.http.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok", "Content-Type": "application/json"}
        c.request(method, path, json.dumps(body).encode() if body is not None else None, h)
        r = c.getresponse()
        j = json.loads(r.read())
        c.close()
        return r.status, j

    def test_discover_adds_fields_off_and_locks_private(self):
        s, m = self.api("POST", "/api/modules", {"kind": "live", "name": "Disc", "source": "127.0.0.1:%d" % self.svc.server_address[1]})
        self.assertEqual(s, 201)
        s, m = self.api("POST", "/api/modules/%s/discover" % m["id"], {})
        self.assertEqual(s, 200)
        by = {f["name"]: f for f in m["fields"]}
        self.assertEqual(sorted(by), ["chat", "hp", "level"])
        self.assertTrue(by["chat"]["locked"])
        self.assertFalse(any(f["allowed"] for f in m["fields"]))

    def test_discover_keeps_existing_choices(self):
        _, m = self.api("POST", "/api/modules", {"kind": "live", "name": "Keep", "source": "127.0.0.1:%d" % self.svc.server_address[1]})
        self.api("POST", "/api/modules/%s/fields" % m["id"], {"fields": [{"name": "level", "allowed": True}]})
        _, m = self.api("POST", "/api/modules/%s/discover" % m["id"], {})
        self.assertTrue({f["name"]: f for f in m["fields"]}["level"]["allowed"])

    def test_discover_on_dead_service_is_400(self):
        _, m = self.api("POST", "/api/modules", {"kind": "live", "name": "Dead", "source": "127.0.0.1:9"})
        s, j = self.api("POST", "/api/modules/%s/discover" % m["id"], {})
        self.assertEqual(s, 400)
        self.assertIn("not answering", j["error"])

    def test_live_info(self):
        s, j = self.api("GET", "/api/live")
        self.assertEqual(s, 200)
        self.assertIsInstance(j["port"], int)
        self.assertFalse(j["tunnel"]["running"])

    def test_tunnel_start_without_cloudflared_is_a_clear_400(self):
        with mock.patch.object(tunnel.shutil, "which", return_value=None):
            s, j = self.api("POST", "/api/tunnel/start", {})
        self.assertEqual(s, 400)
        self.assertIn("cloudflared", j["error"])

    def test_end_to_end_live_through_hub(self):
        import http.client
        _, m = self.api("POST", "/api/modules", {"kind": "live", "name": "E2E", "source": "127.0.0.1:%d" % self.svc.server_address[1]})
        self.api("POST", "/api/modules/%s/discover" % m["id"], {})
        self.api("POST", "/api/modules/%s/fields" % m["id"], {"fields": [{"name": "level", "allowed": True}, {"name": "chat", "allowed": True}]})
        self.api("POST", "/api/modules/%s/toggle" % m["id"], {"on": True})
        hp = self.hub.live_srv.server_address[1]
        c = http.client.HTTPConnection("127.0.0.1", hp, timeout=10)
        c.request("GET", "/m/" + m["id"], headers={"Host": "127.0.0.1:%d" % hp})
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (200, {"level": 3}))


if __name__ == "__main__":
    unittest.main()
