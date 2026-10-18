"""Build the site plus switched-on static modules, write manifest.json, deploy, verify.

The deploy step shells out to `wrangler pages deploy` using the user's own login; Halbach ConnectHub
never sees a credential. Stdlib only.
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .store import MAX_FILES, TAB_VIEWS, ValidationError, collect_files

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
SITE_EXTRA_EXCLUDE = ("readme.md",)       # the site folder's own notes are never public
RESERVED = ("manifest.json",)             # written by the builder
RESERVED_DIR = "modules"
URL_RE = re.compile(r"https://[a-z0-9.-]+\.pages\.dev")


class PublishError(Exception):
    """A publish failed; the message is safe and useful to show the user."""


def classify(files):
    """Decide how the site should show a module. files: [(abs, rel, size)] -> (type, entry_rel)."""
    rels = [f[1] for f in files]
    lows = [r.lower() for r in rels]
    if len(rels) == 1:
        ext = os.path.splitext(lows[0])[1]
        if ext == ".swf":
            return "game", rels[0]
        if ext in (".html", ".htm"):
            return "page", rels[0]
        if ext in IMAGE_EXT:
            return "image", rels[0]
        if ext in (".md", ".markdown"):
            return "doc", rels[0]
        return "file", rels[0]
    if "index.html" in lows:
        return "page", rels[lows.index("index.html")]
    if "index.md" in lows:
        return "doc", rels[lows.index("index.md")]
    swfs = [r for r, l in zip(rels, lows) if l.endswith(".swf")]
    if len(swfs) == 1:
        return "game", swfs[0]
    imgs = [r for r, l in zip(rels, lows) if os.path.splitext(l)[1] in IMAGE_EXT]
    if imgs and len(imgs) * 10 >= len(rels) * 6:
        return "gallery", ""
    return "file", rels[0]


ASSET_REF = re.compile(
    r"""(<(?:script|link)\b[^>]*?\b(?:src|href)\s*=\s*)(["'])([^"'#?]+?\.(?:js|css))(?:\?[^"']*)?(\2)""",
    re.IGNORECASE,
)


def hash_assets(stage):
    """Put a content hash in the file name of every script and stylesheet an HTML page loads.

    Hosts and browsers keep .js and .css files for hours (Cloudflare's Browser Cache TTL even overrides
    the site's own headers), so a changed script at the same address can be served stale to anyone who
    visited recently. A changed file gets a new name, so nothing can be stale. Only pages in the
    site itself are rewritten; the originals are removed unless some other file still mentions them.
    Returns {original relative path: hashed relative path}.
    """
    stage = Path(stage).resolve()
    renamed = {}
    pages = [p for p in stage.rglob("*") if p.is_file() and p.suffix.lower() in (".html", ".htm")]
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")

        def repl(m):
            ref = m.group(3)
            if re.match(r"^(https?:)?//", ref, re.IGNORECASE):
                return m.group(0)                                  # someone else's file
            target = stage / ref.lstrip("/") if ref.startswith("/") else page.parent / ref
            try:
                target = target.resolve()
                target.relative_to(stage)
            except (OSError, ValueError):
                return m.group(0)                                  # outside the site: leave alone
            if not target.is_file():
                return m.group(0)
            if target not in renamed:
                digest = hashlib.sha1(target.read_bytes()).hexdigest()[:10]
                renamed[target] = target.with_name("%s.%s%s" % (target.stem, digest, target.suffix))
                shutil.copy2(target, renamed[target])
            return m.group(1) + m.group(2) + ref[:len(ref) - len(target.name)] + renamed[target].name + m.group(4)

        new_text = ASSET_REF.sub(repl, text)
        if new_text != text:
            page.write_text(new_text, encoding="utf-8", newline="\n")

    others = [p for p in stage.rglob("*") if p.is_file() and p.suffix.lower() in (".js", ".css", ".html", ".htm", ".json")]
    for orig, hashed in renamed.items():
        still_named = False
        for p in others:
            if p in (orig, hashed) or p in renamed.values() or not p.exists():
                continue
            try:
                if orig.name in p.read_text(encoding="utf-8", errors="replace"):
                    still_named = True
                    break
            except OSError:
                pass
        if not still_named:
            orig.unlink()
    return {str(o.relative_to(stage)).replace(os.sep, "/"): str(h.relative_to(stage)).replace(os.sep, "/")
            for o, h in renamed.items()}


def stage_modules(store, stage):
    """Copy every switched-on module into stage/modules and describe it. Returns (manifest modules, files copied)."""
    st = store.state
    stage = Path(stage)
    total = 0
    manifest_mods = []
    for m in st["modules"]:
        if not m["on"]:
            continue
        if m["kind"] == "live":
            item = {"id": m["id"], "name": m["name"], "desc": m.get("desc", ""), "kind": "live"}
            view = m.get("view") or "auto"
            if view != "auto":
                item["view"] = view
                if view in TAB_VIEWS:
                    item["tab"] = {"id": view, "label": TAB_VIEWS[view]}      # the site adds a nav tab for it
            if m.get("poll"):
                item["poll"] = int(m["poll"])
            manifest_mods.append(item)
            continue
        files, probs = collect_files(m["source"])
        if not files:
            raise PublishError("'%s' has nothing publishable%s" % (m["name"], (": " + probs[0]) if probs else ""))
        if probs:
            raise PublishError("'%s': %s" % (m["name"], probs[0]))
        mtype, entry = classify(files)
        base = "modules/%s/" % m["id"]
        for src, rel, _ in files:
            dest = stage / "modules" / m["id"] / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            total += 1
        item = {"id": m["id"], "name": m["name"], "desc": m.get("desc", ""), "kind": "static", "type": mtype,
                "entry": base + entry if entry else base}
        if m.get("listed") is False:
            item["listed"] = False                                        # assets only (e.g. sprites): no card
        if mtype == "gallery":
            item["files"] = [base + rel for _, rel, _ in files if os.path.splitext(rel.lower())[1] in IMAGE_EXT]
        manifest_mods.append(item)
    return manifest_mods, total


def build_stage(store, stage_dir):
    """Create stage_dir from the site folder and enabled static modules. Returns the manifest."""
    st = store.state
    site_dir = (st.get("settings") or {}).get("site_dir")
    if not site_dir or not Path(site_dir).is_dir():
        raise PublishError("Set the site folder in Settings first.")
    stage = Path(stage_dir)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    site_files, problems = collect_files(site_dir, SITE_EXTRA_EXCLUDE)
    total = 0
    for src, rel, _ in site_files:
        top = rel.split("/")[0]
        if rel in RESERVED or top == RESERVED_DIR:
            raise PublishError("The site folder uses '%s', which Halbach ConnectHub reserves. Rename it." % rel)
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        total += 1
    hash_assets(stage)

    manifest_mods, n = stage_modules(store, stage)
    total += n
    if total + 1 > MAX_FILES:
        raise PublishError("%d files is over the %d-file limit for one site." % (total + 1, MAX_FILES))

    live_host = (st.get("settings") or {}).get("live_host")
    has_live = any(x["kind"] == "live" for x in manifest_mods)
    manifest = {"version": 1, "build": secrets.token_hex(6), "generated": int(time.time()),
                "live_base": ("https://" + live_host) if (live_host and has_live) else None,
                "modules": manifest_mods}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest


def find_npx():
    return shutil.which("npx")


def wrangler_deploy(stage, project, branch, log):
    """Run `wrangler pages deploy`; returns the deployment URL. Raises PublishError."""
    npx = find_npx()
    if not npx:
        raise PublishError("Node.js (npx) isn't installed or isn't on PATH. Install Node.js LTS first.")
    cmd = [npx, "-y", "wrangler@4", "pages", "deploy", str(stage), "--project-name", project,
           "--branch", branch, "--commit-dirty=true"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # run from the hub's data folder so wrangler's cache never lands in a project directory
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600,
                           creationflags=flags, encoding="utf-8", errors="replace", cwd=str(Path(stage).parent))
    except subprocess.TimeoutExpired:
        raise PublishError("The upload took longer than 10 minutes and was stopped.")
    except OSError as e:
        raise PublishError("Could not run wrangler: %s" % e)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        low = out.lower()
        if "not authenticated" in low or "wrangler login" in low or "authentication" in low:
            raise PublishError("Not signed in to Cloudflare. Run: npx wrangler@4 login")
        if "project not found" in low or "could not find" in low:
            raise PublishError("Cloudflare doesn't know a Pages project with that name. Check Settings.")
        tail = [ln for ln in out.splitlines() if ln.strip()][-3:]
        raise PublishError("wrangler failed: " + " / ".join(t.strip() for t in tail)[:300])
    m = URL_RE.findall(out)
    return m[-1] if m else None


def fetch_manifest_build(host, build, wait=60, interval=4):
    """Poll https://host/manifest.json until its build id matches. Returns True if seen."""
    end = time.time() + wait
    while time.time() < end:
        try:
            req = urllib.request.Request("https://%s/manifest.json?cb=%s" % (host, secrets.token_hex(3)),
                                         headers={"Cache-Control": "no-cache", "User-Agent": "HalbachConnectHub"})
            with urllib.request.urlopen(req, timeout=8) as r:
                if json.loads(r.read().decode("utf-8")).get("build") == build:
                    return True
        except Exception:
            pass
        time.sleep(interval)
    return False


class Publisher:
    """Runs one publish at a time in a background thread; the UI polls status()."""

    def __init__(self, store, deployer=None, verifier=None, site=None):
        self.store = store
        self.site = site                  # SiteService, set by the hub; None keeps the classic behaviour
        self.deployer = deployer or wrangler_deploy
        self.verifier = verifier or fetch_manifest_build
        self.lock = threading.Lock()
        self._status = {"state": "idle", "step": "", "message": "", "url": None, "finished": None}

    def status(self):
        with self.lock:
            return dict(self._status)

    def _set(self, **kw):
        with self.lock:
            self._status.update(kw)

    def ready(self):
        """(ok, reason) for the Publish button."""
        s = self.store.state.get("settings") or {}
        modular = s.get("site_mode") == "modular" and self.site is not None
        if not s.get("site_dir") and not modular:
            return False, "Set the site folder in Settings."
        if not s.get("project"):
            return False, "Set the Pages project name in Settings."
        if not find_npx():
            return False, "Node.js (npx) isn't installed."
        return True, ""

    def start(self, preview=False):
        ok, why = self.ready()
        if not ok:
            raise ValidationError(why)
        with self.lock:
            if self._status["state"] == "running":
                raise ValidationError("A publish is already running.")
            self._status = {"state": "running", "step": "Building", "message": "", "url": None, "finished": None}
        threading.Thread(target=self._run, args=(preview,), daemon=True).start()

    def _run(self, preview):
        t0 = time.time()
        s = self.store.state["settings"]
        kind = "preview" if preview else "production"
        try:
            self.store.log("info", "Publish started (%s)" % kind)
            stage = self.store.home / "stage"
            if s.get("site_mode") == "modular" and self.site is not None:
                res = self.site.build(stage)
                if not res.ok:
                    raise PublishError("The site has problems to fix first: " + self.site.explain(res))
                manifest = res.manifest
            else:
                manifest = build_stage(self.store, stage)
            n = sum(1 for _ in Path(stage).rglob("*") if _.is_file())
            self._set(step="Uploading")
            url = self.deployer(stage, s["project"], "preview" if preview else "main", self.store.log)
            self.store.log("ok", "Uploaded %d files%s" % (n, " to " + url if url else ""))
            if not preview:
                self.store.mark_published()
            verified = None
            if not preview and s.get("domain"):
                self._set(step="Checking the live site")
                verified = self.verifier(s["domain"], manifest["build"])
                self.store.log("ok" if verified else "warn",
                               "Live site matches the new build" if verified else
                               "Uploaded, but %s hasn't shown the change yet (it can take a minute to appear)" % s["domain"])
            secs = time.time() - t0
            self.store.log("ok", "Publish finished in %.1f s%s" % (secs, " (preview only, the live site is unchanged)" if preview else ""))
            self._set(state="done", step="", url=url, finished=int(time.time()),
                      message=("Preview ready" if preview else "Published") + (" (verified live)" if verified else ""))
        except PublishError as e:
            self.store.log("warn", "Publish failed: %s" % e)
            self._set(state="error", step="", message=str(e), finished=int(time.time()))
        except Exception as e:  # unexpected: still tell the user, never hang the UI
            self.store.log("warn", "Publish failed unexpectedly: %s" % e)
            self._set(state="error", step="", message="Unexpected error: %s" % e, finished=int(time.time()))
