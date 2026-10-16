"""Build the site plus switched-on static modules, write manifest.json, deploy, verify.

The deploy step shells out to `wrangler pages deploy` using the user's own login; Halbach ConnectHub
never sees a credential. Stdlib only.
"""

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

from .store import MAX_FILES, ValidationError, collect_files

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

    manifest_mods = []
    for m in st["modules"]:
        if not m["on"]:
            continue
        if m["kind"] == "live":
            manifest_mods.append({"id": m["id"], "name": m["name"], "desc": m.get("desc", ""), "kind": "live"})
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
        if mtype == "gallery":
            item["files"] = [base + rel for _, rel, _ in files if os.path.splitext(rel.lower())[1] in IMAGE_EXT]
        manifest_mods.append(item)
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

    def __init__(self, store, deployer=None, verifier=None):
        self.store = store
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
        if not s.get("site_dir"):
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
