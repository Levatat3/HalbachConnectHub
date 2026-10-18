"""The modular scaffold's declarative parts must stay valid while the code behind them is built.

These checks are about the data files only: built-in block, theme and template definitions.
"""
import json
import re
import unittest
from pathlib import Path

BUILTIN = Path(__file__).resolve().parent.parent / "halbach_connecthub" / "builtin"
FIELD_TYPES = {"text", "longtext", "markdown", "url", "number", "bool", "select", "list-text", "links",
               "cards", "module", "area", "page", "color", "image"}
CSP_KEYS = {"default-src", "script-src", "style-src", "img-src", "connect-src", "frame-src", "font-src", "media-src"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SLUG_RE = re.compile(r"^([a-z0-9][a-z0-9-]{0,39}(/[a-z0-9][a-z0-9-]{0,39}){0,2})?$")
RESERVED = {"modules", "assets", "plugins", "_halbach", "api", "admin", "404", "robots", "sitemap"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestBlocks(unittest.TestCase):
    def blocks(self):
        return sorted((BUILTIN / "blocks").iterdir())

    def test_every_block_is_well_formed(self):
        self.assertGreaterEqual(len(self.blocks()), 15)
        for d in self.blocks():
            m = load(d / "plugin.json")
            self.assertEqual(m["kind"], "block", d.name)
            self.assertEqual(m["id"], d.name)
            self.assertRegex(m["id"], ID_RE)
            self.assertEqual(m["hostapi"], 1)
            self.assertTrue(m["label"] and m["description"], d.name)
            keys = [f["key"] for f in m["fields"]]
            self.assertEqual(len(keys), len(set(keys)), "duplicate field key in " + d.name)
            for f in m["fields"]:
                self.assertIn(f["type"], FIELD_TYPES, "%s.%s" % (d.name, f["key"]))
                self.assertTrue(f["label"])
                if f["type"] == "select":
                    self.assertTrue(f["options"], d.name)
                    if "default" in f:
                        self.assertIn(f["default"], f["options"])
            for k, hosts in m.get("csp", {}).items():
                self.assertIn(k, CSP_KEYS, d.name)
                for h in hosts:
                    self.assertTrue(h.startswith("https://") or h in ("LIVE_BASE", "'wasm-unsafe-eval'"), "%s: %s" % (d.name, h))
            for fn in m["files"]:
                self.assertTrue((d / fn).is_file(), "%s missing %s" % (d.name, fn))

    def test_scripts_never_write_html_from_data(self):
        for d in self.blocks():
            text = (d / "render.js").read_text(encoding="utf-8")
            for bad in ("eval(", "new Function", "document.write"):
                self.assertNotIn(bad, text, d.name)


class TestThemes(unittest.TestCase):
    def test_themes(self):
        dirs = sorted((BUILTIN / "themes").iterdir())
        self.assertGreaterEqual(len(dirs), 3)
        for d in dirs:
            m = load(d / "plugin.json")
            self.assertEqual((m["kind"], m["id"]), ("theme", d.name))
            self.assertTrue(m["vars"])
            for k, v in m["vars"].items():
                self.assertTrue(k.startswith("--"), k)
                self.assertNotIn(";", v)
                self.assertNotIn("}", v)
            self.assertTrue((d / "theme.css").is_file())


class TestTemplates(unittest.TestCase):
    def test_templates_use_real_blocks_and_valid_addresses(self):
        block_ids = {d.name for d in (BUILTIN / "blocks").iterdir()}
        themes = {d.name for d in (BUILTIN / "themes").iterdir()}
        files = sorted((BUILTIN / "templates").glob("*.json"))
        self.assertGreaterEqual(len(files), 3)
        for f in files:
            t = load(f)
            model = t["model"]
            self.assertEqual(model["version"], 1)
            self.assertIn(model["theme"]["id"], themes)
            ids, slugs = set(), set()
            for p in model["pages"]:
                self.assertRegex(p["id"], ID_RE)
                self.assertRegex(p["slug"], SLUG_RE)
                self.assertNotIn(p["slug"], RESERVED)
                self.assertNotIn(p["id"], ids)
                self.assertNotIn(p["slug"], slugs)
                ids.add(p["id"])
                slugs.add(p["slug"])
                bids = set()
                for b in p["blocks"]:
                    self.assertIn(b["type"], block_ids, "%s uses unknown block %s" % (f.name, b["type"]))
                    self.assertNotIn(b["id"], bids)
                    bids.add(b["id"])
            self.assertIn("", slugs, f.name + " needs a home page")


class TestRuntime(unittest.TestCase):
    def test_runtime_present_and_exposes_the_api(self):
        text = (BUILTIN / "runtime" / "halbach-site.js").read_text(encoding="utf-8")
        for name in ("registerBlock", "registerView", "markdown: markdown", "live: { run: runLive }", "registerEffect"):
            self.assertIn(name, text)


class TestDesignDocs(unittest.TestCase):
    def test_docs_exist_and_cross_link(self):
        docs = BUILTIN.parent.parent / "docs"
        for rel in ("MODULAR-PLAN.md", "design/RESEARCH.md", "design/SITE-MODEL.md", "design/PLUGINS.md", "design/AREAS.md"):
            self.assertTrue((docs / rel).is_file(), rel)
        plan = (docs / "MODULAR-PLAN.md").read_text(encoding="utf-8")
        for img in re.findall(r"\]\((img/[^)]+)\)", plan):
            self.assertTrue((docs / img).is_file(), "plan links a missing image: " + img)


if __name__ == "__main__":
    unittest.main()


class TestRuntimeContract(unittest.TestCase):
    def test_every_ctx_member_a_block_or_view_uses_exists_in_the_runtime(self):
        import re
        runtime = (BUILTIN / "runtime" / "halbach-site.js").read_text(encoding="utf-8")
        start = runtime.index("function ctxFor(")
        ctx_src = runtime[start:runtime.index("// ---------------------------------------------------------------- boot", start)]
        provided = set(re.findall(r"(?:^|[{,])\s*(\w+):", ctx_src, re.M))
        provided |= {"mod", "live"}
        used = {}
        for f in list((BUILTIN / "blocks").glob("*/*.js")) + list((BUILTIN / "views").glob("*/*.js")) + list((BUILTIN / "themes").glob("*/*.js")):
            for name in re.findall(r"\bctx\.(\w+)", f.read_text(encoding="utf-8")):
                used.setdefault(name, f.parent.name)
        missing = {n: where for n, where in used.items() if n not in provided}
        self.assertEqual(missing, {}, "scripts use ctx members the runtime does not provide")
        for sub, names in (("mod", ("view",)), ("live", ("run",))):
            for f in (BUILTIN / "blocks").glob("*/*.js"):
                for n in re.findall(r"\bctx\.%s\.(\w+)" % sub, f.read_text(encoding="utf-8")):
                    self.assertIn(n, names, f.parent.name)
