"""The local web server behind the Halbach ConnectHub window.

Binds to 127.0.0.1 only. Every /api call needs the per-launch token (so a web page you
happen to have open cannot drive the hub), and the Host header must be this server's own
address (so DNS-rebinding tricks fail). Stdlib only.
"""

import json
import re
import mimetypes
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from . import __version__
from .live import UpstreamError, fetch_upstream, field_names, start_live_hub
from .net import LoopbackServer
from . import areaclient, areamode, funkthemes, sitebuilder
from .publisher import Publisher, classify
from .sitemodel import ModelError
from .areas import AreaService
from .autopublish import AutoPublisher
from .siteservice import SiteService
from .store import Store, ValidationError, collect_files, is_locked_field
from .tunnel import Tunnel

UI_DIR = Path(__file__).parent / "ui"
FUNK_ENGINE = Path(__file__).parent / "builtin" / "themes" / "funk" / "funktheme.js"     # Funk's engine, also used by the window
UI_FILES = {"/": "index.html", "/style.css": "style.css", "/app.js": "app.js", "/site.js": "site.js", "/areas.js": "areas.js"}
MAX_BODY = 256 * 1024
PREVIEW_CHARS = 4000

# In the collaborator's area window only these routes exist. Everything else answers 404 exactly like it would for a
# stranger, so a restricted window can never reach the owner's modules, publish, tunnel, settings or areas.
AREA_FILES = {"/": "area.html", "/style.css": "style.css", "/area.js": "area.js"}
AREA_GET = {"/api/site", "/api/area/limits", "/api/area/status"}
AREA_POST = {"/api/site/preview", "/api/area/send"}
# The only site edits a collaborator may make. Everything else SiteService.apply handles (import, settings, theme,
# brand, mode switch, linked sites, restore, page add/delete/duplicate/move) stays 404 in the restricted window.
AREA_SITE_ACTIONS = {"page.update", "block.add", "block.update", "block.delete", "block.move"}


class Hub:
    """Server-side state shared by all requests."""

    def __init__(self, store, token=None, publisher=None, area=False, area_folder=None):
        self.store = store
        self.token = token or secrets.token_urlsafe(24)
        self.area = area                                       # collaborator's restricted window: only area routes work
        self.area_folder = Path(area_folder) if area_folder else None
        self.site = SiteService(store)
        if area and self.area_folder:
            # The collaborator edits one page kept as area.json + files/ in their own folder.
            areamode.load_area(self.area_folder, self.site.model)
            self.store.state["settings"]["site_dir"] = str(self.area_folder)   # so images in files/ preview correctly
        self.areas = AreaService(store, self.site.registry,
                                 slug_taken=lambda s: any(p["slug"] == s for p in self.site.model.data["pages"]))
        self.site.areas = self.areas
        self.publisher = publisher or Publisher(store, site=self.site)
        if getattr(self.publisher, "site", None) is None:
            self.publisher.site = self.site
        self.autopublish = None
        if not area:                                           # an area window never publishes by itself
            self.autopublish = AutoPublisher(store, self.areas, self.publisher)
            self.areas.on_auto_publish = self.autopublish.schedule
            self.autopublish.arm()                             # picks up anything a crash left waiting
        self.live_srv = None
        self.live_state = None
        self.tunnel = Tunnel(store, lambda: self.live_srv.server_address[1] if self.live_srv else 0)

    def area_client(self):
        """The collaborator's stored connection, or a clear error when not connected."""
        conn = areaclient.load_connection()
        if not (conn.get("url") and conn.get("area") and conn.get("key")):
            raise ValidationError("Not connected yet. Run: halbach area connect --url https://live.example.com --area NAME")
        return areaclient.Area(conn["url"], conn["area"], conn["key"])

    def send_area(self):
        """Write the edited page back to area.json, zip it and send it through the drop endpoint."""
        areamode.dump_area(self.site.model, self.area_folder)
        client = self.area_client()
        lim = client.limits()
        if lim.get("paused"):
            raise ValidationError("The area is paused by its owner; nothing can be sent right now.")
        tmp = self.area_folder / ".halbach-bundle.zip"
        try:
            areaclient.build_zip(self.area_folder, tmp, lim.get("limits"))
            code, body = client.send_zip(tmp)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        if code in (200, 201):
            return body
        if code == 422:
            raise ValidationError("The bundle was refused: " + "; ".join(body.get("reasons") or ["unknown reason"]))
        raise ValidationError(body.get("error") or ("The site answered %d." % code))

    def start_inbox_watcher(self, interval=3.0):
        """Import new drop-off folders as switched-off modules, now and every few seconds."""
        self._inbox_stop = threading.Event()

        def run():
            while not self._inbox_stop.is_set():
                try:
                    self.store.scan_inbox()
                except Exception as e:  # never let a bad folder kill the watcher
                    self.store.log("warn", "Inbox scan failed: %s" % e)
                self._inbox_stop.wait(interval)
        threading.Thread(target=run, daemon=True).start()

    def start_live(self, port=0):
        try:
            self.live_srv, self.live_state = start_live_hub(self.store, port)
        except OSError:
            self.live_srv, self.live_state = start_live_hub(self.store, 0)
        self.live_state.areas = self.areas
        return self.live_srv.server_address[1]

    def close(self):
        if getattr(self, "_inbox_stop", None):
            self._inbox_stop.set()
        if self.autopublish:
            self.autopublish.close()
        self.tunnel.stop()
        if self.live_srv:
            self.live_srv.shutdown()
            self.live_srv.server_close()


def make_handler(hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HalbachConnectHub/" + __version__
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
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
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
            if not secrets.compare_digest(self.headers.get("X-Halbach-Token") or "", hub.token):
                self._err(401, "Missing or wrong token")
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in ("http://127.0.0.1:%d" % self.server.server_address[1],
                                          "http://localhost:%d" % self.server.server_address[1]):
                self._err(403, "Bad origin")
                return False
            return True

        def _area_allowed(self, method, path):
            """In area mode only the collaborator's own routes exist; every owner route 404s."""
            if not hub.area:
                return True
            if path in AREA_FILES or path == "/funktheme.js" or path.startswith("/preview"):
                return True
            if method == "GET":
                return path in AREA_GET
            if method == "POST":
                if path in AREA_POST:
                    return True
                if path.startswith("/api/site/"):
                    return path[len("/api/site/"):] in AREA_SITE_ACTIONS
                return False
            return False

        def _area_json(self, fn):
            """Run an area-mode operation and turn its errors into plain answers (never a traceback)."""
            try:
                return self._json(fn())
            except ValidationError as e:
                return self._err(400, str(e))
            except areaclient.ClientError as e:
                return self._err(502, str(e))

        # ---- routes ----
        def do_GET(self):
            path = urlparse(self.path).path
            if not self._area_allowed("GET", path):
                return self._err(404, "Not found")
            if path == "/funktheme.js":
                if not self._guard():
                    return
                return self._send(200, FUNK_ENGINE.read_bytes(), "text/javascript; charset=utf-8")
            files = AREA_FILES if hub.area else UI_FILES
            if path in files:
                if not self._guard():
                    return
                f = UI_DIR / files[path]
                data = f.read_bytes()
                if path == "/":
                    data = data.replace(b"__TOKEN__", hub.token.encode()).replace(b"__VERSION__", __version__.encode())
                ctype = mimetypes.guess_type(f.name)[0] or "text/plain"
                return self._send(200, data, ctype + "; charset=utf-8")
            if path == "/preview" or path.startswith("/preview/"):
                return self._serve_preview(path)
            if not path.startswith("/api/"):
                return self._err(404, "Not found")
            if not self._api_guard():
                return
            if path == "/api/funkthemes":
                return self._json({"themes": funkthemes.library(), "dir": funkthemes.DIR})
            if path == "/api/windowtheme":
                return self._json({"theme": hub.store.window_theme()})
            if path == "/api/areas":
                lh = (hub.store.state.get("settings") or {}).get("live_host")
                return self._json({"areas": hub.areas.list_view(), "pending": hub.areas.pending_count(),
                                   "drop_url": ("https://%s/drop/" % lh) if lh else None,
                                   "auto_publish_enabled": bool((hub.store.state.get("settings") or {}).get("auto_publish_enabled"))})
            m_audit = re.match(r"^/api/areas/([a-z0-9-]+)/audit$", path)
            if m_audit:
                if not hub.areas.get(m_audit.group(1)):
                    return self._err(404, "No such area")
                return self._json({"rows": hub.areas.audit_log(m_audit.group(1))})
            if path == "/api/site":
                return self._json(hub.site.view())
            if path == "/api/area/limits":
                return self._area_json(lambda: hub.area_client().limits())
            if path == "/api/area/status":
                return self._area_json(lambda: hub.area_client().status())
            if path == "/api/site/export":
                data = json.dumps(hub.site.export_package(), indent=1).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition", 'attachment; filename="halbach-site.json"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/state":
                return self._json(hub.store.view())
            if path == "/api/activity":
                return self._json({"rows": hub.store.activity()})
            if path == "/api/publish/plan":
                return self._json(self._plan())
            m = re.match(r"^/api/modules/([a-z0-9-]+)/preview$", path)
            if m:
                try:
                    return self._json(self._preview(m.group(1)))
                except ValidationError as e:
                    return self._err(404, str(e))
            if path == "/api/publish/status":
                return self._json(hub.publisher.status())
            if path == "/api/live":
                return self._json({"port": hub.live_srv.server_address[1] if hub.live_srv else None,
                                   "stats": hub.live_state.stats() if hub.live_state else {},
                                   "tunnel": hub.tunnel.status()})
            return self._err(404, "Not found")

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._area_allowed("POST", path):
                return self._err(404, "Not found")
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
                    if act == "options":
                        return self._json(s.set_options(mid, view=b.get("view"), hide=b.get("hide"),
                                                        poll=b.get("poll"), listed=b.get("listed")))
                    if act == "ack":
                        return self._json(s.ack(mid))
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
                if path == "/api/windowtheme":
                    return self._json({"theme": s.set_window_theme(b.get("theme"))})
                if path == "/api/funkthemes/save":
                    try:
                        funkthemes.save(b.get("theme"))
                    except (ValueError, OSError) as e:
                        raise ValidationError(str(e))
                    return self._json({"themes": funkthemes.library()})
                if path == "/api/funkthemes/delete":
                    try:
                        funkthemes.delete(b.get("name"))
                    except OSError as e:
                        raise ValidationError(str(e))
                    return self._json({"themes": funkthemes.library()})
                if path == "/api/areas/create":
                    view, key = hub.areas.create(b.get("name"), slug=b.get("slug"), trust=b.get("trust", "review"), nav=b.get("nav", True),
                                                 limits=b.get("limits"), blocks=b.get("blocks"))
                    return self._json({"area": view, "key": key}, 201)
                m_area = re.match(r"^/api/areas/([a-z0-9-]+)/([a-z]+)$", path)
                if m_area:
                    aid, act = m_area.groups()
                    ar = hub.areas
                    if act == "update":
                        return self._json({"area": ar.update(aid, **{k: b[k] for k in ("name", "enabled", "paused", "trust", "nav", "limits", "blocks", "embed_hosts") if k in b})})
                    if act == "regenerate":
                        key = ar.regenerate_key(aid)
                        return self._json({"key": key})
                    if act == "revoke":
                        ar.revoke_key(aid)
                        return self._json({"ok": True})
                    if act == "delete":
                        ar.delete(aid)
                        return self._json({"ok": True})
                    if act == "approve":
                        return self._json({"submission": ar.approve(aid, b.get("sid"), note=b.get("note", ""))})
                    if act == "reject":
                        return self._json({"submission": ar.reject(aid, b.get("sid"), note=b.get("note", ""))})
                    if act == "rollback":
                        ar.rollback(aid, b.get("version"))
                        return self._json({"ok": True})
                    if act == "freeze":
                        return self._json({"area": ar.freeze(aid)})
                    return self._err(404, "Not found")
                if path == "/api/site/preview":
                    res = hub.site.build_preview(area_preview=(b["area"], b["sid"]) if b.get("area") and b.get("sid") else None)
                    return self._json(res.summary())
                if path == "/api/area/send":
                    return self._area_json(hub.send_area)
                if path.startswith("/api/site/"):
                    return self._json(hub.site.apply(path[len("/api/site/"):], b))
                if path == "/api/settings":
                    return self._json(s.set_settings(site_dir=b.get("site_dir"), project=b.get("project"),
                                                     domain=b.get("domain"), live_host=b.get("live_host"),
                                                     tunnel_name=b.get("tunnel_name"), extra_origins=b.get("extra_origins"),
                                                     cloudflared_path=b.get("cloudflared_path"), site_mode=b.get("site_mode"),
                                                     auto_publish_enabled=b.get("auto_publish_enabled")))
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
            except ModelError as e:
                return self._json({"error": str(e), "errors": e.errors}, 400)
            except (TypeError, ValueError):
                return self._err(400, "Bad request")

        def _serve_preview(self, path):
            """The modular site as built by Preview, served with the same headers Cloudflare will send (but never cached)."""
            if not self._guard():
                return
            if path == "/preview":
                self.send_response(308)
                self.send_header("Location", "/preview/")
                self.end_headers()
                return
            root = hub.site.preview_dir
            rel = path[len("/preview"):]
            if "\\" in rel or ".." in rel.split("/") or chr(0) in rel:
                return self._err(400, "Bad path")
            target = (root / rel.lstrip("/")).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                return self._err(400, "Bad path")
            status = 200
            if target.is_dir():
                if not rel.endswith("/"):
                    self.send_response(308)
                    self.send_header("Location", "/preview" + rel + "/")
                    self.end_headers()
                    return
                target = target / "index.html"
            if not target.is_file():
                target, status = root / "404.html", 404
                if not target.is_file():
                    return self._err(404, "There is no preview yet. Press Preview first.")
            data = target.read_bytes()
            ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
                     ".json": "application/json", ".wasm": "application/wasm", ".md": "text/markdown",
                     ".txt": "text/plain"}.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            try:
                rules = sitebuilder.parse_headers((root / "_headers").read_text(encoding="utf-8"))
            except OSError:
                rules = []
            hdrs = sitebuilder.headers_for(rules, rel if rel.startswith("/") else "/" + rel)
            self.send_response(status)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/json")) else ""))
            self.send_header("Content-Length", str(len(data)))
            for k, v in hdrs.items():
                if k.lower() not in ("cache-control", "content-length", "content-type"):
                    self.send_header(k, v)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _preview(self, mid):
            """Files in a static module and a short text preview, so a submission can be reviewed."""
            m = next((x for x in hub.store.state["modules"] if x["id"] == mid and x["kind"] == "static"), None)
            if not m:
                raise ValidationError("No such static module")
            files, problems = collect_files(m["source"])
            mtype, entry = classify(files) if files else ("file", "")
            text = None
            if entry and Path(entry).suffix.lower() in (".md", ".markdown", ".txt", ".html", ".htm", ".json", ".csv"):
                target = next((f[0] for f in files if f[1] == entry), None)
                if target:
                    try:
                        with open(target, "r", encoding="utf-8", errors="replace") as fh:
                            text = fh.read(PREVIEW_CHARS)
                    except OSError:
                        text = None
            return {"type": mtype, "entry": entry, "text": text,
                    "files": [{"path": rel, "bytes": size} for _, rel, size in files[:200]],
                    "more": max(0, len(files) - 200), "problems": problems}

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
            try:
                names = field_names(data)
            except UpstreamError as e:
                raise ValidationError(str(e))
            fields = [{"name": f["name"], "allowed": f["allowed"]} for f in m.get("fields", [])]
            have = {f["name"] for f in fields}
            fields += [{"name": k, "allowed": False} for k in names if k not in have and len(str(k)) <= 60]
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
            modular = (s.state.get("settings") or {}).get("site_mode") == "modular"
            if modular:
                stage = s.home / "plan-stage"
                res = hub.site.build(stage)
                files = []
                if res.ok:
                    for f in sorted(stage.rglob("*")):
                        if f.is_file() and len(files) < 400:
                            files.append({"module": "", "path": f.relative_to(stage).as_posix(), "bytes": f.stat().st_size})
            return {"changes": changes, "files": files, "deploy_ready": ok, "note": why, "modular": modular,
                    "site_changes": hub.site.plan() if modular else [],
                    "site_problems": ((res.errors if not res.ok else []) if modular else [])}

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


def serve(home=None, port=0, token=None, publisher=None, live_port=None, inbox=False, area=False, area_folder=None):
    """Start the server; returns (server, hub). Port 0 picks a free one.

    With area=True the window is a collaborator's restricted editor: no live hub, no tunnel, no publisher,
    and only the area routes (AREA_UI_FILES / AREA_GET / AREA_POST) are served.
    """
    store = Store(home)
    hub = Hub(store, token, publisher, area=area, area_folder=area_folder)
    srv = LoopbackServer(("127.0.0.1", port), make_handler(hub))
    if not area and live_port is not None:
        hub.start_live(live_port)
    if not area and inbox:
        hub.start_inbox_watcher()
    return srv, hub


def run_in_thread(srv):
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return t
