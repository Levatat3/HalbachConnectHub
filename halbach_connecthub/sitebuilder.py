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

from . import publisher
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


def build_headers(csp, csp_mode):
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
    return "\n".join(lines)


# --------------------------------------------------------------------------- page HTML
def _nav_html(model, pages, current_id, url_base):
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
    brand_link = '<a class="hs-brand" href="%s"><span class="hs-mark">%s</span><span class="hs-name">%s</span></a>' % (
        html.escape(url_base + "/"), html.escape(b["mark"]), html.escape(b["name"]))
    return ('<!doctype html>\n<html lang="%s">\n<head>\n%s\n</head>\n<body class="hs-body" data-nav="%s">\n'
            '<a class="hs-skip" href="#hs-main">Skip to content</a>\n<div class="hs-shell">\n'
            '<header class="hs-header">%s<nav class="hs-nav" aria-label="Main">%s</nav></header>\n'
            '<main class="hs-main" id="hs-main" tabindex="-1">%s</main>\n<footer class="hs-footer">%s</footer>\n</div>\n%s'
            '<script src="%s" defer></script>\n</body>\n</html>\n') % (
        html.escape(s["lang"], quote=True), "\n".join(heads), html.escape(s["nav_style"]), brand_link, nav, body_main, foot,
        ('<script type="application/json" id="hs-page">%s</script>\n' % page_json) if page_json else "",
        html.escape(runtime_url, quote=True))


# --------------------------------------------------------------------------- the build
def build_site(model, registry, store, stage_dir, url_base="", extra_dir=None, include_drafts=False):
    """Build the whole site into stage_dir. Returns a BuildResult; check .ok before deploying."""
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
    for tid in theme_ids:
        plug = registry.theme(tid)
        css_parts = [rt["css"].read_text(encoding="utf-8")] + block_css
        for f in _plugin_css(plug):
            css_parts.append((plug.dir / f).read_text(encoding="utf-8"))
        vars_ = dict(plug.manifest.get("vars") or {})
        if tid == site_theme:
            vars_.update(data["theme"].get("vars") or {})
        decl = "".join("%s:%s;" % (k, v) for k, v in sorted(vars_.items()))
        mode = data["theme"].get("mode", "auto") if tid == site_theme else "auto"
        scheme = {"light": "color-scheme:light;", "dark": "color-scheme:dark;"}.get(mode, "")
        css_parts.append(":root{%s%s}" % (decl, scheme))
        css = "\n".join(css_parts).encode("utf-8")
        css_name = "_halbach/theme.%s.css" % _hash(css)
        total_bytes += _write(stage / css_name, css)
        script_name = None
        scripts = [f for f in plug.manifest.get("files", []) if f.endswith(".js")]
        if scripts:
            code = b"\n;\n".join((plug.dir / f).read_bytes() for f in scripts)      # a theme may ship an engine plus its glue
            script_name = "_halbach/theme-%s.%s.js" % (tid, _hash(code))
            total_bytes += _write(stage / script_name, code)
        themes[tid] = {"css": css_name, "script": script_name, "effects": list(plug.manifest.get("effects") or []),
                       "vars": vars_}

    # ---- extra files (the owner's own folder: images, games, standalone pages)
    slugs = {p["slug"] for p in data["pages"] if p["slug"]}
    n_extra = 0
    if extra_dir and Path(extra_dir).is_dir():
        files, problems = collect_files(extra_dir, publisher.SITE_EXTRA_EXCLUDE)
        for prob in problems:
            res.warnings.append({"path": "files", "message": prob})
        for src, rel, _ in files:
            top = rel.split("/")[0]
            if top.lower() in LEGACY_TOP:
                continue
            if top in RESERVED_TOP:
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
        theme = themes[p.get("theme") if p.get("theme") in themes else site_theme]
        blocks = []
        for b in p["blocks"]:
            if not b.get("enabled", True):
                continue
            props = json.loads(json.dumps(b.get("props", {})))
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
            "theme": {"id": p.get("theme") or site_theme, "effects": theme["effects"], "vars": theme["vars"],
                      "funk": data["theme"].get("funk") if (p.get("theme") or site_theme) == "funk" else None},
        }
        title = p["title"] if p["slug"] == "" and not p["title"] else p["title"]
        full_title = brand["name"] if p["slug"] == "" else "%s | %s" % (title or p["label"], brand["name"])
        noindex = (not settings["indexable"]) or (not p["listed"]) or p["draft"]
        canonical = (base_url + path) if base_url else None
        main_fallback = '<noscript><p class="hs-empty">This page needs JavaScript to show its content.</p></noscript>'
        text = _shell(data, full_title, p["description"] or brand.get("tagline", ""), [], main_fallback,
                      _nav_html(data, pages, p["id"], url_base), json_for_script(page_json),
                      url_base + "/" + runtime_name, url_base + "/" + theme["css"], url_base, noindex, canonical, icons,
                      credit=bool(p.get("credit", True)) and (p.get("theme") or site_theme) == "funk")
        out = stage / ("index.html" if p["slug"] == "" else p["slug"] + "/index.html")
        total_bytes += _write(out, text)
        res.pages.append({"id": p["id"], "path": path, "file": out.relative_to(stage).as_posix(), "indexable": not noindex})

    # ---- 404
    nf = _shell(data, "Not found | " + brand["name"], "", [],
                '<h1 class="hs-title">Not found</h1><p class="hs-lede">That page is not here. <a href="%s/">Go to the home page</a>.</p>' % html.escape(url_base),
                _nav_html(data, pages, None, url_base), "", url_base + "/" + runtime_name, url_base + "/" + themes[site_theme]["css"],
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
    headers = build_headers(csp, settings["csp"])
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
