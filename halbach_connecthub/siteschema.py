"""Validation rules for the site model: addresses, ids, URLs, text, theme variables and block settings.

Every value that ends up on the public site goes through here first. Nothing is ever treated as HTML:
text is control-character-stripped and length-capped, URLs are limited to a short list of safe forms, and
block settings are checked against the field list a block (or plugin) declares. Stdlib only.
"""

import re

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
RESERVED_SLUGS = frozenset({"modules", "assets", "plugins", "_halbach", "api", "admin", "404", "robots", "sitemap", "preview"})
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
FUNK_FX = (None, "none", "ascii", "matrix", "synthwave", "stars", "rain")
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
    return out
