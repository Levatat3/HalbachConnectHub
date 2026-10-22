import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from halbach_connecthub import csscheck

FILES = {"files/bg.png", "files/logo.jpg", "files/my pic.webp"}


def chk(text, files=FILES, slug=None):
    return csscheck.check(text, files, slug)


class TestPlainCssPasses(unittest.TestCase):
    def test_an_ordinary_readable_stylesheet_passes_unchanged_in_meaning(self):
        css = """
/* my site */
:root { --accent: #ff8800; }
body { background: #112233 url("files/bg.png") no-repeat; font-family: "Georgia", serif; }
.hero h1 { letter-spacing: .08em; text-shadow: 0 0 8px rgba(255,136,0,.6); }
a:hover, a:focus-visible { color: var(--accent); }
.card::before { content: "\\201C"; }
@media (max-width: 600px) { .hero h1 { font-size: 1.6rem; } }
@keyframes pulse { 0% { opacity: .6; } 100% { opacity: 1; } }
@supports (display: grid) { .grid { display: grid; } }
"""
        r = chk(css)
        self.assertTrue(r.ok, r.problems)
        self.assertIn('url("files/bg.png")', r.css)
        self.assertIn('"\\201C"', r.css)

    def test_pictures_become_the_areas_own_addresses_and_only_those(self):
        r = csscheck.check('a{background:url(files/bg.png)} b{cursor:url( \'files/logo.jpg\' ), auto} c{background:url("files/my pic.webp")}',
                           FILES, "funkas-workshop", "/preview")
        self.assertTrue(r.ok, r.problems)
        self.assertIn('url("/preview/funkas-workshop/files/bg.png")', r.css)
        self.assertIn('url("/preview/funkas-workshop/files/logo.jpg")', r.css)
        self.assertIn('url("/preview/funkas-workshop/files/my pic.webp")', r.css)

    def test_text_that_only_looks_like_a_url_inside_quotes_is_left_alone(self):
        r = chk('a::after{content:"see url(files/x.png) and data: here"}')
        self.assertTrue(r.ok, r.problems)
        self.assertIn("see url(files/x.png) and data: here", r.css)

    def test_position_fixed_and_a_huge_z_index_are_allowed_but_flagged_for_the_reviewer(self):
        r = chk(".bar{position:fixed;z-index:99999}")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.warnings), 2, r.warnings)


class TestHostileCssIsRefused(unittest.TestCase):
    CASES = {
        "import": '@import url("https://evil.example/x.css");',
        "import string": '@import "x.css";',
        "outside picture": "a{background:url(https://evil.example/x.png)}",
        "protocol relative": "a{background:url(//evil.example/x.png)}",
        "data address": "a{background:url(data:image/png;base64,AAAA)}",
        "data address in quotes": 'a{background:url("data:image/svg+xml;utf8,<svg/>")}',
        "svg file": "a{background:url(files/x.svg)}",
        "path up": "a{background:url(files/../../x.png)}",
        "missing picture": "a{background:url(files/nothere.png)}",
        "fragment filter": "a{filter:url(#x)}",
        "escaped url": "a{background:u\\72l(https://evil.example/x)}",
        "escaped property": "a{\\62ackground:red}",
        "escaped in selector": ".\\61 {color:red}",
        "bad string escape": 'a::after{content:"\\6"}x{}\na{content:"\\q"}',
        "expression": "a{width:expression(alert(1))}",
        "behavior": "a{behavior:url(x.htc)}",
        "moz binding": "a{-moz-binding:url(x.xml#y)}",
        "javascript": "a{background:javascript:alert(1)}",
        "image-set": 'a{background:image-set("x.png" 1x)}',
        "webkit image-set": 'a{background:-webkit-image-set(url(files/bg.png) 1x)}',
        "image()": 'a{background:image("x.png")}',
        "src()": 'a{background:src("x.png")}',
        "attr()": "a{background:attr(data-x url)}",
        "paint worklet": "a{background:paint(evil)}",
        "namespace": "@namespace url(http://www.w3.org/1999/xhtml);",
        "document rule": "@-moz-document url-prefix() {a{color:red}}",
        "charset": '@charset "utf-8"; a{}',
        "html comment": "<!-- a{color:red} -->",
        "closing style tag": "a{}</style><script>alert(1)</script>",
        "unclosed comment": "a{color:red} /* x",
        "unbalanced": "a{color:red",
        "unclosed string": 'a{content:"abc}\nb{}',
        "hidden character": "a{color:red}\u202e",
        "zero width": "a{co\u200blor:red}",
        "non ascii identifier": "a{c\u043elor:red}",
        "control character": "a{color:red}\x01",
    }

    def test_every_hostile_stylesheet_is_refused_with_a_reason(self):
        for name, css in self.CASES.items():
            with self.subTest(name=name):
                r = chk(css)
                self.assertFalse(r.ok, "was accepted: " + name)
                self.assertTrue(r.problems and all(isinstance(p, str) and p for p in r.problems))
                self.assertEqual(r.css, "")

    def test_a_path_that_leaves_the_files_folder_is_refused_even_when_nobody_checks_the_file_exists(self):
        for bad in ("files/../../x.png", "files//x.png", "files/./x.png", "files/a/../b.png"):
            with self.subTest(bad=bad):
                self.assertFalse(csscheck.check("a{background:url(%s)}" % bad, None).ok)

    def test_the_font_face_rule_may_use_installed_fonts_but_its_urls_are_still_checked(self):
        self.assertTrue(chk('@font-face{font-family:"Mine";src:local("Segoe UI")}').ok)
        self.assertFalse(chk('@font-face{font-family:"Mine";src:url(https://evil.example/f.woff2)}').ok)


class TestObfuscationAndEncodingAreRefused(unittest.TestCase):
    def test_long_encoded_strings_are_refused(self):
        blob = base64.b64encode(os.urandom(600)).decode()
        r = chk('a::after{content:"%s"}' % blob)
        self.assertFalse(r.ok)
        self.assertIn("encoded", " ".join(r.problems))

    def test_long_unquoted_runs_are_refused(self):
        r = chk("a{--payload:%s}" % base64.b64encode(os.urandom(200)).decode().replace("=", "A"))
        self.assertFalse(r.ok)

    def test_a_packed_single_line_is_refused_but_a_long_readable_one_only_warns(self):
        packed = "a{color:red}" * 500                     # 6000 characters on one line
        self.assertFalse(chk(packed).ok)
        readable = ("a{color:red}" * 80)                  # 960 characters
        r = chk(readable)
        self.assertTrue(r.ok)
        self.assertTrue(any("long line" in w for w in r.warnings))

    def test_a_huge_comment_hiding_text_is_refused(self):
        self.assertFalse(chk("/*%s*/a{}" % ("x " * 3000)).ok)

    def test_too_big_or_too_deep(self):
        self.assertFalse(chk("a{color:red}\n" * 12000).ok)
        self.assertFalse(chk("@media x{" * 8 + "a{}" + "}" * 8).ok)

    def test_backslashes_are_the_main_hiding_place_so_none_are_allowed_outside_quotes(self):
        for css in ("a{color:\\72ed}", "\\40 import 'x';", "a{content:\\22 x}"):
            with self.subTest(css=css):
                self.assertFalse(chk(css).ok)


if __name__ == "__main__":
    unittest.main()
