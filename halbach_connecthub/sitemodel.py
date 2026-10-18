"""The site model: pages made of blocks, a theme, brand and settings, kept as data.

Stored as <state>/site/site.json (written atomically) with numbered history snapshots. Every edit goes
through a method here, which validates before changing anything, so the file on disk is always buildable
or at least explainable. Stdlib only.
"""

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from . import siteschema as sc

MODEL_VERSION = 1
HISTORY_KEEP = 100
CSP_MODES = ("enforce", "report-only", "off")
ANALYTICS_MODES = ("none", "cloudflare")
NAV_STYLES = ("top", "side", "none")
THEME_MODES = ("auto", "light", "dark")
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_BASE_URL_RE = re.compile(r"^https://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


class ModelError(ValueError):
    """An edit was refused. .errors holds one or more {key, message} entries."""

    def __init__(self, message, errors=None):
        super().__init__(message)
        self.errors = errors or [{"key": "", "message": message}]


def default_model(theme="neutral-dark"):
    return {
        "version": MODEL_VERSION,
        "brand": {"name": "MY SITE", "mark": "H", "tagline": "", "handle": "", "footer": "", "icon": {"mode": "generated", "file": None}},
        "theme": {"id": theme, "vars": {}, "mode": "auto"},
        "settings": {"lang": "en", "base_url": "", "csp": "report-only", "analytics": "cloudflare", "search": False,
                     "nav_style": "top", "indexable": True},
        "pages": [],
        "collections": [],
        "redirects": [],
        "nav_links": [],
    }


def _page_shell(pid, slug, label, **kw):
    p = {"id": pid, "slug": slug, "label": label, "title": label, "description": "", "nav": True, "listed": True,
         "enabled": True, "draft": False, "og_image": "", "theme": None, "area": None, "credit": True, "blocks": []}
    p.update(kw)
    return p


class SiteModel:
    def __init__(self, home, registry):
        self.home = Path(home)
        self.dir = self.home / "site"
        self.path = self.dir / "site.json"
        self.history_dir = self.dir / "history"
        self.registry = registry
        self.lock = threading.RLock()
        self.data = default_model()
        self.exists = False
        self.load()

    # ---------------------------------------------------------------- persistence
    def load(self):
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data, self.exists = default_model(), False
                return
            self.data = self._migrate(raw)
            self.exists = True

    def _migrate(self, raw):
        if not isinstance(raw, dict):
            raise ModelError("site.json is not a site model")
        v = raw.get("version", 1)
        if not isinstance(v, int) or v > MODEL_VERSION:
            raise ModelError("site.json is version %s but this Halbach understands up to %d. Update Halbach; the file was left untouched." % (v, MODEL_VERSION))
        base = default_model()
        for k, default in base.items():
            if k not in raw:
                raw[k] = copy.deepcopy(default)
        for k in ("brand", "theme", "settings"):
            for kk, vv in base[k].items():
                raw[k].setdefault(kk, copy.deepcopy(vv))
        raw["version"] = MODEL_VERSION
        for p in raw.get("pages", []):
            for k, vv in _page_shell("x", "x", "x").items():
                p.setdefault(k, copy.deepcopy(vv))
            for b in p.get("blocks", []):
                b.setdefault("enabled", True)
                b.setdefault("props", {})
        return raw

    def save(self, label=""):
        """Write the model atomically and add a history snapshot (skipped when nothing changed)."""
        with self.lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, indent=1, sort_keys=False)
            fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            self.exists = True
            self._snapshot(label, text)

    def _snapshot(self, label, text):
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        snaps = self._snapshot_files()
        if snaps:
            try:
                if json.loads(snaps[-1].read_text(encoding="utf-8")).get("meta", {}).get("digest") == digest:
                    return
            except (OSError, ValueError):
                pass
        self.history_dir.mkdir(parents=True, exist_ok=True)
        n = (int(snaps[-1].stem) + 1) if snaps else 1
        snap = {"meta": {"n": n, "time": int(time.time()), "label": sc.clean_line(label, 80), "digest": digest}, "model": self.data}
        (self.history_dir / ("%06d.json" % n)).write_text(json.dumps(snap), encoding="utf-8")
        for old in self._snapshot_files()[:-HISTORY_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass

    def _snapshot_files(self):
        return sorted(self.history_dir.glob("[0-9]*.json")) if self.history_dir.is_dir() else []

    def history(self):
        out = []
        for f in reversed(self._snapshot_files()):
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
                meta = dict(m["meta"])
                meta["pages"] = len(m["model"].get("pages", []))
                out.append(meta)
            except (OSError, ValueError, KeyError):
                continue
        return out

    def restore(self, n):
        """Make snapshot n the current model (and record that as a new snapshot, so it can be undone too)."""
        with self.lock:
            f = self.history_dir / ("%06d.json" % int(n))
            try:
                snap = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ModelError("There is no snapshot %s" % n)
            self.data = self._migrate(snap["model"])
            self.save("restored #%s" % n)

    def diff_since(self, n):
        """A short human summary of what changed between snapshot n and now."""
        f = self.history_dir / ("%06d.json" % int(n))
        try:
            old = json.loads(f.read_text(encoding="utf-8"))["model"]
        except (OSError, ValueError, KeyError):
            raise ModelError("There is no snapshot %s" % n)
        return diff_models(old, self.data)

    # ---------------------------------------------------------------- reference context
    def schema_context(self, module_index=None, area_ids=()):
        """Validation context: pages come from this model; modules from the caller's index {id: (kind, type)}."""
        pages = {p["id"] for p in self.data["pages"]}
        idx = module_index or {}

        def module_ok(mid, kinds):
            if mid not in idx:
                return module_index is None            # no index supplied: accept (used by unit tests)
            if not kinds:
                return True
            kind, typ = idx[mid]
            return kind in kinds or typ in kinds
        return sc.SchemaContext(module_ok=module_ok, page_ok=lambda p: p in pages, area_ok=lambda a: a in set(area_ids))

    # ---------------------------------------------------------------- brand, theme, settings
    def set_brand(self, **kw):
        with self.lock:
            b = copy.deepcopy(self.data["brand"])
            limits = {"name": 40, "mark": 4, "tagline": 120, "handle": 40, "footer": 200}
            for k, mx in limits.items():
                if k in kw and kw[k] is not None:
                    b[k] = sc.clean_line(kw[k], mx)
            if not b["name"]:
                raise ModelError("The site needs a name")
            if not b["mark"]:
                raise ModelError("The site needs a mark (1 to 4 characters) for its icon")
            icon = kw.get("icon")
            if icon is not None:
                if not isinstance(icon, dict) or icon.get("mode") not in ("generated", "file"):
                    raise ModelError("Icon mode must be 'generated' or 'file'")
                b["icon"] = {"mode": icon["mode"], "file": sc.clean_url(icon.get("file")) if icon.get("file") else None}
            self.data["brand"] = b
            return b

    def set_theme(self, theme_id=None, vars=None, mode=None, funk=None):
        with self.lock:
            t = copy.deepcopy(self.data["theme"])
            if theme_id is not None:
                if not self.registry.theme(theme_id):
                    raise ModelError("There is no theme '%s'" % theme_id)
                t["id"] = theme_id
            if vars is not None:
                if not isinstance(vars, dict):
                    raise ModelError("Theme variables must be a set of name and value pairs")
                clean = {}
                for k, v in vars.items():
                    if v in (None, ""):
                        continue
                    try:
                        n, val = sc.clean_css_var(k, v)
                    except sc.FieldError as e:
                        raise ModelError(str(e))
                    clean[n] = val
                if len(clean) > 60:
                    raise ModelError("At most 60 theme variables")
                t["vars"] = clean
            if funk is not None:
                if funk == {}:
                    t.pop("funk", None)
                else:
                    try:
                        t["funk"] = sc.clean_funktheme(funk)
                    except sc.FieldError as e:
                        raise ModelError(str(e))
            if t["id"] != "funk":
                t.pop("funk", None)                    # Funk data only belongs to the Funk theme
            if mode is not None:
                if mode not in THEME_MODES:
                    raise ModelError("Mode must be one of: %s" % ", ".join(THEME_MODES))
                t["mode"] = mode
            self.data["theme"] = t
            return t

    def set_settings(self, **kw):
        with self.lock:
            s = copy.deepcopy(self.data["settings"])
            if "lang" in kw:
                if not _LANG_RE.match(str(kw["lang"])):
                    raise ModelError("Language looks like 'en' or 'pt-BR'")
                s["lang"] = str(kw["lang"])
            if "base_url" in kw:
                v = str(kw["base_url"] or "").strip().rstrip("/")
                if v and not _BASE_URL_RE.match(v):
                    raise ModelError("The site address looks like https://example.com")
                s["base_url"] = v
            for k, opts in (("csp", CSP_MODES), ("analytics", ANALYTICS_MODES), ("nav_style", NAV_STYLES)):
                if k in kw:
                    if kw[k] not in opts:
                        raise ModelError("%s must be one of: %s" % (k, ", ".join(opts)))
                    s[k] = kw[k]
            for k in ("search", "indexable"):
                if k in kw:
                    s[k] = bool(kw[k])
            self.data["settings"] = s
            return s

    def set_redirects(self, redirects):
        with self.lock:
            out, seen = [], set()
            for r in redirects or []:
                if not isinstance(r, dict):
                    raise ModelError("Each redirect needs a from and a to")
                if not str(r.get("from") or "").strip().startswith("/"):
                    raise ModelError("A redirect goes from a path like /old to a page or address")
                try:
                    frm = sc.clean_url(r.get("from"), allow_relative=True)
                    to = sc.clean_url(r.get("to"))
                except sc.FieldError as e:
                    raise ModelError(str(e))
                if not frm.startswith("/") or not to:
                    raise ModelError("A redirect goes from a path like /old to a page or address")
                if frm in seen:
                    raise ModelError("Two redirects start at %s" % frm)
                seen.add(frm)
                status = int(r.get("status", 301))
                if status not in (301, 302, 307, 308):
                    raise ModelError("Redirect status must be 301, 302, 307 or 308")
                out.append({"from": frm, "to": to, "status": status})
            if len(out) > 500:
                raise ModelError("At most 500 redirects")
            self.data["redirects"] = out
            return out

    def set_nav_links(self, links):
        with self.lock:
            clean, errs = sc.validate_props([{"key": "l", "label": "Links", "type": "links", "max_items": 12}], {"l": links})
            if errs:
                raise ModelError(errs[0]["message"], errs)
            self.data["nav_links"] = clean["l"]
            return clean["l"]

    # ---------------------------------------------------------------- pages
    def page(self, pid):
        for p in self.data["pages"]:
            if p["id"] == pid:
                return p
        raise ModelError("There is no page '%s'" % pid)

    def _check_slug_free(self, slug, ignore_id=None):
        for p in self.data["pages"]:
            if p["id"] != ignore_id and p["slug"] == slug:
                raise ModelError("The address '/%s' is already used by '%s'" % (slug, p["label"]))
        for r in self.data["redirects"]:
            if r["from"].strip("/") == slug and slug:
                raise ModelError("A redirect already starts at '/%s'" % slug)

    def add_page(self, label, slug=None, title=None, description="", blocks=None, **opts):
        with self.lock:
            label = sc.clean_line(label, 40)
            if not label:
                raise ModelError("A page needs a label")
            try:
                slug = sc.validate_slug(sc.slugify(label) if slug is None else slug)
            except sc.FieldError as e:
                raise ModelError(str(e))
            self._check_slug_free(slug)
            base = sc.slugify(label) or "page"
            pid, n = base, 1
            taken = {p["id"] for p in self.data["pages"]}
            while pid in taken or not sc.ID_RE.match(pid):
                n += 1
                pid = "%s-%d" % (base[:34], n)
            page = _page_shell(pid, slug, label, title=sc.clean_line(title or label, 120), description=sc.clean_line(description, 300))
            self.data["pages"].append(page)
            for b in blocks or []:
                self.add_block(pid, b["type"], b.get("props", {}))
            if opts:
                self.update_page(pid, **opts)
            return self.page(pid)

    _PAGE_TEXT = {"label": 40, "title": 120, "description": 300}

    def update_page(self, pid, **kw):
        with self.lock:
            p = self.page(pid)
            new = copy.deepcopy(p)
            for k, mx in self._PAGE_TEXT.items():
                if k in kw:
                    new[k] = sc.clean_line(kw[k], mx)
            if not new["label"]:
                raise ModelError("A page needs a label")
            if "slug" in kw:
                try:
                    new["slug"] = sc.validate_slug(kw["slug"])
                except sc.FieldError as e:
                    raise ModelError(str(e))
                self._check_slug_free(new["slug"], ignore_id=pid)
            for k in ("nav", "listed", "enabled", "draft", "credit"):
                if k in kw:
                    new[k] = bool(kw[k])
            if "theme" in kw:
                th = kw["theme"] or None
                if th and not self.registry.theme(th):
                    raise ModelError("There is no theme '%s'" % th)
                new["theme"] = th
            if "og_image" in kw:
                new["og_image"] = sc.clean_line(kw["og_image"], 200)
            if new["slug"] == "" and not new["enabled"]:
                raise ModelError("The home page cannot be switched off")
            p.clear()
            p.update(new)
            return p

    def delete_page(self, pid):
        with self.lock:
            p = self.page(pid)
            if p["slug"] == "":
                raise ModelError("The home page cannot be deleted; switch another page to be home first")
            self.data["pages"].remove(p)

    def move_page(self, pid, delta):
        with self.lock:
            pages = self.data["pages"]
            i = pages.index(self.page(pid))
            j = max(0, min(len(pages) - 1, i + int(delta)))
            pages.insert(j, pages.pop(i))

    def duplicate_page(self, pid):
        with self.lock:
            src = self.page(pid)
            slug, n = (src["slug"] or "home") + "-copy", 1
            while any(p["slug"] == slug for p in self.data["pages"]):
                n += 1
                slug = "%s-copy-%d" % (src["slug"] or "home", n)
            copy_ = self.add_page(src["label"] + " copy", slug=slug, title=src["title"], description=src["description"])
            copy_["blocks"] = copy.deepcopy(src["blocks"])
            copy_["listed"] = False
            copy_["draft"] = True
            return copy_

    # ---------------------------------------------------------------- blocks
    def _block_fields(self, type_):
        plug = self.registry.block(type_)
        if not plug:
            raise ModelError("There is no block type '%s' (or it is not enabled)" % type_)
        return plug.manifest.get("fields", [])

    def _clean_props(self, type_, props, ctx):
        clean, errs = sc.validate_props(self._block_fields(type_), props, ctx)
        if errs:
            raise ModelError("; ".join(e["message"] for e in errs), errs)
        return clean

    def add_block(self, pid, type_, props=None, position=None, ctx=None):
        with self.lock:
            page = self.page(pid)
            clean = self._clean_props(type_, props or {}, ctx or self.schema_context())
            n = 1 + max([int(re.sub(r"\D", "", b["id"]) or 0) for b in page["blocks"]] + [0])
            block = {"id": "b%d" % n, "type": type_, "props": clean, "enabled": True}
            if position is None or position >= len(page["blocks"]):
                page["blocks"].append(block)
            else:
                page["blocks"].insert(max(0, int(position)), block)
            return block

    def block(self, pid, bid):
        for b in self.page(pid)["blocks"]:
            if b["id"] == bid:
                return b
        raise ModelError("There is no block '%s' on '%s'" % (bid, pid))

    def update_block(self, pid, bid, props=None, enabled=None, ctx=None):
        with self.lock:
            b = self.block(pid, bid)
            if props is not None:
                b["props"] = self._clean_props(b["type"], props, ctx or self.schema_context())
            if enabled is not None:
                b["enabled"] = bool(enabled)
            return b

    def delete_block(self, pid, bid):
        with self.lock:
            self.page(pid)["blocks"].remove(self.block(pid, bid))

    def move_block(self, pid, bid, delta):
        with self.lock:
            blocks = self.page(pid)["blocks"]
            i = blocks.index(self.block(pid, bid))
            j = max(0, min(len(blocks) - 1, i + int(delta)))
            blocks.insert(j, blocks.pop(i))

    def duplicate_block(self, pid, bid):
        with self.lock:
            b = self.block(pid, bid)
            return self.add_block(pid, b["type"], copy.deepcopy(b["props"]), position=self.page(pid)["blocks"].index(b) + 1)

    # ---------------------------------------------------------------- templates
    def load_template(self, template_id):
        t = self.registry.templates.get(template_id)
        if not t:
            raise ModelError("There is no template '%s'" % template_id)
        with self.lock:
            self.data = self._migrate(copy.deepcopy(t["model"]))

    # ---------------------------------------------------------------- validation
    def validate(self, module_index=None, area_ids=()):
        """Whole-model check. Returns (errors, warnings); each is {path, message}. Errors block publishing."""
        errors, warnings = [], []
        d = self.data

        def err(path, msg):
            errors.append({"path": path, "message": msg})

        def warn(path, msg):
            warnings.append({"path": path, "message": msg})

        if not self.registry.theme(d["theme"]["id"]):
            err("theme", "The theme '%s' is missing or not enabled" % d["theme"]["id"])
        seen_ids, seen_slugs = set(), {}
        ctx = self.schema_context(module_index, area_ids)
        for p in d["pages"]:
            path = "pages/%s" % p["id"]
            if p["id"] in seen_ids:
                err(path, "Two pages share the id '%s'" % p["id"])
            seen_ids.add(p["id"])
            try:
                sc.validate_slug(p["slug"])
            except sc.FieldError as e:
                err(path, str(e))
            if p["slug"] in seen_slugs:
                err(path, "The address '/%s' is used twice" % p["slug"])
            seen_slugs[p["slug"]] = p["id"]
            if p["enabled"] and p["listed"] and not p["draft"]:
                if not p["title"]:
                    warn(path, "'%s' has no title (search engines and browser tabs use it)" % p["label"])
                if not p["description"]:
                    warn(path, "'%s' has no description" % p["label"])
            for b in p["blocks"]:
                bpath = "%s/%s" % (path, b["id"])
                try:
                    fields = self._block_fields(b["type"])
                except ModelError as e:
                    err(bpath, str(e))
                    continue
                _, errs = sc.validate_props(fields, b.get("props", {}), ctx)
                for e in errs:
                    err(bpath, e["message"])
        if "" not in seen_slugs:
            err("pages", "There is no home page (a page with the address '/')")
        elif not self.page(seen_slugs[""])["enabled"]:
            err("pages", "The home page is switched off")
        return errors, warnings


def diff_models(old, new):
    """A short readable summary of what changed between two models."""
    out = []
    op = {p["id"]: p for p in old.get("pages", [])}
    np_ = {p["id"]: p for p in new.get("pages", [])}
    for pid in np_:
        if pid not in op:
            out.append("page added: %s (/%s)" % (np_[pid]["label"], np_[pid]["slug"]))
    for pid in op:
        if pid not in np_:
            out.append("page removed: %s" % op[pid]["label"])
    for pid, p in np_.items():
        o = op.get(pid)
        if not o:
            continue
        for k in ("label", "slug", "title", "description", "nav", "listed", "enabled", "draft", "credit", "theme"):
            if o.get(k) != p.get(k):
                out.append("page %s: %s changed" % (p["label"], k))
        if o.get("blocks") != p.get("blocks"):
            ob = {b["id"]: b for b in o.get("blocks", [])}
            nb = {b["id"]: b for b in p.get("blocks", [])}
            added = [i for i in nb if i not in ob]
            removed = [i for i in ob if i not in nb]
            changed = [i for i in nb if i in ob and nb[i] != ob[i]]
            moved = [b["id"] for b in p.get("blocks", [])] != [b["id"] for b in o.get("blocks", [])] and not (added or removed)
            if added:
                out.append("page %s: %d block(s) added" % (p["label"], len(added)))
            if removed:
                out.append("page %s: %d block(s) removed" % (p["label"], len(removed)))
            if changed:
                out.append("page %s: %d block(s) edited" % (p["label"], len(changed)))
            if moved:
                out.append("page %s: blocks reordered" % p["label"])
    if [p["id"] for p in old.get("pages", []) if p["id"] in np_] != [p["id"] for p in new.get("pages", []) if p["id"] in op]:
        out.append("page order changed")
    for k in ("brand", "theme", "settings", "redirects", "nav_links"):
        if old.get(k) != new.get(k):
            out.append("%s changed" % k)
    return out
