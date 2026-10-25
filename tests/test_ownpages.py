"""The owner's own pages (settings -> Your own pages): off by default, plain files in <hub>/site/app/, published sandboxed at /app/."""
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st
from halbach_connecthub.sitemodel import ModelError

INDEX = "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Mine</title><link rel='stylesheet' href='style.css'></head><body><h1>Hi</h1><script src='main.js'></script></body></html>"


class OwnPages(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.store = st.Store(self.root / "hub")
        self.store.state["settings"].update(domain="example.com", project="demo")
        self.svc = ss.SiteService(self.store)
        self.svc.model.load_template("blank")
        self.stage = self.root / "stage"

    def tearDown(self):
        self._t.cleanup()

    def put_site(self):
        self.svc.own_write("app/index.html", INDEX)
        self.svc.own_write("app/style.css", "body { color: #333; }")
        self.svc.own_write("app/main.js", "document.title = 'ok';")

    def build(self):
        return sb.build_site(self.svc.model, self.svc.registry, self.store, self.stage)

    def headers(self, path):
        return sb.headers_for(sb.parse_headers((self.stage / "_headers").read_text(encoding="utf-8")), path)

    def test_off_by_default_and_nothing_is_published(self):
        self.assertFalse(self.svc.model.data["settings"]["own_pages"])
        self.put_site()
        res = self.build()
        self.assertTrue(res.ok)
        self.assertFalse((self.stage / "app").exists())
        self.assertNotIn("hs-app", (self.stage / "index.html").read_text(encoding="utf-8") if (self.stage / "index.html").exists() else "")

    def test_switched_on_it_is_published_sandboxed_and_linked(self):
        self.put_site()
        self.svc.apply("settings", {"own_pages": True, "own_pages_label": "Tools"})
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertEqual((self.stage / "app" / "index.html").read_text(encoding="utf-8"), INDEX)
        self.assertTrue((self.stage / "app" / "main.js").is_file())
        h = self.headers("/app/index.html")
        self.assertIn("sandbox allow-scripts", h["Content-Security-Policy"])
        self.assertNotIn("Content-Security-Policy-Report-Only", h)
        self.assertFalse(any("sandbox" in v for v in self.headers("/").values()))      # the rest of the site is not sandboxed
        page = next(p for p in res.pages)
        self.assertIn('class="hs-app" href="/app/">Tools</a>', (self.stage / page["file"]).read_text(encoding="utf-8"))

    def test_no_front_page_means_left_out_with_a_warning(self):
        self.svc.own_write("app/style.css", "body{}")
        self.svc.apply("settings", {"own_pages": True})
        res = self.build()
        self.assertTrue(res.ok)
        self.assertFalse((self.stage / "app").exists())
        self.assertTrue(any("front page" in w["message"] for w in res.warnings))
        self.assertTrue(any("front page" in w["message"] for w in self.svc.check()["warnings"]))

    def test_code_that_fails_the_checks_is_left_out_not_published(self):
        self.put_site()
        self.svc.own_write("app/main.js", "eval('1+1');")
        self.svc.apply("settings", {"own_pages": True})
        res = self.build()
        self.assertFalse((self.stage / "app").exists())
        self.assertTrue(any("left out" in w["message"] for w in res.warnings))
        self.assertTrue(self.svc.own_view()["problems"])

    def test_a_page_called_app_collides(self):
        self.put_site()
        self.svc.apply("settings", {"own_pages": True})
        with self.assertRaises(ModelError):
            self.svc.apply("page.add", {"label": "App", "slug": "app"})

    def test_files_stay_inside_the_app_folder(self):
        for bad in ("index.html", "app/../x.html", "../app/x.html", "app/x.exe", "app/a.js.html", "/etc/x.html", "app" + chr(92) + "x.html", "app/x.svg"):
            with self.assertRaises(ModelError, msg=bad):
                self.svc.own_write(bad, "x")
        with self.assertRaises(ModelError):
            self.svc.own_read("app/missing.html")
        with self.assertRaises(ModelError):
            self.svc.own_write("app/big.html", "x" * (400 * 1024))
        with self.assertRaises(ModelError):
            self.svc.own_write("app/pic.png", "not text")

    def test_read_write_delete_round_trip_and_tidies_folders(self):
        self.svc.own_write("app/sub/deep/page.html", "<p>x</p>")
        self.assertEqual(self.svc.own_read("app/sub/deep/page.html")["text"], "<p>x</p>")
        v = self.svc.own_delete("app/sub/deep/page.html")
        self.assertEqual(v["files"], [])
        self.assertFalse((self.svc.own_root() / "app" / "sub").exists())
        self.assertTrue((self.svc.own_root() / "app").is_dir())

    def test_empty_menu_name_is_refused_and_label_is_trimmed(self):
        with self.assertRaises(ModelError):
            self.svc.apply("settings", {"own_pages_label": "   "})
        self.svc.apply("settings", {"own_pages_label": "  Games  "})
        self.assertEqual(self.svc.model.data["settings"]["own_pages_label"], "Games")


if __name__ == "__main__":
    unittest.main()
