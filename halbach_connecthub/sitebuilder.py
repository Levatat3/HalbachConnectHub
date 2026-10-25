"""Turn a site model into a folder of real, deployable files.

    build_site(model, registry, store, stage_dir, url_base="", extra_dir=None)

Output (all plain files, nothing needs a server beyond Cloudflare Pages' static hosting):
  /index.html, /<slug>/index.html   one real HTML page per enabled page (page data is inert JSON)
  /_halbach/runtime.<hash>.js       the generic runtime
  /_halbach/theme.<hash>.css        base.css + used blocks' css + theme css + variable overrides
  /_halbach/blocks/<type>.<hash>.js only the block scripts the site actually uses
  /_halbach/views/<name>.<hash>.js  only the live views the site actually uses
  /_headers /_redirects /robots.txt /sitemap.xml /404.html /manifest.json /modules/...
The content-security-policy is assembled from what the used plugins declared. Stdlib only.
"""

import hashlib
import html
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

from . import apppages, csscheck, publisher
from .siteschema import RESERVED_SLUGS, FieldError, clean_area_design, embed_url_host
from .store import MAX_FILES, MAX_FILE_BYTES, collect_files

HEADER_RULE_LIMIT = 100
HEADER_LINE_LIMIT = 2000
REDIRECT_STATIC_LIMIT = 2000
REDIRECT_FILE_LIMIT = 100 * 1024
LEGACY_TOP = {"index.html", "css", "js", "_headers", "_redirects", "readme.md", "timeline.md", "robots.txt", "sitemap.xml", "404.html"}
RESERVED_TOP = {"_halbach", "modules", "manifest.json"}
ICON_FILES = ("favicon.svg", "favicon.ico", "favicon-32.png", "apple-touch-icon.png")
LIVE_ASSET_BLOCKS = ("module-list", "module-view", "live-view")
ANALYTICS_HOSTS = {"script-src": ["https://static.cloudflareinsights.com"], "connect-src": ["https://cloudflareinsights.com"]}
BACKSLASH = chr(92)


class BuildResult:
    def __init__(self):
        self.errors, self.warnings = [], []
        self.pages, self.manifest = [], None
        self.files, self.bytes = 0, 0
        self.csp = ""

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings, "pages": self.pages,
                "files": self.files, "bytes": self.bytes, "csp": self.csp}


# --------------------------------------------------------------------------- small helpers
def _hash(data):
    return hashlib.sha256(data).hexdigest()[:10]


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return len(data)


def page_path(page):
    """The address a page has on the site: '/' for home, '/slug/' otherwise."""
    return "/" if page["slug"] == "" else "/%s/" % page["slug"]


def json_for_script(obj):
    """JSON safe to sit inside <script type=application/json>: no early </script>, no HTML comment tricks."""
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for ch, esc in (("<", "u003c"), (">", "u003e"), ("&", "u0026"), (chr(0x2028), "u2028"), (chr(0x2029), "u2029")):
        text = text.replace(ch, BACKSLASH + esc)
    return text


def _origin(url):
    m = re.match(r"^(https://[A-Za-z0-9.-]+(:\d+)?)(/|$)", url or "")
    return m.group(1) if m else None


def _plugin_script(plugin, default):
    name = plugin.manifest.get("script") or default
    return name if name in plugin.manifest.get("files", []) else None


def _plugin_css(plugin):
    return [f for f in plugin.manifest.get("files", []) if f.endswith(".css")]


def generated_icon_svg(brand, accent):
    mark = html.escape((brand.get("mark") or "H")[:4])
    size = 40 if len(mark) <= 1 else (32 if len(mark) == 2 else 24)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="18" fill="#0b0d10"/>'
            '<rect x="5" y="5" width="90" height="90" rx="14" fill="none" stroke="%s" stroke-width="5"/>'
            '<text x="50" y="50" font-family="monospace" font-size="%d" font-weight="700" fill="%s" text-anchor="middle" '
            'dominant-baseline="central">%s</text></svg>' % (accent, size * 1.6, accent, mark))


# --------------------------------------------------------------------------- CSP
def assemble_csp(registry, block_types, view_names, theme_ids, live_base, analytics, extra_frames=(), extra_connect=()):
    merged = {}
    for key, hosts in registry.csp_for(block_types, view_names, theme_ids[0] if theme_ids else None).items():
        merged.setdefault(key, set()).update(hosts)
    for tid in theme_ids[1:]:
        for key, hosts in registry.csp_for((), (), tid).items():
            merged.setdefault(key, set()).update(hosts)
    if analytics == "cloudflare":
        for key, hosts in ANALYTICS_HOSTS.items():
            merged.setdefault(key, set()).update(hosts)
    merged.setdefault("frame-src", set()).update(extra_frames)
    merged.setdefault("connect-src", set()).update(extra_connect)

    def hosts(key):
        out = set()
        for h in merged.get(key, ()):
            if h == "LIVE_BASE":
                if live_base:
                    out.add(live_base)
            else:
                out.add(h)
        return sorted(out)

    parts = [
        ("default-src", ["'self'"]),
        ("script-src", ["'self'"] + hosts("script-src")),
        ("style-src", ["'self'", "'unsafe-inline'"] + hosts("style-src")),
        ("img-src", ["'self'", "data:", "https:"] + hosts("img-src")),
        ("font-src", ["'self'", "data:"] + hosts("font-src")),
        ("connect-src", ["'self'"] + hosts("connect-src")),
        ("frame-src", ["'self'"] + hosts("frame-src")),
        ("media-src", ["'self'", "blob:", "data:"] + hosts("media-src")),
        ("worker-src", ["'self'", "blob:"]),
        ("object-src", ["'none'"]),
        ("base-uri", ["'self'"]),
        ("form-action", ["'self'"]),
        ("frame-ancestors", ["'self'"]),
    ]
    return "; ".join("%s %s" % (k, " ".join(v)) for k, v in parts)


RELAXED_MODULE_CSP = ("default-src 'self' https: data: blob: 'unsafe-inline' 'wasm-unsafe-eval'; object-src 'none'; "
                      "base-uri 'self'; frame-ancestors 'self'")


def app_origins(store, base_url, url_base):
    """The addresses an area's own pages may load their own files from: this site's, nothing else. A local preview is served
    from the hub itself; a deployed build from the site's domain and its Cloudflare Pages addresses (including preview ones)."""
    if url_base:
        return ["http://127.0.0.1:*", "http://localhost:*"]
    st = getattr(store, "state", None) or {}
    cfg = st.get("settings") or {}
    out = []
    for host in (urlparse(base_url or "").hostname, cfg.get("domain")):
        if host and re.match(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$", host.lower()):
            out.append("https://" + host.lower())
    proj = cfg.get("project")
    if proj and re.match(r"^[a-z0-9-]{1,58}$", proj):
        out += ["https://%s.pages.dev" % proj, "https://*.%s.pages.dev" % proj]
    return list(dict.fromkeys(out))


def build_headers(csp, csp_mode, area_rules=(), app_paths=(), app_hosts=()):
    name = {"enforce": "Content-Security-Policy", "report-only": "Content-Security-Policy-Report-Only"}.get(csp_mode)
    lines = ["/*"]
    if name:
        lines.append("  %s: %s" % (name, csp))
    lines += ["  X-Content-Type-Options: nosniff", "  Referrer-Policy: strict-origin-when-cross-origin",
              "  Permissions-Policy: camera=(), microphone=(), geolocation=()",
              "  Cache-Control: public, max-age=0, must-revalidate", "",
              "/_halbach/*", "  ! Cache-Control", "  Cache-Control: public, max-age=31536000, immutable", ""]
    if name:
        lines += ["/modules/*", "  ! %s" % name, "  %s: %s" % (name, RELAXED_MODULE_CSP), ""]
        for slug, area_csp in area_rules:
            # Someone else's content: only what its blocks need, and nothing in its files folder can ever run.
            lines += ["/%s/*" % slug, "  ! %s" % name, "  %s: %s" % (name, area_csp), "",
                      "/%s/files/*" % slug, "  ! %s" % name, "  %s: default-src 'none'; sandbox" % name, ""]
    for base in app_paths:
        # An area's own pages: sandboxed (no storage, no forms, own files only). Always enforced, whatever mode the site's policy is in,
        # and the site's policy is detached so the two cannot combine into something neither meant.
        lines += ["%s/app/*" % base, "  ! Content-Security-Policy", "  ! Content-Security-Policy-Report-Only",
                  "  Content-Security-Policy: %s" % apppages.app_csp(app_hosts),
                  "  Access-Control-Allow-Origin: *", ""]           # a sandboxed page reads its own data files across an opaque origin, so they must allow it
    return "\n".join(lines)


# --------------------------------------------------------------------------- page HTML
def _sites_menu_html(sites):
    """One dropdown ("Sites") that lists every area's sub-site. Plain <details>: no script, keyboard and screen-reader friendly."""
    items = "".join('<a href="%s">%s</a>' % (html.escape(path, quote=True), html.escape(name)) for name, path in sites)
    return '<details class="hs-sites"><summary>Sites</summary><div class="hs-sites-menu">%s</div></details>' % items


def _area_nav_html(area_pages, current_id, url_base, back_name, app=None):
    """A sub-site's own menu: its own pages, then a way back to the main site. Never the main site's tabs."""
    out = []
    for p in area_pages:
        cur = ' aria-current="page"' if p["id"] == current_id else ""
        out.append('<a data-page="%s" href="%s"%s>%s</a>' % (html.escape(p["id"]), html.escape(url_base + page_path(p)), cur, html.escape(p["label"])))
    if app:
        out.append('<a class="hs-app" href="%s">%s</a>' % (html.escape(app["path"], quote=True), html.escape(app["label"])))
    out.append('<a class="hs-back" href="%s/">&larr; %s</a>' % (html.escape(url_base), html.escape(back_name)))
    return "".join(out)


def _nav_html(model, pages, current_id, url_base, sites=(), own=None):
    out = []
    for p in pages:
        if not p["nav"]:
            continue
        cur = ' aria-current="page"' if p["id"] == current_id else ""
        out.append('<a data-page="%s" href="%s"%s>%s</a>' % (html.escape(p["id"]), html.escape(url_base + page_path(p)), cur,
                                                            html.escape(p["label"])))
    for link in model.get("nav_links", []):
        ext = ' target="_blank" rel="noopener noreferrer"' if re.match(r"^https?:", link["url"]) else ""
        out.append('<a href="%s"%s>%s</a>' % (html.escape(link["url"]), ext, html.escape(link["label"])))
    if own:
        out.append('<a class="hs-app" href="%s">%s</a>' % (html.escape(own["path"], quote=True), html.escape(own["label"])))
    if sites:
        out.append(_sites_menu_html(sites))
    return "".join(out)


def _shell(model, title, desc, head_extra, body_main, nav, page_json, runtime_url, css_url, url_base, robots_noindex, canonical, icons,
           credit=False):
    b, s = model["brand"], model["settings"]
    foot = html.escape(b.get("footer") or ((b.get("handle") and b["handle"]) or ""))
    if credit:
        foot += '<span class="hs-credit">Powered by FunkThemes</span>'
    heads = ['<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
             "<title>%s</title>" % html.escape(title)]
    if desc:
        heads.append('<meta name="description" content="%s">' % html.escape(desc, quote=True))
    if robots_noindex:
        heads.append('<meta name="robots" content="noindex">')
    if canonical:
        heads.append('<link rel="canonical" href="%s">' % html.escape(canonical, quote=True))
    heads += ['<meta property="og:title" content="%s">' % html.escape(title, quote=True),
              '<meta property="og:site_name" content="%s">' % html.escape(b["name"], quote=True)]
    if desc:
        heads.append('<meta property="og:description" content="%s">' % html.escape(desc, quote=True))
    heads += icons
    heads += ['<link rel="stylesheet" href="%s">' % html.escape(css_url, quote=True)] + head_extra
    if b.get("logo"):
        mark_html = '<img class="hs-logo" src="%s" alt="">' % html.escape(b["logo"], quote=True)
    else:
        mark_html = '<span class="hs-mark">%s</span>' % html.escape(b["mark"])
    brand_link = '<a class="hs-brand" href="%s">%s<span class="hs-name">%s</span></a>' % (
        html.escape(b.get("home") or (url_base + "/")), mark_html, html.escape(b["name"]))
    return ('<!doctype html>\n<html lang="%s">\n<head>\n%s\n</head>\n<body class="hs-body" data-nav="%s">\n'
            '<a class="hs-skip" href="#hs-main">Skip to content</a>\n<div class="hs-shell">\n'
            '<header class="hs-header">%s<nav class="hs-nav" aria-label="Main">%s</nav></header>\n'
            '<main class="hs-main" id="hs-main" tabindex="-1">%s</main>\n<footer class="hs-footer">%s</footer>\n</div>\n%s'
            '<script src="%s" defer></script>\n</body>\n</html>\n') % (
        html.escape(s["lang"], quote=True), "\n".join(heads), html.escape(s["nav_style"]), brand_link, nav, body_main, foot,
        ('<script type="application/json" id="hs-page">%s</script>\n' % page_json) if page_json else "",
        html.escape(runtime_url, quote=True))


# --------------------------------------------------------------------------- the build
def build_site(model, registry, store, stage_dir, url_base="", extra_dir=None, include_drafts=False, areas=None, extra_css=None, embed_sameorigin=False,
               area_app_dir=None):
    """Build the whole site into stage_dir. Returns a BuildResult; check .ok before deploying.

    extra_css (already checked) is appended to the site theme's stylesheet, and embed_sameorigin gives embed frames the same-origin
    permission an area's approved embeds get on the real site. area_app_dir is a collaborator's folder whose app/ pages are
    published at /app/ with their sandbox header. All three are for a collaborator's local preview only."""
    res = BuildResult()
    data = model.data
    stage = Path(stage_dir)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    url_base = (url_base or "").rstrip("/")
    total_bytes = 0

    # modules first: pages refer to them and validation needs to know what each one is
    try:
        mods, n_mod = publisher.stage_modules(store, stage)
    except publisher.PublishError as e:
        res.errors.append({"path": "modules", "message": str(e)})
        return res
    index = {m["id"]: (m["kind"], m.get("type") or "live") for m in mods}
    errs, warns = model.validate(module_index=index)
    res.errors += errs
    res.warnings += warns
    if res.errors:
        return res

    settings, brand = data["settings"], data["brand"]
    live_host = (store.state.get("settings") or {}).get("live_host")
    has_live = any(m["kind"] == "live" for m in mods)
    live_base = ("https://" + live_host) if (live_host and has_live) else None

    pages = [p for p in data["pages"] if p["enabled"] and (include_drafts or not p["draft"])]
    # ---- areas: approved content from collaborators, confined to each area's own address
    area_pages, area_types, area_frames = [], {}, {}
    area_designs, area_shell, area_page_list, sites_list = {}, {}, {}, []
    area_css = {}
    area_app, app_paths = {}, []                               # areas whose own pages (tier 3) are published, and where they live
    model_slugs = {p["slug"] for p in data["pages"]}
    own_app = None                                              # the owner's own pages (settings), published at /app/ like an area's, same sandbox
    if data["settings"].get("own_pages"):
        own_root = Path(model.dir)
        own_entry = apppages.APP_DIR + "/index.html"
        chk = apppages.check_tree(own_root)
        if "app" in model_slugs:
            res.errors.append({"path": "settings", "message": "Your own pages want the address /app/, which a page of yours already uses. Rename that page."})
        elif not chk.ok:
            res.warnings.append({"path": "own pages", "message": "Your own pages were left out: %s" % chk.problems[0]})
        elif own_entry not in apppages.tree_files(own_root):
            res.warnings.append({"path": "own pages", "message": "Your own pages are switched on but have no front page, so they were left out. Add app/index.html."})
        else:
            own_app = {"label": (str(data["settings"].get("own_pages_label") or "Pages").strip() or "Pages")[:apppages.MAX_LABEL], "path": url_base + "/app/"}
            app_paths.append("")
            res.warnings += [{"path": "own pages", "message": w} for w in chk.warnings]
    for ar in (areas or []):
        slug = ar["slug"]
        if slug in model_slugs or slug in RESERVED_SLUGS or slug in RESERVED_TOP or not slug:
            res.errors.append({"path": "areas/" + ar["id"], "message": "The area '%s' wants the address /%s/, which a page or the site already uses." % (ar["name"], slug)})
            continue
        try:
            doc = json.loads((Path(ar["tree"]) / "area.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            res.errors.append({"path": "areas/" + ar["id"], "message": "The approved content of area '%s' cannot be read." % ar["name"]})
            continue
        allowed = set(ar.get("blocks") or [])
        design = {}
        if doc.get("design"):                                   # checked again here: the owner may have changed the allowed themes since
            ok_themes = [t for t in (ar.get("themes") or []) if registry.theme(t)]
            allow_css = bool(ar.get("custom_css"))
            try:
                design = clean_area_design(doc["design"], ok_themes, allow_css) if (ok_themes or allow_css) else {}
            except FieldError:
                design = {}
            if not design:
                res.warnings.append({"path": "areas/" + ar["id"], "message": "The custom design of area '%s' was left out because the area is no longer allowed to use it." % ar["name"]})
        if design.get("css"):                                   # the stylesheet is read and checked again too, then merged into the area's own stylesheet
            css_path = Path(ar["tree"]) / design["css"]
            have = {"files/" + f.relative_to(Path(ar["tree"]) / "files").as_posix() for f in (Path(ar["tree"]) / "files").rglob("*") if f.is_file()}
            try:
                chk = csscheck.check(css_path.read_text(encoding="utf-8"), have, slug, url_base)
            except (OSError, UnicodeDecodeError):
                chk = None
            if chk is None or not chk.ok:
                res.warnings.append({"path": "areas/" + ar["id"], "message": "The stylesheet of area '%s' was left out: %s" % (ar["name"], "it cannot be read" if chk is None else chk.problems[0])})
                design = {k: v for k, v in design.items() if k != "css"}
            else:
                area_css[ar["id"]] = chk.css
        app_info = None
        if doc.get("app"):                                      # the area's own pages are checked again here too: the owner may have switched them off since
            tree_root = Path(ar["tree"])
            if not ar.get("custom_pages"):
                res.warnings.append({"path": "areas/" + ar["id"], "message": "The own pages of area '%s' were left out because the area is no longer allowed to have them." % ar["name"]})
            else:
                chk = apppages.check_tree(tree_root)
                spec = doc["app"] if isinstance(doc["app"], dict) else {}
                entry = spec.get("entry")
                if not chk.ok or entry not in apppages.tree_files(tree_root):
                    res.warnings.append({"path": "areas/" + ar["id"], "message": "The own pages of area '%s' were left out: %s" % (
                        ar["name"], chk.problems[0] if chk.problems else "the entry page is missing")})
                else:
                    app_info = {"entry": entry, "label": str(spec.get("label") or "Pages")[:apppages.MAX_LABEL], "path": url_base + "/" + slug + "/" + (entry[:-len("index.html")] if entry.endswith("/index.html") else entry)}      # a folder's own address: Cloudflare would only redirect to it
                    area_app[ar["id"]] = app_info
                    app_paths.append("/" + slug)
                    res.warnings += [{"path": "areas/" + ar["id"], "message": "own pages: " + w} for w in chk.warnings]
        area_designs[ar["id"]] = design
        dbrand = design.get("brand") or {}
        bname = dbrand.get("name") or ar["name"]
        area_shell[ar["id"]] = {
            "brand": {"name": bname, "mark": dbrand.get("mark") or (bname[:1] or "?").upper(), "footer": dbrand.get("footer") or "", "handle": "",
                      "tagline": dbrand.get("tagline") or "", "home": url_base + "/" + slug + "/",
                      "logo": (url_base + "/" + slug + "/" + dbrand["logo"]) if dbrand.get("logo") else None},
            "settings": dict(data["settings"], nav_style=(design.get("layout") or {}).get("nav") or data["settings"]["nav_style"]),
            "slug": slug, "app": app_info}
        area_page_list[ar["id"]] = []
        for i, pg in enumerate(doc.get("pages", [])):
            blocks = []
            for b in pg.get("blocks", []):
                if b["type"] not in allowed:
                    res.warnings.append({"path": "areas/" + ar["id"], "message": "A '%s' block in area '%s' was left out because the area is no longer allowed to use it." % (b["type"], ar["name"])})
                    continue
                props = dict(b.get("props", {}))
                if b["type"] == "text":
                    props["base"] = "/%s/" % slug
                if b["type"] == "embed":
                    # Checked again here, not only when the bundle came in: the owner may have removed the host since.
                    host = embed_url_host(props.get("url"))
                    if not host or props.get("module") or host not in (ar.get("embed_hosts") or []):
                        res.warnings.append({"path": "areas/" + ar["id"], "message": "An embed in area '%s' was left out because the area is no longer allowed to embed that address." % ar["name"]})
                        continue
                    props = {"url": props["url"], "height": props.get("height") or 560, "sameorigin": True}   # sameorigin is set here only, never read from a bundle
                    area_frames.setdefault(ar["id"], set()).add("https://" + host)
                blocks.append({"id": b["id"], "type": b["type"], "props": props, "enabled": True})
                area_types.setdefault(ar["id"], set()).add(b["type"])
            name = pg.get("name") or ""
            area_pages.append({"id": "area-%s-%s" % (ar["id"], name or "home"), "slug": slug + ("/" + name if name else ""),
                               "label": bname if i == 0 else (pg.get("title") or name), "title": pg.get("title") or doc.get("title") or ar["name"],
                               "description": doc.get("description", ""), "nav": False, "listed": True, "enabled": True,    # the area has its own menu; the main menu only gets the Sites dropdown
                               "draft": False, "theme": None, "credit": True, "area": ar["id"], "blocks": blocks})
            area_page_list[ar["id"]].append(area_pages[-1])
        if ar.get("nav") and area_page_list[ar["id"]]:
            sites_list.append((bname, url_base + "/" + slug + "/"))
    if res.errors:
        return res
    pages = pages + area_pages
    page_by_id = {p["id"]: p for p in pages}
    site_theme = data["theme"]["id"]
    theme_ids = [site_theme] + sorted({p["theme"] for p in pages if p.get("theme") and p["theme"] != site_theme})
    for tid in theme_ids:
        if not registry.theme(tid):
            res.errors.append({"path": "theme", "message": "The theme '%s' is missing or not enabled" % tid})
    if res.errors:
        return res

    # which block scripts / views the site needs
    used_types = {b["type"] for p in pages for b in p["blocks"] if b.get("enabled", True)}
    if used_types & set(LIVE_ASSET_BLOCKS) and any(m.get("type") == "game" for m in mods):
        used_types.add("ruffle-game")                       # a module-list may open a game
    used_views = set()
    if used_types & set(LIVE_ASSET_BLOCKS):
        used_views.add("auto")
        used_views.update(m.get("view") for m in mods if m["kind"] == "live" and m.get("view"))
    for p in pages:
        for b in p["blocks"]:
            if b["type"] == "live-view" and b.get("props", {}).get("view") not in (None, "", "auto"):
                used_views.add(b["props"]["view"])
    used_views.discard(None)
    for v in sorted(used_views):
        if not registry.view(v):
            res.warnings.append({"path": "views/" + v, "message": "The live view '%s' is not available; the standard view is used instead" % v})
            used_views.discard(v)
            used_views.add("auto")

    # ---- assets
    rt = registry.runtime_files()
    runtime_js = rt["js"].read_bytes()
    runtime_name = "_halbach/runtime.%s.js" % _hash(runtime_js)
    total_bytes += _write(stage / runtime_name, runtime_js)
    block_assets, view_assets = {}, {}
    block_css = []
    for t in sorted(used_types):
        plug = registry.block(t)
        script = _plugin_script(plug, "render.js") if plug else None
        if not script:
            res.errors.append({"path": "blocks/" + t, "message": "The block type '%s' is missing or has no script" % t})
            continue
        code = (plug.dir / script).read_bytes()
        name = "_halbach/blocks/%s.%s.js" % (t, _hash(code))
        total_bytes += _write(stage / name, code)
        block_assets[t] = name
        for css in _plugin_css(plug):
            block_css.append((plug.dir / css).read_text(encoding="utf-8"))
    for v in sorted(used_views):
        plug = registry.view(v)
        script = _plugin_script(plug, "view.js") if plug else None
        if not script:
            res.errors.append({"path": "views/" + v, "message": "The view '%s' has no script" % v})
            continue
        code = (plug.dir / script).read_bytes()
        name = "_halbach/views/%s.%s.js" % (v, _hash(code))
        total_bytes += _write(stage / name, code)
        view_assets[v] = name
    if res.errors:
        return res

    themes = {}
    theme_specs = [(tid, tid, (data["theme"].get("vars") or {}) if tid == site_theme else {}) for tid in theme_ids]
    for aid, d in area_designs.items():                         # an area with its own look gets its own stylesheet
        if d.get("theme") or d.get("vars") or aid in area_css:
            theme_specs.append(("area:" + aid, d.get("theme") or site_theme, d.get("vars") or {}))
    for key, tid, over_vars in theme_specs:
        plug = registry.theme(tid)
        css_parts = [rt["css"].read_text(encoding="utf-8")] + block_css
        for f in _plugin_css(plug):
            css_parts.append((plug.dir / f).read_text(encoding="utf-8"))
        vars_ = dict(plug.manifest.get("vars") or {})
        vars_.update(over_vars)
        decl = "".join("%s:%s;" % (k, v) for k, v in sorted(vars_.items()))
        mode = data["theme"].get("mode", "auto") if key == site_theme else "auto"
        scheme = {"light": "color-scheme:light;", "dark": "color-scheme:dark;"}.get(mode, "")
        css_parts.append(":root{%s%s}" % (decl, scheme))
        if extra_css and key == site_theme:
            css_parts.append("/* the area's own stylesheet (preview) */\n" + extra_css)
        if key.startswith("area:") and area_css.get(key[5:]):
            css_parts.append("/* the area's own stylesheet */\n" + area_css[key[5:]])      # last, so its rules can override the theme
        css = "\n".join(css_parts).encode("utf-8")
        css_name = "_halbach/theme.%s.css" % _hash(css)
        total_bytes += _write(stage / css_name, css)
        script_name = None
        scripts = [f for f in plug.manifest.get("files", []) if f.endswith(".js")]
        if scripts:
            code = b"\n;\n".join((plug.dir / f).read_bytes() for f in scripts)      # a theme may ship an engine plus its glue
            script_name = "_halbach/theme-%s.%s.js" % (tid, _hash(code))
            total_bytes += _write(stage / script_name, code)
        themes[key] = {"css": css_name, "script": script_name, "effects": list(plug.manifest.get("effects") or []),
                       "vars": vars_, "tid": tid}

    # ---- extra files (the owner's own folder: images, games, standalone pages)
    slugs = {p["slug"] for p in data["pages"] if p["slug"]} | {ar["slug"] for ar in (areas or [])}
    n_extra = 0
    if extra_dir and Path(extra_dir).is_dir():
        files, problems = collect_files(extra_dir, publisher.SITE_EXTRA_EXCLUDE)
        for prob in problems:
            res.warnings.append({"path": "files", "message": prob})
        for src, rel, _ in files:
            top = rel.split("/")[0]
            if top.lower() in LEGACY_TOP:
                continue
            if top in RESERVED_TOP or (top == "app" and own_app):
                res.errors.append({"path": "files/" + rel, "message": "The site folder uses '%s', which Halbach reserves. Rename it." % top})
                continue
            if top in slugs and (len(rel.split("/")) == 1 or rel.split("/")[1].lower() == "index.html"):
                res.errors.append({"path": "files/" + rel, "message": "The folder '%s' collides with the page at /%s/. Rename one of them." % (top, top)})
                continue
            dest = stage / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                continue
            shutil.copy2(src, dest)
            n_extra += 1
    if res.errors:
        return res

    # ---- area files (copied only under /<slug>/files/, never anywhere else)
    for ar in (areas or []):
        src_root = Path(ar["tree"]) / "files"
        base = (stage / ar["slug"] / "files").resolve()
        if not src_root.is_dir():
            continue
        for f in sorted(src_root.rglob("*")):
            if f.is_symlink() or not f.is_file() or f.suffix.lower() == ".css":     # a stylesheet is merged into the area's own, never published as it came
                continue
            dest = (base / f.relative_to(src_root)).resolve()
            try:
                dest.relative_to(base)
            except ValueError:
                res.errors.append({"path": "areas/" + ar["id"], "message": "A file in area '%s' would land outside the area's folder." % ar["name"]})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        if ar["id"] in area_app:                                # the area's own pages, only at /<slug>/app/ (and only after the checks above passed)
            app_base = (stage / ar["slug"] / "app").resolve()
            for name, f in apppages.tree_files(ar["tree"]).items():
                dest = (stage / ar["slug"] / name).resolve()
                try:
                    dest.relative_to(app_base)
                except ValueError:
                    res.errors.append({"path": "areas/" + ar["id"], "message": "A file in area '%s' would land outside the area's folder." % ar["name"]})
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
    if own_app:                                                 # the owner's own pages, only at /app/ (checked above)
        for name, f in apppages.tree_files(model.dir).items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    if area_app_dir:                                            # a collaborator's own preview: their folder's app/ at /app/
        chk = apppages.check_tree(area_app_dir)
        if chk.ok and apppages.tree_files(area_app_dir):
            for name, f in apppages.tree_files(area_app_dir).items():
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
            if "" not in app_paths:
                app_paths.append("")
        elif not chk.ok:
            res.warnings.append({"path": "app", "message": "Preview leaves out your own pages: %s (the owner would refuse them)." % chk.problems[0]})
    if res.errors:
        return res

    # ---- icons
    icons = []
    accent = themes[site_theme]["vars"].get("--accent", "#7cc4ff")
    have = {f: (stage / f).is_file() for f in ICON_FILES}
    if brand.get("icon", {}).get("mode") == "generated" or not have["favicon.svg"]:
        if not have["favicon.svg"] or brand.get("icon", {}).get("mode") == "generated":
            _write(stage / "favicon.svg", generated_icon_svg(brand, accent))
            have["favicon.svg"] = True
    if have["favicon.svg"]:
        icons.append('<link rel="icon" type="image/svg+xml" href="%s/favicon.svg">' % url_base)
    if have["favicon-32.png"]:
        icons.append('<link rel="icon" type="image/png" sizes="32x32" href="%s/favicon-32.png">' % url_base)
    if have["favicon.ico"]:
        icons.append('<link rel="shortcut icon" href="%s/favicon.ico">' % url_base)
    if have["apple-touch-icon.png"]:
        icons.append('<link rel="apple-touch-icon" href="%s/apple-touch-icon.png">' % url_base)

    # ---- content-security-policy
    extra_frames, extra_connect = set(), set()
    for p in pages:
        if p.get("area"):
            continue                                        # an area's embeds widen only that area's own policy (below), never the whole site's
        for b in p["blocks"]:
            if b["type"] == "embed" and b.get("props", {}).get("url"):
                o = _origin(b["props"]["url"])
                if o:
                    extra_frames.add(o)
    csp = assemble_csp(registry, sorted(used_types), sorted(used_views), theme_ids, live_base, settings["analytics"],
                       extra_frames, extra_connect)
    res.csp = csp

    # ---- manifest
    manifest = {"version": 1, "build": secrets.token_hex(6), "generated": int(time.time()), "live_base": live_base,
                "modules": mods}
    total_bytes += _write(stage / "manifest.json", json.dumps(manifest, indent=1))
    res.manifest = manifest

    # ---- pages
    hash_map = {}
    for p in data["pages"]:
        if p["enabled"] and p["id"] in page_by_id:
            hash_map[p["id"]] = page_path(p)
    base_url = settings["base_url"]
    for p in pages:
        ad = area_designs.get(p.get("area")) if p.get("area") else None
        akey = ("area:%s" % p["area"]) if p.get("area") else None
        if akey in themes:
            theme, theme_id = themes[akey], (ad.get("theme") or site_theme)
        else:
            theme = themes[p.get("theme") if p.get("theme") in themes else site_theme]
            theme_id = p.get("theme") or site_theme
        shell_model = area_shell.get(p.get("area")) if p.get("area") else None
        blocks = []
        for b in p["blocks"]:
            if not b.get("enabled", True):
                continue
            props = json.loads(json.dumps(b.get("props", {})))
            if embed_sameorigin and b["type"] == "embed" and not p.get("area"):
                props["sameorigin"] = True
            if b["type"] == "cards":
                for item in props.get("items", []):
                    target = item.pop("page", None)
                    if target:
                        if target in page_by_id:
                            item["href"] = url_base + page_path(page_by_id[target])
                        else:
                            res.warnings.append({"path": "pages/%s/%s" % (p["id"], b["id"]),
                                                 "message": "Card '%s' points at a page that is switched off or hidden" % item.get("title")})
                    elif item.get("url"):
                        item["href"] = item.pop("url")
            if b["type"] == "linked-sites":
                wanted = props.get("ids") or []
                props = {"items": [{"title": x["label"], "text": x["description"], "href": x["url"]} for x in data.get("linked_sites", [])
                                   if not wanted or x["id"] in wanted], "style": props.get("style", "cards")}
            blocks.append({"id": b["id"], "type": b["type"], "props": props})
        path = page_path(p)
        page_json = {
            "site": {"base": url_base, "hash_map": {k: url_base + v for k, v in hash_map.items()}, "live_base": live_base,
                     "name": brand["name"]},
            "page": {"id": p["id"], "slug": p["slug"], "title": p["title"], "path": url_base + path},
            "blocks": blocks,
            "assets": {"blocks": {t: url_base + "/" + block_assets[t] for t in block_assets},
                       "views": {v: url_base + "/" + view_assets[v] for v in view_assets},
                       "theme_script": (url_base + "/" + theme["script"]) if theme["script"] else None},
            "theme": {"id": theme_id, "effects": theme["effects"], "vars": theme["vars"],
                      "funk": ((ad or {}).get("funk") or data["theme"].get("funk")) if theme_id == "funk" else None},
        }
        title = p["title"] if p["slug"] == "" and not p["title"] else p["title"]
        if shell_model:
            sb_name = shell_model["brand"]["name"]
            full_title = sb_name if p["slug"] == shell_model["slug"] else "%s | %s" % (title or p["label"], sb_name)
        else:
            full_title = brand["name"] if p["slug"] == "" else "%s | %s" % (title or p["label"], brand["name"])
        noindex = (not settings["indexable"]) or (not p["listed"]) or p["draft"]
        canonical = (base_url + path) if base_url else None
        main_fallback = '<noscript><p class="hs-empty">This page needs JavaScript to show its content.</p></noscript>'
        if shell_model:
            nav_html = _area_nav_html(area_page_list[p["area"]], p["id"], url_base, brand["name"], shell_model.get("app"))
            page_desc = p["description"] or shell_model["brand"].get("tagline", "")
        else:
            nav_html = _nav_html(data, pages, p["id"], url_base, sites_list, own_app)
            page_desc = p["description"] or brand.get("tagline", "")
        text = _shell(shell_model or data, full_title, page_desc, [], main_fallback,
                      nav_html, json_for_script(page_json),
                      url_base + "/" + runtime_name, url_base + "/" + theme["css"], url_base, noindex, canonical, icons,
                      credit=bool(p.get("credit", True)) and theme_id == "funk")
        out = stage / ("index.html" if p["slug"] == "" else p["slug"] + "/index.html")
        total_bytes += _write(out, text)
        res.pages.append({"id": p["id"], "path": path, "file": out.relative_to(stage).as_posix(), "indexable": not noindex})

    # ---- 404
    nf = _shell(data, "Not found | " + brand["name"], "", [],
                '<h1 class="hs-title">Not found</h1><p class="hs-lede">That page is not here. <a href="%s/">Go to the home page</a>.</p>' % html.escape(url_base),
                _nav_html(data, pages, None, url_base, sites_list, own_app), "", url_base + "/" + runtime_name, url_base + "/" + themes[site_theme]["css"],
                url_base, True, None, icons, credit=(site_theme == "funk"))
    total_bytes += _write(stage / "404.html", nf)

    # ---- robots, sitemap, redirects, headers
    if settings["indexable"]:
        robots = "User-agent: *\nAllow: /\n" + (("Sitemap: %s/sitemap.xml\n" % base_url) if base_url else "")
    else:
        robots = "User-agent: *\nDisallow: /\n"
    total_bytes += _write(stage / "robots.txt", robots)
    if base_url and settings["indexable"]:
        urls = "".join("<url><loc>%s</loc></url>\n" % html.escape(base_url + page_path(p)) for p in pages if p["listed"] and not p["draft"])
        total_bytes += _write(stage / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n%s</urlset>\n' % urls)
    elif settings["indexable"]:
        res.warnings.append({"path": "settings", "message": "No site address is set, so no sitemap was made. Add your address in Settings."})
    redirects = data.get("redirects", [])
    if redirects:
        text = "".join("%s %s %d\n" % (r["from"], r["to"], r["status"]) for r in redirects)
        if len(redirects) > REDIRECT_STATIC_LIMIT or len(text.encode("utf-8")) > REDIRECT_FILE_LIMIT:
            res.errors.append({"path": "redirects", "message": "Too many redirects for Cloudflare Pages (2,000 and 100 KB at most)"})
        else:
            total_bytes += _write(stage / "_redirects", text)
    area_rules = []
    for ar in (areas or []):
        if ar["id"] in area_types:
            area_rules.append((ar["slug"], assemble_csp(registry, sorted(area_types[ar["id"]]), [], [area_designs.get(ar["id"], {}).get("theme") or site_theme], live_base, settings["analytics"],
                                                                  sorted(area_frames.get(ar["id"], ())))))
    headers = build_headers(csp, settings["csp"], area_rules, app_paths, app_origins(store, base_url, url_base))
    rules = sum(1 for ln in headers.splitlines() if ln and not ln.startswith(" "))
    if rules > HEADER_RULE_LIMIT or any(len(ln) > HEADER_LINE_LIMIT for ln in headers.splitlines()):
        res.errors.append({"path": "headers", "message": "The security policy is too long for Cloudflare Pages (a header line over 2,000 characters). Use fewer embedded sites."})
    total_bytes += _write(stage / "_headers", headers)

    # ---- limits
    all_files = [p for p in stage.rglob("*") if p.is_file()]
    res.files, res.bytes = len(all_files), total_bytes
    if len(all_files) > MAX_FILES:
        res.errors.append({"path": "files", "message": "%d files is over the %d-file limit for one site." % (len(all_files), MAX_FILES)})
    for f in all_files:
        if f.stat().st_size > MAX_FILE_BYTES:
            res.errors.append({"path": f.relative_to(stage).as_posix(), "message": "File is over the 25 MB limit."})
    return res


# --------------------------------------------------------------------------- reading _headers back (used by the local preview)
def parse_headers(text):
    """Parse a Cloudflare-style _headers file into [(path pattern, [(op, name, value)])]; op is 'set' or 'detach'."""
    rules, cur = [], None
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        if not raw.startswith((" ", "\t")):
            cur = (raw.strip(), [])
            rules.append(cur)
        elif cur is not None:
            line = raw.strip()
            if line.startswith("!"):
                cur[1].append(("detach", line[1:].strip(), None))
            elif ":" in line:
                name, _, value = line.partition(":")
                cur[1].append(("set", name.strip(), value.strip()))
    return rules


def headers_for(rules, path):
    """The response headers a _headers file gives to a path ('/x/y'): later rules add to, or detach from, earlier ones."""
    out = {}
    for pattern, ops in rules:
        if pattern.endswith("/*"):
            hit = path.startswith(pattern[:-1]) or path == pattern[:-2]
        elif pattern == "/*":
            hit = True
        else:
            hit = path == pattern
        if not hit:
            continue
        for op, name, value in ops:
            key = name.lower()
            if op == "detach":
                out.pop(key, None)
            elif key in out:
                out[key] = (out[key][0], out[key][1] + ", " + value)
            else:
                out[key] = (name, value)
    return {name: value for name, value in out.values()}
