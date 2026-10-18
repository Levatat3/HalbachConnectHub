"""State for Halbach ConnectHub: modules, validation and pending-change tracking.

Two kinds of module:
  static  a file or folder that gets uploaded to the site's host
  live    a service running on this PC, reached through a tunnel

Everything is deny-by-default: a new module is switched off, and a live module
exposes no fields until you allow them. Stdlib only.
"""

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

MAX_FILE_BYTES = 25 * 1024 * 1024  # Cloudflare Pages per-file limit
MAX_FILES = 20000                  # Cloudflare Pages per-site limit
MAX_NAME = 60
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# Drop-off folder for submissions from chats and tools (see Store.scan_inbox).
INBOX_MAX_TOTAL_BYTES = 100 * 1024 * 1024   # per submission
INBOX_MAX_MODULES = 100                     # imported submissions kept at once
INBOX_SETTLE_SECONDS = 2.0                  # ignore a folder still being written
META_MAX_BYTES = 8 * 1024
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Never uploaded, whatever folder you point at.
EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode"}
EXCLUDE_NAME_RE = re.compile(
    r"^(\.|thumbs\.db$|desktop\.ini$|timeline\.md$|module\.json$)"   # dotfiles, OS junk, notes, drop-off metadata
    r"|\.(pem|key|pfx|p12|kdbx|log)$"                      # keys, vaults, logs
    r"|^(id_rsa|id_ed25519|ingest_key|\.dev\.vars)"        # ssh keys and known secret files
    r"|\.bak-|\.bak$|\.env",                               # backups, env files
    re.IGNORECASE,
)

# A live module can never expose a field whose name contains one of these.
LOCKED_FIELD_PARTS = ("chat", "note", "loot", "account", "passw", "token", "secret",
                      "apikey", "api_key", "credential", "email", "cookie", "session")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

# How the site may show a live module. "auto" is the generic card/table view; a named view is a
# purpose-built one on the site, and a few of them also get their own tab.
LIVE_VIEWS = ("auto", "pets")
TAB_VIEWS = {"pets": "PETS"}
MAX_HIDE = 50


class ValidationError(ValueError):
    """The user asked for something that is not allowed or does not make sense."""


def home_dir():
    """Where state lives; never inside the repo.

    HALBACH_HOME overrides (CONNECTHUB_HOME still works). Otherwise the folder is
    HalbachConnectHub inside %LOCALAPPDATA%, unless an older ConnectHub folder exists there
    and the new one does not, in which case the older one keeps being used (no migration).
    """
    env = os.environ.get("HALBACH_HOME") or os.environ.get("CONNECTHUB_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        new, old = Path.home() / ".halbach-connecthub", Path.home() / ".connecthub"
    else:
        new, old = Path(base) / "HalbachConnectHub", Path(base) / "ConnectHub"
    return old if (old.exists() and not new.exists()) else new


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "module"


def is_locked_field(name):
    low = name.lower()
    return any(p in low for p in LOCKED_FIELD_PARTS)


def is_excluded(name):
    return bool(EXCLUDE_NAME_RE.search(name))


def collect_files(source, extra_exclude=()):
    """Return (files, problems) for a static source.

    extra_exclude: extra lowercase file names to skip (the site builder drops README.md).

    files: list of (absolute path, relative posix path, size).
    problems: human-readable reasons something was skipped or is too big.
    """
    src = Path(source)
    files, problems = [], []
    if src.is_file():
        if is_excluded(src.name):
            return [], ["%s is on the never-publish list" % src.name]
        entries = [(src, src.name)]
    elif src.is_dir():
        entries = []
        for root, dirs, names in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith("."))
            for n in sorted(names):
                if is_excluded(n) or n.lower() in extra_exclude:
                    continue
                p = Path(root) / n
                entries.append((p, p.relative_to(src).as_posix()))
    else:
        return [], ["source no longer exists"]
    for p, rel in entries:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            problems.append("%s is %.1f MB; the limit is 25 MB per file" % (rel, size / 1048576))
            continue
        files.append((str(p), rel, size))
    if len(files) > MAX_FILES:
        problems.append("%d files; the limit is %d per site" % (len(files), MAX_FILES))
    return files, problems


def fingerprint(source, extra_exclude=()):
    """Cheap change detector: hash of (relative path, size, mtime) of what would be uploaded."""
    files, _ = collect_files(source, extra_exclude)
    h = hashlib.sha1()
    for p, rel, size in files:
        try:
            mtime = int(os.stat(p).st_mtime)
        except OSError:
            mtime = 0
        h.update(("%s|%d|%d\n" % (rel, size, mtime)).encode("utf-8"))
    return h.hexdigest()


def normalize_live_source(text):
    """Accept 'host:port', 'host:port/path' or an http URL; allow this PC only."""
    t = text.strip()
    t = re.sub(r"^https?://", "", t, flags=re.IGNORECASE)
    m = re.match(r"^(\[::1\]|[A-Za-z0-9.]+):(\d{1,5})(/[^\s]*)?$", t)
    if not m:
        raise ValidationError("Use the form 127.0.0.1:8787 or 127.0.0.1:8787/overlay")
    host, port, path = m.group(1).lower(), int(m.group(2)), m.group(3) or ""
    if host not in LOCAL_HOSTS:
        raise ValidationError("Only services on this PC are allowed (127.0.0.1 or localhost)")
    if not 1 <= port <= 65535:
        raise ValidationError("Port must be between 1 and 65535")
    return "127.0.0.1:%d%s" % (port, path)


def validate_static_source(text):
    if not text or not text.strip():
        raise ValidationError("Choose a file or folder")
    p = Path(text.strip().strip('"')).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValidationError("That path does not exist")
    if p == Path(p.anchor):
        raise ValidationError("Pick a file or a specific folder, not a whole drive")
    hp = Path.home().resolve()
    if p == hp or p in hp.parents:
        raise ValidationError("Pick a specific folder, not your whole user folder")
    try:
        p.relative_to(home_dir().resolve())
        raise ValidationError("That is Halbach ConnectHub's own data folder")
    except ValueError:
        pass
    files, problems = collect_files(p)
    if not files:
        raise ValidationError(problems[0] if problems else "Nothing in there can be published")
    return str(p)


class Store:
    """JSON-file backed state. One lock; every change is written atomically."""

    def __init__(self, home=None):
        self.home = Path(home) if home else home_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.path = self.home / "state.json"
        self.log_path = self.home / "activity.jsonl"
        self.lock = threading.RLock()
        self.state = self._load()

    # ---- persistence -------------------------------------------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                s = json.load(f)
            if isinstance(s, dict) and isinstance(s.get("modules"), list):
                s.setdefault("settings", {})
                return s
        except (OSError, ValueError):
            pass
        return {"version": 1, "settings": {}, "modules": [], "last_deploy": None}

    def _save(self):
        fd, tmp = tempfile.mkstemp(dir=str(self.home), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def log(self, level, message):
        """Append to the activity log. level: ok | info | warn."""
        row = {"t": time.strftime("%H:%M:%S"), "d": time.strftime("%Y-%m-%d"), "level": level, "msg": message}
        with self.lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    def activity(self, limit=100):
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                rows = [json.loads(x) for x in f.read().splitlines()[-limit:]]
        except (OSError, ValueError):
            rows = []
        return list(reversed(rows))

    # ---- modules -----------------------------------------------------
    def _find(self, mid):
        for m in self.state["modules"]:
            if m["id"] == mid:
                return m
        raise ValidationError("No such module")

    def add_module(self, kind, name, source, desc=""):
        if kind not in ("static", "live"):
            raise ValidationError("Kind must be static or live")
        name = (name or "").strip()
        if not name or len(name) > MAX_NAME:
            raise ValidationError("Give it a name (1 to %d characters)" % MAX_NAME)
        src = validate_static_source(source) if kind == "static" else normalize_live_source(source)
        mod = self._append_module(kind, name, src, desc)
        self.log("info", "Module '%s' added (off)" % name)
        return mod

    def _append_module(self, kind, name, src, desc="", **extra):
        """Add an already-validated module. Always starts switched off."""
        with self.lock:
            base, n = slugify(name), 1
            taken = {m["id"] for m in self.state["modules"]}
            mid = base
            while mid in taken:
                n += 1
                mid = "%s-%d" % (base, n)
            mod = {"id": mid, "name": name, "kind": kind, "desc": (desc or "").strip()[:200],
                   "source": src, "on": False, "added": int(time.time())}
            if kind == "static":
                mod["published_fp"] = None
            else:
                mod["fields"] = []
                mod["viewer_cap"] = 40
            mod.update(extra)
            self.state["modules"].append(mod)
            self._save()
        return mod

    def set_on(self, mid, on):
        with self.lock:
            m = self._find(mid)
            m["on"] = bool(on)
            if on:
                m["new"] = False        # switching a submission on counts as reviewing it
            self._save()
        self.log("info", "Module '%s' switched %s" % (m["name"], "on" if on else "off"))
        return m

    def ack(self, mid):
        """Mark a submission as reviewed (clears the 'from a chat' flag)."""
        with self.lock:
            m = self._find(mid)
            m["new"] = False
            self._save()
        return m

    def remove(self, mid):
        with self.lock:
            m = self._find(mid)
            self.state["modules"].remove(m)
            self._save()
        if m.get("from_inbox"):
            self._shelve_submission(m)
        self.log("info", "Module '%s' removed" % m["name"])

    # ---- drop-off folder (inbox) -------------------------------------
    @property
    def inbox_dir(self):
        return self.home / "inbox"

    def _shelve_submission(self, m):
        """A removed submission's folder moves aside, so the scanner does not import it again."""
        try:
            src = Path(m["source"])
            if src.is_dir() and src.parent.resolve() == self.inbox_dir.resolve():
                dest = self.inbox_dir / ".removed"
                dest.mkdir(exist_ok=True)
                os.replace(str(src), str(dest / ("%s-%d" % (src.name, int(time.time())))))
        except OSError:
            pass

    @staticmethod
    def _newest_mtime(folder):
        newest, seen = os.stat(folder).st_mtime, 0
        for root, _dirs, names in os.walk(folder):
            for n in names:
                seen += 1
                if seen > MAX_FILES:
                    return time.time()          # absurdly large: treat as still changing
                try:
                    newest = max(newest, os.stat(os.path.join(root, n)).st_mtime)
                except OSError:
                    pass
        return newest

    @staticmethod
    def read_submission_meta(folder, fallback_name):
        """name and description from module.json (optional); text only, control characters removed."""
        name = desc = ""
        mj = Path(folder) / "module.json"
        try:
            if mj.is_file() and mj.stat().st_size <= META_MAX_BYTES:
                d = json.loads(mj.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    name = str(d.get("name") or "")
                    desc = str(d.get("desc") or d.get("description") or "")
        except (OSError, ValueError):
            pass
        name = _CTRL_RE.sub(" ", name).strip()[:MAX_NAME]
        desc = _CTRL_RE.sub(" ", desc).strip()[:200]
        if not name:
            name = _CTRL_RE.sub(" ", fallback_name.replace("-", " ").replace("_", " ")).strip().title()[:MAX_NAME]
        return name or "Submission", desc

    def scan_inbox(self, now=None):
        """Import new submission folders as switched-off modules. Returns the modules added.

        A submission is a sub-folder of the inbox (an optional module.json gives its name and
        description). It is never switched on and never published by this scan; the user
        reviews it first. Folders still being written, empty folders, oversized folders and
        anything that resolves outside the inbox are skipped.
        """
        inbox = self.inbox_dir
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            entries = sorted(inbox.iterdir())
        except OSError:
            return []
        now = time.time() if now is None else now
        root = inbox.resolve()
        added = []
        skipped = getattr(self, "_inbox_skipped", None)
        if skipped is None:
            skipped = self._inbox_skipped = set()
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            try:
                real = entry.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if real.parent != root:
                self._warn_once(skipped, str(real), "outside the inbox",
                                "Submission '%s' skipped: it points outside the inbox" % entry.name)
                continue
            src = str(real)
            with self.lock:
                if any(m["source"] == src for m in self.state["modules"]):
                    continue
                count = sum(1 for m in self.state["modules"] if m.get("from_inbox"))
            if count >= INBOX_MAX_MODULES:
                self._warn_once(skipped, "cap", "cap", "Inbox full (%d submissions); review or remove some" % INBOX_MAX_MODULES)
                break
            if self._newest_mtime(real) > now - INBOX_SETTLE_SECONDS:
                continue                                    # still being written; try again next scan
            files, _problems = collect_files(real)
            total = sum(f[2] for f in files)
            if not files:
                self._warn_once(skipped, src, "empty", "Submission '%s' skipped: nothing in it can be published" % entry.name)
                continue
            if total > INBOX_MAX_TOTAL_BYTES:
                self._warn_once(skipped, src, "big", "Submission '%s' skipped: over %d MB" % (entry.name, INBOX_MAX_TOTAL_BYTES // 1048576))
                continue
            name, desc = self.read_submission_meta(real, entry.name)
            mod = self._append_module("static", name, src, desc, from_inbox=True, new=True)
            self.log("info", "New submission '%s' (%d files, %d KB) is waiting for review (off)" % (name, len(files), total // 1024))
            added.append(mod)
        return added

    def _warn_once(self, seen, key, reason, message):
        if (key, reason) not in seen:
            seen.add((key, reason))
            self.log("warn", message)

    def set_fields(self, mid, fields):
        """fields: list of {name, allowed}. Locked names are forced off, always."""
        with self.lock:
            m = self._find(mid)
            if m["kind"] != "live":
                raise ValidationError("Only live modules have fields")
            out, seen = [], set()
            for f in fields:
                nm = str(f.get("name", "")).strip()
                if not nm or len(nm) > 60 or nm in seen:
                    continue
                seen.add(nm)
                locked = is_locked_field(nm)
                out.append({"name": nm, "locked": locked, "allowed": bool(f.get("allowed")) and not locked})
            m["fields"] = out
            self._save()
        return m

    def set_options(self, mid, view=None, hide=None, poll=None, listed=None):
        """Per-module display options. Live: view, hide (names of items to drop), poll (seconds).
        Static: listed (show a card in the MORE list). Anything for the wrong kind is refused."""
        with self.lock:
            m = self._find(mid)
            live = m["kind"] == "live"
            if (view is not None or hide is not None or poll is not None) and not live:
                raise ValidationError("Only live modules have a view, hidden items or a refresh rate")
            if listed is not None and live:
                raise ValidationError("Only static modules can be hidden from the list")
            if view is not None:
                if view not in LIVE_VIEWS:
                    raise ValidationError("View must be one of: %s" % ", ".join(LIVE_VIEWS))
                m["view"] = view
            if hide is not None:
                if isinstance(hide, str):
                    hide = hide.split(",")
                names = []
                for h in hide:
                    h = _CTRL_RE.sub(" ", str(h)).strip()
                    if h and len(h) <= 60 and h not in names:
                        names.append(h)
                if len(names) > MAX_HIDE:
                    raise ValidationError("At most %d names" % MAX_HIDE)
                m["hide"] = names
            if poll is not None:
                try:
                    n = int(poll)
                except (TypeError, ValueError):
                    raise ValidationError("Refresh rate must be a number of seconds")
                if not 1 <= n <= 60:
                    raise ValidationError("Refresh rate must be between 1 and 60 seconds")
                m["poll"] = n
            if listed is not None:
                m["listed"] = bool(listed)
            self._save()
        return m

    def set_cap(self, mid, cap):
        with self.lock:
            m = self._find(mid)
            if m["kind"] != "live":
                raise ValidationError("Only live modules have a viewer cap")
            m["viewer_cap"] = max(1, min(500, int(cap)))
            self._save()
        return m

    def set_settings(self, **kw):
        with self.lock:
            s = self.state["settings"]
            for k in ("site_dir", "project", "domain", "live_host", "tunnel_name", "cloudflared_path", "extra_origins", "site_mode"):
                if k in kw and kw[k] is not None:
                    v = str(kw[k]).strip()
                    if k == "site_mode" and v not in ("classic", "modular"):
                        raise ValidationError("Site mode is classic or modular")
                    if k == "site_dir" and v:
                        p = Path(v).expanduser()
                        if not p.is_dir():
                            raise ValidationError("Site folder does not exist")
                        v = str(p.resolve())
                    if k == "project" and v and not re.match(r"^[a-z0-9-]{1,58}$", v):
                        raise ValidationError("Project name: lowercase letters, digits and dashes")
                    if k in ("domain", "live_host") and v and not re.match(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", v.lower()):
                        raise ValidationError("%s: a bare host name like example.com" % ("Domain" if k == "domain" else "Live host"))
                    if k in ("domain", "live_host"):
                        v = v.lower()
                    if k == "extra_origins":
                        items = [x.strip().rstrip("/").lower() for x in v.split(",") if x.strip()]
                        for x in items:
                            if not re.match(r"^https?://[a-z0-9.-]+(:\d{1,5})?$", x):
                                raise ValidationError("Extra sites look like https://preview.example.pages.dev")
                        v = ", ".join(items)
                    if k == "tunnel_name" and v and not re.match(r"^[a-z0-9][a-z0-9-]{0,62}$", v):
                        raise ValidationError("Tunnel name: lowercase letters, digits and dashes")
                    if k == "cloudflared_path" and v:
                        pth = Path(v.strip('"')).expanduser()
                        if not pth.is_file() or "cloudflared" not in pth.name.lower():
                            raise ValidationError("That isn't a cloudflared program")
                        v = str(pth.resolve())
                    s[k] = v
            self._save()
        return s

    # ---- pending changes ---------------------------------------------
    SITE_ID = "__site__"
    MODEL_ID = "__model__"
    AREAS_ID = "__areas__"

    def _model_fp(self):
        """Fingerprint of the modular site model file (None when there isn't one yet)."""
        try:
            return hashlib.sha1((self.home / "site" / "site.json").read_bytes()).hexdigest()
        except OSError:
            return None

    def _site_fp(self):
        d = (self.state.get("settings") or {}).get("site_dir")
        return fingerprint(d, ("readme.md",)) if d and Path(d).is_dir() else None

    def pending(self):
        """Things whose state on the site differs from what you've chosen."""
        out = []
        fp = self._site_fp()
        if fp is not None and fp != self.state.get("published_site_fp"):
            out.append({"id": self.SITE_ID, "name": "Site files", "change": "update"})
        if (self.state.get("settings") or {}).get("site_mode") == "modular":
            mfp = self._model_fp()
            if mfp != self.state.get("published_model_fp"):
                out.append({"id": self.MODEL_ID, "name": "Site pages and design", "change": "update"})
        live_areas = {a["id"]: a.get("current") for a in self.state.get("areas", []) if a.get("enabled") and a.get("current")}
        if live_areas != (self.state.get("published_areas") or {}):
            out.append({"id": self.AREAS_ID, "name": "Area content", "change": "update"})
        for m in self.state["modules"]:
            if m["kind"] != "static":
                continue
            pub = m.get("published_fp")
            if m["on"]:
                fp = fingerprint(m["source"])
                if pub is None:
                    out.append({"id": m["id"], "name": m["name"], "change": "add"})
                elif pub != fp:
                    out.append({"id": m["id"], "name": m["name"], "change": "update"})
            elif pub is not None:
                out.append({"id": m["id"], "name": m["name"], "change": "remove"})
        return out

    def mark_published(self):
        """Record that the site now matches the current choices (called after a deploy)."""
        with self.lock:
            for m in self.state["modules"]:
                if m["kind"] == "static":
                    m["published_fp"] = fingerprint(m["source"]) if m["on"] else None
            self.state["last_deploy"] = int(time.time())
            self.state["published_site_fp"] = self._site_fp()
            self.state["published_model_fp"] = self._model_fp()
            self.state["published_areas"] = {a["id"]: a.get("current") for a in self.state.get("areas", []) if a.get("enabled") and a.get("current")}
            self._save()

    # ---- window theme (a FunkThemes theme for this window; kept here so it survives a new port or browser profile) ----
    MAX_WINDOW_THEME = 200_000

    def window_theme(self):
        return self.state.get("window_theme")

    def set_window_theme(self, theme):
        with self.lock:
            if theme is None:
                self.state.pop("window_theme", None)
            else:
                if not isinstance(theme, dict) or theme.get("funkTheme") != 1 or not str(theme.get("name") or "").strip():
                    raise ValidationError("That is not a FunkTheme")
                if len(json.dumps(theme)) > self.MAX_WINDOW_THEME:
                    raise ValidationError("That theme is too big to keep here (a large picture?). It stays in this browser only.")
                self.state["window_theme"] = theme
            self._save()
        return self.state.get("window_theme")

    def view(self):
        """State as the UI sees it, with a status for each module."""
        plist = self.pending()
        pend = {p["id"]: p["change"] for p in plist}
        mods = []
        for m in self.state["modules"]:
            v = dict(m)
            v.pop("published_fp", None)
            v["pending"] = pend.get(m["id"])
            if m["kind"] == "static":
                files, problems = collect_files(m["source"])
                v["file_count"] = len(files)
                v["bytes"] = sum(f[2] for f in files)
                v["problems"] = problems
                v["missing"] = not Path(m["source"]).exists()
            mods.append(v)
        return {"modules": mods, "settings": self.state["settings"],
                "last_deploy": self.state.get("last_deploy"), "pending": plist}
