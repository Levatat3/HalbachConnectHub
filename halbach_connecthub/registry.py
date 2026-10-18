"""Plugin registry: finds blocks, views, themes and templates, checks their manifests and trust.

Built-in plugins ship in halbach_connecthub/builtin. User plugins live in <state>/plugins/<kind>/<id>/ and
are only usable after the owner enables them, pinned to a SHA-256 of their files (see docs/design/PLUGINS.md).
This module reads and validates; it never runs plugin code and never downloads anything. Stdlib only.
"""

import hashlib
import json
import re
from pathlib import Path

from .siteschema import FIELD_TYPES, ID_RE

SUPPORTED_HOSTAPI = 1
KINDS = ("block", "view", "theme", "template")
KIND_DIRS = {"block": "blocks", "view": "views", "theme": "themes", "template": "templates"}
ALLOWED_EXT = {".js", ".css", ".json", ".svg", ".png", ".woff2", ".md"}
MAX_PLUGIN_BYTES = 2 * 1024 * 1024
CSP_KEYS = ("default-src", "script-src", "style-src", "img-src", "connect-src", "frame-src", "font-src", "media-src")
CSP_TOKENS = ("LIVE_BASE", "'wasm-unsafe-eval'")
_ORIGIN_RE = re.compile(r"^https://(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


class Plugin:
    """One discovered plugin: its manifest, where it lives, and whether it may be used."""

    def __init__(self, kind, manifest, folder, builtin, errors=None):
        self.kind, self.manifest, self.dir, self.builtin = kind, manifest, Path(folder), builtin
        self.id = self.dir.name                    # identity is the folder; a manifest that disagrees is an error
        self.errors = list(errors or [])
        self.digest = None
        self.trusted = builtin
        self.enabled = builtin

    @property
    def ok(self):
        return not self.errors

    def file_paths(self):
        return [self.dir / f for f in self.manifest.get("files", [])]

    def summary(self):
        m = self.manifest
        return {"kind": self.kind, "id": self.id, "name": m.get("name") or m.get("label") or self.id,
                "label": m.get("label") or m.get("name") or self.id, "description": m.get("description", ""),
                "category": m.get("category", ""), "version": m.get("version", ""), "builtin": self.builtin,
                "trusted": self.trusted, "enabled": self.enabled, "ok": self.ok, "errors": self.errors,
                "digest": self.digest, "fields": m.get("fields", []), "csp": m.get("csp", {}),
                "files": m.get("files", []), "tab": m.get("tab"), "status": m.get("status", "ready"), "isolated": bool(m.get("isolated"))}


def digest_of(plugin):
    """SHA-256 over the manifest and every declared file, in a fixed order."""
    h = hashlib.sha256()
    for rel in ["plugin.json"] + sorted(plugin.manifest.get("files", [])):
        p = plugin.dir / rel
        if p.is_file():
            h.update(rel.encode("utf-8") + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def validate_manifest(kind, manifest, folder):
    """Return a list of plain-language problems (empty means the manifest is acceptable)."""
    errs = []
    folder = Path(folder)
    if not isinstance(manifest, dict):
        return ["plugin.json is not an object"]
    if manifest.get("kind") != kind:
        errs.append("kind must be '%s'" % kind)
    mid = manifest.get("id")
    if not isinstance(mid, str) or not ID_RE.match(mid):
        errs.append("id must be lowercase letters, digits and hyphens")
    elif kind != "template" and mid != folder.name:
        errs.append("id '%s' must match the folder name '%s'" % (mid, folder.name))
    ha = manifest.get("hostapi")
    if kind != "template":
        if not isinstance(ha, int) or isinstance(ha, bool):
            errs.append("hostapi must be a whole number")
        elif ha > SUPPORTED_HOSTAPI or ha < 1:
            errs.append("needs host API %s but this Halbach supports up to %d" % (ha, SUPPORTED_HOSTAPI))
    files = manifest.get("files", [])
    if not isinstance(files, list):
        errs.append("files must be a list")
        files = []
    total = 0
    try:
        root = folder.resolve()
    except OSError:
        root = folder
    for rel in files:
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts or "\\" in rel:
            errs.append("file '%s' is not a plain relative path" % rel)
            continue
        p = folder / rel
        try:
            real = p.resolve(strict=True)
            real.relative_to(root)
        except (OSError, ValueError):
            errs.append("file '%s' is missing or outside the plugin folder" % rel)
            continue
        if p.is_symlink() or not real.is_file():
            errs.append("file '%s' must be a regular file" % rel)
            continue
        if real.suffix.lower() not in ALLOWED_EXT:
            errs.append("file '%s' has a type that is not allowed" % rel)
        total += real.stat().st_size
    if total > MAX_PLUGIN_BYTES:
        errs.append("files total %d KB; the limit is %d KB" % (total // 1024, MAX_PLUGIN_BYTES // 1024))
    csp = manifest.get("csp", {})
    if not isinstance(csp, dict):
        errs.append("csp must be an object")
        csp = {}
    for k, hosts in csp.items():
        if k not in CSP_KEYS:
            errs.append("csp key '%s' is not allowed" % k)
            continue
        if not isinstance(hosts, list):
            errs.append("csp.%s must be a list" % k)
            continue
        for h in hosts:
            if not (isinstance(h, str) and (h in CSP_TOKENS or _ORIGIN_RE.match(h))):
                errs.append("csp.%s: '%s' must be an https origin" % (k, h))
    if kind == "block":
        errs += _validate_fields(manifest.get("fields", []))
    return errs


def _validate_fields(fields):
    errs, seen = [], set()
    if not isinstance(fields, list):
        return ["fields must be a list"]
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get("key"), str) or not f.get("key"):
            errs.append("every field needs a key")
            continue
        k = f["key"]
        if k in seen:
            errs.append("duplicate field key '%s'" % k)
        seen.add(k)
        if f.get("type") not in FIELD_TYPES:
            errs.append("field '%s' has unknown type '%s'" % (k, f.get("type")))
        if f.get("type") == "select" and not f.get("options"):
            errs.append("select field '%s' needs options" % k)
    return errs


class Registry:
    """All plugins the hub knows about. Build once per operation; cheap to rebuild."""

    def __init__(self, builtin_dir=None, user_dir=None, trust=None):
        self.builtin_dir = Path(builtin_dir) if builtin_dir else Path(__file__).parent / "builtin"
        self.user_dir = Path(user_dir) if user_dir else None
        self.trust = dict(trust or {})          # "kind/id" -> approved digest
        self.plugins = {k: {} for k in KINDS}
        self.templates = {}
        self.load()

    def load(self):
        self.plugins = {k: {} for k in KINDS}
        self.templates = {}
        for root, builtin in ((self.builtin_dir, True), (self.user_dir, False)):
            if not root or not root.is_dir():
                continue
            for kind in KINDS:
                kdir = root / KIND_DIRS[kind]
                if not kdir.is_dir():
                    continue
                if kind == "template":
                    for f in sorted(kdir.glob("*.json")):
                        self._load_template(f, builtin)
                    continue
                for d in sorted(p for p in kdir.iterdir() if p.is_dir()):
                    self._load_plugin(kind, d, builtin)

    def _load_template(self, path, builtin):
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(t, dict) and isinstance(t.get("model"), dict) and ID_RE.match(str(t.get("id", ""))):
            if builtin or t["id"] not in self.templates:
                t["builtin"] = builtin
                self.templates[t["id"]] = t

    def _load_plugin(self, kind, folder, builtin):
        mf = folder / "plugin.json"
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            p = Plugin(kind, {"id": folder.name}, folder, builtin, ["plugin.json cannot be read: %s" % e])
            self.plugins[kind][p.id] = p
            return
        errs = validate_manifest(kind, manifest, folder)
        p = Plugin(kind, manifest, folder, builtin, errs)
        if not p.ok:
            p.enabled = p.trusted = False
        p.digest = digest_of(p) if p.ok else None
        if not builtin:
            approved = self.trust.get("%s/%s" % (kind, p.id))
            p.trusted = bool(p.ok and approved and approved == p.digest)
            p.enabled = p.trusted
            if approved and p.ok and approved != p.digest:
                p.errors.append("files changed since you approved this plugin; approve it again to use it")
        existing = self.plugins[kind].get(p.id)
        if existing and existing.builtin and not builtin:
            return                                  # a user plugin cannot replace a built-in
        self.plugins[kind][p.id] = p

    # ---- lookups ----
    def block(self, type_):
        p = self.plugins["block"].get(type_)
        return p if p and p.enabled and p.ok else None

    def view(self, name):
        p = self.plugins["view"].get(name)
        return p if p and p.enabled and p.ok else None

    def theme(self, tid):
        p = self.plugins["theme"].get(tid)
        return p if p and p.enabled and p.ok else None

    def catalog(self, kind="block"):
        """Everything the window needs to list plugins of a kind."""
        if kind == "template":
            return [{"id": t["id"], "name": t.get("name", t["id"]), "description": t.get("description", ""), "builtin": t.get("builtin", True)}
                    for t in self.templates.values()]
        return [p.summary() for p in self.plugins[kind].values()]

    def csp_for(self, block_types=(), view_names=(), theme_id=None):
        """Merge the CSP hosts the used plugins declared: {directive: sorted list}."""
        merged = {}
        plugs = [self.plugins["block"].get(t) for t in block_types] + [self.plugins["view"].get(v) for v in view_names]
        if theme_id:
            plugs.append(self.plugins["theme"].get(theme_id))
        for p in plugs:
            if not p or not p.ok:
                continue
            for k, hosts in (p.manifest.get("csp") or {}).items():
                merged.setdefault(k, set()).update(hosts)
        return {k: sorted(v) for k, v in merged.items()}

    def runtime_files(self):
        r = self.builtin_dir / "runtime"
        return {"js": r / "halbach-site.js", "css": r / "base.css"}
