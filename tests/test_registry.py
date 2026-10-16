import json
import os
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import registry as rg


def write_plugin(root, kind, pid, manifest=None, files=None):
    d = Path(root) / rg.KIND_DIRS[kind] / pid
    d.mkdir(parents=True, exist_ok=True)
    m = {"kind": kind, "id": pid, "hostapi": 1, "version": "1.0.0", "name": pid, "files": ["render.js"]}
    m.update(manifest or {})
    (d / "plugin.json").write_text(json.dumps(m), encoding="utf-8")
    for name, text in (files if files is not None else {"render.js": "// ok"}).items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")
    return d


class TestBuiltins(unittest.TestCase):
    def test_all_builtins_load_clean(self):
        r = rg.Registry()
        for kind in ("block", "view", "theme"):
            self.assertTrue(r.plugins[kind], kind)
            for p in r.plugins[kind].values():
                self.assertTrue(p.ok, "%s/%s: %s" % (kind, p.id, p.errors))
                self.assertTrue(p.enabled and p.trusted)
        self.assertGreaterEqual(len(r.templates), 3)
        self.assertIsNotNone(r.block("hero"))
        self.assertIsNotNone(r.view("pets"))
        self.assertIsNotNone(r.theme("neutral-dark"))

    def test_catalog_shape(self):
        cat = rg.Registry().catalog("block")
        hero = [c for c in cat if c["id"] == "hero"][0]
        self.assertTrue(hero["builtin"] and hero["fields"] and hero["label"])

    def test_csp_is_the_union_of_what_used_blocks_declare(self):
        r = rg.Registry()
        csp = r.csp_for(["twitch", "github-projects", "hero"])
        self.assertEqual(csp["frame-src"], ["https://player.twitch.tv"])
        self.assertEqual(csp["connect-src"], ["https://api.github.com"])
        self.assertNotIn("script-src", csp)
        self.assertEqual(r.csp_for([]), {})
        self.assertIn("'wasm-unsafe-eval'", r.csp_for(["ruffle-game"])["script-src"])


class TestUserPlugins(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def load(self, trust=None):
        return rg.Registry(user_dir=self.root, trust=trust)

    def test_untrusted_by_default_and_trusted_only_with_matching_pin(self):
        write_plugin(self.root, "block", "countdown", {"fields": [{"key": "to", "label": "To", "type": "text"}]})
        r = self.load()
        p = r.plugins["block"]["countdown"]
        self.assertTrue(p.ok)
        self.assertFalse(p.enabled)
        self.assertIsNone(r.block("countdown"))
        r = self.load({"block/countdown": p.digest})
        self.assertTrue(r.block("countdown").enabled)

    def test_changed_file_disables_a_trusted_plugin(self):
        d = write_plugin(self.root, "block", "countdown")
        pin = self.load().plugins["block"]["countdown"].digest
        (d / "render.js").write_text("// tampered", encoding="utf-8")
        r = self.load({"block/countdown": pin})
        self.assertIsNone(r.block("countdown"))
        self.assertTrue(any("changed" in e for e in r.plugins["block"]["countdown"].errors))

    def test_user_plugin_cannot_replace_a_builtin(self):
        write_plugin(self.root, "block", "hero")
        r = self.load()
        self.assertTrue(r.plugins["block"]["hero"].builtin)

    def test_bad_manifests_are_rejected_with_reasons(self):
        cases = {
            "wrong-kind": {"kind": "theme"},
            "bad-api": {"hostapi": 99},
            "no-api": {"hostapi": "1"},
            "bad-csp-key": {"csp": {"script-evil": ["https://a.com"]}},
            "bad-csp-host": {"csp": {"connect-src": ["http://a.com"]}},
            "wild-csp": {"csp": {"connect-src": ["*"]}},
            "dup-field": {"fields": [{"key": "a", "type": "text"}, {"key": "a", "type": "text"}]},
            "bad-type": {"fields": [{"key": "a", "type": "html"}]},
            "bad-ext": {"files": ["render.exe"]},
            "traversal": {"files": ["../x.js"]},
            "absolute": {"files": ["/etc/passwd"]},
            "missing": {"files": ["nope.js"]},
        }
        for pid, mani in cases.items():
            write_plugin(self.root, "block", pid, mani, files={"render.js": "//", "render.exe": "x"})
            p = rg.Registry(user_dir=self.root).plugins["block"][pid]
            self.assertFalse(p.ok, pid)
            self.assertTrue(p.errors, pid)

    def test_id_must_match_folder_and_json_must_parse(self):
        write_plugin(self.root, "block", "one", {"id": "two"})
        d = self.root / "blocks" / "broken"
        d.mkdir(parents=True)
        (d / "plugin.json").write_text("{not json", encoding="utf-8")
        r = self.load()
        self.assertFalse(r.plugins["block"]["one"].ok)
        self.assertFalse(r.plugins["block"]["broken"].ok)

    def test_size_cap(self):
        write_plugin(self.root, "block", "big", files={"render.js": "x" * (rg.MAX_PLUGIN_BYTES + 10)})
        self.assertFalse(self.load().plugins["block"]["big"].ok)

    @unittest.skipUnless(os.name == "nt" or hasattr(os, "symlink"), "needs links")
    def test_symlinked_file_is_refused(self):
        d = write_plugin(self.root, "block", "linked", files={"render.js": "//"})
        outside = self.root / "outside.js"
        outside.write_text("// secret", encoding="utf-8")
        (d / "render.js").unlink()
        try:
            os.symlink(str(outside), str(d / "render.js"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted here")
        self.assertFalse(self.load().plugins["block"]["linked"].ok)

    def test_digest_depends_on_content(self):
        d = write_plugin(self.root, "block", "x", files={"render.js": "a"})
        a = self.load().plugins["block"]["x"].digest
        (d / "render.js").write_text("b", encoding="utf-8")
        self.assertNotEqual(a, self.load().plugins["block"]["x"].digest)


if __name__ == "__main__":
    unittest.main()
