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


class TestAreaEmbeds(Base):
    """An area's embed widens only that area's own policy, only for a host its owner listed, and is checked again at build time."""
    URL = "https://player.example.com/app"

    def build_area(self, hosts, url=None):
        self.model.load_template("blank")
        tree = self.root / "tree"
        tree.mkdir(exist_ok=True)
        doc = {"format": 1, "title": "W", "description": "", "pages": [{"name": "", "title": "W", "blocks": [
            {"id": "b1", "type": "embed", "props": {"url": url or self.URL, "height": 560}}]}]}
        (tree / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        areas = [{"id": "workshop", "slug": "workshop", "name": "Workshop", "nav": True, "tree": str(tree),
                  "blocks": ["embed", "text"], "embed_hosts": hosts}]
        return sb.build_site(self.model, self.reg, self.store, self.stage, areas=areas)

    def rules(self):
        """{path pattern: its content-security-policy line} from the built _headers."""
        out, cur = {}, None
        for line in (self.stage / "_headers").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith(" "):
                cur = line.strip()
            elif "ontent-Security-Policy" in line and not line.strip().startswith("!"):
                out[cur] = line.strip()
        return out

    def test_a_listed_host_is_framed_only_inside_that_areas_own_policy(self):
        res = self.build_area(["player.example.com"])
        self.assertTrue(res.ok, res.errors)
        rules = self.rules()
        self.assertIn("frame-src 'self' https://player.example.com", rules["/workshop/*"])
        self.assertNotIn("player.example.com", rules["/*"], "an area's embed must never widen the whole site's policy")
        self.assertNotIn("player.example.com", rules["/workshop/files/*"])
        page = page_json((self.stage / "workshop" / "index.html").read_text(encoding="utf-8"))
        props = page["blocks"][0]["props"]
        self.assertEqual(props,{"url": self.URL, "height": 560, "sameorigin": True})

    def test_a_host_the_owner_removed_is_dropped_at_build_time_with_a_warning(self):
        res = self.build_area(["other.example.org"])                     # approved earlier, no longer allowed
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("left out" in w["message"] and "embed" in w["message"].lower() for w in res.warnings), res.warnings)
        self.assertNotIn("player.example.com", (self.stage / "_headers").read_text(encoding="utf-8"))
        self.assertNotIn("player.example.com", (self.stage / "workshop" / "index.html").read_text(encoding="utf-8"))

    def test_the_trust_flag_and_a_tricky_address_never_come_from_the_bundle(self):
        for url in ("https://player.example.com@evil.example/", "https://player.example.com:8443/", "http://player.example.com/"):
            with self.subTest(url=url):
                res = self.build_area(["player.example.com"], url=url)
                self.assertTrue(res.ok, res.errors)
                self.assertNotIn("evil.example", (self.stage / "_headers").read_text(encoding="utf-8"))
                self.assertNotIn("sameorigin", (self.stage / "workshop" / "index.html").read_text(encoding="utf-8"))


class TestPreviewExtras(Base):
    """build_site's preview-only extras: an area's stylesheet on the site theme, and same-origin embeds."""

    def test_extra_css_goes_after_the_theme_and_embeds_get_sameorigin_only_when_asked(self):
        self.model.load_template("blank")
        self.model.add_page("Demo", "demo") if hasattr(self.model, "add_page") else None
        data = self.model.data
        data["pages"][0]["blocks"].append({"id": "e1", "type": "embed", "enabled": True, "props": {"url": "https://player.example.dev/", "height": 400}})
        plain = sb.build_site(self.model, self.reg, self.store, self.stage / "a")
        self.assertTrue(plain.ok, plain.errors)
        html = (self.stage / "a" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('"sameorigin"', html.replace(" ", ""))
        res = sb.build_site(self.model, self.reg, self.store, self.stage / "b", extra_css="h1{letter-spacing:.3em}\n", embed_sameorigin=True)
        self.assertTrue(res.ok, res.errors)
        html = (self.stage / "b" / "index.html").read_text(encoding="utf-8")
        self.assertIn('"sameorigin":true', html.replace(" ", ""))
        css = next((self.stage / "b" / "_halbach").glob("theme.*.css")).read_text(encoding="utf-8")
        self.assertLess(css.index(":root{"), css.index("letter-spacing:.3em"))
        css_a = next((self.stage / "a" / "_halbach").glob("theme.*.css")).read_text(encoding="utf-8")
        self.assertNotIn("letter-spacing:.3em", css_a)


class TestSubSites(Base):
    """An area is a sub-site: its own menu, brand and look; the main menu only gets one "Sites" dropdown."""

    def build_area(self, design=None, themes=None, nav=True, pages=None, name="Funkas Workshop", slug="funkas-workshop"):
        self.model.load_template("blank")
        tree = self.root / "tree"
        tree.mkdir(exist_ok=True)
        (tree / "files").mkdir(exist_ok=True)
        (tree / "files" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        doc = {"format": 1, "title": "Workshop", "description": "", "pages": pages or [
            {"name": "", "title": "Home", "blocks": [{"id": "b1", "type": "text", "props": {"markdown": "hello"}}]},
            {"name": "tools", "title": "Tools", "blocks": [{"id": "b1", "type": "text", "props": {"markdown": "tools"}}]}]}
        if design is not None:
            doc["design"] = design
        (tree / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        areas = [{"id": slug, "slug": slug, "name": name, "nav": nav, "tree": str(tree), "blocks": ["text"], "embed_hosts": [],
                  "themes": themes if themes is not None else []}]
        return sb.build_site(self.model, self.reg, self.store, self.stage, areas=areas)

    def read(self, rel):
        return (self.stage / rel).read_text(encoding="utf-8")

    def nav_of(self, html_text):
        m = re.search(r'<nav class="hs-nav"[^>]*>(.*?)</nav>', html_text, re.S)
        return m.group(1)

    def test_the_main_menu_gets_one_sites_dropdown_and_none_of_the_areas_tabs(self):
        res = self.build_area()
        self.assertTrue(res.ok, res.errors)
        home = self.nav_of(self.read("index.html"))
        self.assertIn('<details class="hs-sites"><summary>Sites</summary>', home)
        self.assertIn('href="/funkas-workshop/">Funkas Workshop</a>', home)
        self.assertNotIn("/funkas-workshop/tools/", home)
        self.assertEqual(home.count('class="hs-sites"'), 1)
        self.assertIn("hs-sites", self.nav_of(self.read("404.html")))

    def test_in_preview_every_sites_link_carries_the_preview_prefix(self):
        self.model.load_template("blank")
        tree = self.root / "tree"
        tree.mkdir(exist_ok=True)
        (tree / "area.json").write_text(json.dumps({"format": 1, "title": "W", "description": "", "pages": [
            {"name": "", "title": "W", "blocks": [{"id": "b1", "type": "text", "props": {"markdown": "x"}}]}]}), encoding="utf-8")
        areas = [{"id": "ws", "slug": "ws", "name": "Workshop", "nav": True, "tree": str(tree), "blocks": ["text"], "embed_hosts": [], "themes": []}]
        res = sb.build_site(self.model, self.reg, self.store, self.stage, url_base="/preview", areas=areas)
        self.assertTrue(res.ok, res.errors)
        self.assertIn('<a href="/preview/ws/">Workshop</a>', self.nav_of(self.read("index.html")))
        self.assertIn('class="hs-back" href="/preview/"', self.nav_of(self.read("ws/index.html")))

    def test_an_area_hidden_from_the_menu_gets_no_dropdown(self):
        res = self.build_area(nav=False)
        self.assertTrue(res.ok, res.errors)
        self.assertNotIn("hs-sites", self.read("index.html"))

    def test_the_area_has_its_own_menu_and_not_the_main_sites(self):
        self.model.load_template("personal")
        res = self.build_area()
        self.assertTrue(res.ok, res.errors)
        nav = self.nav_of(self.read("funkas-workshop/index.html"))
        self.assertIn(">Funkas Workshop</a>", nav)          # its first page, named after the area
        self.assertIn('href="/funkas-workshop/tools/"', nav)
        self.assertIn("hs-back", nav)                       # the way back to the main site
        for other in ("/about/", "/projects/"):
            self.assertNotIn('href="%s"' % other, nav)
        self.assertNotIn("hs-sites", nav)
        tools = self.nav_of(self.read("funkas-workshop/tools/index.html"))
        self.assertIn('aria-current="page"', tools)

    def test_its_own_brand_theme_colours_and_layout_when_the_owner_allowed_themes(self):
        design = {"brand": {"name": "Funka's Workshop", "mark": "FW", "tagline": "Tools and mods", "footer": "made by Funka", "logo": "files/logo.png"},
                  "theme": "neutral-light", "vars": {"--accent": "#ff8800"}, "layout": {"nav": "side"}}
        res = self.build_area(design=design, themes=["neutral-light", "neutral-dark"])
        self.assertTrue(res.ok, res.errors)
        page = self.read("funkas-workshop/index.html")
        self.assertIn("<title>Funka&#x27;s Workshop</title>", page)
        self.assertIn('<img class="hs-logo" src="/funkas-workshop/files/logo.png" alt="">', page)
        self.assertIn('data-nav="side"', page)
        self.assertIn("made by Funka", page)
        self.assertIn('content="Tools and mods"', page)
        css_href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1)
        area_css = self.read(css_href.lstrip("/"))
        self.assertIn("--accent:#ff8800", area_css)
        main_css = self.read(re.search(r'<link rel="stylesheet" href="([^"]+)"', self.read("index.html")).group(1).lstrip("/"))
        self.assertNotIn("#ff8800", main_css, "an area's colours must never reach the main site's stylesheet")
        self.assertNotEqual(css_href, re.search(r'<link rel="stylesheet" href="([^"]+)"', self.read("index.html")).group(1))
        self.assertIn("Tools and mods", page)
        self.assertNotIn("data-nav=\"side\"", self.read("index.html"))     # the main site keeps its own layout

    def test_colours_alone_give_the_area_its_own_background_without_picking_a_theme(self):
        res = self.build_area(design={"vars": {"--bg": "#112233"}}, themes=["neutral-light"])
        self.assertTrue(res.ok, res.errors)
        page = self.read("funkas-workshop/index.html")
        area_css = self.read(re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1).lstrip("/"))
        self.assertIn("--bg:#112233", area_css)
        main_css = self.read(re.search(r'<link rel="stylesheet" href="([^"]+)"', self.read("index.html")).group(1).lstrip("/"))
        self.assertNotIn("#112233", main_css)

    def build_with_css(self, css, allow=True):
        self.model.load_template("blank")
        tree = self.root / "tree"
        (tree / "files").mkdir(parents=True, exist_ok=True)
        (tree / "files" / "bg.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tree / "files" / "style.css").write_text(css, encoding="utf-8")
        doc = {"format": 1, "title": "W", "description": "", "design": {"css": "files/style.css"},
               "pages": [{"name": "", "title": "Home", "blocks": [{"id": "b1", "type": "text", "props": {"markdown": "hi"}}]}]}
        (tree / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        areas = [{"id": "funkas-workshop", "slug": "funkas-workshop", "name": "Funkas Workshop", "nav": True, "tree": str(tree), "blocks": ["text"],
                  "embed_hosts": [], "themes": [], "custom_css": allow}]
        return sb.build_site(self.model, self.reg, self.store, self.stage, areas=areas)

    def test_an_areas_own_stylesheet_is_merged_into_its_own_stylesheet_only(self):
        res = self.build_with_css('body{background:#abcdef url("files/bg.png")}\n')
        self.assertTrue(res.ok, res.errors)
        page = self.read("funkas-workshop/index.html")
        href = re.search(r'<link rel="stylesheet" href="([^"]+)"', page).group(1)
        css = self.read(href.lstrip("/"))
        self.assertIn("#abcdef", css)
        self.assertIn('url("/funkas-workshop/files/bg.png")', css)
        self.assertLess(css.index(":root{"), css.index("#abcdef"))                   # after the theme, so it can override it
        main = self.read(re.search(r'<link rel="stylesheet" href="([^"]+)"', self.read("index.html")).group(1).lstrip("/"))
        self.assertNotIn("#abcdef", main)
        self.assertTrue((self.stage / "funkas-workshop" / "files" / "bg.png").is_file())
        self.assertFalse((self.stage / "funkas-workshop" / "files" / "style.css").exists(), "the raw stylesheet must not be published as-is")

    def test_the_stylesheet_is_checked_again_at_build_and_left_out_with_a_warning(self):
        res = self.build_with_css('@import url("https://evil.example/x.css");\nbody{background:#abcdef}\n')       # got in somehow
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("stylesheet" in w["message"] and "left out" in w["message"] for w in res.warnings), res.warnings)
        every = "".join(f.read_text(encoding="utf-8", errors="ignore") for f in self.stage.rglob("*.css")) + self.read("funkas-workshop/index.html")
        self.assertNotIn("evil.example", every)
        self.assertNotIn("#abcdef", every)

    def test_when_the_owner_takes_the_permission_away_the_stylesheet_is_dropped_but_the_site_still_builds(self):
        res = self.build_with_css("body{background:#abcdef}\n", allow=False)
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("custom design" in w["message"] for w in res.warnings), res.warnings)
        self.assertNotIn("#abcdef", "".join(f.read_text(encoding="utf-8", errors="ignore") for f in self.stage.rglob("*.css")))

    def test_a_design_is_dropped_with_a_warning_when_the_owner_has_not_allowed_themes(self):
        res = self.build_area(design={"theme": "neutral-light", "vars": {"--accent": "#ff8800"}}, themes=[])
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("custom design" in w["message"] for w in res.warnings), res.warnings)
        self.assertNotIn("#ff8800", "".join(f.read_text(encoding="utf-8", errors="ignore") for f in self.stage.rglob("*.css")))
        self.assertIn("Funkas Workshop", self.read("funkas-workshop/index.html"))     # still a sub-site, just with the default look

    def test_a_hostile_design_is_dropped_at_build_time_even_if_it_got_in(self):
        for bad in ({"vars": {"--accent": "url(https://evil.example/x)"}, "theme": "neutral-light"},
                    {"brand": {"logo": "files/../../x.png"}, "theme": "neutral-light"},
                    {"theme": "not-a-theme"}, {"script": "alert(1)", "theme": "neutral-light"}):
            with self.subTest(design=bad):
                res = self.build_area(design=bad, themes=["neutral-light"])
                self.assertTrue(res.ok, res.errors)
                self.assertTrue(any("custom design" in w["message"] for w in res.warnings), res.warnings)
                every = "".join(f.read_text(encoding="utf-8", errors="ignore") for f in self.stage.rglob("*") if f.is_file() and f.suffix in (".css", ".html"))
                self.assertNotIn("evil.example", every)


if __name__ == "__main__":
    unittest.main()
