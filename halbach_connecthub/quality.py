"""Plain-language quality checks on a built site. These are advice, never blockers: the owner decides.

Returns a list of {level: 'warn'|'info', path, message}. Stdlib only.
"""

import re
from pathlib import Path

PLACEHOLDERS = ("your-github-name", "your-twitch-name", "your-video-id", "example.com", "Example card", "Example link",
                "Replace this with something of yours")
BIG_FILE = 5 * 1024 * 1024
BIG_SITE = 200 * 1024 * 1024


def _rgb(color):
    c = (color or "").strip().lower()
    m = re.match(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", c)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(rgb):
    def f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    """WCAG contrast ratio between two #hex colours, or None when either is not a plain hex colour."""
    ra, rb = _rgb(a), _rgb(b)
    if not ra or not rb:
        return None
    la, lb = _lum(ra), _lum(rb)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def check_site(model, theme_vars, stage=None, extra_warnings=()):
    data = model.data if hasattr(model, "data") else model
    out = []

    def warn(path, msg, level="warn"):
        out.append({"level": level, "path": path, "message": msg})

    # ---- colours people can read
    for fg, label in (("--text", "Text"), ("--muted", "Quiet text"), ("--accent", "The accent colour")):
        r = contrast(theme_vars.get(fg), theme_vars.get("--bg"))
        need = 4.5
        if r is not None and r < need:
            warn("theme", "%s is hard to read on the background (contrast %.1f:1, aim for %.1f:1 or better)." % (label, r, need))

    # ---- leftovers from a template
    for p in data["pages"]:
        if not p["enabled"] or p["draft"]:
            continue
        for b in p["blocks"]:
            if b.get("enabled", True) is False:
                continue
            blob = str(b.get("props", {}))
            for ph in PLACEHOLDERS:
                if ph in blob:
                    warn("pages/%s/%s" % (p["id"], b["id"]), "'%s' still has template placeholder text ('%s')." % (p["label"], ph))
                    break
            if b["type"] == "image" and not (b.get("props", {}).get("alt") or "").strip():
                warn("pages/%s/%s" % (p["id"], b["id"]), "An image on '%s' has no description for screen readers." % p["label"])
        heroes = [b for b in p["blocks"] if b["type"] == "hero" and b.get("enabled", True)]
        if len(heroes) > 1:
            warn("pages/" + p["id"], "'%s' has more than one big heading block; a page should have one main heading." % p["label"])

    # ---- links to places inside the site that do not exist
    if stage is not None:
        stage = Path(stage)
        known = set()
        for f in stage.rglob("*"):
            if f.is_file():
                known.add("/" + f.relative_to(stage).as_posix())
                if f.name == "index.html":
                    known.add("/" + (f.parent.relative_to(stage).as_posix() + "/").lstrip("."))
        known.add("/")

        def exists(target):
            t = target.split("#")[0].split("?")[0]
            if not t.startswith("/"):
                return True
            return t in known or (t + "/") in known or t.rstrip("/") in known or t == ""
        redirect_from = {r["from"] for r in data.get("redirects", [])}
        for p in data["pages"]:
            for b in p["blocks"]:
                pr = b.get("props", {})
                urls = [i.get("url") for i in pr.get("items", []) if isinstance(i, dict)] + [pr.get("url"), pr.get("swf")]
                for u in urls:
                    if isinstance(u, str) and u.startswith("/") and not exists(u) and u not in redirect_from:
                        warn("pages/%s/%s" % (p["id"], b["id"]), "A link on '%s' points to %s, which is not in the site." % (p["label"], u))
        big = [f for f in stage.rglob("*") if f.is_file() and f.stat().st_size > BIG_FILE]
        for f in big[:5]:
            warn("files/" + f.relative_to(stage).as_posix(), "%s is %.1f MB; big files make the site slow for visitors." % (f.name, f.stat().st_size / 1048576))
        total = sum(f.stat().st_size for f in stage.rglob("*") if f.is_file())
        if total > BIG_SITE:
            warn("files", "The whole site is %.0f MB." % (total / 1048576))

    # ---- credit for the theme engine
    if (data.get("theme", {}).get("id") == "funk") or any(p.get("theme") == "funk" for p in data["pages"]):
        hidden = [p["label"] for p in data["pages"] if p["enabled"] and not p["draft"] and not p.get("credit", True)
                  and (p.get("theme") or data.get("theme", {}).get("id")) == "funk"]
        if hidden:
            warn("pages", "The 'Powered by FunkThemes' credit is switched off on: %s." % ", ".join(hidden[:6]), "info")

    # ---- security policy state
    s = data["settings"]
    if s["csp"] == "report-only":
        warn("settings", "The security policy is in report-only mode: nothing is blocked yet. Check Preview, then switch it to Enforce.", "info")
    elif s["csp"] == "off":
        warn("settings", "The security policy is switched off.")
    if s["indexable"] and not s["base_url"]:
        pass  # the builder already says this
    names = {p["label"].lower() for p in data["pages"] if p["nav"] and p["enabled"] and not p["draft"]}
    if len(names) < len([p for p in data["pages"] if p["nav"] and p["enabled"] and not p["draft"]]):
        warn("pages", "Two menu entries have the same name.")
    out += [dict(w, level=w.get("level", "warn")) for w in extra_warnings]
    return out
