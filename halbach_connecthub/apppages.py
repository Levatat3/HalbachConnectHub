"""An area's own pages (tier 3 of "areas are sub-sites"): the rules for the app/ folder of a bundle, in one place.

When the owner allows it, an area may send a small self-contained site: HTML pages, scripts, stylesheets, JSON data and
pictures in an app/ folder, with "app": {"entry": "app/index.html", "label": "Play"} in area.json. The builder publishes the
folder at /<area>/app/ and puts a security header on it (app_csp below): the pages run in a browser sandbox with no access to
the site's storage, cookies or forms, and can load nothing but the area's own files.

What the owner reviews is code that can be read: htmlcheck, jscheck and csscheck refuse hidden, packed or encoded code. The
checks run when the bundle arrives, again when the owner edits it, and again at every build. Stdlib only.
"""
import json
import posixpath
import re
from pathlib import Path

from . import csscheck, htmlcheck, jscheck
from . import siteschema as sc

APP_DIR = "app"
APP_EXT = {"html", "js", "css", "json", "txt", "png", "jpg", "jpeg", "gif", "webp"}
TEXT_EXT = {"html", "js", "css", "json", "txt"}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_APP_FILES = 200
MAX_TEXT_BYTES = 300 * 1024
ENTRY_RE = re.compile(r"^app/[A-Za-z0-9_()+,@~/. \-]+\.html$")
MAX_LABEL = 40
BAD_TEXT = jscheck.BAD_CHARS



def app_csp(origins):
    """The policy sent with every file under /<area>/app/. `sandbox allow-scripts` (no allow-same-origin) makes the pages an opaque
    origin: no localStorage, no cookies, no reading of the site. The rest lets them load only files from the owner's own site and
    send nothing anywhere else.

    Allowed sources are 'self' plus the site's own addresses written out (`origins`). Browsers disagree about what 'self' means for
    a sandboxed page that has no origin of its own, so naming the site's addresses as well keeps the page's own scripts and styles
    loading everywhere. Both only ever mean the owner's own site."""
    src = " ".join(["'self'"] + list(origins))
    return ("sandbox allow-scripts; default-src 'none'; script-src %s 'unsafe-inline'; style-src %s 'unsafe-inline'; "
            "img-src %s data:; font-src %s; connect-src %s; base-uri 'none'; form-action 'none'; frame-ancestors %s") % ((src,) * 6)


class Result:
    def __init__(self):
        self.problems, self.warnings = [], []

    @property
    def ok(self):
        return not self.problems


def name_problem(name):
    """The extension rule for a file under app/ (names were already checked by bundle.check_name). None when fine."""
    base = name.rsplit("/", 1)[-1].lower()
    parts = base.split(".")
    if len(parts) < 2:
        return "'%s' has no file type" % name[:60]
    ext = parts[-1]
    # "a.js.png" and friends: a script or page type hidden inside a name is refused; "app.min.js" is not (min is not a type)
    hidden = [p for p in parts[1:-1] if p in {"html", "htm", "xhtml", "js", "mjs", "svg", "xml", "exe", "dll", "bat", "cmd", "ps1", "vbs", "php", "py", "sh"}]
    if hidden:
        return "'%s' hides a program or script type inside its name" % name[:60]
    if ext not in APP_EXT:
        return "'%s' is not an allowed file type for the area's own pages (allowed: %s)" % (name[:60], ", ".join(sorted(APP_EXT)))
    return None


def clean_app(value, files):
    """Validate the "app" section of area.json. Returns ({"entry", "label"}, [problems])."""
    if not isinstance(value, dict):
        return None, ["area.json needs an \"app\" section (\"entry\": \"app/index.html\", \"label\": \"Play\") for the files in app/"]
    errors = []
    for k in value:
        if k not in ("entry", "label"):
            errors.append("app has a setting that does not exist (%s)" % str(k)[:30])
    entry = value.get("entry")
    if not isinstance(entry, str) or not ENTRY_RE.match(entry) or ".." in entry.split("/") or "//" in entry:
        errors.append("app.entry must be a page in the app folder, such as app/index.html")
        entry = None
    elif entry not in files:
        errors.append("app.entry %s is not in the bundle" % entry[:60])
        entry = None
    try:
        label = sc.clean_line(value.get("label"), MAX_LABEL)
    except sc.FieldError as e:
        errors.append("app.label: %s" % e)
        label = ""
    if not label:
        errors.append("app.label is the name shown in the area's menu (1 to %d characters)" % MAX_LABEL)
    if errors:
        return None, errors
    return {"entry": entry, "label": label}, []


def tree_files(root):
    """{"app/x/y.html": Path} for every regular file under <root>/app (links are never followed)."""
    base = Path(root) / APP_DIR
    out = {}
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*")):
        if f.is_symlink() or not f.is_file():
            continue
        out[APP_DIR + "/" + f.relative_to(base).as_posix()] = f
    return out


def check_tree(root):
    """Read and check every file of <root>/app with the rules for its type. Returns a Result; problem sentences start with the file."""
    res = Result()
    found = tree_files(root)
    if len(found) > MAX_APP_FILES:
        res.problems.append("the area's own pages have %d files; the limit is %d" % (len(found), MAX_APP_FILES))
        return res
    names = set(found)
    for rel, path in found.items():
        ext = rel.rsplit(".", 1)[-1].lower()
        bad = name_problem(rel)
        if bad:
            res.problems.append(bad)
            continue
        if ext in IMAGE_EXT:
            continue                                         # pictures were verified when they arrived
        try:
            data = path.read_bytes()
        except OSError:
            res.problems.append("%s cannot be read" % rel)
            continue
        if len(data) > MAX_TEXT_BYTES:
            res.problems.append("%s is larger than %d KB" % (rel, MAX_TEXT_BYTES // 1024))
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            res.problems.append("%s is not UTF-8 text" % rel)
            continue
        if ext == "html":
            r = htmlcheck.check(text, rel, names)
        elif ext == "js":
            r = jscheck.check(text)
        elif ext == "css":
            r = csscheck.check(text, names, rel_base=posixpath.dirname(rel))
        elif ext == "json":
            r = Result()
            try:
                json.loads(text)
            except ValueError:
                r.problems.append("it is not valid JSON")
        else:
            r = Result()
            if BAD_TEXT.search(text):
                r.problems.append("it contains hidden or control characters")
        res.problems += ["%s: %s" % (rel, p) for p in r.problems]
        res.warnings += ["%s: %s" % (rel, w) for w in r.warnings]
    return res
