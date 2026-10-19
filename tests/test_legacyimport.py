import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from halbach_connecthub import __main__ as entry
from halbach_connecthub import legacyimport as li
from halbach_connecthub import registry as rg
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st

import os

REAL = Path(os.environ.get("HALBACH_TEST_CLASSIC_SITE", "nonexistent-folder"))   # point at a real classic site to run the extra test

# A tiny classic site: home/game/about are standard tabs, 'zen' is not, so it must come out as plain text.
INDEX_HTML = """<!doctype html>
<html><head><title>SYNTH SITE</title><meta name="description" content="A synthetic classic site."></head>
<body>
<span class="sym">S</span>
<nav class="tabs">
  <a data-tab="home" class="on">HOME</a>
  <a data-tab="game">GAME</a>
  <a data-tab="about">ABOUT</a>
  <a data-tab="zen">ZEN</a>
</nav>
<section id="home">
  <p class="prompt">ready_</p>
  <span id="typed" data-text="Hello there"></span>
  <a class="card" data-tab="game"><b>Play</b><span>The game, in your browser</span></a>
</section>
<section id="game"><h2>THE GAME</h2><p class="lede">Jump around.</p></section>
<section id="about"><h2>ABOUT</h2><p class="lede">Just me and a text editor.</p></section>
<section id="zen"><h2>ZEN</h2><p class="lede">Breathe in, breathe out.</p></section>
</body></html>
"""

SITE_CONFIG = """// the classic site's settings
window.SITE = {
  name: "SYNTH",
  tagline: "A synthetic classic site",
  handle: "synth",
  links: [
    { label: "Mail", url: "mailto:me@example.com" },
  ],
};
"""


def write_classic_site(folder):
    folder = Path(folder)
    (folder / "js").mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (folder / "js" / "site-config.js").write_text(SITE_CONFIG, encoding="utf-8")
    return folder


class TestJsReader(unittest.TestCase):
    def test_reads_comments_unquoted_keys_and_trailing_commas(self):
        src = """// hi
window.SITE = {
  name: "A // not a comment", /* block */ list: [1, 2.5, 'x',], flag: true, nest: { a: null, },
  url: "https://example.com/x",
};"""
        d = li.parse_js_object(src)
        self.assertEqual(d["name"], "A // not a comment")
        self.assertEqual(d["list"], [1, 2.5, "x"])
        self.assertEqual(d["nest"], {"a": None})
        self.assertEqual(d["url"], "https://example.com/x")

    def test_garbage_is_an_error_not_a_hang(self):
        with self.assertRaises((ValueError, IndexError)):
            li.parse_js_object("window.SITE = { a: ??? }")


@unittest.skipUnless((REAL / "index.html").is_file(), "set HALBACH_TEST_CLASSIC_SITE to a classic site folder to run this")
class TestRealSite(unittest.TestCase):
    def test_imports_into_a_valid_model(self):
        model, notes = li.import_classic(REAL, live_tab_modules=[("pets", "pets", "PETS")])
        ids = [p["id"] for p in model["pages"]]
        self.assertEqual(ids[:6], ["home", "game", "live", "videos", "forge", "more"])
        self.assertEqual(ids[6], "pets")
        self.assertEqual(model["brand"]["name"], "LEVMORIUM")
        self.assertEqual(model["pages"][1]["slug"], "play")
        with tempfile.TemporaryDirectory() as t:
            m = sm.SiteModel(t, rg.Registry())
            m.data = model
            errs, _ = m.validate(module_index={"pets": ("live", "live")})
            self.assertEqual(errs, [])


class TestImportReport(unittest.TestCase):
    """The importer says what it understood: one row per tab, and which tabs were only plain text."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.site = write_classic_site(Path(self._t.name) / "site")

    def tearDown(self):
        self._t.cleanup()

    def test_report_holds_the_block_recipe_of_every_tab(self):
        report = []
        model, notes = li.import_classic(self.site, report=report)
        by = {r["tab"]: r for r in report}
        self.assertEqual(list(by), ["home", "game", "about", "zen"])
        self.assertEqual(by["home"]["blocks"], ["hero", "cards"])
        self.assertEqual(by["game"]["blocks"], ["text", "ruffle-game"])
        self.assertEqual(by["about"]["blocks"], ["text", "links"])
        self.assertEqual(by["zen"]["blocks"], ["text"])
        self.assertTrue(by["zen"]["plain"])                     # not a standard tab
        self.assertFalse(by["about"]["plain"])                  # a standard tab kept its own recipe
        self.assertEqual([p["id"] for p in model["pages"]], ["home", "game", "about", "zen"])

    def test_format_report_is_a_table_that_flags_the_plain_text_tab(self):
        report = []
        _, notes = li.import_classic(self.site, report=report)
        lines = li.format_report(report, notes)
        self.assertIn("BLOCK RECIPE", lines[0])
        self.assertRegex("\n".join(lines), r"(?m)^home\s+hero \+ cards$")
        self.assertRegex("\n".join(lines), r"(?m)^game\s+text \+ ruffle-game$")
        self.assertEqual([ln for ln in lines if ln.startswith("Plain text")],
                         ["Plain text (worth a look): zen"])
        self.assertTrue(any(ln.startswith("note:") and "'zen'" in ln for ln in lines))

    def test_no_report_list_is_fine_and_the_model_still_validates(self):
        model, notes = li.import_classic(self.site)
        self.assertEqual([p["id"] for p in model["pages"]], ["home", "game", "about", "zen"])
        with tempfile.TemporaryDirectory() as t:
            m = sm.SiteModel(t, rg.Registry())
            m.data = model
            errs, _ = m.validate(module_index={})
            self.assertEqual(errs, [])

    def test_empty_report_formats_without_fuss(self):
        self.assertIn("TAB", "\n".join(li.format_report([], [])))


class TestCliImportClassic(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        root = Path(self._t.name)
        self.site = write_classic_site(root / "site")
        self.home = root / "hub"
        st.Store(self.home).set_settings(site_dir=str(self.site), project="p")

    def tearDown(self):
        self._t.cleanup()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = entry.main(["site", "--home", str(self.home)] + list(args))
        return code, buf.getvalue()

    def test_dry_run_prints_the_summary_and_saves_nothing(self):
        code, out = self.run_cli("import-classic", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertRegex(out, r"(?m)^home\s+hero \+ cards$")
        self.assertIn("Plain text (worth a look): zen", out)
        self.assertIn("Nothing was saved", out)
        self.assertFalse((self.home / "site" / "site.json").exists())
        self.assertNotEqual(ss.SiteService(st.Store(self.home)).model.data["brand"]["name"], "SYNTH")

    def test_import_prints_the_same_table_and_saves_the_site(self):
        code, out = self.run_cli("import-classic")
        self.assertEqual(code, 0, out)
        self.assertIn("BLOCK RECIPE", out)
        self.assertIn("Plain text (worth a look): zen", out)
        saved = ss.SiteService(st.Store(self.home)).model.data
        self.assertEqual(saved["brand"]["name"], "SYNTH")
        self.assertEqual([p["id"] for p in saved["pages"]], ["home", "game", "about", "zen"])

    def test_dry_run_refuses_when_no_site_folder_is_set(self):
        other = Path(self._t.name) / "nosite"
        st.Store(other).set_settings(project="p")
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = entry.main(["site", "--home", str(other), "import-classic", "--dry-run"])
        self.assertEqual(code, 1, buf.getvalue())
        self.assertIn("site folder", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
