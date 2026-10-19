"""Import a classic Halbach starter site (one index.html with tabs + js/site-config.js) into a site model.

Nothing here edits the classic site: it only reads it and returns a model dict. The owner reviews it,
previews it, and only then switches to modular mode. Stdlib only.
"""

import html as htmllib
import json
import re
from pathlib import Path

from .siteschema import slugify

TAB_RE = re.compile(r'<a\b[^>]*\bdata-tab="([a-z0-9-]+)"[^>]*>(.*?)</a>', re.I | re.S)
NAV_RE = re.compile(r'<nav\b[^>]*class="tabs"[^>]*>(.*?)</nav>', re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")


def _text(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(TAG_RE.sub("", fragment))).strip()


# --------------------------------------------------------------------------- a tiny JavaScript-literal reader
def parse_js_object(src):
    """Read the object literal assigned to window.SITE: comments, unquoted keys, single quotes, trailing commas."""
    start = src.find("{", src.find("SITE"))
    if start < 0:
        raise ValueError("No SITE object found")
    pos = [start]

    def skip():
        while pos[0] < len(src):
            c = src[pos[0]]
            if c in " \t\r\n":
                pos[0] += 1
            elif src.startswith("//", pos[0]):
                nl = src.find("\n", pos[0])
                pos[0] = len(src) if nl < 0 else nl
            elif src.startswith("/*", pos[0]):
                end = src.find("*/", pos[0] + 2)
                pos[0] = len(src) if end < 0 else end + 2
            else:
                break

    def string():
        q = src[pos[0]]
        pos[0] += 1
        out = []
        while pos[0] < len(src) and src[pos[0]] != q:
            c = src[pos[0]]
            if c == chr(92):
                pos[0] += 1
                n = src[pos[0]]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(n, n))
            else:
                out.append(c)
            pos[0] += 1
        pos[0] += 1
        return "".join(out)

    def value():
        skip()
        c = src[pos[0]]
        if c == "{":
            return obj()
        if c == "[":
            return arr()
        if c in "'\"":
            return string()
        m = re.compile(r"-?\d+(\.\d+)?|true|false|null|undefined").match(src, pos[0])
        if not m:
            raise ValueError("Unexpected text near: %r" % src[pos[0]:pos[0] + 20])
        pos[0] = m.end()
        t = m.group(0)
        return {"true": True, "false": False, "null": None, "undefined": None}.get(t, float(t) if "." in t else (int(t) if t.lstrip("-").isdigit() else t))

    def key():
        skip()
        if src[pos[0]] in "'\"":
            return string()
        m = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*").match(src, pos[0])
        if not m:
            raise ValueError("Bad key near: %r" % src[pos[0]:pos[0] + 20])
        pos[0] = m.end()
        return m.group(0)

    def obj():
        pos[0] += 1
        out = {}
        while True:
            skip()
            if src[pos[0]] == "}":
                pos[0] += 1
                return out
            k = key()
            skip()
            if src[pos[0]] != ":":
                raise ValueError("Expected ':' after %s" % k)
            pos[0] += 1
            out[k] = value()
            skip()
            if src[pos[0]] == ",":
                pos[0] += 1

    def arr():
        pos[0] += 1
        out = []
        while True:
            skip()
            if src[pos[0]] == "]":
                pos[0] += 1
                return out
            out.append(value())
            skip()
            if src[pos[0]] == ",":
                pos[0] += 1

    return obj()


# --------------------------------------------------------------------------- the importer
def _panel(index_html, pid):
    m = re.search(r'<section\b[^>]*\bid="%s"[^>]*>(.*?)</section>' % re.escape(pid), index_html, re.I | re.S)
    return m.group(1) if m else ""


def _first(rx, text, flags=re.I | re.S):
    m = re.search(rx, text, flags)
    return _text(m.group(1)) if m else ""


def import_classic(site_dir, live_tab_modules=(), base_url="https://levmorium.com", report=None):
    """Return (model dict, notes). live_tab_modules: [(module id, view, tab label)] for tabs the classic site added at run time.

    report: pass a list to have it filled with one dict per tab understood -- {"tab", "label", "blocks", "plain"} --
    which is what format_report() turns into the table the CLI prints.
    """
    site_dir = Path(site_dir)
    index = (site_dir / "index.html").read_text(encoding="utf-8")
    cfg_path = site_dir / "js" / "site-config.js"
    S = parse_js_object(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    notes = []
    report = [] if report is None else report

    nav = NAV_RE.search(index)
    tabs = [(i, _text(label)) for i, label in TAB_RE.findall(nav.group(1))] if nav else []
    if not tabs:
        tabs = [("home", "HOME")]
        notes.append("No tabs found in index.html; only a home page was made.")
    sym = _first(r'<span class="sym">(.*?)</span>', index) or "H"
    title = _first(r"<title>(.*?)</title>", index)
    desc = _first(r'<meta name="description" content="([^"]*)"', index)

    model = {
        "version": 1,
        "brand": {"name": S.get("name") or "MY SITE", "mark": sym[:4], "tagline": S.get("tagline", ""), "handle": S.get("handle", ""),
                  "footer": "%s · built by hand, no template" % S["handle"] if S.get("handle") else "built by hand, no template",
                  "icon": {"mode": "file", "file": None}},
        "theme": {"id": "retro-terminal", "vars": {}, "mode": "auto"},
        "settings": {"lang": "en", "base_url": base_url, "csp": "report-only", "analytics": "cloudflare", "search": False,
                     "nav_style": "top", "indexable": True},
        "pages": [], "collections": [], "redirects": [], "nav_links": [],
    }
    n = [0]

    def blk(type_, props):
        n[0] += 1
        return {"id": "b%d" % n[0], "type": type_, "props": props, "enabled": True}

    def page(pid, label, blocks, description="", ptitle=None):
        slug = "" if pid == "home" else ("play" if pid == "game" else slugify(pid))
        return {"id": pid, "slug": slug, "label": label, "title": ptitle or label, "description": description, "nav": True,
                "listed": True, "enabled": True, "draft": False, "og_image": "", "theme": None, "area": None, "blocks": blocks}

    for pid, label in tabs:
        panel = _panel(index, pid)
        lede = _first(r'<p class="lede"[^>]*>(.*?)</p>', panel)
        heading = _first(r"<h2[^>]*>(.*?)</h2>", panel) or label
        blocks = []
        plain = False
        if pid == "home":
            typed = re.search(r'id="typed"[^>]*data-text="([^"]*)"', panel)
            prompt = _first(r'<p class="prompt">(.*?)</p>', panel).rstrip("_")
            blocks.append(blk("hero", {"heading": htmllib.unescape(typed.group(1)) if typed else (S.get("name") or "Welcome"),
                                       "typed": True, "prompt": prompt, "tagline": S.get("tagline", "")}))
            cards = []
            for tab, inner in re.findall(r'<a class="card"[^>]*data-tab="([a-z0-9-]+)"[^>]*>(.*?)</a>', panel, re.I | re.S):
                cards.append({"title": _first(r"<b>(.*?)</b>", inner), "text": _first(r"<span>(.*?)</span>", inner), "page": tab})
            if cards:
                blocks.append(blk("cards", {"items": cards}))
            pg = page(pid, label, blocks, desc, title or label)
        elif pid == "game":
            g = S.get("game") or {}
            swf = g.get("swf") or "assets/game/game.swf"
            blocks.append(blk("text", {"markdown": "## %s\n\n%s" % (g.get("title") or heading, g.get("blurb") or lede)}))
            blocks.append(blk("ruffle-game", {"swf": "/" + swf.lstrip("/"), "autoplay": "auto"}))
            pg = page(pid, label, blocks, "Play the Flash game in your browser.")
        elif pid == "live":
            blocks.append(blk("text", {"markdown": "## LIVE"}))
            blocks.append(blk("twitch", {"channel": S.get("twitchChannel") or "your-twitch-name", "muted": True}))
            pg = page(pid, label, blocks, "Watch the stream.")
        elif pid == "videos":
            blocks.append(blk("text", {"markdown": "## VIDEOS"}))
            if S.get("youtubeVideoId"):
                blocks.append(blk("youtube", {"id": S["youtubeVideoId"], "mode": "video"}))
            elif S.get("youtubePlaylistId"):
                blocks.append(blk("youtube", {"id": S["youtubePlaylistId"], "mode": "playlist"}))
            else:
                blocks.append(blk("youtube", {"id": "your-video-id", "mode": "video"}))
            pg = page(pid, label, blocks, "Videos.")
        elif pid == "forge":
            blocks.append(blk("text", {"markdown": "## %s\n\n%s" % (heading, lede)}))
            blocks.append(blk("embed", {"url": "/pitforge/", "height": 560}))
            blocks.append(blk("links", {"items": [{"label": "Open full screen", "url": "/pitforge/"}], "style": "buttons"}))
            pg = page(pid, label, blocks, "An interactive 3D design of an in-ground crucible furnace.")
        elif pid == "more":
            blocks.append(blk("text", {"markdown": "## %s\n\n%s" % (heading, lede)}))
            blocks.append(blk("module-list", {}))
            pg = page(pid, label, blocks, "Extra things, added as they come.")
        elif pid == "projects":
            blocks.append(blk("text", {"markdown": "## PROJECTS"}))
            extra = [{"title": p.get("name", ""), "text": p.get("desc", ""), **({"url": p["url"]} if p.get("url") else {})}
                     for p in S.get("projects", []) if p.get("name")]
            if extra:
                blocks.append(blk("cards", {"items": extra}))
            blocks.append(blk("github-projects", {"user": S.get("githubUser") or "your-github-name", "hide": S.get("githubHide") or [],
                                                  "forks": bool(S.get("githubShowForks"))}))
            pg = page(pid, label, blocks, "What I'm working on.")
        elif pid == "about":
            blocks.append(blk("text", {"markdown": "## %s\n\n%s" % (heading, lede)}))
            links = [l for l in S.get("links", []) if l.get("url")]
            if links:
                blocks.append(blk("links", {"items": [{"label": l.get("label") or l["url"], "url": l["url"]} for l in links], "style": "list"}))
            pg = page(pid, label, blocks, "About this site.")
        else:
            body = lede or _text(panel)
            blocks.append(blk("text", {"markdown": "## %s\n\n%s" % (heading, body)}))
            pg = page(pid, label, blocks)
            plain = True
            notes.append("The tab '%s' is not one of the standard ones; it was imported as a plain text page. Check it." % pid)
        model["pages"].append(pg)
        report.append({"tab": pid, "label": label, "blocks": [b["type"] for b in pg["blocks"]], "plain": plain})

    for mid, view, label in live_tab_modules:
        pid = slugify(view or mid)
        if any(p["id"] == pid for p in model["pages"]):
            continue
        pg = page(pid, label, [blk("text", {"markdown": "## %s" % label}), blk("live-view", {"module": mid, "view": view or "auto"})],
                  "Live data from my PC.")
        pg["slug"] = pid
        ids = [x["id"] for x in model["pages"]]
        model["pages"].insert(ids.index("more") + 1 if "more" in ids else len(ids), pg)       # the classic bar put it after MORE
        report.append({"tab": pid, "label": label, "blocks": [b["type"] for b in pg["blocks"]], "plain": False})
    return model, notes


def format_report(report, notes=()):
    """The lines `halbach site import-classic` prints: a tab -> block recipe table, the tabs left as plain text, then the notes."""
    width = max([len(r["tab"]) for r in report] + [3])
    lines = ["%-*s  %s" % (width, "TAB", "BLOCK RECIPE"), "%s  %s" % ("-" * width, "-" * len("BLOCK RECIPE"))]
    for r in report:
        lines.append("%-*s  %s" % (width, r["tab"], " + ".join(r.get("blocks") or []) or "(no blocks)"))
    plain = [r["tab"] for r in report if r.get("plain")]
    if plain:
        lines.append("")
        lines.append("Plain text (worth a look): %s" % ", ".join(plain))
    lines.extend("note: %s" % n for n in notes)
    return lines
