"""Checks one HTML page of an area's own pages (tier 3 of "areas are sub-sites").

Same rule as for stylesheets and scripts: it passes as written or it is refused with a reason the sender can act on; nothing is
rewritten. The page may only use plain, readable markup: a fixed list of tags, no inline event handlers, no forms, frames,
objects or outside resources. Everything it loads must be one of the area's own files inside the bundle (relative paths that
stay inside app/); the only outside address allowed is a plain link (<a href="https://...">). Inline scripts and styles are
checked with the same rules as script and stylesheet files (jscheck, csscheck).

check(text, rel, files) -> Result(problems, warnings). `rel` is the page's own path in the bundle ("app/index.html"); `files`
is the set of every path in the bundle (None = do not check that referenced files exist). Stdlib only.
"""
import json
import posixpath
import re
from html.parser import HTMLParser

from . import csscheck, jscheck

MAX_HTML_BYTES = 300 * 1024
MAX_ATTR = 2000
MAX_COMMENT = 3000
MAX_ELEMENTS = 5000
MAX_PROBLEMS = 20
BAD_CHARS = jscheck.BAD_CHARS

TAGS = set("""html head body title meta link style script noscript main header footer nav section article aside div span p br hr
h1 h2 h3 h4 h5 h6 a ul ol li dl dt dd table thead tbody tfoot tr td th caption colgroup col img figure figcaption blockquote pre
code kbd samp var em strong b i u s small sub sup mark abbr cite q time del ins button input textarea select option optgroup label
fieldset legend output progress meter details summary canvas""".split())
VOID = {"br", "hr", "img", "input", "meta", "link", "col"}
WHY_TAG = {
    "form": "forms cannot send anything from these pages",
    "iframe": "frames are not allowed", "frame": "frames are not allowed", "frameset": "frames are not allowed",
    "object": "objects and plug-ins are not allowed", "embed": "objects and plug-ins are not allowed", "applet": "objects and plug-ins are not allowed",
    "base": "<base> is not allowed (it changes where every address points)", "svg": "inline SVG is not supported yet (use a png, jpg, gif or webp picture)",
    "math": "this tag is not supported", "template": "this tag is not supported", "audio": "audio and video files are not supported yet",
    "video": "audio and video files are not supported yet", "source": "audio and video files are not supported yet",
    "track": "audio and video files are not supported yet", "dialog": "this tag is not supported", "slot": "this tag is not supported",
}
DENY_ATTRS = {"srcdoc", "formaction", "action", "ping", "srcset", "poster", "data", "background", "manifest", "longdesc", "imagesrcset", "http-equiv"}
URL_TAGS = {"a": "href", "link": "href", "img": "src", "script": "src"}
META_NAMES = {"viewport", "description", "theme-color", "color-scheme", "author"}
LINK_RELS = {"stylesheet", "icon"}
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*:", re.I)


class Result:
    def __init__(self):
        self.problems, self.warnings = [], []

    @property
    def ok(self):
        return not self.problems


def _short(s):
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in str(s))[:40]


class _Page(HTMLParser):
    def __init__(self, rel, files, res):
        super().__init__(convert_charrefs=True)
        self.rel, self.dir, self.files, self.res = rel, posixpath.dirname(rel), files, res
        self.elements, self.cur, self.buf = 0, None, []

    def bad(self, why):
        if len(self.res.problems) < MAX_PROBLEMS:
            self.res.problems.append("line %d: %s" % (self.getpos()[0], why))

    # ---- addresses
    def check_ref(self, tag, value):
        v = (value or "").strip()
        if not v:
            return
        if v.startswith("#"):
            if tag != "a":
                self.bad("<%s> cannot point at a page fragment" % tag)
            return
        if SCHEME.match(v):
            if tag == "a" and v.lower().startswith(("https://", "http://", "mailto:")):
                return
            self.bad("the address %s is not allowed (%s)" % (_short(v), "a link may be https:// or mailto:" if tag == "a" else "load only your own files"))
            return
        if v.startswith("//") or "\\" in v:
            self.bad("the address %s is not allowed (no outside addresses)" % _short(v))
            return
        path = re.split(r"[?#]", v, maxsplit=1)[0]
        if path == "":
            return                                           # only a query: the same page
        if path.startswith("/"):
            self.bad("the address %s must be relative to this page, without a leading /" % _short(v))
            return
        target = posixpath.normpath(posixpath.join(self.dir, path))
        if path.endswith("/"):
            target += "/index.html"
        if not target.startswith("app/") or ".." in target.split("/"):
            self.bad("the address %s points outside your own pages" % _short(v))
            return
        low = target.lower()
        if tag == "link" and not low.endswith(".css") and "icon" not in (self.rel_hint or ""):
            self.bad("%s is not a stylesheet" % _short(v))
        if tag == "script" and not low.endswith(".js"):
            self.bad("%s is not a script (.js)" % _short(v))
        if tag == "img" and not low.endswith(IMAGE_EXT):
            self.bad("%s is not a picture (png, jpg, gif or webp)" % _short(v))
        if self.files is not None and target not in self.files:
            self.bad("%s is not in the bundle" % _short(v))

    rel_hint = ""

    # ---- tags
    def handle_starttag(self, tag, attrs):
        self.elements += 1
        if self.elements == MAX_ELEMENTS + 1:
            self.bad("more than %d elements on one page" % MAX_ELEMENTS)
        if tag not in TAGS:
            self.bad("<%s> is not allowed (%s)" % (_short(tag), WHY_TAG.get(tag, "only plain page tags are allowed")))
            return
        names = {k for k, _ in attrs}
        self.rel_hint = ""
        for k, v in attrs:
            v = v or ""
            if len(v) > MAX_ATTR:
                self.bad("an attribute on <%s> is longer than %d characters" % (tag, MAX_ATTR))
                continue
            kl = k.lower()
            if kl.startswith("on"):
                self.bad("inline event handlers (%s) are not allowed; add listeners in a script file instead" % _short(k))
            elif kl in DENY_ATTRS:
                self.bad("the attribute %s is not allowed" % _short(k))
            elif kl == "style":
                r = csscheck.check("x{%s}" % v, self.files, rel_base=self.dir)
                for p in r.problems:
                    self.bad("style attribute: %s" % p)
            elif kl in ("src", "href"):
                if URL_TAGS.get(tag) != kl:
                    self.bad("<%s> cannot use %s" % (tag, kl))
            elif kl == "target" and tag == "a" and v not in ("_self", ""):
                self.bad("links open in the same tab on these pages (remove target=\"%s\")" % _short(v))
            elif kl == "type" and tag == "input" and v.lower() in ("password", "file"):
                self.bad("<input type=\"%s\"> is not allowed" % _short(v))
            elif kl == "rel" and tag == "link":
                self.rel_hint = v.lower()
        if tag == "meta":
            if "charset" not in names:
                name = dict(attrs).get("name", "")
                if (name or "").lower() not in META_NAMES:
                    self.bad("<meta name=\"%s\"> is not allowed (allowed: %s)" % (_short(name), ", ".join(sorted(META_NAMES))))
        if tag == "link":
            rels = set(re.split(r"\s+", self.rel_hint.strip())) if self.rel_hint else set()
            if not rels or not rels <= LINK_RELS:
                self.bad("<link> may only load a stylesheet or an icon of your own")
        attr = URL_TAGS.get(tag)
        if attr:
            val = dict(attrs).get(attr)
            if val is not None:
                self.check_ref(tag, val)
            elif tag == "link":
                self.bad("<link> needs an href")
        if tag in ("script", "style"):
            self.cur = (tag, dict(attrs), self.getpos()[0])
            self.buf = []
            if tag == "script":
                kind = (dict(attrs).get("type") or "text/javascript").strip().lower()
                if kind not in ("text/javascript", "application/javascript", "application/json", ""):
                    self.bad("<script type=\"%s\"> is not allowed (plain scripts and JSON data only)" % _short(kind))
                if "src" in names and dict(attrs).get("src") is None:
                    self.bad("<script> needs an address in src")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in ("script", "style"):
            self.finish_cur()

    def finish_cur(self):
        if not self.cur:
            return
        tag, attrs, line = self.cur
        text = "".join(self.buf)
        self.cur = None
        if not text.strip():
            return
        if tag == "script":
            if "src" in attrs:
                self.bad("a script with a src must be empty")
            kind = (attrs.get("type") or "text/javascript").strip().lower()
            if kind == "application/json":
                try:
                    json.loads(text)
                except ValueError:
                    self.bad("the JSON data in this <script> is not valid JSON")
            else:
                r = jscheck.check(text)
                for p in r.problems:
                    if len(self.res.problems) < MAX_PROBLEMS:
                        self.res.problems.append("line %d: inline script: %s" % (line, p))
                self.res.warnings += ["line %d: inline script: %s" % (line, w) for w in r.warnings]
        else:
            r = csscheck.check(text, self.files, rel_base=self.dir)
            for p in r.problems:
                if len(self.res.problems) < MAX_PROBLEMS:
                    self.res.problems.append("line %d: <style>: %s" % (line, p))
            self.res.warnings += ["line %d: <style>: %s" % (line, w) for w in r.warnings]

    def handle_endtag(self, tag):
        if self.cur and tag == self.cur[0]:
            self.finish_cur()

    def handle_data(self, data):
        if self.cur:
            self.buf.append(data)

    def handle_comment(self, data):
        if len(data) > MAX_COMMENT:
            self.bad("a comment is longer than %d characters (long hidden text is not allowed)" % MAX_COMMENT)
        if data.lstrip().startswith("[if"):
            self.bad("conditional comments are not allowed")

    def handle_decl(self, decl):
        if decl.strip().lower() != "doctype html":
            self.bad("only <!doctype html> is allowed")

    def handle_pi(self, data):
        self.bad("processing instructions (<? ... >) are not allowed")

    def unknown_decl(self, data):
        self.bad("CDATA sections are not allowed")


def check(text, rel="app/index.html", files=None):
    """Check one page. Returns a Result."""
    res = Result()
    if not isinstance(text, str):
        res.problems.append("the page is not text")
        return res
    if len(text.encode("utf-8", "replace")) > MAX_HTML_BYTES:
        res.problems.append("the page is larger than %d KB" % (MAX_HTML_BYTES // 1024))
        return res
    if BAD_CHARS.search(text):
        res.problems.append("the page contains hidden or control characters")
        return res
    longest = max((len(x) for x in text.splitlines()), default=0)
    if longest > jscheck.MAX_LINE:
        res.problems.append("a line is %d characters long; write the page out over several lines so it can be reviewed (the limit is %d)" % (longest, jscheck.MAX_LINE))
        return res
    parser = _Page(rel, files, res)
    try:
        parser.feed(text)
        parser.close()
    except Exception:                                       # the parser is lenient; anything it cannot take is refused rather than guessed at
        res.problems.append("the page could not be read as HTML")
    parser.finish_cur()
    seen = set()
    res.problems = [p for p in res.problems if not (p in seen or seen.add(p))]
    return res
