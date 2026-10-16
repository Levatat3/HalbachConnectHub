import re
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import publisher as pub
from halbach_connecthub import store as st


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.stage = Path(self._t.name) / "stage"
        self.stage.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def write(self, rel, text):
        p = self.stage / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def names(self):
        return sorted(p.relative_to(self.stage).as_posix() for p in self.stage.rglob("*") if p.is_file())


class TestHashAssets(Base):
    def page(self):
        return (self.stage / "index.html").read_text(encoding="utf-8")

    def test_scripts_and_styles_get_content_hashed_names(self):
        self.write("index.html", '<link rel="stylesheet" href="css/site.css?v=4"><script src="js/app.js"></script>')
        self.write("css/site.css", "body{}")
        self.write("js/app.js", "console.log(1)")
        mapping = pub.hash_assets(self.stage)
        self.assertEqual(set(mapping), {"css/site.css", "js/app.js"})
        html = self.page()
        self.assertRegex(html, r'href="css/site\.[0-9a-f]{10}\.css"')
        self.assertRegex(html, r'src="js/app\.[0-9a-f]{10}\.js"')
        self.assertNotIn("?v=", html)
        for new in mapping.values():
            self.assertIn(new, self.names())
        self.assertNotIn("js/app.js", self.names())               # originals removed
        self.assertEqual((self.stage / mapping["js/app.js"]).read_text(), "console.log(1)")

    def test_hash_depends_on_content_only(self):
        self.write("index.html", '<script src="a.js"></script>')
        self.write("a.js", "one")
        first = pub.hash_assets(self.stage)["a.js"]
        self.write("index.html", '<script src="a.js"></script>')
        self.write("a.js", "one")
        (self.stage / first).unlink()
        self.assertEqual(pub.hash_assets(self.stage)["a.js"], first)       # same bytes, same name
        self.write("index.html", '<script src="a.js"></script>')
        self.write("a.js", "two")
        self.assertNotEqual(pub.hash_assets(self.stage)["a.js"], first)    # changed bytes, new name

    def test_external_missing_and_escaping_references_are_left_alone(self):
        outside = self.stage.parent / "outside.js"
        outside.write_text("x")
        html = ('<script src="https://cdn.example.com/x.js"></script><script src="//cdn.example.com/y.js"></script>'
                '<script src="missing.js"></script><script src="../outside.js"></script>')
        self.write("index.html", html)
        self.assertEqual(pub.hash_assets(self.stage), {})
        self.assertEqual(self.page(), html)

    def test_pages_in_subfolders_and_root_relative_paths(self):
        self.write("index.html", '<script src="/pit/app.js"></script>')
        self.write("pit/index.html", '<script src="app.js"></script><script src="vendor/three.min.js"></script>')
        self.write("pit/app.js", "A")
        self.write("pit/vendor/three.min.js", "T")
        pub.hash_assets(self.stage)
        self.assertRegex(self.page(), r'src="/pit/app\.[0-9a-f]{10}\.js"')
        sub = (self.stage / "pit" / "index.html").read_text()
        self.assertRegex(sub, r'src="app\.[0-9a-f]{10}\.js"')
        self.assertRegex(sub, r'src="vendor/three\.min\.[0-9a-f]{10}\.js"')
        self.assertEqual([n for n in self.names() if n in ("pit/app.js", "pit/vendor/three.min.js")], [])

    def test_shared_file_is_hashed_once(self):
        self.write("index.html", '<script src="a.js"></script>')
        self.write("two.html", '<script src="a.js"></script>')
        self.write("a.js", "same")
        mapping = pub.hash_assets(self.stage)
        self.assertEqual(len(mapping), 1)
        new = Path(mapping["a.js"]).name
        self.assertIn(new, self.page())
        self.assertIn(new, (self.stage / "two.html").read_text())

    def test_original_kept_if_another_file_still_names_it(self):
        self.write("index.html", '<script src="js/a.js"></script><script src="js/loader.js"></script>')
        self.write("js/a.js", "A")
        self.write("js/loader.js", 'fetch("a.js")')
        mapping = pub.hash_assets(self.stage)
        self.assertIn("js/a.js", self.names())                      # something loads it by name at run time
        self.assertIn(mapping["js/a.js"], self.names())

    def test_single_quotes_and_attribute_order(self):
        self.write("index.html", "<script defer src='a.js' async></script><link href='s.css' rel='stylesheet'>")
        self.write("a.js", "1")
        self.write("s.css", "2")
        pub.hash_assets(self.stage)
        self.assertRegex(self.page(), r"src='a\.[0-9a-f]{10}\.js'")
        self.assertRegex(self.page(), r"href='s\.[0-9a-f]{10}\.css'")

    def test_non_asset_links_are_untouched(self):
        html = '<a href="page.js.html">x</a><link rel="icon" href="fav.png"><script>var s = "a.js";</script>'
        self.write("index.html", html)
        self.write("a.js", "1")
        self.assertEqual(pub.hash_assets(self.stage), {})
        self.assertEqual(self.page(), html)

    def test_no_pages_means_no_changes(self):
        self.write("a.js", "1")
        self.assertEqual(pub.hash_assets(self.stage), {})
        self.assertEqual(self.names(), ["a.js"])


class TestBuildStageUsesIt(unittest.TestCase):
    def test_site_build_hashes_assets_but_not_module_pages(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            site = tmp / "site"
            (site / "js").mkdir(parents=True)
            (site / "index.html").write_text('<script src="js/site.js?v=3"></script>')
            (site / "js" / "site.js").write_text("site")
            mod = tmp / "mod"
            mod.mkdir()
            (mod / "index.html").write_text('<script src="own.js"></script>')
            (mod / "own.js").write_text("own")
            s = st.Store(tmp / "hub")
            s.set_settings(site_dir=str(site), project="p")
            m = s.add_module("static", "Mod", str(mod))
            s.set_on(m["id"], True)
            stage = tmp / "stage"
            man = pub.build_stage(s, stage)
            html = (stage / "index.html").read_text()
            self.assertRegex(html, r'src="js/site\.[0-9a-f]{10}\.js"')
            self.assertFalse((stage / "js" / "site.js").exists())
            self.assertEqual((stage / "modules" / "mod" / "index.html").read_text(), '<script src="own.js"></script>')
            self.assertTrue((stage / "modules" / "mod" / "own.js").exists())
            self.assertEqual(man["modules"][0]["entry"], "modules/mod/index.html")
            self.assertTrue((stage / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
