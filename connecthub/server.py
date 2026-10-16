"""The local web server behind the ConnectHub window.

Binds to 127.0.0.1 only. Every /api call needs the per-launch token (so a web page you
happen to have open cannot drive the hub), and the Host header must be this server's own
address (so DNS-rebinding tricks fail). Stdlib only.
"""

import json
import mimetypes
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .live import UpstreamError, fetch_upstream, start_live_hub
from .publisher import Publisher
from .store import Store, ValidationError, collect_files, is_locked_field
from .tunnel import Tunnel

UI_DIR = Path(__file__).parent / "ui"
UI_FILES = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js"}
MAX_BODY = 64 * 1024


class Hub:
    """Server-side state shared by all requests."""

    def __init__(self, store, token=None, publisher=None):
        self.store = store
        self.token = token or secrets.token_urlsafe(24)
        self.publisher = publisher or Publisher(store)
        self.live_srv = None
        self.live_state = None
        self.tunnel = Tunnel(store, lambda: self.live_srv.server_address[1] if self.live_srv else 0)

    def start_live(self, port=0):
        try:
            self.live_srv, self.live_state = start_live_hub(self.store, port)
        except OSError:
            self.live_srv, self.live_state = start_live_hub(self.store, 0)
        return self.live_srv.server_address[1]

    def close(self):
        self.tunnel.stop()
        if self.live_srv:
            self.live_srv.shutdown()
            self.live_srv.server_close()


def make_handler(hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ConnectHub/" + __version__
        sys_version = ""

        def log_message(self, *a):  # quiet; activity goes to the activity log instead
            pass

        # ---- helpers ----
        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            port = self.server.server_address[1]
            return host in ("127.0.0.1:%d" % port, "localhost:%d" % port)

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValidationError("Request too large")
            if not n:
                return {}
            try:
                d = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                raise ValidationError("Bad JSON")
            if not isinstance(d, dict):
                raise ValidationError("Expected a JSON object")
            return d

        def _guard(self):
            if not self._host_ok():
                self._err(403, "Bad host")
                return False
            return True

        def _api_guard(self):
            if not self._guard():
                return False
            if not secrets.compare_digest(self.headers.get("X-ConnectHub-Token") or "", hub.token):
                self._err(401, "Missing or wrong token")
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in ("http://127.0.0.1:%d" % self.server.server_address[1],
                                          "http://localhost:%d" % self.server.server_address[1]):
                self._err(403, "Bad origin")
                return False
            return True

        # ---- routes ----
        def do_GET(self):
            path = urlparse(self.path).path
            if path in UI_FILES:
                if not self._guard():
                    return
                f = UI_DIR / UI_FILES[path]
                data = f.read_bytes()
                if path == "/":
                    data = data.replace(b"__TOKEN__", hub.token.encode()).replace(b"__VERSION__", __version__.encode())
                ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
                return self._send(200, data, ctype + "; charset=utf-8")
            if not path.startswith("/api/"):
                return self._err(404, "Not found")
            if not self._api_guard():
                return
            if path == "/api/state":
                return self._json(hub.store.view())
            if path == "/api/activity":
                return self._json({"rows": hub.store.activity()})
            if path == "/api/publish/plan":
                return self._json(self._plan())
            if path == "/api/publish/status":
                return self._json(hub.publisher.status())
            if path == "/api/live":
                return self._json({"port": hub.live_srv.server_address[1] if hub.live_srv else None,
                                   "stats": hub.live_state.stats() if hub.live_state else {},
                                   "tunnel": hub.tunnel.status()})
            return self._err(404, "Not found")

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/"):
                return self._err(404, "Not found")
            if not self._api_guard():
                return
            try:
                b = self._body()
                s = hub.store
                if path == "/api/modules":
                    m = s.add_module(b.get("kind"), b.get("name"), b.get("source"), b.get("desc", ""))
                    return self._json(m, 201)
                parts = path.split("/")  # ['', 'api', 'modules', id, action]
                if path in ("/api/tunnel/start", "/api/tunnel/stop"):
                    if path.endswith("start"):
                        hub.tunnel.start()
                    else:
                        hub.tunnel.stop()
                    return self._json(hub.tunnel.status())
                if len(parts) == 5 and parts[2] == "modules":
                    mid, act = parts[3], parts[4]
                    if act == "discover":
                        return self._json(self._discover(mid))
                    if act == "toggle":
                        return self._json(s.set_on(mid, b.get("on")))
                    if act == "fields":
                        return self._json(s.set_fields(mid, b.get("fields") or []))
                    if act == "cap":
                        return self._json(s.set_cap(mid, b.get("cap")))
                    if act == "remove":
                        s.remove(mid)
                        return self._json({"ok": True})
                if path == "/api/settings":
                    return self._json(s.set_settings(site_dir=b.get("site_dir"), project=b.get("project"),
                                                     domain=b.get("domain"), live_host=b.get("live_host"),
                                                     tunnel_name=b.get("tunnel_name"), extra_origins=b.get("extra_origins"),
                                                     cloudflared_path=b.get("cloudflared_path")))
                if path == "/api/publish":
                    hub.publisher.start(preview=bool(b.get("preview")))
                    return self._json(hub.publisher.status(), 202)
                if path == "/api/quit":
                    self._json({"ok": True})
                    s.log("info", "Quit requested from the window")
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if path == "/api/pick":
                    return self._json({"path": pick_path(b.get("kind"))})
                return self._err(404, "Not found")
            except ValidationError as e:
                return self._err(400, str(e))
            except (TypeError, ValueError):
                return self._err(400, "Bad request")

        def _discover(self, mid):
            """Ask the module's own service what fields it has; new ones are added switched off."""
            s = hub.store
            m = next((x for x in s.state["modules"] if x["id"] == mid and x["kind"] == "live"), None)
            if not m:
                raise ValidationError("No such live module")
            try:
                data = fetch_upstream(m["source"])
            except UpstreamError as e:
                raise ValidationError(str(e))
            if not isinstance(data, dict):
                raise ValidationError("The service must return a JSON object")
            fields = [{"name": f["name"], "allowed": f["allowed"]} for f in m.get("fields", [])]
            have = {f["name"] for f in fields}
            fields += [{"name": k, "allowed": False} for k in data if k not in have and len(str(k)) <= 60]
            return s.set_fields(mid, fields)

        def _plan(self):
            """What a publish would do right now."""
            s = hub.store
            changes = s.pending()
            files = []
            site_dir = (s.state.get("settings") or {}).get("site_dir")
            if site_dir and Path(site_dir).is_dir():
                fl, _ = collect_files(site_dir, ("readme.md",))
                files += [{"module": "site", "path": rel, "bytes": size} for _, rel, size in fl]
            for m in s.state["modules"]:
                if m["kind"] == "static" and m["on"]:
                    fl, _ = collect_files(m["source"])
                    files += [{"module": m["id"], "path": rel, "bytes": size} for _, rel, size in fl]
            ok, why = hub.publisher.ready()
            return {"changes": changes, "files": files, "deploy_ready": ok, "note": why}

    return Handler


def pick_path(kind):
    """Native file/folder dialog, run in a child process so tkinter stays off our threads."""
    fn = "askdirectory" if kind == "dir" else "askopenfilename"
    code = ("import tkinter as t, tkinter.filedialog as d\n"
            "r=t.Tk(); r.withdraw(); r.attributes('-topmost',True)\n"
            "print(d.%s() or '')" % fn)
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        raise ValidationError("Could not open the file picker; type the path instead")
    return out.stdout.strip()


def serve(home=None, port=0, token=None, publisher=None, live_port=None):
    """Start the server; returns (server, hub). Port 0 picks a free one."""
    store = Store(home)
    hub = Hub(store, token, publisher)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(hub))
    if live_port is not None:
        hub.start_live(live_port)
    return srv, hub


def run_in_thread(srv):
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t
