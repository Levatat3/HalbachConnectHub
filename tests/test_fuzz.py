"""Property/fuzz tests: random input, fixed seeds, the same answer every run.

Four promises, a few hundred seeded iterations each (stdlib random only, the whole file runs in
a few seconds):

1. Hostile text in every text-ish field of every block, in page labels/titles/descriptions and in
   the brand never becomes markup: the built pages hold exactly the script tags the builder
   writes, and html.parser sees exactly the tags and attributes a clean build has.
2. validate_slug either refuses a random address or returns one that is safe to use as a path.
3. parse_area_doc only ever raises BundleError for random documents (random nesting, wrong types,
   huge numbers), and a well-formed document full of hostile text is accepted, not refused.
4. validate_bundle only ever raises BundleError for random bytes in a zip, and never leaves a
   folder behind.
"""

import html
import json
import os
import random
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from halbach_connecthub import bundle
from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import siteschema as sc
from halbach_connecthub import store as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk

SEED_TEXT, SEED_SLUG, SEED_DOC, SEED_ZIP = 1701, 1702, 1703, 1704
REG = rg.Registry()
AREA = {"id": "workshop", "slug": "workshop", "blocks": list(bundle.DEFAULT_BLOCKS),
        "limits": {"max_file_mb": 5, "max_bundle_mb": 10, "max_total_mb": 20, "max_files": 20, "max_image_px": 4000,
                   "extensions": ["md", "txt", "png", "jpg", "jpeg", "gif", "webp", "pdf"]}}
TEXTISH = frozenset({"text", "longtext", "markdown", "list-text", "links", "cards"})

# Strings that try to become markup, become an attribute, or hide a control character.
HOSTILE = [
    "<script>alert(1)</script>",
    "</script><script>alert(2)</script>",
    "<SCRIPT SRC=//evil.example/x.js></SCRIPT>",
    "<img src=x onerror=alert(1)>",
    "</title><script>alert(1)</script>",
    "</a><a href=\"javascript:alert(1)\">click</a>",
    "<!--<script>alert(1)</script>-->",
    "&lt;script&gt;alert(1)&lt;/script&gt;",
    "\" onmouseover=\"alert(1)",
    "' onfocus='alert(1)' autofocus='x",
    "</script>",
    "javascript:alert(1)",
    "`><svg onload=alert(1)>",
    "\"'><iframe src=//evil.example>",
    "z\x00s\x00cript",
    "\x07\x1b[31mred\x1b[0m",
    "\u202eGNP.EXE\u202c file",
    "\u200b\u200d\u00adhidden",
    "\ufeff<script>alert(1)</script>",
    "]]><script>alert(1)</script>",
    "Ünïcödé 日本語 emoji 🎉 ß",
    "<b>bold</b> <i>italic</i>",
]
_POOL = ("<", ">", "\"", "'", "/", "\\", "=", " ", "\t", "\n", "\r", "\x00", "a", "Z", "0", "-", "_",
         "&", ";", "(", ")", "{", "}", "`", "!", "%", "#", "?", "|", "~", ".", ":", "é", "あ", "😀",
         "\u202e", "\u0301", "\ufeff")


def _raw_text(rnd):
    if rnd.random() < 0.4:
        return rnd.choice(HOSTILE)
    n = rnd.choice([1, 2, 3, 6, 12, 40, 150, 600, 4000])          # includes the very long case
    return "".join(rnd.choice(_POOL) for _ in range(n))


def nasty(rnd):
    """A hostile string that is still non-empty after cleaning, so required fields stay filled."""
    return "z" + _raw_text(rnd)


def fuzzable_blocks(reg):
    """Blocks with a text-ish field that can sit on a built site.

    Left out: blocks without any text field (their settings are not text), and blocks whose
    required field is not text-ish -- a built site can only check those against a real module or
    area, so hostile text never reaches them (dropbox is the one block with text fields that
    needs an area id a built model can never have).
    """
    out = []
    for t in sorted(reg.plugins["block"]):
        plug = reg.block(t)
        fields = plug.manifest.get("fields", []) if plug else []
        if not any(f["type"] in TEXTISH for f in fields):
            continue
        if any(f.get("required") and f["type"] not in TEXTISH for f in fields):
            continue
        out.append(t)
    return out


def expected_text_fields(reg):
    """Every (block, key) pair the fuzz has to reach at least once."""
    out = set()
    for t in fuzzable_blocks(reg):
        for f in reg.block(t).manifest.get("fields", []):
            if f["type"] in TEXTISH:
                out.add((t, f["key"]))
    return out


def block_props(fields, gen):
    """One block's settings: a hostile value in every text-ish field, valid addresses elsewhere."""
    props = {}
    for f in fields:
        key, t = f["key"], f["type"]
        if t in ("text", "longtext", "markdown"):
            props[key] = gen()
        elif t == "list-text":
            props[key] = [gen(), gen()]
        elif t == "links":
            props[key] = [{"label": gen(), "url": "https://example.com/one"}]
        elif t == "cards":
            props[key] = [{"title": gen(), "text": gen(), "url": "https://example.com/two"}]
    return props


def fill_model(model, reg, gen, blocks=None):
    """One whole model with gen() in every text-ish slot; returns the (block, key) pairs it set.

    blocks=None fills every fuzzable block; pass a list to spread them over iterations.
    """
    model.load_template("blank")
    model.set_brand(name=gen(), mark=gen(), tagline=gen(), handle=gen(), footer=gen())
    home = model.data["pages"][0]
    model.update_page(home["id"], label=gen(), title=gen(), description=gen())
    for i in (1, 2):
        model.add_page(gen(), slug="fuzz-%d" % i, title=gen(), description=gen())
    model.set_nav_links([{"label": gen(), "url": "https://example.com/nav%d" % i} for i in range(2)])
    model.add_linked(gen(), "https://example.com/linked", description=gen())
    covered = set()
    for t in (fuzzable_blocks(reg) if blocks is None else blocks):
        fields = reg.block(t).manifest.get("fields", [])
        model.add_block(home["id"], t, block_props(fields, gen))
        covered.update((t, f["key"]) for f in fields if f["type"] in TEXTISH)
    return covered


class _Survey(HTMLParser):
    """What a built page really is: every tag, every attribute name, every piece of text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.text = set(), set(), []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag)
        for name, _ in attrs:
            self.attrs.add((tag, name))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        self.text.append(data)


def survey(text):
    s = _Survey()
    s.feed(text)
    s.close()
    return frozenset(s.tags), frozenset(s.attrs), "".join(s.text)


def read_pages(stage):
    """{relative path: text} for every built HTML page."""
    return {p.relative_to(stage).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(stage.rglob("*.html"))}


_RUNTIME_TAG = re.compile(r'<script src="/_halbach/runtime\.[0-9a-f]{10}\.js" defer>')
_PAGE_JSON_TAG = '<script type="application/json" id="hs-page">'
_SCRIPT_TAGS = re.compile(r"<script\b[^>]*>", re.I)


def script_problem(rel, text):
    """None if the page holds exactly the script tags the builder writes, else a description."""
    found = _SCRIPT_TAGS.findall(text)
    wanted = 1 if rel == "404.html" else 2                       # 404 has no page data block
    if len(found) != wanted or text.lower().count("<script") != wanted or text.lower().count("</script>") != wanted:
        return "%d script tags (%d expected): %r" % (len(found), wanted, found)
    if sum(1 for f in found if _RUNTIME_TAG.fullmatch(f)) != 1:
        return "the runtime script tag is missing or repeated: %r" % found
    if found.count(_PAGE_JSON_TAG) != (0 if rel == "404.html" else 1):
        return "the page data script tag is missing or repeated: %r" % found
    return None


# --------------------------------------------------------------------------- 1. hostile text
class TestHostileText(unittest.TestCase):
    """Hostile text anywhere ends up in the page as inert data, never as markup."""

    ITERATIONS = 200

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.model = sm.SiteModel(self.root / "hub", REG)
        self.store = st.Store(self.root / "hub")
        self.stage = self.root / "stage"
        # the same shape with plain text: this is what every fuzzed page must look like
        self.assertEqual(fill_model(self.model, REG, lambda: "Plain text"), expected_text_fields(REG))
        res = sb.build_site(self.model, REG, self.store, self.stage)
        self.assertTrue(res.ok, res.errors)
        self.pages = read_pages(self.stage)
        self.expected = {rel: survey(text) for rel, text in self.pages.items()}
        self.assertTrue(self.expected)

    def tearDown(self):
        self._t.cleanup()

    def test_hostile_text_in_every_field_never_becomes_markup(self):
        # One block type per iteration (the fuzzed values land in the page JSON either way; the
        # HTML structure is identical, which the baseline in setUp already proved), so the whole
        # file stays fast and every block type still gets its turn.
        rnd = random.Random(SEED_TEXT)
        fuzzable = fuzzable_blocks(REG)
        covered = set()
        for i in range(self.ITERATIONS):
            model = sm.SiteModel(self.root / "hub", REG)
            covered |= fill_model(model, REG, lambda: nasty(rnd), blocks=[fuzzable[i % len(fuzzable)]])
            res = sb.build_site(model, REG, self.store, self.stage)
            self.assertTrue(res.ok, "iteration %d: %s" % (i, res.errors))
            pages = read_pages(self.stage)
            self.assertEqual(sorted(pages), sorted(self.expected), "iteration %d: other html files" % i)
            for rel, text in pages.items():
                problem = script_problem(rel, text)
                if problem:
                    self.fail("iteration %d, %s: %s" % (i, rel, problem))
                tags, attrs, _ = survey(text)
                want_tags, want_attrs, _ = self.expected[rel]
                self.assertEqual(tags, want_tags, "iteration %d, %s: the tag set changed" % (i, rel))
                self.assertEqual(attrs, want_attrs, "iteration %d, %s: an attribute was injected" % (i, rel))
            name = model.data["brand"]["name"]
            _, _, text_blob = survey(pages["index.html"])
            self.assertIn(name, text_blob, "iteration %d: the brand name never reached the page" % i)
            self.assertIn(html.escape(name, quote=True), pages["index.html"])
        self.assertEqual(covered, expected_text_fields(REG), "a text-ish field was never fuzzed")


# --------------------------------------------------------------------------- 2. random slugs
_SLUG_PIECES = ["a", "page", "x9", "-", "", "/", "//", "..", "../", "..\\", "\\", "modules", "Modules",
                "404", " ", "\x00", "é", "😀", "c-" * 30, "\t", "robots", "A", "0", "a/b", "z" * 41,
                "%2e%2e", "a//b", "PREVIEW"]


def random_slug(rnd):
    if rnd.random() < 0.15:                                       # some of them have to be acceptable
        return "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(rnd.randrange(1, 41)))
    return "".join(rnd.choice(_SLUG_PIECES) for _ in range(rnd.randrange(1, 6)))


class TestRandomSlugs(unittest.TestCase):
    def test_a_random_slug_is_refused_or_safe_to_use_as_a_path(self):
        rnd = random.Random(SEED_SLUG)
        accepted = 0
        for _ in range(500):
            slug = random_slug(rnd)
            try:
                out = sc.validate_slug(slug)
            except sc.FieldError:
                continue                                          # refused: always allowed
            accepted += 1
            self.assertIsInstance(out, str)
            self.assertNotIn("\\", out, "%r -> %r" % (slug, out))
            self.assertNotIn("..", out, "%r -> %r" % (slug, out))
            self.assertNotIn("//", out, "%r -> %r" % (slug, out))
            self.assertFalse(out.startswith("/") and out != "", "%r -> %r" % (slug, out))
            self.assertFalse(out.endswith("/"), "%r -> %r" % (slug, out))
            self.assertEqual(out, out.lower(), "%r -> %r" % (slug, out))
            self.assertFalse(sc._CONTROL_ALL.search(out), "%r -> %r" % (slug, out))
            if out == "":
                continue                                          # the home address: no path at all
            parts = out.split("/")
            self.assertLessEqual(len(parts), sc.MAX_SLUG_DEPTH)
            if parts[0]:
                self.assertNotIn(parts[0], sc.RESERVED_SLUGS, slug)
            for part in parts:
                self.assertTrue(sc.SEGMENT_RE.match(part), "%r -> %r" % (slug, out))
        self.assertGreater(accepted, 0, "the fuzz never let a slug through")


# --------------------------------------------------------------------------- 3. random area.json
def _doc_text(rnd):
    """Hostile text that still passes the address rules (no ':' for a scheme, no files/ path)."""
    return nasty(rnd).replace(":", ";")


EXPECTED_HEADING = "z<script>alert(1)</script>"
_BLOCK_TYPES = tuple(sorted(bundle.ALLOWABLE_BLOCKS)) + ("nonsense", "<script>", "5", 5, None, ["hero"])


def valid_doc(rnd):
    """A well-formed document full of hostile text: it must be accepted, not refused."""
    t = lambda: _doc_text(rnd)
    blocks = [{"type": "hero", "props": {"heading": EXPECTED_HEADING, "prompt": t(), "tagline": t(), "typed": False}},
              {"type": "text", "props": {"markdown": t()}},
              {"type": "cards", "props": {"items": [{"title": t(), "text": t(), "url": "https://example.com/c"}]}},
              {"type": "links", "props": {"items": [{"label": t(), "url": "https://example.com/l"}], "style": "buttons"}},
              {"type": "divider", "props": {"label": t()}}]
    return {"format": 1, "title": t(), "description": t(),
            "pages": [{"name": "", "title": t(), "blocks": blocks},
                      {"name": "second", "title": t(), "blocks": []}]}


_DOC_KEYS = ("format", "title", "description", "pages", "blocks", "props", "type", "name", "items",
             "label", "markdown", "heading", "url", "id", "target", "channel", "<script>", "a b")


def _random_leaf(rnd):
    pick = rnd.random()
    if pick < 0.35:
        return nasty(rnd)
    if pick < 0.55:
        return rnd.choice([0, 1, -1, 42, True, False, 10 ** rnd.randrange(1, 300),
                           -(10 ** rnd.randrange(1, 300)), 2 ** rnd.randrange(1, 600)])
    if pick < 0.7:
        return rnd.choice([0.0, 1e300, -1e-300, float("inf"), float("nan"), rnd.random()])
    if pick < 0.8:
        return None
    if pick < 0.9:
        return "".join(rnd.choice(_POOL) for _ in range(rnd.randrange(500, 4000)))
    return rnd.choice(["", "{}", "[]", "1", "<script>x</script>", "files/a.png", "javascript:alert(1)"])


def _random_json(rnd, depth=0):
    """Any JSON value at all: random nesting, wrong types, huge numbers."""
    if depth >= 3 or rnd.random() < 0.5:
        return _random_leaf(rnd)
    if rnd.random() < 0.5:
        return [_random_json(rnd, depth + 1) for _ in range(rnd.randrange(0, 4))]
    return {rnd.choice(_DOC_KEYS): _random_json(rnd, depth + 1) for _ in range(rnd.randrange(0, 4))}


def _props_node(rnd):
    if rnd.random() < 0.2:
        return rnd.choice([None, "props", [], 5, [{}]])
    props = {}
    for _ in range(rnd.randrange(0, 5)):
        props[rnd.choice(_DOC_KEYS)] = _random_leaf(rnd)
    return props


def _blocks_node(rnd):
    if rnd.random() < 0.25:
        return rnd.choice([None, "blocks", 7, {}, [1, 2]])
    out = []
    for _ in range(rnd.randrange(0, 5)):
        if rnd.random() < 0.2:
            out.append(rnd.choice(["block", 5, None, [], "hero"]))
            continue
        out.append({"type": rnd.choice(_BLOCK_TYPES), "props": _props_node(rnd)})
    return out


def _pages_node(rnd):
    if rnd.random() < 0.25:
        return rnd.choice([None, "pages", 7, {}, [1, 2, 3], True])
    out = []
    for i in range(rnd.randrange(1, 4)):
        if rnd.random() < 0.2:
            out.append(rnd.choice(["page", 5, None, [], {"blocks": []}]))
            continue
        out.append({"name": rnd.choice(["", "second", "p%d" % i, _doc_text(rnd), None, 5, ["x"]]),
                    "title": rnd.choice([_doc_text(rnd), None, 7, ["t"], True]),
                    "blocks": _blocks_node(rnd)})
    return out


def _doc_skeleton(rnd):
    """A document shaped like area.json with random wrong types dropped into each key."""
    return {"format": rnd.choice([1, 1, 1, 1.0, True, "1", 2, None, [1], 10 ** rnd.randrange(1, 100)]),
            "title": rnd.choice([_doc_text(rnd), None, 5, ["a"], {"x": 1}, True]),
            "description": rnd.choice([_doc_text(rnd), None, 3.5, [], {}]),
            "pages": _pages_node(rnd),
            "<script>": rnd.choice(["ignored", 5, None])}


class TestRandomAreaDoc(unittest.TestCase):
    ITERATIONS = 400

    def setUp(self):
        self.files = {"files/a.png": {"size": 1, "sha256": "0" * 64}}

    def test_only_bundleerror_comes_out(self):
        rnd = random.Random(SEED_DOC)
        accepted = rejected = 0
        for i in range(self.ITERATIONS):
            must_accept = i % 10 == 9
            if must_accept:
                doc = valid_doc(rnd)
            elif rnd.random() < 0.45:
                doc = _random_json(rnd)
            else:
                doc = _doc_skeleton(rnd)
            raw = json.dumps(doc, allow_nan=True).encode("utf-8")
            try:
                out = bundle.parse_area_doc(raw, AREA, REG, self.files, [])
            except bundle.BundleError as e:
                rejected += 1
                if must_accept:
                    self.fail("iteration %d refused a well-formed document: %s" % (i, "; ".join(e.reasons)))
            except Exception as e:                                # noqa: BLE001 - the point of the test
                self.fail("iteration %d raised %s instead of BundleError: %r\ndocument: %.400r"
                          % (i, type(e).__name__, e, doc))
            else:
                accepted += 1
                if must_accept:
                    self.assertEqual(out["format"], bundle.FORMAT)
                    self.assertEqual(len(out["pages"]), 2)
                    self.assertEqual(out["pages"][0]["blocks"][0]["props"]["heading"], EXPECTED_HEADING)
        self.assertGreater(accepted, 0, "nothing was ever accepted")
        self.assertGreater(rejected, 0, "nothing was ever refused")


# --------------------------------------------------------------------------- 4. random zip bytes
class TestRandomZipBytes(unittest.TestCase):
    ITERATIONS = 300
    NAMES = ["area.json", "files/a.png", "../evil.png", "files/../../x.png", "/etc/passwd", "c:x.png",
             "..\\evil.png", "files/a\x00.png", "files/‮png.exe", "files//x.png", "files/.",
             "files/CON.png", "files/a .png", "EVIL.HTML", "files/ünï.png",
             "files/" + "d" * 3 + "/" + "x" * 80 + ".png"]

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = REG
        self.zip = self.root / "in.zip"
        self.valid = self.root / "valid.zip"
        zk.make_zip(self.valid, [("area.json", zk.area_json()), ("files/a.png", zk.png())])
        self.base = self.valid.read_bytes()

    def tearDown(self):
        self._t.cleanup()

    def _random_zip(self, rnd):
        mode = rnd.randrange(5)
        if mode == 0:                                             # plain random bytes
            return rnd.randbytes(rnd.randrange(0, 3000))
        if mode == 1:                                             # random bytes behind a zip signature
            return rnd.choice([b"PK\x03\x04", b"PK\x05\x06", b"PK\x01\x02", b"PK\x07\x08"]) + rnd.randbytes(rnd.randrange(0, 3000))
        if mode == 2:                                             # a real bundle, cut short
            return self.base[:rnd.randrange(0, len(self.base))]
        if mode == 3:                                             # a real bundle with bytes flipped
            data = bytearray(self.base)
            for _ in range(rnd.randrange(1, 8)):
                data[rnd.randrange(len(data))] = rnd.randrange(256)
            return bytes(data)
        seen = set()
        entries = []
        for n in rnd.sample(self.NAMES, rnd.randrange(1, 5)):
            key = n.replace("\\", "/")                         # ZipInfo turns '\' into '/' on Windows
            if key in seen:
                continue                                       # else writestr would warn about a duplicate
            seen.add(key)
            entries.append((n, zk.png() if n.lower().endswith(".png") else rnd.randbytes(rnd.randrange(0, 500))))
        if rnd.random() < 0.5 and "area.json" not in seen:
            entries.append(("area.json", rnd.randbytes(rnd.randrange(0, 500))))
        zk.make_zip(self.zip, entries)
        return self.zip.read_bytes()

    def test_random_bytes_only_raise_bundleerror_and_leave_nothing_behind(self):
        rnd = random.Random(SEED_ZIP)
        for i in range(self.ITERATIONS):
            self.zip.write_bytes(self._random_zip(rnd))
            dest = self.root / ("tree%d" % i)
            try:
                bundle.validate_bundle(self.zip, dest, AREA, self.reg)
            except bundle.BundleError:
                self.assertFalse(dest.exists(), "iteration %d left %s behind" % (i, dest.name))
                self.assertFalse(Path(str(dest) + ".part").exists(), "iteration %d left %s.part behind" % (i, dest.name))
                continue
            except Exception as e:                                # noqa: BLE001 - the point of the test
                self.fail("iteration %d raised %s instead of BundleError: %r" % (i, type(e).__name__, e))
            self.assertTrue((dest / "area.json").is_file(), "iteration %d unpacked nothing" % i)
            shutil.rmtree(dest, ignore_errors=True)               # only a genuinely well-formed bundle may unpack
        self.assertEqual([p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("tree")], [],
                         "a folder was left behind")


if __name__ == "__main__":
    unittest.main()
