"""The live hub: the only thing a tunnel points at.

Serves GET /status and GET /m/<module-id>. A module is served only while it is switched on;
its service is called on 127.0.0.1, its JSON is cut down to the allowed fields (and any
private-looking key is removed at every depth), and the result is cached for a second so a
crowd of viewers makes one request a second to the service. Stdlib only.
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from .net import LoopbackServer
from .store import is_locked_field

MAX_UPSTREAM_BYTES = 256 * 1024
MAX_ITEMS = 100             # most items passed on from a list
CACHE_SECONDS = 1.0
VIEWER_WINDOW = 12.0        # a viewer counts while seen within this many seconds
UPSTREAM_TIMEOUT = 4
ID_PATH = re.compile(r"^/m/([a-z0-9][a-z0-9-]{0,62})/?$")


class UpstreamError(Exception):
    """The module's own service did not give usable JSON."""


def strip_private(value):
    """Remove any key that looks private, at every depth."""
    if isinstance(value, dict):
        return {k: strip_private(v) for k, v in value.items() if not is_locked_field(str(k))}
    if isinstance(value, list):
        return [strip_private(v) for v in value[:500]]
    return value


def filter_payload(obj, fields):
    """Keep only allowed, non-locked fields. fields: the module's field list.

    The service may return one object, or a list of objects (a list of bots, say); the same
    allow-list is applied to every item, and at most MAX_ITEMS items are passed on.
    """
    allowed = {f["name"] for f in fields if f.get("allowed") and not f.get("locked") and not is_locked_field(f["name"])}

    def cut(item):
        return {k: strip_private(v) for k, v in item.items() if k in allowed}

    if isinstance(obj, dict):
        return cut(obj)
    if isinstance(obj, list):
        if not all(isinstance(i, dict) for i in obj):
            raise UpstreamError("The service must return a JSON object or a list of objects")
        return [cut(i) for i in obj[:MAX_ITEMS]]
    raise UpstreamError("The service must return a JSON object or a list of objects")


def field_names(obj):
    """Names to offer in 'Discover fields': the keys of an object, or the union over a list's items."""
    if isinstance(obj, dict):
        return list(obj)
    if isinstance(obj, list):
        if not obj:
            raise UpstreamError("The service returned an empty list, so there is nothing to discover yet")
        names = []
        for item in obj[:20]:
            if not isinstance(item, dict):
                raise UpstreamError("The service must return a JSON object or a list of objects")
            names += [k for k in item if k not in names]
        return names
    raise UpstreamError("The service must return a JSON object or a list of objects")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A service on this PC must answer directly; never follow it to another address."""

    def redirect_request(self, *a, **k):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch_upstream(source, timeout=UPSTREAM_TIMEOUT):
    """GET http://<source> (127.0.0.1 only, validated at add time) and parse JSON."""
    url = "http://" + source
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "HalbachConnectHub"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "json" not in ctype:
                raise UpstreamError("The service did not return JSON")
            raw = r.read(MAX_UPSTREAM_BYTES + 1)
    except UpstreamError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpstreamError("The service is not answering")
    if len(raw) > MAX_UPSTREAM_BYTES:
        raise UpstreamError("The service's response is too large")
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise UpstreamError("The service returned invalid JSON")


class LiveState:
    """Caches, viewer counts and stats, shared by all hub requests."""

    def __init__(self, store, fetcher=None, clock=time.time):
        self.store = store
        self.fetcher = fetcher or fetch_upstream
        self.clock = clock
        self.lock = threading.Lock()
        self.cache = {}      # module id -> (time, filtered payload)
        self.viewers = {}    # module id -> {viewer key -> last seen}
        self.requests = {}   # module id -> count

    def module(self, mid):
        for m in self.store.state["modules"]:
            if m["id"] == mid and m["kind"] == "live" and m["on"]:
                return m
        return None

    def active_ids(self):
        return [m["id"] for m in self.store.state["modules"] if m["kind"] == "live" and m["on"]]

    def admit(self, m, key):
        """True if this viewer may be served (under the cap, or already counted)."""
        now = self.clock()
        with self.lock:
            seen = self.viewers.setdefault(m["id"], {})
            for k in [k for k, t in seen.items() if now - t > VIEWER_WINDOW]:
                del seen[k]
            if key not in seen and len(seen) >= m.get("viewer_cap", 40):
                return False
            seen[key] = now
            self.requests[m["id"]] = self.requests.get(m["id"], 0) + 1
            return True

    def payload(self, m):
        now = self.clock()
        with self.lock:
            hit = self.cache.get(m["id"])
            if hit and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        data = filter_payload(self.fetcher(m["source"]), m.get("fields") or [])
        with self.lock:
            self.cache[m["id"]] = (now, data)
        return data

    def stats(self):
        now = self.clock()
        with self.lock:
            return {mid: {"viewers": sum(1 for t in s.values() if now - t <= VIEWER_WINDOW),
                          "requests": self.requests.get(mid, 0)} for mid, s in self.viewers.items()}


def allowed_origins(settings, port):
    out = {"http://127.0.0.1:%d" % port, "http://localhost:%d" % port}
    d = (settings or {}).get("domain")
    if d:
        out |= {"https://" + d, "https://www." + d}
    out |= {x.strip() for x in ((settings or {}).get("extra_origins") or "").split(",") if x.strip()}
    return out


def make_hub_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HalbachConnectHubLive"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _host_ok(self):
            host = (self.headers.get("Host") or "").lower()
            port = self.server.server_address[1]
            ok = {"127.0.0.1:%d" % port, "localhost:%d" % port}
            lh = (state.store.state.get("settings") or {}).get("live_host")
            if lh:
                ok.add(lh)
            return host in ok

        def _cors(self):
            origin = self.headers.get("Origin")
            if origin and origin in allowed_origins(state.store.state.get("settings"), self.server.server_address[1]):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")

        def _json(self, obj, code=200):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            path = urlparse(self.path).path
            if path == "/status":
                return self._json({"online": True, "modules": state.active_ids()})
            m = ID_PATH.match(path)
            if not m:
                return self._json({"error": "not found"}, 404)
            mod = state.module(m.group(1))
            if not mod:
                return self._json({"error": "not found"}, 404)
            fwd = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For") or self.client_address[0]
            key = hashlib.sha1((fwd.split(",")[0].strip() + (self.headers.get("User-Agent") or "")).encode()).hexdigest()[:16]
            if not state.admit(mod, key):
                return self._json({"error": "full"}, 503)
            try:
                return self._json(state.payload(mod))
            except UpstreamError as e:
                return self._json({"error": str(e)}, 502)

        def do_POST(self):
            self._json({"error": "read only"}, 405)

        do_PUT = do_DELETE = do_PATCH = do_POST

    return Handler


def start_live_hub(store, port=0, fetcher=None):
    """Start the hub server on 127.0.0.1. Returns (server, state)."""
    state = LiveState(store, fetcher)
    srv = LoopbackServer(("127.0.0.1", port), make_hub_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state
