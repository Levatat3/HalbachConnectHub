import json
import re
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import store as st


def page_json(html_text):
    m = re.search(r'<script type="application/json" id="hs-page">(.*?)</script>', html_text, re.S)
    return json.loads(m.group(1))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = rg.Registry()
        self.model = sm.SiteModel(self.root / "hub", self.reg)
        self.store = st.Store(self.root / "hub")
        self.stage = self.root / "stage"

    def tearDown(self):
        self._t.cleanup()

    def build(self, **kw):
        return sb.build_site(self.model, self.reg, self.store, self.stage, **kw)


class TestBuild(Base):
    def test_every_template_builds_cleanly(self):
        for tid in ("blank", "personal", "streamer", "link-in-bio", "portfolio", "docs-site"):
            self.model.load_template(tid)
            errors, _ = self.model.validate()
            self.assertEqual(errors, [], (tid, errors))
            res = self.build()
            self.assertTrue(res.ok, (tid, res.errors))
            self.assertTrue((self.stage / "index.html").is_file())
            self.assertTrue((self.stage / "_headers").is_file())
            self.assertTrue((self.stage / "404.html").is_file())

    def test_every_new_theme_builds_the_personal_template(self):
        """Task 1.1: paper, solarized-dark and high-contrast must each build a real site."""
        self.model.load_template("personal")
        for tid in ("paper", "solarized-dark", "high-contrast"):
            self.assertIsNotNone(self.reg.theme(tid), tid)
            self.model.set_theme(tid)
            res = self.build()
            self.assertTrue(res.ok, (tid, res.errors))
            page = page_json((self.stage / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(page["theme"]["id"], tid)

    def test_pages_are_real_files_with_inert_json_and_nav(self):
        self.model.load_template("personal")
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        home = (self.stage / "index.html").read_text(encoding="utf-8")
        pj = page_json(home)
        self.assertEqual(pj["page"]["slug"], "")
        self.assertIn('class="hs-nav"', home)
        self.assertIn('href="/projects/"', home)
        self.assertTrue((self.stage / "projects" / "index.html").is_file())
        self.assertIn('aria-current="page"', home)

    def test_page_json_cannot_break_out_of_its_script_tag(self):
        self.model.load_template("blank")
        pid = self.model.data["pages"][0]["id"]
        bid = self.model.data["pages"][0]["blocks"][0]["id"]
        self.model.update_block(pid, bid, props={"markdown": "</script><script>alert(1)</script>"}) if \
            self.model.data["pages"][0]["blocks"][0]["type"] == "text" else self.model.add_block(
                pid, "text", {"markdown": "</script><script>alert(1)</script>"})
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        text = (self.stage / "index.html").read_text(encoding="utf-8")
        self.assertEqual(text.lower().count("<script"), 2)       # the data block and the runtime, nothing injected

    def test_only_used_block_scripts_are_written(self):
        self.model.load_template("blank")
        res = self.build()
        names = [p.name for p in (self.stage / "_halbach" / "blocks").iterdir()]
        self.assertTrue(res.ok)
        self.assertFalse(any(n.startswith("twitch") for n in names))
        self.assertTrue(all(re.search(r"\.[0-9a-f]{10}\.js$", n) for n in names))

    def test_url_base_prefixes_every_address_for_preview(self):
        self.model.load_template("personal")
        res = self.build(url_base="/preview")
        self.assertTrue(res.ok, res.errors)
        home = (self.stage / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/preview/projects/"', home)
        self.assertNotIn('src="/_halbach', home)
        self.assertEqual(page_json(home)["site"]["base"], "/preview")

    def test_draft_and_disabled_pages_are_left_out(self):
        self.model.load_template("personal")
        extra = self.model.add_page("Secret", slug="secret")
        self.model.update_page(extra["id"], draft=True)
        res = self.build()
        self.assertFalse((self.stage / "secret").exists())
        res = self.build(include_drafts=True)
        self.assertTrue((self.stage / "secret" / "index.html").is_file())
        self.assertIn("noindex", (self.stage / "secret" / "index.html").read_text(encoding="utf-8"))

    def test_invalid_model_stops_the_build(self):
        self.model.load_template("blank")
        self.model.data["pages"][0]["enabled"] = False
        res = self.build()
        self.assertFalse(res.ok)

    def test_the_four_example_blocks_build(self):
        """Task 1.3: countdown, accordion, stat-tiles and code each render into a real page."""
        self.model.load_template("blank")
        pid = self.model.data["pages"][0]["id"]
        props = {
            "countdown": {"target": "2030-01-01T00:00", "label": "Launch", "done": "It's live!"},
            "accordion": {"items": [{"title": "One", "text": "First **body**"}, {"title": "Two", "text": "Second"}]},
            "stat-tiles": {"items": [{"title": "1.2k", "text": "Stars"}, {"title": "42", "text": "Repos"}]},
            "code": {"lang": "python", "code": "print('hi')\n"},
        }
        for type_, p in props.items():
            self.model.add_block(pid, type_, p)
        errors, _ = self.model.validate()
        self.assertEqual(errors, [])
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        page = page_json((self.stage / "index.html").read_text(encoding="utf-8"))
        built = {b["type"]: b["props"] for b in page["blocks"]}
        for type_ in props:
            self.assertIn(type_, built, type_)
            self.assertTrue(list((self.stage / "_halbach" / "blocks").glob(type_ + ".*.js")), type_)
        self.assertEqual(built["countdown"]["target"], "2030-01-01T00:00")
        self.assertEqual(built["accordion"]["items"][1]["title"], "Two")
        self.assertEqual(built["stat-tiles"]["items"][0]["text"], "Stars")
        self.assertIn("print", built["code"]["code"])
        css = next((self.stage / "_halbach").glob("theme.*.css")).read_text(encoding="utf-8")
        for cls in (".hs-countdown-num", ".hs-accordion", ".hs-stats", ".hs-code-body"):
            self.assertIn(cls, css, cls)

    def test_example_block_text_cannot_break_out_of_the_page(self):
        """Task 1.3: text in the new blocks is inert data, never markup."""
        self.model.load_template("blank")
        pid = self.model.data["pages"][0]["id"]
        nasty = "</script><script>alert(1)</script>"
        self.model.add_block(pid, "countdown", {"target": "2030-01-01T00:00", "label": nasty})
        self.model.add_block(pid, "code", {"lang": "html", "code": nasty})
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        html = (self.stage / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert", html)
        self.assertIn(r"\u003cscript\u003ealert", html)


class TestHeaders(Base):
    def test_csp_is_report_only_by_default_then_enforce(self):
        self.model.load_template("personal")
        self.model.set_settings(csp="report-only")
        self.assertTrue(self.build().ok)
        h = (self.stage / "_headers").read_text(encoding="utf-8")
        self.assertIn("Content-Security-Policy-Report-Only:", h)
        self.model.set_settings(csp="enforce")
        self.assertTrue(self.build().ok)
        h = (self.stage / "_headers").read_text(encoding="utf-8")
        self.assertIn("  Content-Security-Policy: default-src 'self'", h)
        self.assertNotIn("Report-Only", h)
        self.model.set_settings(csp="off")
        self.assertTrue(self.build().ok)
        self.assertNotIn("Content-Security-Policy", (self.stage / "_headers").read_text(encoding="utf-8"))

    def test_csp_only_lists_hosts_of_blocks_in_use(self):
        self.model.load_template("blank")
        self.assertTrue(self.build().ok)
        h = (self.stage / "_headers").read_text(encoding="utf-8")
        self.assertNotIn("twitch.tv", h)
        self.assertNotIn("unpkg.com", h)
        self.assertIn("cloudflareinsights.com", h)
        self.model.load_template("streamer")
        self.assertTrue(self.build().ok)
        self.assertIn("player.twitch.tv", (self.stage / "_headers").read_text(encoding="utf-8"))

    def test_analytics_off_removes_cloudflare_hosts(self):
        self.model.load_template("blank")
        self.model.set_settings(analytics="none")
        self.assertTrue(self.build().ok)
        self.assertNotIn("cloudflareinsights", (self.stage / "_headers").read_text(encoding="utf-8"))

    def test_assets_are_immutable_and_modules_get_their_own_policy(self):
        self.model.load_template("blank")
        self.model.set_settings(csp="enforce")
        self.build()
        h = (self.stage / "_headers").read_text(encoding="utf-8")
        self.assertIn("/_halbach/*\n  ! Cache-Control", h)
        self.assertIn("immutable", h)
        self.assertIn("/modules/*\n  ! Content-Security-Policy", h)

    def test_header_lines_stay_inside_cloudflare_limits(self):
        self.model.load_template("streamer")
        self.assertTrue(self.build().ok)
        for ln in (self.stage / "_headers").read_text(encoding="utf-8").splitlines():
            self.assertLess(len(ln), 2000)


class TestSeoAndRedirects(Base):
    def test_robots_sitemap_and_noindex(self):
        self.model.load_template("personal")
        self.model.set_settings(base_url="https://example.com")
        self.assertTrue(self.build().ok)
        self.assertIn("Sitemap: https://example.com/sitemap.xml", (self.stage / "robots.txt").read_text())
        sm_text = (self.stage / "sitemap.xml").read_text()
        self.assertIn("<loc>https://example.com/</loc>", sm_text)
        self.assertIn('rel="canonical" href="https://example.com/projects/"', (self.stage / "projects" / "index.html").read_text())
        self.model.set_settings(indexable=False)
        self.assertTrue(self.build().ok)
        self.assertIn("Disallow: /", (self.stage / "robots.txt").read_text())
        self.assertFalse((self.stage / "sitemap.xml").exists())

    def test_redirects_file(self):
        self.model.load_template("blank")
        self.model.set_redirects([{"from": "/old", "to": "/", "status": 301}])
        self.assertTrue(self.build().ok)
        self.assertEqual((self.stage / "_redirects").read_text().strip(), "/old / 301")


class TestExtrasAndModules(Base):
    def test_extra_folder_is_copied_without_legacy_files(self):
        extra = self.root / "extra"
        (extra / "pitforge").mkdir(parents=True)
        (extra / "pitforge" / "index.html").write_text("<p>forge</p>", encoding="utf-8")
        (extra / "index.html").write_text("old", encoding="utf-8")
        (extra / "js").mkdir()
        (extra / "js" / "site.js").write_text("old", encoding="utf-8")
        (extra / "favicon.ico").write_bytes(b"ico")
        self.model.load_template("blank")
        res = self.build(extra_dir=extra)
        self.assertTrue(res.ok, res.errors)
        self.assertTrue((self.stage / "pitforge" / "index.html").is_file())
        self.assertFalse((self.stage / "js").exists())
        self.assertNotEqual((self.stage / "index.html").read_text(encoding="utf-8"), "old")
        self.assertIn("favicon.ico", (self.stage / "index.html").read_text(encoding="utf-8"))

    def test_reserved_and_colliding_folders_are_refused(self):
        extra = self.root / "extra"
        (extra / "_halbach").mkdir(parents=True)
        (extra / "_halbach" / "x.txt").write_text("x", encoding="utf-8")
        self.model.load_template("blank")
        self.assertFalse(self.build(extra_dir=extra).ok)
        import shutil
        shutil.rmtree(extra)
        (extra / "play").mkdir(parents=True)
        (extra / "play" / "index.html").write_text("x", encoding="utf-8")
        self.model.add_page("Play", slug="play")
        self.assertFalse(self.build(extra_dir=extra).ok)

    def test_generated_icon_when_none_supplied(self):
        self.model.load_template("blank")
        self.assertTrue(self.build().ok)
        self.assertIn("<svg", (self.stage / "favicon.svg").read_text())

    def test_static_modules_are_staged_and_listed_in_manifest(self):
        src = self.root / "doc.md"
        src.write_text("# hello", encoding="utf-8")
        mod = self.store.add_module("static", "Notes", str(src))
        self.store.set_on(mod["id"], True)
        self.model.load_template("blank")
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        man = json.loads((self.stage / "manifest.json").read_text())
        self.assertEqual(man["modules"][0]["type"], "doc")
        self.assertTrue((self.stage / "modules" / mod["id"] / "doc.md").is_file())

    def test_module_block_with_unknown_module_is_an_error(self):
        self.model.load_template("blank")
        pid = self.model.data["pages"][0]["id"]
        self.model.data["pages"][0]["blocks"].append({"id": "zz", "type": "module-view", "props": {"module": "nope"}, "enabled": True})
        self.assertFalse(self.build().ok)


if __name__ == "__main__":
    unittest.main()
