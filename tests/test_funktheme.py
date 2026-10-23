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
# Funk's engine is used exactly as he wrote it (FunkThemes 2, the Claude Watch master of 2026-10-22 19:00, 82,428 bytes).
# If he sends a new version, update this fingerprint (of the file with line endings normalised) in the same commit that replaces the file.
ENGINE_SHA256 = "8d9a9cd4757b11ddf9de51bea1de6508bbcb0185b4a0821f6963b31fb9548c67"

GOOD = {"funkTheme": 1, "angle": 135, "accent": None, "blur": 0, "fx": "ascii", "name": "Funk", "style": "gradient",
        "colors": ["#462a50", "#2f0a66"], "strength": 0.45, "fxColor": "#c604fb", "fxSpeed": 2.6, "fxAmount": 0.88}


class TestEngine(unittest.TestCase):
    def test_engine_is_unmodified(self):
        data = ENGINE.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(data).hexdigest(), ENGINE_SHA256)

    def test_engine_makes_no_network_calls_and_runs_no_dynamic_code(self):
        src = ENGINE.read_text(encoding="utf-8")
        for bad in ("fetch(", "XMLHttpRequest", "eval(", "new Function", "WebSocket", "sendBeacon", "importScripts",
                    "localStorage", "sessionStorage", "document.cookie", "getUserMedia"):
            self.assertNotIn(bad, src)

    def test_the_engines_effects_and_controls_match_what_the_validator_accepts(self):
        """If Funk adds an effect or a control, this fails until the validator's whitelist is looked at on purpose."""
        import re
        src = ENGINE.read_text(encoding="utf-8")
        effects = set(re.findall(r'registerFx\("([a-z]+)"', src))
        self.assertEqual(effects, {f for f in sc.FUNK_FX if f and f != "none"})
        self.assertEqual(effects, set(sc.FUNK_FX_VALUES))
        for name, spec in sc.FUNK_FX_VALUES.items():
            block = src[src.index('registerFx("%s"' % name):]
            block = block[:block.index("} });")]
            self.assertEqual(set(re.findall(r"^\s+([a-z]+): \{ type:", block, re.M)), set(spec), name)


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


V2 = {**GOOD, "fx": "spectrum", "fxValues": {"style": "ring", "height": 1.5, "mirror": False, "peaks": True},
      "fxLayers": [{"fx": "rain", "opacity": 0.6, "blend": "screen", "fxSpeed": 1.5, "fxAmount": 0.5, "fxColor": "#00ffcc", "fxValues": {"wind": 0.4, "lightning": False}},
                   {"fx": "stars", "opacity": 1, "blend": "lighten", "fxValues": {"warp": 1}}],
      "motion": {"fps": 60, "half": True, "drift": True, "pauseFullscreen": False, "listen": True}}


class TestFunkThemes2(unittest.TestCase):
    """FunkThemes 2: Spectrum, per-effect settings, up to two stacked effect layers and motion settings."""

    def test_the_new_settings_are_accepted_and_listening_is_always_off_on_a_site(self):
        out = sc.clean_funktheme(V2)
        self.assertEqual(out["fx"], "spectrum")
        self.assertEqual(out["fxValues"], {"style": "ring", "height": 1.5, "mirror": False, "peaks": True})
        self.assertEqual([l["fx"] for l in out["fxLayers"]], ["rain", "stars"])
        self.assertEqual(out["fxLayers"][0]["fxValues"], {"wind": 0.4, "lightning": False})
        self.assertEqual(out["fxLayers"][1]["opacity"], 1.0)
        self.assertEqual(out["motion"], {"fps": 60, "half": True, "drift": True, "pauseFullscreen": False, "listen": False})

    def test_an_old_theme_comes_out_exactly_as_before_with_no_new_keys(self):
        out = sc.clean_funktheme(GOOD)
        for k in ("fxValues", "fxLayers", "motion"):
            self.assertNotIn(k, out)

    def test_defaults_fill_in_for_a_sparse_layer_and_motion(self):
        out = sc.clean_funktheme({**GOOD, "fxLayers": [{"fx": "matrix"}], "motion": {"fps": 15}})
        self.assertEqual(out["fxLayers"], [{"fx": "matrix", "opacity": 1.0, "blend": "normal"}])
        self.assertEqual(out["motion"], {"fps": 15, "half": False, "drift": False, "pauseFullscreen": True, "listen": False})

    def test_values_that_do_not_belong_are_dropped_and_wrong_values_are_refused(self):
        out = sc.clean_funktheme({**GOOD, "fx": "matrix", "fxValues": {"trail": 0.2, "sun": True, "evil": "x", "url": "https://evil.example/"}})
        self.assertEqual(out["fxValues"], {"trail": 0.2})            # 'sun' belongs to synthwave, the rest to nothing
        bad_values = [{"trail": 2}, {"trail": "high"}, {"trail": True}]
        for v in bad_values:
            with self.subTest(v=v), self.assertRaises(sc.FieldError):
                sc.clean_funktheme({**GOOD, "fx": "matrix", "fxValues": v})
        for fx, v in (("spectrum", {"style": "bars; x"}), ("spectrum", {"height": 9}), ("spectrum", {"mirror": "yes"}), ("rain", {"wind": -1}),
                      ("stars", {"warp": 3}), ("ascii", {"mouse": 1}), ("synthwave", {"sun": "true"})):
            with self.subTest(fx=fx, v=v), self.assertRaises(sc.FieldError):
                sc.clean_funktheme({**GOOD, "fx": fx, "fxValues": v})
        with self.assertRaises(sc.FieldError):
            sc.clean_funktheme({**GOOD, "fxValues": "x"})
        self.assertNotIn("fxValues", sc.clean_funktheme({**GOOD, "fx": "matrix", "fxValues": {"trail": None}}))      # null = not set

    def test_layers_and_motion_refusals(self):
        three = [{"fx": "rain"}, {"fx": "stars"}, {"fx": "matrix"}]
        bad = [{"fxLayers": three}, {"fxLayers": "rain"}, {"fxLayers": [{"fx": "none"}]}, {"fxLayers": [{"fx": "fire"}]}, {"fxLayers": [{}]},
               {"fxLayers": ["rain"]}, {"fxLayers": [{"fx": "rain", "blend": "difference"}]}, {"fxLayers": [{"fx": "rain", "blend": "normal; x"}]},
               {"fxLayers": [{"fx": "rain", "opacity": 0}]}, {"fxLayers": [{"fx": "rain", "opacity": 2}]}, {"fxLayers": [{"fx": "rain", "fxColor": "url(x)"}]},
               {"fxLayers": [{"fx": "rain", "fxSpeed": 9}]}, {"fxLayers": [{"fx": "rain", "fxValues": {"wind": 5}}]},
               {"motion": "fast"}, {"motion": {"fps": 24}}, {"motion": {"fps": True}}, {"motion": {"half": "yes"}}, {"motion": {"drift": 1}}]
        for extra in bad:
            with self.subTest(extra=extra), self.assertRaises(sc.FieldError):
                sc.clean_funktheme({**GOOD, **extra})

    def test_what_does_not_belong_on_a_website_is_still_refused_or_dropped(self):
        for style in ("image", "video", "web", "link"):
            with self.subTest(style=style), self.assertRaises(sc.FieldError):
                sc.clean_funktheme({**GOOD, "style": style})
        out = sc.clean_funktheme({**GOOD, "video": "asset:evil.mp4", "web": "https://evil.example/", "link": "we:123", "sound": "asset:x.mp3",
                                  "image": "data:image/png;base64,AAAA", "assets": {"a": "b"}})
        dumped = json.dumps(out)
        for needle in ("evil", "asset:", "we:123", "mp3", "data:"):
            self.assertNotIn(needle, dumped)


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

    def test_the_new_settings_reach_the_page_and_the_layers_are_hidden_in_print(self):
        self.m.set_theme(theme_id="funk", funk=V2)
        res = sb.build_site(self.m, self.reg, self.store, self.root / "stage")
        self.assertTrue(res.ok, res.errors)
        pj = json.loads(__import__("re").search(r'id="hs-page">(.*?)</script>', (self.root / "stage" / "index.html").read_text(encoding="utf-8"), __import__("re").S).group(1))
        self.assertEqual(pj["theme"]["funk"]["fx"], "spectrum")
        self.assertEqual(len(pj["theme"]["funk"]["fxLayers"]), 2)
        self.assertFalse(pj["theme"]["funk"]["motion"]["listen"])
        css = next((self.root / "stage" / "_halbach").glob("theme.*.css")).read_text(encoding="utf-8")
        self.assertIn("#ft-layers", css)

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


class TestCredit(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = rg.Registry()
        self.m = sm.SiteModel(self.root / "hub", self.reg)
        self.m.load_template("personal")
        self.store = st.Store(self.root / "hub")
        self.stage = self.root / "stage"

    def tearDown(self):
        self._t.cleanup()

    def build(self):
        res = sb.build_site(self.m, self.reg, self.store, self.stage)
        self.assertTrue(res.ok, res.errors)
        return res

    def text(self, rel):
        return (self.stage / rel).read_text(encoding="utf-8")

    def test_every_page_on_the_funk_theme_credits_funkthemes_by_default(self):
        self.m.set_theme(theme_id="funk", funk=GOOD)
        self.build()
        for rel in ("index.html", "projects/index.html", "about/index.html", "404.html"):
            self.assertIn("Powered by FunkThemes", self.text(rel), rel)

    def test_it_can_be_switched_off_per_page_and_only_for_that_page(self):
        self.m.set_theme(theme_id="funk", funk=GOOD)
        self.m.update_page("about", credit=False)
        self.build()
        self.assertNotIn("Powered by FunkThemes", self.text("about/index.html"))
        self.assertIn("Powered by FunkThemes", self.text("index.html"))

    def test_other_themes_never_show_it(self):
        self.build()
        for rel in ("index.html", "404.html"):
            self.assertNotIn("FunkThemes", self.text(rel))

    def test_a_page_with_its_own_funk_theme_gets_the_credit(self):
        self.m.update_page("about", theme="funk")
        self.build()
        self.assertIn("Powered by FunkThemes", self.text("about/index.html"))
        self.assertNotIn("Powered by FunkThemes", self.text("index.html"))

    def test_hiding_it_is_noted_not_blocked(self):
        from halbach_connecthub import quality
        self.m.set_theme(theme_id="funk", funk=GOOD)
        self.m.update_page("about", credit=False)
        notes = quality.check_site(self.m, {})
        self.assertTrue(any("FunkThemes" in n["message"] and n["level"] == "info" for n in notes))
        self.assertEqual(self.m.validate()[0], [])

    def test_old_saved_sites_default_to_showing_it(self):
        raw = json.loads(json.dumps(self.m.data))
        for p in raw["pages"]:
            p.pop("credit", None)
        self.assertTrue(all(p["credit"] for p in self.m._migrate(raw)["pages"]))


class TestWindowThemeStorage(unittest.TestCase):
    def test_the_window_theme_is_kept_by_the_hub_and_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as t:
            s = st.Store(Path(t) / "hub")
            self.assertIsNone(s.window_theme())
            s.set_window_theme(GOOD)
            self.assertEqual(st.Store(Path(t) / "hub").window_theme()["name"], "Funk")
            s.set_window_theme(None)
            self.assertIsNone(st.Store(Path(t) / "hub").window_theme())

    def test_junk_and_huge_themes_are_refused(self):
        with tempfile.TemporaryDirectory() as t:
            s = st.Store(Path(t) / "hub")
            for bad in ({"name": "x"}, {"funkTheme": 1, "name": ""}, "text", {**GOOD, "image": "data:image/png;base64," + "A" * 300000}):
                with self.assertRaises(st.ValidationError):
                    s.set_window_theme(bad)
            self.assertIsNone(s.window_theme())

    def test_api_round_trip(self):
        with tempfile.TemporaryDirectory() as t:
            srv, hub = server.serve(Path(t) / "hub", 0, token="tok")
            server.run_in_thread(srv)
            try:
                def call(method, body=None):
                    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                    c.request(method, "/api/windowtheme", json.dumps(body).encode() if body is not None else None,
                              {"Host": "127.0.0.1:%d" % srv.server_address[1], "X-Halbach-Token": "tok", "Content-Type": "application/json"})
                    r = c.getresponse()
                    out = (r.status, json.loads(r.read()))
                    c.close()
                    return out
                self.assertEqual(call("GET"), (200, {"theme": None}))
                self.assertEqual(call("POST", {"theme": GOOD})[1]["theme"]["name"], "Funk")
                self.assertEqual(call("GET")[1]["theme"]["fx"], "ascii")
                self.assertEqual(call("POST", {"theme": {"nope": 1}})[0], 400)
                self.assertEqual(call("POST", {"theme": None}), (200, {"theme": None}))
            finally:
                srv.shutdown()
                srv.server_close()
