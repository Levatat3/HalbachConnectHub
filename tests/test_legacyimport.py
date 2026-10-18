import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import legacyimport as li
from halbach_connecthub import registry as rg
from halbach_connecthub import sitemodel as sm

import os

REAL = Path(os.environ.get("HALBACH_TEST_CLASSIC_SITE", "nonexistent-folder"))   # point at a real classic site to run the extra test


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


if __name__ == "__main__":
    unittest.main()
