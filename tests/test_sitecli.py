import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from halbach_connecthub import __main__ as entry
from halbach_connecthub import quality
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st


def run(*args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = entry.main(list(args))
    return code, buf.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.home = self.root / "hub"
        svc = ss.SiteService(st.Store(self.home))
        svc.apply("template.load", {"id": "personal"})

    def tearDown(self):
        self._t.cleanup()

    def test_check_reports_advice_and_exits_zero_when_nothing_blocks(self):
        code, out = run("site", "--home", str(self.home), "check")
        self.assertEqual(code, 0, out)
        self.assertIn("placeholder", out)              # the template's example text is called out

    def test_build_writes_the_site_and_refuses_a_broken_one(self):
        out_dir = self.root / "out"
        code, out = run("site", "--home", str(self.home), "build", "--out", str(out_dir))
        self.assertEqual(code, 0, out)
        self.assertTrue((out_dir / "index.html").is_file())
        svc = ss.SiteService(st.Store(self.home))
        svc.model.data["pages"][0]["enabled"] = False
        svc.model.save("break")
        code, out = run("site", "--home", str(self.home), "build", "--out", str(self.root / "out2"))
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "out2" / "index.html").exists())

    def test_export_then_import_round_trips(self):
        f = self.root / "site.json"
        self.assertEqual(run("site", "--home", str(self.home), "export", str(f))[0], 0)
        pkg = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(pkg["format"], "halbach-site")
        pkg["model"]["brand"]["name"] = "CHANGED"
        f.write_text(json.dumps(pkg), encoding="utf-8")
        code, out = run("site", "--home", str(self.home), "import", str(f))
        self.assertEqual(code, 0, out)
        self.assertEqual(ss.SiteService(st.Store(self.home)).model.data["brand"]["name"], "CHANGED")

    def test_import_refuses_junk_and_newer_versions_and_leaves_the_site_alone(self):
        bad = self.root / "bad.json"
        for content in ('{"nope": 1}', "not json", json.dumps({"format": "halbach-site", "version": 99, "model": {"pages": []}}),
                        json.dumps({"format": "halbach-site", "version": 1, "model": {"pages": [{"blocks": [0] * 500}]}})):
            bad.write_text(content, encoding="utf-8")
            self.assertEqual(run("site", "--home", str(self.home), "import", str(bad))[0], 1, content[:30])
        self.assertEqual(ss.SiteService(st.Store(self.home)).model.data["brand"]["name"], "PERSONAL SITE")


class TestQuality(unittest.TestCase):
    def test_contrast_maths(self):
        self.assertAlmostEqual(quality.contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertLess(quality.contrast("#777777", "#808080"), 1.5)
        self.assertIsNone(quality.contrast("red", "#fff"))

    def test_low_contrast_theme_is_flagged_and_good_one_is_not(self):
        data = {"pages": [], "settings": {"csp": "enforce", "indexable": True, "base_url": "https://x.y"}}
        bad = quality.check_site(data, {"--text": "#777777", "--bg": "#808080", "--muted": "#888888", "--accent": "#999999"})
        self.assertTrue(any("hard to read" in w["message"] for w in bad))
        good = quality.check_site(data, {"--text": "#ffffff", "--bg": "#000000", "--muted": "#cccccc", "--accent": "#7cc4ff"})
        self.assertEqual(good, [])


if __name__ == "__main__":
    unittest.main()
