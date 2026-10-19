import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import registry as rg
from halbach_connecthub import sitemodel as sm


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.home = Path(self._t.name)
        self.reg = rg.Registry()
        self.m = sm.SiteModel(self.home, self.reg)
        self.m.load_template("blank")
        self.m.save("start")

    def tearDown(self):
        self._t.cleanup()


class TestPersistence(Base):
    def test_round_trip_and_atomic_file(self):
        again = sm.SiteModel(self.home, self.reg)
        self.assertTrue(again.exists)
        self.assertEqual(again.data["pages"][0]["slug"], "")
        self.assertEqual([f.name for f in (self.home / "site").iterdir() if f.suffix == ".tmp"], [])

    def test_missing_file_gives_a_default_model(self):
        with tempfile.TemporaryDirectory() as t:
            fresh = sm.SiteModel(t, self.reg)
            self.assertFalse(fresh.exists)
            self.assertEqual(fresh.data["pages"], [])

    def test_newer_version_is_refused_and_left_alone(self):
        (self.home / "site" / "site.json").write_text(json.dumps({"version": 99}), encoding="utf-8")
        with self.assertRaises(sm.ModelError):
            sm.SiteModel(self.home, self.reg)
        self.assertEqual(json.loads((self.home / "site" / "site.json").read_text())["version"], 99)

    def test_old_models_gain_missing_keys(self):
        old = {"version": 1, "pages": [{"id": "home", "slug": "", "label": "H", "blocks": [{"id": "b1", "type": "text"}]}]}
        (self.home / "site" / "site.json").write_text(json.dumps(old), encoding="utf-8")
        m = sm.SiteModel(self.home, self.reg)
        self.assertIn("redirects", m.data)
        self.assertTrue(m.data["pages"][0]["enabled"])
        self.assertTrue(m.data["pages"][0]["blocks"][0]["enabled"])

    def test_garbage_file_becomes_default(self):
        (self.home / "site" / "site.json").write_text("{nope", encoding="utf-8")
        self.assertFalse(sm.SiteModel(self.home, self.reg).exists)

    def test_older_version_is_backed_up_before_migrating(self):
        old = self.home / "site" / "site.json"
        old.write_text(json.dumps({"version": 0, "brand": {"name": "Old"}}), encoding="utf-8")
        original = old.read_bytes()
        model = sm.SiteModel(self.home, self.reg)
        self.assertEqual(model.data["version"], sm.MODEL_VERSION)
        baks = sorted((self.home / "site").glob("site.json.bak-*"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(baks[0].read_bytes(), original)

    def test_current_version_is_not_backed_up(self):
        sm.SiteModel(self.home, self.reg)
        self.assertEqual(list((self.home / "site").glob("site.json.bak-*")), [])


class TestHistory(Base):
    def test_snapshots_only_when_changed_and_restore_is_undoable(self):
        n0 = len(self.m.history())
        self.m.save("no change")
        self.assertEqual(len(self.m.history()), n0)
        self.m.add_page("About")
        self.m.save("added about")
        self.assertEqual(len(self.m.history()), n0 + 1)
        first = self.m.history()[-1]["n"]
        self.m.restore(first)
        self.assertEqual([p["slug"] for p in self.m.data["pages"]], [""])
        self.assertTrue(self.m.history()[0]["label"].startswith("restored"))
        self.m.restore(self.m.history()[1]["n"])                    # undo the restore
        self.assertIn("about", [p["slug"] for p in self.m.data["pages"]])

    def test_history_is_pruned(self):
        old = sm.HISTORY_KEEP
        sm.HISTORY_KEEP = 5
        try:
            for i in range(10):
                self.m.set_brand(tagline="t%d" % i)
                self.m.save("edit %d" % i)
            self.assertEqual(len(self.m.history()), 5)
        finally:
            sm.HISTORY_KEEP = old

    def test_diff_summary(self):
        n = self.m.history()[0]["n"]
        self.m.add_page("About")
        self.m.update_page("home", title="New title")
        self.m.add_block("home", "text", {"markdown": "hi"})
        out = " | ".join(self.m.diff_since(n))
        self.assertIn("page added: About", out)
        self.assertIn("title changed", out)
        self.assertIn("block(s) added", out)

    def test_restore_of_unknown_snapshot(self):
        with self.assertRaises(sm.ModelError):
            self.m.restore(9999)


class TestPages(Base):
    def test_add_with_generated_address_and_unique_ids(self):
        a = self.m.add_page("About Us")
        self.assertEqual((a["id"], a["slug"]), ("about-us", "about-us"))
        b = self.m.add_page("About Us", slug="about-us-2")
        self.assertNotEqual(a["id"], b["id"])

    def test_address_rules(self):
        for bad in ("Forge", "modules", "a/b/c/d", "x y"):
            with self.assertRaises(sm.ModelError, msg=bad):
                self.m.add_page("X", slug=bad)
        self.m.add_page("One", slug="one")
        with self.assertRaises(sm.ModelError):
            self.m.add_page("Two", slug="one")                       # duplicate address

    def test_redirect_source_blocks_that_address(self):
        self.m.set_redirects([{"from": "/old", "to": "/new/"}])
        with self.assertRaises(sm.ModelError):
            self.m.add_page("Old", slug="old")

    def test_update_and_flags(self):
        self.m.add_page("About")
        p = self.m.update_page("about", title="T" * 500, nav=False, listed=False, draft=True)
        self.assertEqual(len(p["title"]), 120)
        self.assertFalse(p["nav"] or p["listed"])
        self.assertTrue(p["draft"])
        with self.assertRaises(sm.ModelError):
            self.m.update_page("about", label="  ")

    def test_home_is_protected(self):
        with self.assertRaises(sm.ModelError):
            self.m.delete_page("home")
        with self.assertRaises(sm.ModelError):
            self.m.update_page("home", enabled=False)

    def test_move_delete_duplicate(self):
        self.m.add_page("A")
        self.m.add_page("B")
        self.m.move_page("b", -1)
        self.assertEqual([p["id"] for p in self.m.data["pages"]], ["home", "b", "a"])
        self.m.move_page("b", -99)
        self.assertEqual(self.m.data["pages"][0]["id"], "b")
        dup = self.m.duplicate_page("a")
        self.assertTrue(dup["draft"] and not dup["listed"])
        self.assertNotEqual(dup["slug"], "a")
        self.m.delete_page("a")
        self.assertNotIn("a", [p["id"] for p in self.m.data["pages"]])

    def test_unknown_theme_override_refused(self):
        self.m.add_page("A")
        with self.assertRaises(sm.ModelError):
            self.m.update_page("a", theme="nope")
        self.assertEqual(self.m.update_page("a", theme="neutral-light")["theme"], "neutral-light")


class TestBlocks(Base):
    def test_add_validates_against_the_block_schema(self):
        with self.assertRaises(sm.ModelError) as cm:
            self.m.add_block("home", "twitch", {})
        self.assertEqual(cm.exception.errors[0]["key"], "channel")
        with self.assertRaises(sm.ModelError):
            self.m.add_block("home", "no-such-block", {})
        b = self.m.add_block("home", "twitch", {"channel": "someone"})
        self.assertEqual(b["props"]["channel"], "someone")
        self.assertTrue(b["props"]["muted"])                        # default filled in

    def test_hostile_props_are_cleaned_or_refused(self):
        with self.assertRaises(sm.ModelError):
            self.m.add_block("home", "links", {"items": [{"label": "x", "url": "javascript:alert(1)"}]})
        b = self.m.add_block("home", "text", {"markdown": "<script>alert(1)</script>\x00hello"})
        self.assertNotIn("\x00", b["props"]["markdown"])             # stored as text; the renderer never makes HTML from it

    def test_edit_move_duplicate_delete(self):
        a = self.m.add_block("home", "text", {"markdown": "one"})
        b = self.m.add_block("home", "text", {"markdown": "two"})
        self.m.move_block("home", b["id"], -1)
        ids = [x["id"] for x in self.m.page("home")["blocks"]]
        self.assertLess(ids.index(b["id"]), ids.index(a["id"]))
        self.m.update_block("home", a["id"], props={"markdown": "ONE"}, enabled=False)
        self.assertEqual(self.m.block("home", a["id"])["props"]["markdown"], "ONE")
        self.assertFalse(self.m.block("home", a["id"])["enabled"])
        d = self.m.duplicate_block("home", a["id"])
        self.assertNotEqual(d["id"], a["id"])
        self.m.delete_block("home", d["id"])
        with self.assertRaises(sm.ModelError):
            self.m.block("home", d["id"])

    def test_failed_edit_changes_nothing(self):
        a = self.m.add_block("home", "text", {"markdown": "keep"})
        with self.assertRaises(sm.ModelError):
            self.m.update_block("home", a["id"], props={"markdown": ""})
        self.assertEqual(self.m.block("home", a["id"])["props"]["markdown"], "keep")

    def test_module_and_page_references(self):
        idx = {"bots": ("live", "live"), "notes": ("static", "doc")}
        ctx = self.m.schema_context(idx)
        self.m.add_block("home", "live-view", {"module": "bots"}, ctx=ctx)
        with self.assertRaises(sm.ModelError):
            self.m.add_block("home", "live-view", {"module": "notes"}, ctx=ctx)       # wrong kind
        with self.assertRaises(sm.ModelError):
            self.m.add_block("home", "live-view", {"module": "ghost"}, ctx=ctx)       # unknown
        with self.assertRaises(sm.ModelError):
            self.m.add_block("home", "cards", {"items": [{"title": "x", "page": "nowhere"}]}, ctx=ctx)
        self.m.add_page("About")
        self.m.add_block("home", "cards", {"items": [{"title": "x", "page": "about"}]}, ctx=self.m.schema_context(idx))


class TestSettings(Base):
    def test_brand(self):
        b = self.m.set_brand(name="Example", mark="Ex", tagline="t" * 500)
        self.assertEqual(len(b["tagline"]), 120)
        with self.assertRaises(sm.ModelError):
            self.m.set_brand(name="")
        with self.assertRaises(sm.ModelError):
            self.m.set_brand(mark="")
        with self.assertRaises(sm.ModelError):
            self.m.set_brand(icon={"mode": "weird"})

    def test_theme(self):
        t = self.m.set_theme("retro-terminal", {"--accent": "#112233", "--blank": ""}, "dark")
        self.assertEqual((t["id"], t["vars"], t["mode"]), ("retro-terminal", {"--accent": "#112233"}, "dark"))
        for kw in ({"theme_id": "nope"}, {"vars": {"--x": "red; }"}}, {"vars": {"bad": "#fff"}}, {"mode": "neon"}, {"vars": "x"}):
            with self.assertRaises(sm.ModelError, msg=str(kw)):
                self.m.set_theme(**kw)

    def test_settings(self):
        s = self.m.set_settings(lang="pt-BR", base_url="https://example.com/", csp="enforce", analytics="none", search=True)
        self.assertEqual((s["lang"], s["base_url"], s["csp"]), ("pt-BR", "https://example.com", "enforce"))
        for kw in ({"lang": "english"}, {"base_url": "http://x.com"}, {"base_url": "https://x.com/path"}, {"csp": "maybe"}, {"nav_style": "left"}):
            with self.assertRaises(sm.ModelError, msg=str(kw)):
                self.m.set_settings(**kw)

    def test_redirects(self):
        r = self.m.set_redirects([{"from": "/a", "to": "/b/"}, {"from": "/c", "to": "https://example.com", "status": 302}])
        self.assertEqual(len(r), 2)
        for bad in ([{"from": "a", "to": "/b"}], [{"from": "/a", "to": ""}], [{"from": "/a", "to": "/b"}, {"from": "/a", "to": "/c"}],
                    [{"from": "/a", "to": "javascript:1"}], [{"from": "/a", "to": "/b", "status": 200}], ["x"]):
            with self.assertRaises(sm.ModelError, msg=str(bad)):
                self.m.set_redirects(bad)

    def test_nav_links(self):
        self.assertEqual(len(self.m.set_nav_links([{"label": "Docs", "url": "https://example.com"}])), 1)
        with self.assertRaises(sm.ModelError):
            self.m.set_nav_links([{"label": "x", "url": "javascript:1"}])


class TestValidateWholeModel(Base):
    def test_blank_template_is_valid(self):
        errors, warnings = self.m.validate()
        self.assertEqual(errors, [])

    def test_missing_home_and_duplicates_are_errors(self):
        shell = {"id": "x", "slug": "x", "label": "X", "title": "", "description": "", "nav": True, "listed": True,
                 "enabled": True, "draft": False, "og_image": "", "theme": None, "area": None, "blocks": []}
        self.m.data["pages"].append(dict(shell))
        self.m.data["pages"].append(dict(shell, id="y"))
        errors, _ = self.m.validate()
        self.assertTrue(any("used twice" in e["message"] for e in errors))
        self.m.data["pages"] = [p for p in self.m.data["pages"] if p["slug"] != ""]
        errors, _ = self.m.validate()
        self.assertTrue(any("no home page" in e["message"] for e in errors))

    def test_broken_blocks_and_missing_theme_are_errors_and_missing_text_is_a_warning(self):
        self.m.data["pages"][0]["blocks"].append({"id": "b9", "type": "twitch", "props": {}, "enabled": True})
        self.m.data["pages"][0]["blocks"].append({"id": "b10", "type": "ghost", "props": {}, "enabled": True})
        self.m.data["theme"]["id"] = "vanished"
        self.m.data["pages"][0]["description"] = ""
        errors, warnings = self.m.validate()
        msgs = " | ".join(e["message"] for e in errors)
        self.assertIn("Channel is required", msgs)
        self.assertIn("no block type 'ghost'", msgs)
        self.assertIn("theme 'vanished'", msgs)
        self.assertTrue(any("no description" in w["message"] for w in warnings))

    def test_templates_load_and_report_what_to_fill_in(self):
        for tid in ("blank", "personal", "streamer", "link-in-bio", "portfolio", "docs-site"):
            self.m.load_template(tid)
            errors, _ = self.m.validate()
            for e in errors:                                          # only "fill this in" errors, never structural ones
                self.assertIn("required", e["message"], (tid, e))


if __name__ == "__main__":
    unittest.main()
