import random
import string
import unittest

from halbach_connecthub import siteschema as sc


class TestSlugs(unittest.TestCase):
    def test_valid(self):
        for s, want in (("", ""), ("/", ""), ("forge", "forge"), ("/forge/", "forge"), ("tools/forge-2", "tools/forge-2"), ("a/b/c", "a/b/c")):
            self.assertEqual(sc.validate_slug(s), want, s)

    def test_invalid(self):
        for s in ("Forge", "has space", "a/b/c/d", "../x", "a//b", "x\\y", "x.html", "-x", "x" * 41, "modules", "assets/x",
                  "_halbach", "api", "404", "a\x00b", "é", "preview", "robots"):
            with self.assertRaises(sc.FieldError, msg=repr(s)):
                sc.validate_slug(s)

    def test_slugify(self):
        self.assertEqual(sc.slugify("Funk's Workshop!"), "funks-workshop")
        self.assertEqual(sc.slugify("  ---  "), "")

    def test_id(self):
        self.assertEqual(sc.validate_id("pit-forge"), "pit-forge")
        for bad in ("", "Pit", "a b", "-a", "x" * 41):
            with self.assertRaises(sc.FieldError):
                sc.validate_id(bad)


class TestUrls(unittest.TestCase):
    def test_accepted(self):
        cases = {"https://example.com": "https://example.com", "http://example.com/a?b=1#c": "http://example.com/a?b=1#c",
                 "mailto:me@example.com": "mailto:me@example.com", "#top": "#top", "/forge/": "/forge/", "forge/": "/forge/",
                 "https://example.com:8080/x": "https://example.com:8080/x", "": ""}
        for u, want in cases.items():
            self.assertEqual(sc.clean_url(u), want, u)

    def test_refused(self):
        for u in ("javascript:alert(1)", "JaVaScRiPt:x", "data:text/html,x", "vbscript:x", "//evil.com", "file:///etc/passwd",
                  "ftp://x.com", "https://user:pw@example.com", "https://", "https://exa mple.com", "/a/../b", "..", "a\\b",
                  "https://example.com/\x00", "mailto:not-an-email", "x" * 2100, "tel:123", "blob:x"):
            with self.assertRaises(sc.FieldError, msg=repr(u)):
                sc.clean_url(u)

    def test_no_relative_when_forbidden(self):
        with self.assertRaises(sc.FieldError):
            sc.clean_url("/x", allow_relative=False)


class TestText(unittest.TestCase):
    def test_lines_lose_control_characters_and_are_capped(self):
        self.assertEqual(sc.clean_line("a\x00b\nc\x07", 10), "a b c")
        self.assertEqual(len(sc.clean_line("x" * 500, 50)), 50)
        self.assertEqual(sc.clean_line(None), "")

    def test_block_text_keeps_newlines(self):
        self.assertEqual(sc.clean_block_text("a\r\nb\x00\tc"), "a\nb\tc")

    def test_css_vars(self):
        self.assertEqual(sc.clean_css_var("--accent", "#7cc4ff"), ("--accent", "#7cc4ff"))
        self.assertEqual(sc.clean_css_var("--mono", 'ui-monospace, "Cascadia Mono", monospace')[0], "--mono")
        for n, v in (("accent", "#fff"), ("--a;b", "#fff"), ("--x", "red; } body { display:none"), ("--x", "url(http://evil)"),
                     ("--x", "expression(alert(1))"), ("--x", "@import 'x'"), ("--x", "</style>"), ("--x", "x" * 300), ("--x", "")):
            with self.assertRaises(sc.FieldError, msg=(n, v)):
                sc.clean_css_var(n, v)


FIELDS = [
    {"key": "title", "label": "Title", "type": "text", "required": True, "max": 20},
    {"key": "body", "label": "Body", "type": "markdown"},
    {"key": "n", "label": "Count", "type": "number", "default": 5, "min": 1, "max": 10},
    {"key": "on", "label": "On", "type": "bool", "default": True},
    {"key": "mode", "label": "Mode", "type": "select", "options": ["a", "b"], "default": "a"},
    {"key": "tags", "label": "Tags", "type": "list-text"},
    {"key": "links", "label": "Links", "type": "links"},
    {"key": "cards", "label": "Cards", "type": "cards"},
    {"key": "mod", "label": "Module", "type": "module", "modules": ["live"]},
    {"key": "color", "label": "Colour", "type": "color"},
]


class TestProps(unittest.TestCase):
    def ctx(self):
        return sc.SchemaContext(module_ok=lambda m, kinds: m == "bots" and (not kinds or "live" in kinds),
                                page_ok=lambda p: p in ("home", "about"))

    def test_defaults_and_unknown_keys(self):
        clean, errs = sc.validate_props(FIELDS, {"title": "Hi", "evil": "<script>"}, self.ctx())
        self.assertEqual(errs, [])
        self.assertEqual(clean["n"], 5)
        self.assertTrue(clean["on"])
        self.assertEqual(clean["mode"], "a")
        self.assertEqual(clean["tags"], [])
        self.assertNotIn("evil", clean)

    def test_required(self):
        _, errs = sc.validate_props(FIELDS, {}, self.ctx())
        self.assertEqual([e["key"] for e in errs], ["title"])
        _, errs = sc.validate_props(FIELDS, {"title": "   "}, self.ctx())
        self.assertEqual([e["key"] for e in errs], ["title"])

    def test_each_type_rejects_bad_input(self):
        bad = {"title": "ok", "n": 99, "mode": "z", "color": "red", "mod": "nope",
               "links": [{"label": "x", "url": "javascript:1"}], "cards": [{"title": "t", "page": "missing"}]}
        _, errs = sc.validate_props(FIELDS, bad, self.ctx())
        self.assertEqual(sorted(e["key"] for e in errs), ["cards", "color", "links", "mod", "mode", "n"])

    def test_good_values_are_cleaned(self):
        good = {"title": "Hello\x00 world " + "x" * 50, "tags": "a, b ,a,,c", "n": "7", "on": "false", "mode": "b",
                "links": [{"label": "", "url": "https://example.com"}, {"label": "skip", "url": ""}],
                "cards": [{"title": "Go", "text": "t", "page": "about"}, {"title": "Ext", "url": "https://x.org"}],
                "mod": "bots", "color": "#ABCDEF"}
        clean, errs = sc.validate_props(FIELDS, good, self.ctx())
        self.assertEqual(errs, [])
        self.assertEqual(len(clean["title"]), 20)
        self.assertEqual(clean["tags"], ["a", "b", "c"])
        self.assertEqual((clean["n"], clean["on"], clean["mode"]), (7, False, "b"))
        self.assertEqual(clean["links"], [{"label": "https://example.com", "url": "https://example.com"}])
        self.assertEqual(clean["cards"][0], {"title": "Go", "text": "t", "page": "about"})
        self.assertEqual(clean["cards"][1], {"title": "Ext", "text": "", "url": "https://x.org"})
        self.assertEqual(clean["color"], "#abcdef")

    def test_hostile_strings_never_survive_as_markup_carriers(self):
        """URLs are the only place a value could be executed; every string field is just text."""
        evil = ["javascript:alert(1)", "JAVASCRIPT:alert(1)", " javascript:alert(1)", "java\tscript:alert(1)", "data:text/html;base64,AAAA",
                "//evil.example", "https://a.com\"><script>", "https://a.com' onmouseover='x", "/x\"><img src=x>", "vbscript:x"]
        for u in evil:
            clean, errs = sc.validate_props([{"key": "u", "label": "U", "type": "url"}], {"u": u}, self.ctx())
            if not errs:
                self.assertNotIn("<", clean["u"])
                self.assertNotIn('"', clean["u"])
                self.assertNotIn("'", clean["u"])
                self.assertFalse(clean["u"].lower().startswith(("javascript", "data", "vbscript")))

    def test_random_input_never_crashes_and_stays_bounded(self):
        rnd = random.Random(7)
        alphabet = string.printable + "\x00\x1f‮﻿<>\"'&"
        for _ in range(300):
            props = {f["key"]: "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 5000))) for f in FIELDS}
            clean, errs = sc.validate_props(FIELDS, props, self.ctx())
            for v in clean.values():
                if isinstance(v, str):
                    self.assertLessEqual(len(v), 20000)

    def test_non_dict_props(self):
        clean, errs = sc.validate_props(FIELDS, "garbage", self.ctx())
        self.assertEqual([e["key"] for e in errs], ["title"])


if __name__ == "__main__":
    unittest.main()
