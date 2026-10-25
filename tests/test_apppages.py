"""Tier 3: an area's own pages (app/). Off unless the owner allows it; checked as written; published sandboxed."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import apppages
from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import store as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk                                  # noqa: E402
from test_bundle import AREA, Base                   # noqa: E402

PAGE = b'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Workshop app</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<h1 id="t">Hello</h1>
<p><a href="about.html">About</a> <a href="https://example.org/docs">Docs</a></p>
<img src="img/logo.png" alt="logo">
<script src="main.js" defer></script>
</body>
</html>
'''
ABOUT = b'<!doctype html><title>About</title><p>About this app. <a href="index.html">Back</a></p>\n'
CSS = b'body { margin: 0; background: #101418 url("img/logo.png"); }\nh1 { letter-spacing: .1em; }\n'
JS = b'''"use strict";
var t = document.getElementById("t");
document.addEventListener("click", function () { t.textContent = "clicked"; });
fetch("data/items.json").then(function (r) { return r.json(); }).then(function (d) { t.title = String(d.length); });
'''
APP = {"entry": "app/index.html", "label": "Play"}
SITE_HOSTS = ["https://example.com", "https://demo.pages.dev", "https://*.demo.pages.dev"]       # what the builder derives from domain "example.com", project "demo"
LOCAL_HOSTS = ["http://127.0.0.1:*", "http://localhost:*"]


def app_entries(extra=(), app=APP, doc_extra=None, drop=()):
    files = [("app/index.html", PAGE), ("app/about.html", ABOUT), ("app/style.css", CSS), ("app/main.js", JS), ("app/img/logo.png", zk.png()),
             ("app/data/items.json", b'[1, 2, 3]')]
    doc = dict(doc_extra or {})
    if app is not None:
        doc["app"] = app
    return [("area.json", zk.area_json(**doc))] + [f for f in files if f[0] not in drop] + list(extra)


def pages_area(**kw):
    return dict(AREA, custom_pages=True, limits=dict(AREA["limits"], max_files=300), **kw)


class TestCheckTree(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def put(self, files):
        for name, data in files:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

    def test_a_good_set_of_pages_passes(self):
        self.put(app_entries()[1:])
        r = apppages.check_tree(self.root)
        self.assertEqual((r.problems, r.warnings), ([], []))

    def test_each_file_is_checked_by_its_own_rules_and_named_in_the_reason(self):
        self.put(app_entries(drop=("app/main.js",))[1:] + [("app/main.js", b'eval("1")')])
        r = apppages.check_tree(self.root)
        self.assertTrue(any(p.startswith("app/main.js:") and "eval" in p for p in r.problems), r.problems)

    def test_a_page_that_points_at_a_missing_file_is_refused(self):
        self.put(app_entries(drop=("app/about.html",))[1:])
        r = apppages.check_tree(self.root)
        self.assertTrue(any("about.html" in p and "not in the bundle" in p for p in r.problems), r.problems)

    def test_a_json_file_must_be_json_and_a_stylesheet_may_only_use_its_own_pictures(self):
        self.put(app_entries(drop=("app/data/items.json",))[1:] + [("app/data/items.json", b"{nope")])
        self.assertTrue(any("items.json" in p and "JSON" in p for p in apppages.check_tree(self.root).problems))
        self.put([("app/data/items.json", b"[]"), ("app/style.css", b'a { background: url("https://x.example/a.png") }')])
        self.assertTrue(any("style.css" in p for p in apppages.check_tree(self.root).problems))

    def test_names_that_hide_a_program_type_or_use_a_forbidden_type_are_refused(self):
        for bad in ("app/run.exe", "app/pic.svg", "app/a.js.png", "app/x.php", "app/noext"):
            self.assertTrue(apppages.name_problem(bad), bad)
        for good in ("app/app.min.js", "app/index.html", "app/img/a.PNG", "app/data/x.json"):
            self.assertIsNone(apppages.name_problem(good), good)


class TestTheAppSection(unittest.TestCase):
    FILES = {"app/index.html", "app/x.js", "files/a.png"}

    def test_a_good_section(self):
        clean, errs = apppages.clean_app({"entry": "app/index.html", "label": "  Play  "}, self.FILES)
        self.assertEqual((clean, errs), ({"entry": "app/index.html", "label": "Play"}, []))

    def test_every_way_to_get_it_wrong_has_a_reason(self):
        for bad in ({"entry": "app/missing.html", "label": "x"}, {"entry": "app/x.js", "label": "x"}, {"entry": "files/a.png", "label": "x"},
                    {"entry": "../app/index.html", "label": "x"}, {"entry": "app/index.html"}, {"entry": "app/index.html", "label": ""},
                    {"entry": "app/index.html", "label": "x", "extra": 1}, "app/index.html", None):
            clean, errs = apppages.clean_app(bad, self.FILES)
            self.assertIsNone(clean, bad)
            self.assertTrue(errs, bad)

    def test_a_long_label_is_trimmed_like_other_names(self):
        clean, errs = apppages.clean_app({"entry": "app/index.html", "label": "x" * 60}, self.FILES)
        self.assertEqual((len(clean["label"]), errs), (apppages.MAX_LABEL, []))


class TestBundle(Base):
    def test_an_allowed_set_of_pages_is_accepted_and_kept_exactly_as_written(self):
        res = self.run_bundle(app_entries(), area=pages_area())
        tree = self.work / "tree"
        saved = json.loads((tree / "area.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["app"], APP)
        self.assertEqual((tree / "app" / "main.js").read_bytes(), JS)
        self.assertEqual((tree / "app" / "index.html").read_bytes(), PAGE)
        self.assertEqual(res["warnings"], [])
        self.assertIn("app/img/logo.png", res["files"])

    def test_not_allowed_means_refused_with_a_reason(self):
        e = self.refused(app_entries(), area=dict(AREA, limits=dict(AREA["limits"], max_files=300)))
        self.assertIn("own pages", " ".join(e.reasons))

    def test_an_app_section_without_any_files_is_ignored_with_a_note_when_not_allowed(self):
        res = self.run_bundle([("area.json", zk.area_json(app=APP))])
        self.assertTrue(any("'app' section was ignored" in w for w in res["warnings"]), res["warnings"])
        self.assertNotIn("app", json.loads((self.work / "tree" / "area.json").read_text(encoding="utf-8")))

    def test_files_without_an_app_section_and_a_bad_section_are_refused(self):
        self.refused(app_entries(app=None), expect="app", area=pages_area())
        self.refused(app_entries(app={"entry": "app/nope.html", "label": "x"}), expect="not in the bundle", area=pages_area())
        self.refused(app_entries(app={"entry": "app/index.html"}), expect="label", area=pages_area())

    def test_hostile_pages_are_refused_naming_the_file(self):
        bad_js = app_entries(drop=("app/main.js",), extra=[("app/main.js", b"var u = window.open('x');")])
        self.refused(bad_js, expect="app/main.js", area=pages_area())
        bad_html = app_entries(drop=("app/index.html",), extra=[("app/index.html", PAGE.replace(b"<h1", b"<h1 onclick='x()'"))])
        self.refused(bad_html, expect="app/index.html", area=pages_area())
        missing = app_entries(drop=("app/main.js",))
        self.refused(missing, expect="main.js", area=pages_area())

    def test_file_types_outside_the_allowed_list_are_refused(self):
        for name in ("app/run.exe", "app/pic.svg", "app/a.js.png", "app/sound.mp3", "app/font.woff2"):
            self.refused(app_entries(extra=[(name, b"x")]), area=pages_area())

    def test_a_page_may_not_be_named_app_and_names_may_not_escape(self):
        doc = zk.area_json(app=APP).decode()
        data = json.loads(doc)
        data["pages"].append({"name": "app", "title": "x", "blocks": []})
        self.refused([("area.json", json.dumps(data).encode())] + app_entries()[1:], expect="reserved", area=pages_area())
        self.refused(app_entries(extra=[("app/../escape.js", b"var a = 1;")]), area=pages_area())

    def test_the_number_of_files_is_limited(self):
        many = [("app/d/f%03d.txt" % i, b"x") for i in range(apppages.MAX_APP_FILES + 1)]
        self.refused(app_entries(extra=many), expect="own pages have too many files", area=pages_area())

    def test_an_area_stylesheet_rule_does_not_catch_the_pages_own_stylesheets(self):
        self.run_bundle(app_entries(), area=dict(pages_area(), custom_css=False))
        self.assertTrue((self.work / "tree" / "app" / "style.css").is_file())


class TestPublishedBuild(unittest.TestCase):
    """Published at /<slug>/app/, sandboxed, linked from the area's menu, and checked again at build time."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = rg.Registry()
        self.model = sm.SiteModel(self.root / "hub", self.reg)
        self.store = st.Store(self.root / "hub")
        self.stage = self.root / "stage"
        self.model.load_template("blank")
        self.store.state["settings"].update(domain="example.com", project="demo")

    def tearDown(self):
        self._t.cleanup()

    def tree(self, files=None, app=APP):
        tree = self.root / "tree"
        for name, data in (files if files is not None else app_entries()[1:]):
            p = tree / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        (tree / "files").mkdir(exist_ok=True)
        doc = {"format": 1, "title": "Workshop", "description": "", "pages": [
            {"name": "", "title": "Home", "blocks": [{"id": "b1", "type": "text", "props": {"markdown": "hello"}}]}]}
        if app:
            doc["app"] = app
        (tree / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        return tree

    def build(self, pages=True, **kw):
        areas = [{"id": "ws", "slug": "ws", "name": "Workshop", "nav": True, "tree": str(self.tree(**kw)), "blocks": ["text"], "embed_hosts": [],
                  "themes": [], "custom_pages": pages}]
        return sb.build_site(self.model, self.reg, self.store, self.stage, areas=areas)

    def headers(self, path):
        return sb.headers_for(sb.parse_headers((self.stage / "_headers").read_text(encoding="utf-8")), path)

    def test_the_pages_are_published_under_the_areas_app_folder_exactly_as_checked(self):
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        for name, data in app_entries()[1:]:
            self.assertEqual((self.stage / "ws" / name).read_bytes(), data, name)
        self.assertFalse((self.stage / "app").exists())

    def test_the_area_menu_links_to_the_entry_page(self):
        self.build()
        html = (self.stage / "ws" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<a class="hs-app" href="/ws/app/">Play</a>', html)

    def test_everything_under_app_gets_exactly_one_enforced_sandbox_policy(self):
        self.build()
        h = self.headers("/ws/app/index.html")
        policy = {k.lower(): v for k, v in h.items()}
        self.assertEqual(policy["content-security-policy"], apppages.app_csp(SITE_HOSTS))
        self.assertEqual(policy["access-control-allow-origin"], "*")
        self.assertNotIn("content-security-policy-report-only", policy)
        self.assertTrue(policy["content-security-policy"].startswith("sandbox allow-scripts;"))
        for host in SITE_HOSTS:                                                                         # named outright too: browsers differ on 'self' in a sandbox
            self.assertIn(host, policy["content-security-policy"])
        self.assertNotIn("https:", policy["content-security-policy"].replace("https://", ""))          # never a blanket scheme
        self.assertNotIn("allow-same-origin", policy["content-security-policy"])
        self.assertEqual(policy["x-content-type-options"], "nosniff")
        other = {k.lower(): v for k, v in self.headers("/ws/").items()}
        self.assertNotEqual(other.get("content-security-policy"), apppages.app_csp(SITE_HOSTS))             # the area's ordinary pages are not sandboxed this way

    def test_the_policy_is_enforced_even_when_the_sites_own_policy_is_only_reporting_or_off(self):
        for mode in ("report-only", "off"):
            self.model.data["settings"]["csp"] = mode
            self.build()
            policy = {k.lower(): v for k, v in self.headers("/ws/app/a.html").items()}
            self.assertEqual(policy.get("content-security-policy"), apppages.app_csp(SITE_HOSTS), mode)
            self.assertNotIn("content-security-policy-report-only", policy, mode)

    def test_switched_off_means_left_out_with_a_warning_and_no_link(self):
        res = self.build(pages=False)
        self.assertTrue(res.ok, res.errors)
        self.assertFalse((self.stage / "ws" / "app").exists())
        self.assertTrue(any("own pages" in w["message"] and "no longer allowed" in w["message"] for w in res.warnings), res.warnings)
        self.assertNotIn("hs-app", (self.stage / "ws" / "index.html").read_text(encoding="utf-8"))
        self.assertNotIn("/ws/app/", (self.stage / "_headers").read_text(encoding="utf-8"))

    def test_pages_that_no_longer_pass_the_checks_are_left_out_at_build_time(self):
        files = app_entries(drop=("app/main.js",))[1:] + [("app/main.js", b"eval('x')")]
        res = self.build(files=files)
        self.assertTrue(res.ok, res.errors)
        self.assertFalse((self.stage / "ws" / "app").exists())
        self.assertTrue(any("left out" in w["message"] and "eval" in w["message"] for w in res.warnings), res.warnings)

    def test_the_collaborators_preview_serves_their_folder_at_app_with_the_same_policy(self):
        tree = self.tree()
        res = sb.build_site(self.model, self.reg, self.store, self.stage, url_base="/preview", area_app_dir=tree)
        self.assertTrue(res.ok, res.errors)
        self.assertEqual((self.stage / "app" / "main.js").read_bytes(), JS)
        self.assertEqual({k.lower(): v for k, v in self.headers("/app/index.html").items()}["content-security-policy"], apppages.app_csp(LOCAL_HOSTS))

    def test_a_preview_leaves_out_pages_the_owner_would_refuse(self):
        tree = self.tree(files=app_entries(drop=("app/main.js",))[1:] + [("app/main.js", b"eval('x')")])
        res = sb.build_site(self.model, self.reg, self.store, self.stage, area_app_dir=tree)
        self.assertFalse((self.stage / "app").exists())
        self.assertTrue(any("Preview leaves out your own pages" in w["message"] for w in res.warnings), res.warnings)




class TestOwnerSide(unittest.TestCase):
    """The switch, owner edit carrying the pages, the review card, and reading the code before approving."""

    def setUp(self):
        from test_areas import Clock
        from halbach_connecthub import areas
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.store = st.Store(self.root / "hub")
        self.svc = areas.AreaService(self.store, rg.Registry(), min_free_bytes=0, clock=Clock())
        self.svc.create("Workshop", limits={"max_files": 300})
        self.zip_n = 0

    def tearDown(self):
        self._t.cleanup()

    def send(self, entries):
        self.zip_n += 1
        z = self.root / ("in%d.zip" % self.zip_n)
        zk.make_zip(z, entries)
        return self.svc.ingest("workshop", z, "ip")

    def test_it_is_off_by_default_and_the_owner_can_switch_it_on_and_off(self):
        view = self.svc.get("workshop")
        self.assertFalse(view.get("custom_pages"))
        self.assertFalse(self.svc.sender_limits(view)["custom_pages"])
        self.assertTrue(self.svc.update("workshop", custom_pages=True)["custom_pages"])
        self.assertTrue(self.svc.sender_limits(self.svc.get("workshop"))["custom_pages"])
        self.assertEqual(self.svc.trees(), [])
        self.assertFalse(self.svc.update("workshop", custom_pages=False)["custom_pages"])

    def test_pages_sent_while_it_is_off_are_refused_and_nothing_is_left_behind(self):
        meta = self.send(app_entries())
        self.assertEqual(meta["state"], "invalid")
        self.assertIn("own pages", " ".join(meta["reasons"]))

    def test_a_good_submission_waits_for_review_and_the_card_lists_the_files(self):
        self.svc.update("workshop", custom_pages=True)
        meta = self.send(app_entries())
        self.assertEqual(meta["state"], "pending", meta.get("reasons"))
        self.assertIn("app/index.html", meta["changes"]["files_added"])
        self.assertIn("app/main.js", meta["changes"]["files_added"])

    def test_the_owner_can_read_the_code_of_a_pending_submission_and_nothing_else(self):
        from halbach_connecthub.store import ValidationError
        self.svc.update("workshop", custom_pages=True)
        meta = self.send(app_entries())
        got = self.svc.source("workshop", meta["id"], "app/main.js")
        self.assertEqual((got["path"], got["text"]), ("app/main.js", JS.decode()))
        for bad in ("area.json", "../area.json", "app/../../meta.json", "app/img/logo.png", "app/missing.js", "/etc/passwd", "app" + chr(92) + "main.js", None, 5):
            with self.assertRaises(ValidationError, msg=bad):
                self.svc.source("workshop", meta["id"], bad)
        with self.assertRaises(ValidationError):
            self.svc.source("workshop", "nope", "app/main.js")

    def test_an_owner_edit_carries_the_pages_over_and_a_changed_page_shows_up_in_the_card(self):
        self.svc.update("workshop", custom_pages=True)
        first = self.send(app_entries())
        self.svc.approve("workshop", first["id"])
        content = self.svc.content("workshop")
        self.assertTrue(content["custom_pages"])
        self.assertIn("app/main.js", content["app_files"])
        edited = self.svc.owner_edit("workshop", content["doc"])
        self.assertEqual((edited["state"], edited["origin"]), ("pending", "owner"))
        tree = self.svc._sub_dir("workshop", edited["id"]) / "tree"
        self.assertEqual((tree / "app" / "main.js").read_bytes(), JS)
        self.assertEqual(edited["changes"]["files_added"] + edited["changes"]["files_changed"] + edited["changes"]["files_removed"], [])
        newer = self.send(app_entries(drop=("app/main.js",), extra=[("app/main.js", JS + b"// a change" + bytes([10]))]))
        self.assertEqual(newer["changes"]["files_changed"], ["app/main.js"])

    def test_code_always_waits_for_the_owner_even_in_a_trusted_area_but_other_edits_do_not(self):
        self.svc.update("workshop", custom_pages=True, trust="trusted")
        first = self.send(app_entries())
        self.assertEqual(first["state"], "pending")                                          # trusted, yet pending: it is code
        self.assertTrue(first["needs_reading"])
        self.assertTrue(any("waits for the owner to read it" in w for w in first["warnings"]), first["warnings"])
        self.svc.approve("workshop", first["id"])
        same_code = self.send(app_entries(doc_extra={"description": "a new description"}))   # only the words around it changed
        self.assertEqual(same_code["state"], "approved", same_code.get("reasons"))
        changed = self.send(app_entries(drop=("app/main.js",), extra=[("app/main.js", JS + b"// more")]))
        self.assertEqual(changed["state"], "pending")
        relabelled = self.send(app_entries(app={"entry": "app/index.html", "label": "Other"}))
        self.assertEqual(relabelled["state"], "pending")

    def test_an_owner_edit_still_works_after_the_switch_is_turned_off_and_shows_the_pages_as_removed(self):
        self.svc.update("workshop", custom_pages=True)
        self.svc.approve("workshop", self.send(app_entries())["id"])
        self.svc.update("workshop", custom_pages=False)
        content = self.svc.content("workshop")
        edited = self.svc.owner_edit("workshop", content["doc"])
        self.assertEqual(edited["state"], "pending", edited.get("reasons"))
        self.assertIn("app/main.js", edited["changes"]["files_removed"])
        self.assertTrue(edited["changes"]["app_changed"])
        self.assertTrue(edited["needs_reading"])                                              # dropping code is a change to it too

    def test_the_gate_looks_at_every_change_not_only_the_first_fifty_the_card_lists(self):
        self.svc.update("workshop", custom_pages=True, trust="trusted")
        many = [("files/note%03d.txt" % i, b"x") for i in range(60)]
        first = self.send(app_entries(extra=many))
        self.svc.approve("workshop", first["id"])
        changed = self.send(app_entries(drop=("app/main.js",), extra=many + [("app/main.js", JS + b"// more")]))
        self.assertEqual(changed["state"], "pending")
        self.assertTrue(changed["changes"]["app_touched"])

    def test_approved_pages_reach_the_builder_only_while_the_switch_is_on(self):
        self.svc.update("workshop", custom_pages=True)
        self.svc.approve("workshop", self.send(app_entries())["id"])
        self.assertTrue(self.svc.trees()[0]["custom_pages"])
        self.svc.update("workshop", custom_pages=False)
        self.assertFalse(self.svc.trees()[0]["custom_pages"])


class TestCollaboratorSide(unittest.TestCase):
    """What the collaborator's folder, client and window do with the pages."""

    @classmethod
    def setUpClass(cls):
        import http.client
        import zipfile
        from halbach_connecthub import areaclient, areamode, server
        cls.areaclient, cls.areamode, cls.zipfile, cls.http = areaclient, areamode, zipfile, http.client
        cls._t = tempfile.TemporaryDirectory()
        cls.folder = Path(cls._t.name) / "page"
        areaclient.init_folder(cls.folder)
        for name, data in app_entries()[1:]:
            p = cls.folder / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        doc = json.loads((cls.folder / "area.json").read_text(encoding="utf-8"))
        doc["app"] = APP
        (cls.folder / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok", area=True, area_folder=str(cls.folder))
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None, token="tok"):
        c = self.http.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token:
            h["X-Halbach-Token"] = token
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        headers = dict(r.getheaders())
        c.close()
        return r.status, raw, headers

    def test_the_zip_carries_app_only_when_the_owner_allows_it(self):
        out = self.folder.parent / "a.zip"
        with self.assertRaises(self.areaclient.ClientError) as cm:
            self.areaclient.build_zip(self.folder, out, None)
        self.assertIn("not allowed this area its own pages", str(cm.exception))
        self.areaclient.build_zip(self.folder, out, {"max_files": 300}, custom_pages=True)
        with self.zipfile.ZipFile(out) as z:
            names = set(z.namelist())
        self.assertTrue({"area.json", "app/index.html", "app/main.js", "app/img/logo.png"} <= names)

    def test_the_offline_check_runs_the_owners_own_validator_over_the_pages(self):
        summary = self.areaclient.validate_folder(self.folder, {"max_files": 300}, custom_pages=True)
        self.assertEqual(summary["warnings"], [])
        (self.folder / "app" / "bad.js").write_bytes(b"eval('1')")
        try:
            with self.assertRaises(Exception) as cm:
                self.areaclient.validate_folder(self.folder, {"max_files": 300}, custom_pages=True)
            self.assertIn("eval", " ".join(getattr(cm.exception, "reasons", [str(cm.exception)])))
        finally:
            (self.folder / "app" / "bad.js").unlink()

    def test_saving_the_look_in_the_window_does_not_lose_the_pages_section(self):
        self.areamode.dump_area(self.hub.site.model, self.folder, self.hub.design)
        self.assertEqual(json.loads((self.folder / "area.json").read_text(encoding="utf-8")).get("app"), APP)

    def test_the_window_says_what_it_knows_about_the_pages(self):
        s, raw, _ = self.req("GET", "/api/area/design")
        pages = json.loads(raw)["pages"]
        self.assertEqual((pages["app"], pages["files"], pages["allowed"]), (APP, len(app_entries()) - 1, None))      # not connected: unknown

    def test_preview_serves_the_pages_sandboxed_exactly_as_the_real_site_will(self):
        s, raw, _ = self.req("POST", "/api/site/preview", {})
        self.assertEqual(s, 200, raw)
        self.assertTrue(json.loads(raw)["ok"], raw)
        s, body, headers = self.req("GET", "/preview/app/index.html", token=None)
        self.assertEqual(s, 200)
        self.assertEqual(body, PAGE)
        policy = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(policy.get("content-security-policy"), apppages.app_csp(LOCAL_HOSTS))
        self.assertEqual(policy.get("access-control-allow-origin"), "*")
        self.assertNotIn("content-security-policy-report-only", policy)
        s, js, _ = self.req("GET", "/preview/app/main.js", token=None)
        self.assertEqual((s, js), (200, JS))


if __name__ == "__main__":
    unittest.main()
