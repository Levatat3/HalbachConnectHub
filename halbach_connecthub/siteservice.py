"""The modular site as the hub sees it: model + registry + builder, and every edit the window can make.

One SiteService per hub. Every mutating call is all-or-nothing: if an edit is refused the model is put back
exactly as it was, and each accepted edit is saved with a history snapshot. Stdlib only.
"""

import copy
import threading
import time
from pathlib import Path

import json

from . import legacyimport, quality, sitebuilder
from .publisher import classify
from .registry import Registry
from .sitemodel import MODEL_VERSION, ModelError, SiteModel, diff_models
from .store import TAB_VIEWS, collect_files


class SiteService:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.registry = Registry(user_dir=store.home / "plugins", trust=(store.state.get("plugin_trust") or {}))
        self.model = SiteModel(store.home, self.registry)
        self.preview_dir = store.home / "preview"
        self.last_build = None
        self.import_notes = []

    # ---------------------------------------------------------------- what the site can refer to
    def mode(self):
        return (self.store.state.get("settings") or {}).get("site_mode") or "classic"

    def extra_dir(self):
        d = (self.store.state.get("settings") or {}).get("site_dir")
        return d if d and Path(d).is_dir() else None

    def module_index(self):
        """{module id: (kind, type)} for everything the site may use right now (switched-on modules)."""
        idx = {}
        for m in self.store.state["modules"]:
            if not m["on"]:
                continue
            if m["kind"] == "live":
                idx[m["id"]] = ("live", "live")
                continue
            files, _ = collect_files(m["source"])
            idx[m["id"]] = ("static", classify(files)[0] if files else "file")
        return idx

    def modules_view(self):
        out = []
        idx = self.module_index()
        for m in self.store.state["modules"]:
            out.append({"id": m["id"], "name": m["name"], "kind": m["kind"], "on": m["on"],
                        "type": idx.get(m["id"], (None, None))[1], "view": m.get("view") or "auto",
                        "listed": m.get("listed", True)})
        return out

    def theme_vars(self):
        plug = self.registry.theme(self.model.data["theme"]["id"])
        vars_ = dict((plug.manifest.get("vars") if plug else None) or {})
        vars_.update(self.model.data["theme"].get("vars") or {})
        return vars_

    @staticmethod
    def _merge(*lists):
        seen, out = set(), []
        for lst in lists:
            for w in lst:
                key = (w.get("path"), w.get("message"))
                if key not in seen:
                    seen.add(key)
                    out.append(w)
        return out

    def check(self):
        errors, warnings = self.model.validate(self.module_index())
        if not errors:
            warnings = self._merge(warnings, quality.check_site(self.model, self.theme_vars()))
        return {"errors": errors, "warnings": warnings}

    def view(self):
        with self.lock:
            c = self.check()
            return {
                "exists": self.model.exists, "mode": self.mode(), "model": self.model.data,
                "catalog": {k: self.registry.catalog(k) for k in ("block", "view", "theme", "template")},
                "modules": self.modules_view(), "history": self.model.history()[:30],
                "errors": c["errors"], "warnings": c["warnings"], "last_build": self.last_build,
                "has_site_dir": bool(self.extra_dir()), "import_notes": self.import_notes,
            }

    # ---------------------------------------------------------------- edits
    def apply(self, action, args):
        """Run one edit by name. Returns the new check result; raises ModelError (nothing changed) if refused."""
        with self.lock:
            before = copy.deepcopy(self.model.data)
            try:
                label = self._dispatch(action, args or {})
                self.model.save(label)
            except Exception:
                self.model.data = before
                raise
            return self.check()

    def _dispatch(self, action, a):
        m = self.model
        ctx = m.schema_context(self.module_index())
        if action == "page.add":
            m.add_page(a.get("label"), slug=a.get("slug"), title=a.get("title"), description=a.get("description", ""))
            return "page added: %s" % a.get("label")
        if action == "page.update":
            fields = {k: v for k, v in a.items() if k in ("label", "slug", "title", "description", "nav", "listed", "enabled",
                                                          "draft", "credit", "theme", "og_image")}
            m.update_page(a.get("id"), **fields)
            return "page edited: %s" % a.get("id")
        if action == "page.delete":
            m.delete_page(a.get("id"))
            return "page deleted: %s" % a.get("id")
        if action == "page.move":
            m.move_page(a.get("id"), a.get("delta", 0))
            return "page moved: %s" % a.get("id")
        if action == "page.duplicate":
            m.duplicate_page(a.get("id"))
            return "page duplicated: %s" % a.get("id")
        if action == "block.add":
            m.add_block(a.get("page"), a.get("type"), a.get("props") or {}, position=a.get("position"), ctx=ctx)
            return "block added: %s" % a.get("type")
        if action == "block.update":
            m.update_block(a.get("page"), a.get("id"), props=a.get("props"), enabled=a.get("enabled"), ctx=ctx)
            return "block edited: %s/%s" % (a.get("page"), a.get("id"))
        if action == "block.delete":
            m.delete_block(a.get("page"), a.get("id"))
            return "block deleted"
        if action == "block.move":
            m.move_block(a.get("page"), a.get("id"), a.get("delta", 0))
            return "block moved"
        if action == "block.duplicate":
            m.duplicate_block(a.get("page"), a.get("id"))
            return "block duplicated"
        if action == "brand":
            m.set_brand(**{k: a[k] for k in ("name", "mark", "tagline", "handle", "footer", "icon") if k in a})
            return "brand changed"
        if action == "theme":
            m.set_theme(theme_id=a.get("id"), vars=a.get("vars"), mode=a.get("mode"), funk=a.get("funk"))
            return "theme changed"
        if action == "settings":
            m.set_settings(**{k: a[k] for k in ("lang", "base_url", "csp", "analytics", "search", "nav_style", "indexable") if k in a})
            return "settings changed"
        if action == "redirects":
            m.set_redirects(a.get("redirects") or [])
            return "redirects changed"
        if action == "nav_links":
            m.set_nav_links(a.get("links") or [])
            return "menu links changed"
        if action == "template.load":
            m.load_template(a.get("id"))
            return "started from template %s" % a.get("id")
        if action == "import.classic":
            site_dir = self.extra_dir()
            if not site_dir:
                raise ModelError("Set the site folder in Settings first; the import reads your current site from there.")
            tabs = [(x["id"], x.get("view") or "auto", TAB_VIEWS.get(x.get("view"), x["name"]))
                    for x in self.store.state["modules"] if x["kind"] == "live" and x.get("view") in TAB_VIEWS and x["on"]]
            try:
                model, notes = legacyimport.import_classic(site_dir, live_tab_modules=tabs,
                                                           base_url=("https://" + self.store.state["settings"]["domain"])
                                                           if self.store.state["settings"].get("domain") else "")
            except (OSError, ValueError, IndexError) as e:
                raise ModelError("The current site could not be read: %s" % e)
            m.data = m._migrate(model)
            self.import_notes = notes
            return "imported the current site"
        if action == "import.package":
            self.import_package(a.get("package"))
            return "imported a site package"
        if action == "restore":
            m.restore(a.get("n"))
            return None
        raise ModelError("Unknown action '%s'" % action)

    # ---------------------------------------------------------------- building
    def build(self, stage, preview=False):
        """Build the modular site into stage. Preview builds use /preview/ addresses and include drafts."""
        with self.lock:
            res = sitebuilder.build_site(self.model, self.registry, self.store, stage,
                                         url_base="/preview" if preview else "", extra_dir=self.extra_dir(),
                                         include_drafts=preview)
            if res.ok:
                res.warnings = self._merge(res.warnings, quality.check_site(self.model, self.theme_vars(), stage))
            if preview:
                self.last_build = {"ok": res.ok, "files": res.files, "pages": len(res.pages), "errors": res.errors[:20],
                                   "warnings": res.warnings[:50], "csp": res.csp, "at": time.time()}
            return res

    def build_preview(self):
        return self.build(self.preview_dir, preview=True)

    def explain(self, res):
        """A one-line, plain-language reason a build failed."""
        if res.errors:
            e = res.errors[0]
            more = len(res.errors) - 1
            return "%s%s" % (e["message"], (" (and %d more problem%s)" % (more, "" if more == 1 else "s")) if more else "")
        return ""

    # ---------------------------------------------------------------- what changed since the last publish
    @property
    def published_path(self):
        return self.model.dir / "published.json"

    def record_published(self):
        """Remember exactly what went live, so the next publish can say what changed."""
        try:
            self.model.dir.mkdir(parents=True, exist_ok=True)
            self.published_path.write_text(json.dumps(self.model.data), encoding="utf-8")
        except OSError:
            pass

    def plan(self):
        """Plain-language list of what the next publish changes on the site's pages and design."""
        try:
            old = json.loads(self.published_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ["The first publish of the modular site: every page below goes live."]
        return diff_models(old, self.model.data)

    # ---------------------------------------------------------------- export / import (one JSON file)
    def export_package(self):
        return {"format": "halbach-site", "version": MODEL_VERSION, "exported": int(time.time()), "model": self.model.data}

    def import_package(self, pkg):
        if not isinstance(pkg, dict) or pkg.get("format") != "halbach-site" or not isinstance(pkg.get("model"), dict):
            raise ModelError("That file is not a Halbach site export.")
        if not isinstance(pkg.get("version"), int) or pkg["version"] > MODEL_VERSION:
            raise ModelError("That export is from a newer Halbach. Update Halbach to open it.")
        model = pkg["model"]
        pages = model.get("pages")
        if not isinstance(pages, list) or len(pages) > 200 or any(not isinstance(p, dict) or len(p.get("blocks", [])) > 200 for p in pages):
            raise ModelError("That export is too large or malformed (at most 200 pages with 200 blocks each).")
        if len(json.dumps(model)) > 2 * 1024 * 1024:
            raise ModelError("That export is over 2 MB.")
        self.model.data = self.model._migrate(model)
