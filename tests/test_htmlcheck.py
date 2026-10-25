import unittest

from halbach_connecthub import htmlcheck

FILES = {"app/index.html", "app/about.html", "app/css/style.css", "app/js/main.js", "app/img/logo.png", "app/data/items.json", "app/sub/index.html"}

PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My tools</title>
<link rel="stylesheet" href="css/style.css">
<style>body { margin: 0; background: #101418 url("img/logo.png"); }</style>
</head>
<body>
<h1 id="t">Hello</h1>
<p><a href="about.html">About</a> <a href="sub/">Sub</a> <a href="https://example.org/docs">Docs</a> <a href="#t">Top</a></p>
<img src="img/logo.png" alt="logo" width="40">
<canvas id="c" width="200" height="100"></canvas>
<button type="button" id="go">Go</button>
<script src="js/main.js" defer></script>
<script>document.getElementById("go").addEventListener("click", function () { console.log("hi"); });</script>
<script type="application/json" id="d">{"a": 1}</script>
</body>
</html>
'''


def check(text, rel="app/index.html"):
    return htmlcheck.check(text, rel, FILES)


class TestAGoodPage(unittest.TestCase):
    def test_a_plain_readable_page_passes(self):
        r = check(PAGE)
        self.assertEqual((r.problems, r.warnings), ([], []))

    def test_references_are_relative_to_the_pages_own_folder(self):
        sub = '<!doctype html><link rel="stylesheet" href="../css/style.css"><a href="../about.html">x</a><img src="../img/logo.png" alt="">'
        self.assertEqual(check(sub, "app/sub/index.html").problems, [])
        self.assertTrue(check(sub.replace("../css", "css"), "app/sub/index.html").problems)       # from sub/ that path does not exist


class TestTagsAndAttributes(unittest.TestCase):
    def test_tags_that_load_or_send_things_are_refused(self):
        for tag in ("<form></form>", '<iframe src="x"></iframe>', "<object></object>", "<embed>", '<base href="/">', "<svg></svg>", "<video></video>", "<my-thing></my-thing>"):
            self.assertTrue(check("<!doctype html>" + tag).problems, tag)

    def test_inline_event_handlers_and_dangerous_attributes_are_refused(self):
        for attr in ('onclick="x()"', 'onload="x()"', 'srcdoc="<b>"', 'formaction="x"', 'ping="x"', 'http-equiv="refresh"'):
            self.assertTrue(check("<!doctype html><div %s></div>" % attr).problems, attr)

    def test_a_meta_refresh_and_odd_meta_names_are_refused(self):
        self.assertTrue(check('<!doctype html><meta http-equiv="refresh" content="0;url=https://evil.example">').problems)
        self.assertTrue(check('<!doctype html><meta name="referrer" content="x">').problems)

    def test_password_and_file_inputs_and_new_tab_links_are_refused(self):
        self.assertTrue(check('<!doctype html><input type="password">').problems)
        self.assertTrue(check('<!doctype html><input type="file">').problems)
        self.assertTrue(check('<!doctype html><a href="https://example.org" target="_blank">x</a>').problems)
        self.assertEqual(check('<!doctype html><input type="range" min="0" max="9"><a href="https://example.org" target="_self">x</a>').problems, [])

    def test_script_types_other_than_plain_scripts_and_json_are_refused(self):
        self.assertTrue(check('<!doctype html><script type="module">var a = 1;</script>').problems)
        self.assertTrue(check('<!doctype html><script type="importmap">{}</script>').problems)
        self.assertTrue(check('<!doctype html><script type="application/json">{nope}</script>').problems)

    def test_comments_conditional_comments_and_processing_instructions(self):
        self.assertTrue(check("<!doctype html><!--[if IE]><p>x</p><![endif]-->").problems)
        self.assertTrue(check("<!doctype html><!-- %s -->" % ("x" * 3100)).problems)
        self.assertTrue(check("<?xml version='1.0'?><!doctype html>").problems)
        self.assertEqual(check("<!doctype html><!-- a normal note --><p>x</p>").problems, [])


class TestAddresses(unittest.TestCase):
    def test_only_the_areas_own_files_may_be_loaded(self):
        for src in ("https://cdn.example/x.js", "//cdn.example/x.js", "/js/main.js", "data:text/javascript,alert(1)", "javascript:alert(1)",
                    "../../x.js", "js/missing.js", "js/main.png"):
            self.assertTrue(check('<!doctype html><script src="%s"></script>' % src).problems, src)
        self.assertTrue(check('<!doctype html><img src="https://x.example/a.png">').problems)
        self.assertTrue(check('<!doctype html><link rel="stylesheet" href="https://x.example/a.css">').problems)
        self.assertTrue(check('<!doctype html><link rel="preload" href="css/style.css">').problems)

    def test_a_link_may_leave_only_as_https_http_or_mailto(self):
        self.assertEqual(check('<!doctype html><a href="mailto:a@b.example">m</a><a href="http://example.org">h</a>').problems, [])
        for href in ("javascript:alert(1)", "data:text/html,x", "ftp://x.example/a", "//evil.example/", "java&#115;cript:alert(1)"):
            self.assertTrue(check('<!doctype html><a href="%s">x</a>' % href).problems, href)

    def test_hidden_whitespace_in_a_scheme_does_not_get_through(self):
        self.assertTrue(check('<!doctype html><a href="java\nscript:alert(1)">x</a>').problems)

    def test_other_tags_may_not_carry_src_or_href(self):
        self.assertTrue(check('<!doctype html><div src="img/logo.png"></div>').problems)
        self.assertTrue(check('<!doctype html><input type="image" src="img/logo.png">').problems)


class TestInlineCodeIsCheckedLikeFiles(unittest.TestCase):
    def test_an_inline_script_gets_the_script_rules(self):
        r = check('<!doctype html><script>eval("1")</script>')
        self.assertTrue(any("inline script" in p and "eval" in p for p in r.problems), r.problems)

    def test_a_style_block_and_a_style_attribute_get_the_stylesheet_rules(self):
        self.assertTrue(any("<style>" in p for p in check('<!doctype html><style>a { background: url("https://x.example/a.png") }</style>').problems))
        self.assertTrue(any("style attribute" in p for p in check('<!doctype html><div style="background:url(https://x.example/a.png)"></div>').problems))
        self.assertTrue(any("style attribute" in p for p in check('<!doctype html><div style="behavior:url(x)"></div>').problems))
        self.assertEqual(check('<!doctype html><div style="color: red; margin: 0"></div>').problems, [])

    def test_a_script_with_both_src_and_a_body_is_refused(self):
        self.assertTrue(check('<!doctype html><script src="js/main.js">var a = 1;</script>').problems)


class TestSizeAndHiddenCharacters(unittest.TestCase):
    def test_huge_hidden_and_packed_pages_are_refused(self):
        self.assertTrue(check("<!doctype html>" + "<p>x</p>\n" * 40000).problems)
        self.assertTrue(check("<!doctype html><p>a" + chr(0x200B) + "b</p>").problems)
        self.assertTrue(check("<!doctype html>" + "<p>x</p>" * 500).problems)               # one 4,000-character line


if __name__ == "__main__":
    unittest.main()
