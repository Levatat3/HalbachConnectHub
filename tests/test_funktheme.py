import hashlib
import http.client
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from halbach_connecthub import funkthemes, server
from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import siteschema as sc
from halbach_connecthub import store as st

ENGINE = Path(sb.__file__).parent / "builtin" / "themes" / "funk" / "funktheme.js"
# Funk's engine is used exactly as he wrote it (FunkThemes 2026-10-17). If he sends a new version, update this
# fingerprint (of the file with line endings normalised) in the same commit that replaces the file.
ENGINE_SHA256 = "667532814e4ab9a825e43fae1e4af9c670cd069fb6319ff49d26c2ebce329dc7"

GOOD = {"funkTheme": 1, "angle": 135, "accent": None, "blur": 0, "fx": "ascii", "name": "Funk", "style": "gradient",
        "colors": ["#462a50", "#2f0a66"], "strength": 0.45, "fxColor": "#c604fb", "fxSpeed": 2.6, "fxAmount": 0.88}


class TestEngine(unittest.TestCase):
    def test_engine_is_unmodified(self):
        data = ENGINE.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), ENGINE_SHA256)

    def test_engine_makes_no_network_calls_and_runs_no_dynamic_code(self):
        src = ENGINE.read_text(encoding="utf-8")
        for bad in ("fetch(", "XMLHttpRequest", "eval(", "new Function", "WebSocket", "sendBeacon", "importScripts"):
            self.assertNotIn(bad, src)


class TestValidator(unittest.TestCase):
    def test_a_real_funk_theme_is_accepted_and_canonical(self):
        out = sc.clean_funktheme(GOOD)
        self.assertEqual(out["name"], "Funk")
        self.assertEqual(out["colors"], ["#462a50", "#2f0a66"])
        self.assertEqual(out["fx"], "ascii")
        self.assertNotIn("image", out)

    def test_the_three_themes_funk_shipped_all_validate(self):
        for f in (Path(__file__).parent.parent / "third_party" / "funkthemes" / "themes").glob("*.funktheme"):
            sc.clean_funktheme(json.loads(f.read_text(encoding="utf-8")))

    def test_refusals(self):
        bad = [
            {**GOOD, "funkTheme": 2}, {**GOOD, "name": ""}, {**GOOD, "style": "image", "image": "data:image/png;base64,AAAA"},
            {**GOOD, "colors": ["#12345"]}, {**GOOD, "colors": ['red")}body{background:url(//evil.example/x)']},
            {**GOOD, "colors": ["#fff"] * 9}, {**GOOD, "fx": "fire"}, {**GOOD, "strength": 5}, {**GOOD, "fxSpeed": "fast"},
            {**GOOD, "accent": ["url(x)"]}, {**GOOD, "fxColor": "#zzz"}, "text", None, [],
        ]
        for b in bad:
            with self.assertRaises(sc.FieldError, msg=str(b)[:60]):
                sc.clean_funktheme(b)

    def test_unknown_keys_are_dropped(self):
        out = sc.clean_funktheme({**GOOD, "evil": "x", "image": "http://evil.example/a.png", "tint": "javascript:1"})
        self.assertNotIn("evil", out)
        self.assertIsNone(out["tint"])
        self.assertEqual(out["blur"], 0)


class TestModelAndBuild(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = rg.Registry()
        self.m = sm.SiteModel(self.root / "hub", self.reg)
        self.m.load_template("personal")
        self.store = st.Store(self.root / "hub")

    def tearDown(self):
        self._t.cleanup()

    def test_funk_data_only_lives_with_the_funk_theme(self):
        self.m.set_theme(theme_id="funk", funk=GOOD)
        self.assertEqual(self.m.data["theme"]["funk"]["name"], "Funk")
        self.m.set_theme(theme_id="neutral-dark")
        self.assertNotIn("funk", self.m.data["theme"])
        with self.assertRaises(sm.ModelError):
            self.m.set_theme(theme_id="funk", funk={**GOOD, "style": "image"})

    def test_the_site_builds_with_the_engine_and_the_theme_data(self):
        self.m.set_theme(theme_id="funk", funk=GOOD)
        res = sb.build_site(self.m, self.reg, self.store, self.root / "stage")
        self.assertTrue(res.ok, res.errors)
        pj = json.loads(__import__("re").search(r'id="hs-page">(.*?)</script>', (self.root / "stage" / "index.html").read_text(encoding="utf-8"), __import__("re").S).group(1))
        self.assertEqual(pj["theme"]["id"], "funk")
        self.assertEqual(pj["theme"]["funk"]["fx"], "ascii")
        self.assertEqual(pj["theme"]["effects"], ["funktheme"])
        script = (self.root / "stage" / pj["assets"]["theme_script"].lstrip("/")).read_text(encoding="utf-8")
        self.assertIn("window.FunkTheme = {", script)                 # the engine
        self.assertIn('registerEffect("funktheme"', script)           # the Halbach glue, after it
        self.assertLess(script.index("window.FunkTheme = {"), script.index('registerEffect("funktheme"'))
        css = next((self.root / "stage" / "_halbach").glob("theme.*.css")).read_text(encoding="utf-8")
        self.assertIn("body.hs-funk::before", css)
        headers = (self.root / "stage" / "_headers").read_text(encoding="utf-8")
        self.assertNotIn("unpkg", headers)                            # the engine needs no outside address

    def test_other_themes_carry_no_funk_data(self):
        res = sb.build_site(self.m, self.reg, self.store, self.root / "stage")
        self.assertTrue(res.ok)
        text = (self.root / "stage" / "index.html").read_text(encoding="utf-8")
        self.assertIn('"funk":null', text.replace(" ", ""))


class TestLibraryAndApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        tmp = Path(cls._t.name)
        cls.lib = tmp / "FunkThemes"
        cls.patch = mock.patch.object(funkthemes, "DIR", str(cls.lib))
        cls.patch.start()
        cls.srv, cls.hub = server.serve(tmp / "hub", 0, token="tok")
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.patch.stop()
        cls._t.cleanup()

    def req(self, method, path, body=None, token=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token:
            h["X-Halbach-Token"] = "tok"
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, raw

    def test_library_roundtrip_in_the_shared_folder(self):
        s, raw = self.req("GET", "/api/funkthemes")
        self.assertEqual((s, json.loads(raw)["themes"]), (200, []))
        s, raw = self.req("POST", "/api/funkthemes/save", {"theme": GOOD})
        self.assertEqual(s, 200)
        self.assertEqual([t["name"] for t in json.loads(raw)["themes"]], ["Funk"])
        self.assertTrue((self.lib / "Funk.funktheme").is_file())
        s, raw = self.req("POST", "/api/funkthemes/delete", {"name": "Funk"})
        self.assertEqual(json.loads(raw)["themes"], [])

    def test_names_cannot_escape_the_folder_and_junk_is_refused(self):
        self.req("POST", "/api/funkthemes/save", {"theme": {**GOOD, "name": "../../escape"}})
        self.assertFalse((self.lib.parent / "escape.funktheme").exists())
        self.assertFalse((self.lib.parent.parent / "escape.funktheme").exists())
        s, _ = self.req("POST", "/api/funkthemes/save", {"theme": {"name": "x"}})
        self.assertEqual(s, 400)
        self.req("POST", "/api/funkthemes/delete", {"name": "../../hub/state"})
        self.assertTrue((self.lib.parent / "hub").is_dir())

    def test_needs_the_token_and_the_engine_is_served_to_the_window(self):
        self.assertEqual(self.req("GET", "/api/funkthemes", token=False)[0], 401)
        s, raw = self.req("GET", "/funktheme.js", token=False)
        self.assertEqual(s, 200)
        self.assertIn(b"window.FunkTheme", raw)


if __name__ == "__main__":
    unittest.main()
