"""Validation rules for the site model: addresses, ids, URLs, text, theme variables and block settings.

Every value that ends up on the public site goes through here first. Nothing is ever treated as HTML:
text is control-character-stripped and length-capped, URLs are limited to a short list of safe forms, and
block settings are checked against the field list a block (or plugin) declares. Stdlib only.
"""

import re

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_SLUGS = frozenset({"modules", "assets", "plugins", "_halbach", "api", "admin", "404", "robots", "sitemap", "preview", "app"})
MAX_SLUG_DEPTH = 3
MAX_URL = 2000
FIELD_TYPES = frozenset({"text", "longtext", "markdown", "url", "number", "bool", "select", "list-text", "links",
                         "cards", "module", "area", "page", "color", "image"})

_CONTROL_ALL = re.compile(r"[\x00-\x1f\x7f]")
_CONTROL_KEEP_NEWLINES = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")
_EMAIL_RE = re.compile(r"^[^\s@/?#]+@[A-Za-z0-9.-]+$")
_CSS_VALUE_RE = re.compile(r"^[A-Za-z0-9 #,.()%'\"_/+*-]{1,200}$")
_CSS_FORBIDDEN = ("url(", "expression(", "@import", "javascript:", "</", "<!--")


class FieldError(ValueError):
    """A value is not acceptable; the message says why in plain words."""


def clean_line(value, max_len=200):
    """One line of text: control characters removed, whitespace trimmed, length capped."""
    if value is None:
        return ""
    s = _CONTROL_ALL.sub(" ", str(value)).strip()
    return s[:max_len]


def clean_block_text(value, max_len=4000):
    """Multi-line text: newlines and tabs kept, other control characters removed, length capped."""
    if value is None:
        return ""
    s = _CONTROL_KEEP_NEWLINES.sub("", str(value).replace("\r\n", "\n").replace("\r", "\n"))
    return s.strip()[:max_len]


def slugify(label):
    """A friendly starting point for an address from a label (the user can still edit it)."""
    s = re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", str(label).lower())).strip("-")      # "Funk's" -> "funks"
    return s[:40]


def validate_slug(slug):
    """Returns the normalised address ('' for the home page) or raises FieldError."""
    if slug is None:
        raise FieldError("An address is required")
    s = str(slug).strip().strip("/")
    if s == "":
        return ""
    if _CONTROL_ALL.search(s) or "\\" in s or ".." in s or s != s.lower():
        raise FieldError("Addresses are lowercase letters, digits and hyphens only")
    parts = s.split("/")
    if len(parts) > MAX_SLUG_DEPTH:
        raise FieldError("Addresses can be at most %d levels deep" % MAX_SLUG_DEPTH)
    for p in parts:
        if not SEGMENT_RE.match(p):
            raise FieldError("'%s' is not a valid address part (lowercase letters, digits and hyphens, up to 40 characters)" % p)
    if parts[0] in RESERVED_SLUGS:
        raise FieldError("'%s' is reserved" % parts[0])
    return s


def validate_id(value, what="id"):
    v = str(value or "").strip()
    if not ID_RE.match(v):
        raise FieldError("A %s is lowercase letters, digits and hyphens (up to 40 characters)" % what)
    return v


def clean_url(value, allow_relative=True):
    """Accepts https/http addresses, mailto:, #anchors and site-relative paths. Returns a safe string."""
    v = "" if value is None else str(value).strip()
    if not v:
        return ""
    if len(v) > MAX_URL:
        raise FieldError("Address is too long")
    if _CONTROL_ALL.search(v) or any(ch.isspace() for ch in v) or "\\" in v:
        raise FieldError("Addresses cannot contain spaces, backslashes or control characters")
    low = v.lower()
    if v.startswith("#"):
        return v
    if low.startswith("mailto:"):
        if not _EMAIL_RE.match(v[7:].split("?")[0]):
            raise FieldError("That mailto address is not valid")
        return v
    if low.startswith("http://") or low.startswith("https://"):
        rest = v.split("://", 1)[1]
        host = re.split(r"[/?#]", rest, 1)[0]
        if not host or "@" in host or not _HOST_RE.match(host):
            raise FieldError("That web address is not valid")
        return v
    if v.startswith("//"):
        raise FieldError("Protocol-relative addresses are not allowed")
    if _SCHEME_RE.match(v):
        raise FieldError("Only https, http and mailto addresses are allowed")
    if not allow_relative:
        raise FieldError("Use a full https:// address")
    if ".." in v.split("?")[0].split("#")[0].split("/"):
        raise FieldError("Paths cannot go up with '..'")
    return v if v.startswith("/") else "/" + v


def clean_css_var(name, value):
    """A theme variable override: name like --accent, value a plain colour, size or font stack."""
    n = str(name or "").strip()
    if not re.match(r"^--[a-z0-9][a-z0-9-]{0,38}$", n):
        raise FieldError("Theme variable names look like --accent")
    v = str(value if value is not None else "").strip()
    low = v.lower()
    if not _CSS_VALUE_RE.match(v) or any(bad in low for bad in _CSS_FORBIDDEN):
        raise FieldError("'%s' is not an allowed value for %s" % (v[:40], n))
    return n, v


class SchemaContext:
    """What the validator may check references against. Subclass or pass callables; None means 'anything goes'."""

    def __init__(self, module_ok=None, page_ok=None, area_ok=None):
        self._module_ok, self._page_ok, self._area_ok = module_ok, page_ok, area_ok

    def module_ok(self, mid, kinds=None):
        return True if self._module_ok is None else bool(self._module_ok(mid, kinds))

    def page_ok(self, pid):
        return True if self._page_ok is None else bool(self._page_ok(pid))

    def area_ok(self, aid):
        return True if self._area_ok is None else bool(self._area_ok(aid))


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [x for x in value.split(",")]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise FieldError("Expected a list")


def _clean_value(field, value, ctx):
    t = field["type"]
    if t == "text":
        return clean_line(value, field.get("max", 200))
    if t == "longtext":
        return clean_block_text(value, field.get("max", 4000))
    if t == "markdown":
        return clean_block_text(value, field.get("max", 20000))
    if t == "url":
        return clean_url(value)
    if t == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("1", "true", "yes", "on"):
            return True
        if str(value).lower() in ("0", "false", "no", "off", ""):
            return False
        raise FieldError("Expected on or off")
    if t == "number":
        try:
            n = float(value)
        except (TypeError, ValueError):
            raise FieldError("Expected a number")
        if n != n or n in (float("inf"), float("-inf")):
            raise FieldError("Expected a number")
        if field.get("integer") or float(n).is_integer():
            n = int(n)
        if "min" in field and n < field["min"]:
            raise FieldError("Must be at least %s" % field["min"])
        if "max" in field and n > field["max"]:
            raise FieldError("Must be at most %s" % field["max"])
        return n
    if t == "select":
        v = str(value)
        if v not in field.get("options", []):
            raise FieldError("Choose one of: %s" % ", ".join(field.get("options", [])))
        return v
    if t == "color":
        v = str(value or "").strip()
        if not _COLOR_RE.match(v):
            raise FieldError("Use a colour like #1a56db")
        return v.lower()
    if t == "list-text":
        items = []
        for x in _as_list(value):
            s = clean_line(x, field.get("max", 80))
            if s and s not in items:
                items.append(s)
        if len(items) > field.get("max_items", 50):
            raise FieldError("At most %d items" % field.get("max_items", 50))
        return items
    if t == "links":
        out = []
        for item in _as_list(value):
            if not isinstance(item, dict):
                raise FieldError("Each link needs a label and an address")
            url = clean_url(item.get("url"))
            if not url:
                continue
            out.append({"label": clean_line(item.get("label"), 80) or url, "url": url})
        if len(out) > field.get("max_items", 50):
            raise FieldError("At most %d links" % field.get("max_items", 50))
        return out
    if t == "cards":
        out = []
        for item in _as_list(value):
            if not isinstance(item, dict):
                raise FieldError("Each card needs a title")
            card = {"title": clean_line(item.get("title"), 100), "text": clean_line(item.get("text"), 300)}
            if not card["title"]:
                raise FieldError("Every card needs a title")
            for wrong in ("href", "link", "to", "target", "path", "address", "uri"):    # a link under another name used to vanish without a word
                if item.get(wrong):
                    raise FieldError("Card '%s' links with url or page, not '%s'" % (card["title"], wrong))
            page, url = clean_line(item.get("page"), 40), clean_url(item.get("url"))
            if page:
                if not ctx.page_ok(page):
                    raise FieldError("Card '%s' points at a page that does not exist" % card["title"])
                card["page"] = page
            elif url:
                card["url"] = url
            out.append(card)
        if len(out) > field.get("max_items", 50):
            raise FieldError("At most %d cards" % field.get("max_items", 50))
        return out
    if t in ("module", "image"):
        v = clean_line(value, 80)
        if not v:
            return ""
        if t == "image" and (v.startswith("/") or v.lower().startswith("https://")):
            return clean_url(v)
        if not ctx.module_ok(v, field.get("modules")):
            raise FieldError("'%s' is not a module of the right kind" % v)
        return v
    if t == "area":
        v = clean_line(value, 40)
        if v and not ctx.area_ok(v):
            raise FieldError("'%s' is not an area" % v)
        return v
    if t == "page":
        v = clean_line(value, 40)
        if v and not ctx.page_ok(v):
            raise FieldError("'%s' is not a page" % v)
        return v
    raise FieldError("Unknown field type '%s'" % t)


# --------------------------------------------------------------------------- embed hosts (areas)
# An area may embed another site only if its owner listed that exact host. These rules are shared by the bundle check
# (what a sender may write), the owner's setting and the builder (which checks again at build time).
_EMBED_NETLOC = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_EMBED_HOST = re.compile(r"^(?=.{4,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_EMBED_REFUSED_ENDINGS = ("localhost", "local", "internal", "test", "invalid", "example", "lan", "home", "corp", "intranet")
MAX_EMBED_HOSTS = 5


def embed_url_host(url):
    """The host of a plain https address, lower-cased and safe to compare with the owner's list, or None.

    Only https://host[/path][?query] qualifies: no user@host, no port, no brackets or escapes in the host, no
    spaces, control characters or backslashes anywhere, and at least two labels.
    """
    if not isinstance(url, str) or len(url) > 2000 or re.search(r"[\s\x00-\x1f\x7f\\]", url):
        return None
    m = re.match(r"^https://([^/?#]*)(?:[/?#].*)?$", url, re.I | re.S)
    if not m or not _EMBED_NETLOC.match(m.group(1)):
        return None
    host = m.group(1).lower()
    return host if _EMBED_HOST.match(host) else None


def clean_embed_hosts(value):
    """The owner's list of hosts an area may embed. Exact host names only: no wildcards, ports, addresses or private names.

    Accepts a list, or text with one host per line (a pasted https address is reduced to its host).
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.replace(",", "\n").split()
    if not isinstance(value, list):
        raise FieldError("Embed hosts must be a list of host names")
    out = []
    for raw in value:
        text = str(raw).strip().lower()
        if not text:
            continue
        host = embed_url_host(text) if "://" in text else (text if _EMBED_HOST.match(text) else None)
        if not host or re.match(r"^[0-9.]+$", host) or host.rsplit(".", 1)[-1] in _EMBED_REFUSED_ENDINGS:
            raise FieldError("'%s' is not a host name an area can embed (use a full public name such as player.example.com, no wildcards, ports or addresses)" % text[:60])
        if host not in out:
            out.append(host)
    if len(out) > MAX_EMBED_HOSTS:
        raise FieldError("At most %d embed hosts per area" % MAX_EMBED_HOSTS)
    return out


def validate_props(fields, props, ctx=None):
    """Check a block's settings against its field list.

    Returns (clean, errors): clean has only the declared keys (defaults filled in), errors is a list of
    {"key", "message"}. A block with errors must not be saved or built.
    """
    ctx = ctx or SchemaContext()
    props = props if isinstance(props, dict) else {}
    clean, errors = {}, []
    for f in fields:
        key = f["key"]
        raw = props.get(key)
        present = key in props and raw not in (None, "")
        if not present:
            if f.get("required"):
                errors.append({"key": key, "message": "%s is required" % f.get("label", key)})
                continue
            if "default" in f:
                clean[key] = f["default"]
            elif f["type"] in ("list-text", "links", "cards"):
                clean[key] = []
            elif f["type"] == "bool":
                clean[key] = False
            elif f["type"] in ("text", "longtext", "markdown", "url", "module", "area", "page", "image"):
                clean[key] = ""
            continue
        try:
            value = _clean_value(f, raw, ctx)
        except FieldError as e:
            errors.append({"key": key, "message": "%s: %s" % (f.get("label", key), e)})
            continue
        if f.get("required") and value in ("", [], None):
            errors.append({"key": key, "message": "%s is required" % f.get("label", key)})
            continue
        clean[key] = value
    return clean, errors


# --------------------------------------------------------------------------- FunkTheme data (a .funktheme file)
FUNK_STYLES = ("solid", "gradient", "rainbow")
FUNK_FX = (None, "none", "ascii", "matrix", "synthwave", "stars", "rain", "spectrum")
# FunkThemes 2: what each effect's own controls may hold (the engine's registerFx controls), as ("bool") or (lo, hi) or a choice.
FUNK_FX_VALUES = {
    "ascii": {"mouse": "bool", "beats": "bool"},
    "matrix": {"trail": (0, 1)},
    "synthwave": {"sun": "bool"},
    "stars": {"warp": (0, 2)},
    "rain": {"wind": (0, 1), "lightning": "bool"},
    "spectrum": {"style": ("bars", "ring", "wave"), "height": (0.3, 2), "mirror": "bool", "peaks": "bool"},
}
FUNK_BLENDS = ("normal", "screen", "lighten", "overlay", "multiply")
FUNK_FPS = (15, 30, 60)
MAX_FUNK_LAYERS = 2
_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _funk_hex(v, what):
    if not isinstance(v, str) or not _HEX_RE.match(v):
        raise FieldError("%s must be a colour like #ff7a3d" % what)
    return v.lower()


def _funk_num(v, lo, hi, what, default):
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not (lo <= v <= hi):
        raise FieldError("%s must be a number from %s to %s" % (what, lo, hi))
    return float(v)


def _funk_fx_values(fx, value, where):
    """The values of one effect's own controls: only the keys that effect declares, each of the right type and range.
    Unknown keys are dropped (a newer engine may add controls); a wrong value for a known one is refused."""
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise FieldError("%s must be a set of effect settings" % where)
    spec, out = FUNK_FX_VALUES.get(fx) or {}, {}
    for key, rule in spec.items():
        if key not in value:
            continue
        v = value[key]
        if v is None:                                           # null means "not set": the effect's own default
            continue
        if rule == "bool":
            if not isinstance(v, bool):
                raise FieldError("%s: '%s' must be on or off" % (where, key))
            out[key] = v
        elif isinstance(rule[0], str):
            if v not in rule:
                raise FieldError("%s: '%s' must be one of %s" % (where, key, ", ".join(rule)))
            out[key] = v
        else:
            out[key] = _funk_num(v, rule[0], rule[1], "%s: '%s'" % (where, key), None)
    return out


def _funk_layer(layer, i):
    where = "Effect layer %d" % (i + 1)
    if not isinstance(layer, dict):
        raise FieldError("%s is not an object" % where)
    fx = layer.get("fx")
    if fx in (None, "none") or fx not in FUNK_FX:
        raise FieldError("%s needs an effect (%s)" % (where, ", ".join(f for f in FUNK_FX if f)))
    out = {"fx": fx, "opacity": _funk_num(layer.get("opacity"), 0.1, 1, where + " opacity", 1.0)}
    blend = layer.get("blend", "normal")
    if blend not in FUNK_BLENDS:
        raise FieldError("%s blend must be one of %s" % (where, ", ".join(FUNK_BLENDS)))
    out["blend"] = blend
    for key, lo, hi, what in (("fxSpeed", 0.2, 3, "speed"), ("fxAmount", 0, 1, "amount")):
        if layer.get(key) is not None:
            out[key] = _funk_num(layer[key], lo, hi, "%s %s" % (where, what), None)
    if layer.get("fxColor"):
        out["fxColor"] = _funk_hex(layer["fxColor"], where + " colour")
    vals = _funk_fx_values(fx, layer.get("fxValues"), where)
    if vals:
        out["fxValues"] = vals
    return out


def _funk_motion(value):
    """How the background moves. `listen` (react to music) is always off on a site: there is no audio source there."""
    if not isinstance(value, dict):
        raise FieldError("motion must be a set of settings")
    out = {"fps": 30, "half": False, "drift": False, "pauseFullscreen": True, "listen": False}
    if "fps" in value:
        if isinstance(value["fps"], bool) or value["fps"] not in FUNK_FPS:
            raise FieldError("motion fps must be one of %s" % ", ".join(str(f) for f in FUNK_FPS))
        out["fps"] = int(value["fps"])
    for key in ("half", "drift", "pauseFullscreen"):
        if key in value:
            if not isinstance(value[key], bool):
                raise FieldError("motion %s must be on or off" % key)
            out[key] = value[key]
    return out


def clean_funktheme(value):
    """Check a .funktheme object and return a canonical copy, or raise FieldError.

    Only plain colours, numbers and effect names are kept: nothing that could pull in an outside address.
    Pictures are refused for the public site for now (they are large and would be copied into every history snapshot).
    """
    if not isinstance(value, dict) or value.get("funkTheme") != 1:
        raise FieldError("That is not a FunkTheme file")
    name = clean_line(value.get("name"), 60)
    if not name:
        raise FieldError("The theme needs a name")
    style = value.get("style", "solid")
    if style == "image":
        raise FieldError("Picture themes are not supported on the site yet. Use a gradient or an effect.")
    if style not in FUNK_STYLES:
        raise FieldError("Style must be solid, gradient or rainbow")
    colors = value.get("colors") or []
    if not isinstance(colors, list) or len(colors) > 8:
        raise FieldError("A theme has at most 8 colours")
    out = {"funkTheme": 1, "name": name, "style": style, "colors": [_funk_hex(c, "A colour") for c in colors],
           "angle": _funk_num(value.get("angle"), 0, 360, "Angle", 135.0),
           "strength": _funk_num(value.get("strength"), 0, 1, "Strength", 0.5),
           "blur": 0, "tint": None}
    acc = value.get("accent")
    if acc in (None, [], ""):
        out["accent"] = None
    else:
        if not isinstance(acc, list) or not 1 <= len(acc) <= 2:
            raise FieldError("The accent is one or two colours")
        out["accent"] = [_funk_hex(c, "An accent colour") for c in acc]
    fx = value.get("fx")
    if fx not in FUNK_FX:
        raise FieldError("Unknown effect '%s'" % (fx if isinstance(fx, str) else "?")[:20])
    out["fx"] = None if fx in (None, "none") else fx
    out["fxSpeed"] = _funk_num(value.get("fxSpeed"), 0.2, 3, "Effect speed", 1.0)
    out["fxAmount"] = _funk_num(value.get("fxAmount"), 0, 1, "Effect amount", 0.7)
    out["fxColor"] = _funk_hex(value["fxColor"], "The effect colour") if value.get("fxColor") else None
    vals = _funk_fx_values(out["fx"], value.get("fxValues"), "The effect")
    if vals:
        out["fxValues"] = vals
    layers = value.get("fxLayers")
    if layers not in (None, []):
        if not isinstance(layers, list) or len(layers) > MAX_FUNK_LAYERS:
            raise FieldError("A theme has at most %d extra effect layers" % MAX_FUNK_LAYERS)
        out["fxLayers"] = [_funk_layer(l, i) for i, l in enumerate(layers)]
    if value.get("motion") not in (None, {}):
        out["motion"] = _funk_motion(value["motion"])
    return out


# --------------------------------------------------------------------------- an area's own design (sub-site branding)
AREA_NAVS = ("top", "side", "none")
_CSS_FILE_RE = re.compile(r"^files/[A-Za-z0-9][A-Za-z0-9 ._()+,@~-]{0,100}\.css$")
_LOGO_RE = re.compile(r"^files/[A-Za-z0-9][A-Za-z0-9 ._()+,@~-]{0,100}\.(?:png|jpg|jpeg|gif|webp)$")
MAX_AREA_VARS = 40


def clean_area_design(value, allowed_themes, allow_css=False):
    """An area's own branding, validated. Returns the canonical dict ({} when empty); raises FieldError with a plain reason.

    {"brand": {name, mark, tagline, footer, logo}, "theme": id, "vars": {"--accent": "#fa0"}, "layout": {"nav": "top|side|none"},
     "funk": {...}, "css": "files/style.css"}. allowed_themes is the list of theme ids the owner lets this area use; "css" (the
    area's own stylesheet, checked separately by csscheck) is accepted only when the owner allowed it (allow_css). Only plain text, validated
    colours/sizes, a theme from that list, a layout preset and a picture from the area's own files: no markup, no scripts.
    """
    if value in (None, {}):
        return {}
    if not isinstance(value, dict):
        raise FieldError("design must be an object")
    for k in value:
        if k not in ("brand", "theme", "vars", "layout", "funk", "css"):
            raise FieldError("design has a setting that does not exist (%s)" % str(k)[:30])
    out = {}
    b = value.get("brand")
    if b not in (None, {}):
        if not isinstance(b, dict):
            raise FieldError("design.brand must be an object")
        brand = {}
        for key, n in (("name", 40), ("mark", 4), ("tagline", 160), ("footer", 200)):
            if b.get(key) not in (None, ""):
                brand[key] = clean_line(b[key], n)
        if b.get("logo") not in (None, ""):
            if not isinstance(b["logo"], str) or not _LOGO_RE.match(b["logo"]):
                raise FieldError("design.brand.logo must be a picture in the files folder, such as files/logo.png")
            brand["logo"] = b["logo"]
        for key in b:
            if key not in ("name", "mark", "tagline", "footer", "logo"):
                raise FieldError("design.brand has a setting that does not exist (%s)" % str(key)[:30])
        if brand:
            out["brand"] = brand
    theme = value.get("theme")
    if theme not in (None, ""):
        if not isinstance(theme, str) or theme not in allowed_themes:
            raise FieldError("the theme '%s' is not available to this area (allowed: %s)" % (str(theme)[:30], ", ".join(allowed_themes) or "none"))
        out["theme"] = theme
    vars_ = value.get("vars")
    if vars_ not in (None, {}):
        if not isinstance(vars_, dict):
            raise FieldError("design.vars must be a set of name and value pairs")
        clean = {}
        for k, v in vars_.items():
            if v in (None, ""):
                continue
            n, val = clean_css_var(k, v)
            clean[n] = val
        if len(clean) > MAX_AREA_VARS:
            raise FieldError("At most %d design variables" % MAX_AREA_VARS)
        if clean:
            out["vars"] = clean
    layout = value.get("layout")
    if layout not in (None, {}):
        if not isinstance(layout, dict) or set(layout) - {"nav"}:
            raise FieldError("design.layout only has a 'nav' setting")
        if layout.get("nav") not in AREA_NAVS:
            raise FieldError("design.layout.nav must be one of: %s" % ", ".join(AREA_NAVS))
        out["layout"] = {"nav": layout["nav"]}
    css = value.get("css")
    if css not in (None, ""):
        if not allow_css:
            raise FieldError("design.css is not available: the owner has not allowed this area its own stylesheet")
        if not isinstance(css, str) or not _CSS_FILE_RE.match(css):
            raise FieldError("design.css must be a stylesheet in the files folder, such as files/style.css")
        out["css"] = css
    funk = value.get("funk")
    if funk not in (None, {}):
        if out.get("theme") != "funk":
            raise FieldError("design.funk needs design.theme to be 'funk'")
        out["funk"] = clean_funktheme(funk)
    return out
