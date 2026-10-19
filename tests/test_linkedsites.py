import email.message
import json
import re
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from halbach_connecthub import linkedsites as ls
from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st

PLAIN = "https://funkstudio-share.example-user.workers.dev/"
KEYED = PLAIN + "?k=SECRETVALUE123"


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"<html>"):
        self.status = status
        m = email.message.Message()
        for k, v in (headers or {}).items():
            m[k] = v
        self.headers = m
        self._b = body

    def read(self, n=-1):
        return self._b[:n]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, result):
        self.result, self.calls = result, []

    def open(self, req, timeout=None):
        self.calls.append((req.full_url, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class TestHelpers(unittest.TestCase):
    def test_keys_in_addresses_are_found_by_name_never_by_value(self):
        self.assertEqual(ls.secret_params(KEYED), ["k"])
        self.assertEqual(ls.secret_params("https://x.dev/?a=1&Token=abc&b=2"), ["token"])
        self.assertEqual(ls.secret_params("https://x.dev/#key=abc"), ["key"])
        self.assertEqual(ls.secret_params("https://user:pw@x.dev/"), ["user:password in the address"])
        self.assertEqual(ls.secret_params(PLAIN), [])
        self.assertEqual(ls.secret_params("https://x.dev/?k"), [])           # a bare flag carries no value
        self.assertNotIn("SECRETVALUE123", str(ls.secret_params(KEYED)))

    def test_cloudflare_hosts(self):
        self.assertTrue(ls.looks_cloudflare("https://a.b.workers.dev/x"))
        self.assertTrue(ls.looks_cloudflare("https://site.pages.dev"))
        self.assertFalse(ls.looks_cloudflare("https://example.com"))

    def test_probe_never_touches_private_or_non_https_addresses(self):
        for url in ("http://example.com", "https://localhost/", "https://127.0.0.1/", "https://10.0.0.5/", "https://192.168.1.1/x",
                    "https://[::1]/", "https://printer.local/"):
            op = FakeOpener(FakeResponse())
            r = ls.probe(url, opener=op)
            self.assertFalse(r["reachable"], url)
            self.assertEqual(op.calls, [], url)

    def test_probe_reads_a_cloudflare_site_and_its_framing_rules(self):
        with mock.patch.object(ls, "is_private_host", return_value=False):
            ok = ls.probe(PLAIN, opener=FakeOpener(FakeResponse(200, {"Server": "cloudflare", "CF-Ray": "abc"})))
            self.assertEqual((ok["reachable"], ok["cloudflare"], ok["embeddable"]), (True, True, True))
            blocked = ls.probe(PLAIN, opener=FakeOpener(FakeResponse(200, {"X-Frame-Options": "DENY"})))
            self.assertIs(blocked["embeddable"], False)
            self.assertTrue(any("inside other sites" in n for n in blocked["notes"]))
            csp = ls.probe(PLAIN, opener=FakeOpener(FakeResponse(200, {"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"})))
            self.assertIs(csp["embeddable"], False)

    def test_probe_survives_errors_and_logins(self):
        with mock.patch.object(ls, "is_private_host", return_value=False):
            hdr = email.message.Message()
            r403 = ls.probe(PLAIN, opener=FakeOpener(urllib.error.HTTPError(PLAIN, 403, "no", hdr, None)))
            self.assertTrue(r403["reachable"])
            self.assertTrue(any("login or key" in n for n in r403["notes"]))
            r500 = ls.probe(PLAIN, opener=FakeOpener(urllib.error.HTTPError(PLAIN, 500, "boom", hdr, None)))
            self.assertFalse(r500["reachable"])
            gone = ls.probe(PLAIN, opener=FakeOpener(urllib.error.URLError("dns failure")))
            self.assertFalse(gone["reachable"])
            self.assertTrue(gone["notes"])

    def test_probe_refuses_ipv4_mapped_ipv6_private_addresses(self):
        mapped_resolver = lambda host: [(10, 1, 6, "", ("::ffff:127.0.0.1", 0, 0, 0))]
        op = FakeOpener(FakeResponse())
        r = ls.probe(PLAIN, opener=op, resolver=mapped_resolver)
        self.assertFalse(r["reachable"])
        self.assertEqual(op.calls, [])
        self.assertTrue(any("private network" in n for n in r["notes"]))

    def test_probe_resolves_once_and_refuses_private_results(self):
        private_resolver = lambda host: [(2, 1, 6, "", ("10.0.0.5", 0))]
        op = FakeOpener(FakeResponse())
        r = ls.probe(PLAIN, opener=op, resolver=private_resolver)
        self.assertFalse(r["reachable"])
        self.assertEqual(op.calls, [])
        self.assertTrue(any("private network" in n for n in r["notes"]))

    def test_probe_proceeds_when_resolver_returns_a_public_address(self):
        public_resolver = lambda host: [(2, 1, 6, "", ("93.184.216.34", 0))]
        op = FakeOpener(FakeResponse(200, {"Server": "cloudflare", "CF-Ray": "abc"}))
        r = ls.probe(PLAIN, opener=op, resolver=public_resolver)
        self.assertTrue(r["reachable"])
        self.assertEqual(len(op.calls), 1)
        with mock.patch.object(ls, "is_private_host", return_value=False):
            op = FakeOpener(FakeResponse())
            ls.probe(PLAIN, opener=op)
            self.assertEqual(op.calls[0][1], ls.PROBE_TIMEOUT)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.reg = rg.Registry()
        self.m = sm.SiteModel(self.root / "hub", self.reg)
        self.m.load_template("personal")
        self.store = st.Store(self.root / "hub")

    def tearDown(self):
        self._t.cleanup()

    def build(self):
        res = sb.build_site(self.m, self.reg, self.store, self.root / "stage")
        self.assertTrue(res.ok, res.errors)
        return res


class TestModel(Base):
    def test_only_full_https_addresses_can_be_linked(self):
        for bad in ("http://x.dev", "javascript:alert(1)", "file:///c:/x", "//x.dev", "/local", "x.dev", "https://x y.dev", "", None):
            with self.assertRaises(sm.ModelError, msg=str(bad)):
                self.m.add_linked("X", bad)

    def test_a_key_in_the_address_needs_an_explicit_confirmation(self):
        with self.assertRaises(sm.ModelError) as cm:
            self.m.add_linked("Funk Studio", KEYED)
        self.assertIn("key in it (k)", str(cm.exception))
        self.assertNotIn("SECRETVALUE123", str(cm.exception))
        self.assertEqual(self.m.data["linked_sites"], [])
        self.m.add_linked("Funk Studio", KEYED, confirm_public_key=True)
        self.assertEqual(len(self.m.data["linked_sites"]), 1)

    def test_confirmed_keys_are_still_flagged_by_the_checks_without_printing_them(self):
        self.m.add_linked("Funk Studio", KEYED, confirm_public_key=True)
        errs, warns = self.m.validate()
        self.assertEqual(errs, [])
        msgs = " ".join(w["message"] for w in warns)
        self.assertIn("has a key in it", msgs)
        self.assertNotIn("SECRETVALUE123", msgs)

    def test_own_page_embed_makes_a_page_and_allows_the_frame(self):
        e = self.m.add_linked("Funk Studio", PLAIN, "Funk's share site", mode="embed", own_page=True, menu=True)
        page = self.m.page(e["page"])
        self.assertTrue(page["nav"])
        self.assertEqual(page["blocks"][-1]["type"], "embed")
        self.build()
        headers = (self.root / "stage" / "_headers").read_text(encoding="utf-8")
        self.assertIn("https://funkstudio-share.example-user.workers.dev", headers)
        self.assertTrue((self.root / "stage" / page["slug"] / "index.html").is_file())

    def test_own_page_link_mode_and_menu_only_mode(self):
        e = self.m.add_linked("Friend", "https://friend.pages.dev", own_page=True)
        self.assertEqual(self.m.page(e["page"])["blocks"][-1]["type"], "links")
        self.m.add_linked("Other", "https://other.pages.dev", menu=True)
        self.assertEqual(self.m.data["nav_links"][-1]["url"], "https://other.pages.dev")

    def test_delete_cleans_up_its_page_and_menu_link(self):
        a = self.m.add_linked("A", "https://a.pages.dev", own_page=True)
        b = self.m.add_linked("B", "https://b.pages.dev", menu=True)
        self.m.delete_linked(a["id"])
        self.m.delete_linked(b["id"])
        self.assertEqual(self.m.data["linked_sites"], [])
        self.assertEqual(self.m.data["nav_links"], [])
        self.assertNotIn(a["page"], [p["id"] for p in self.m.data["pages"]])

    def test_changing_the_address_updates_its_page_and_menu(self):
        e = self.m.add_linked("A", "https://a.pages.dev", mode="embed", own_page=True)
        self.m.update_linked(e["id"], url="https://a2.pages.dev")
        self.assertEqual(self.m.page(e["page"])["blocks"][-1]["props"]["url"], "https://a2.pages.dev")
        with self.assertRaises(sm.ModelError):
            self.m.update_linked(e["id"], url=KEYED)

    def test_ids_are_unique_and_the_list_is_capped(self):
        a = self.m.add_linked("Same", "https://a.pages.dev")
        b = self.m.add_linked("Same", "https://b.pages.dev")
        self.assertNotEqual(a["id"], b["id"])

    def test_the_linked_sites_block_lists_them_on_a_page(self):
        self.m.add_linked("Funk Studio", PLAIN, "Funk's share site")
        self.m.add_linked("Friend", "https://friend.pages.dev")
        self.m.add_block("about", "linked-sites", {"ids": ["friend"], "style": "cards"})
        self.build()
        text = (self.root / "stage" / "about" / "index.html").read_text(encoding="utf-8")
        pj = json.loads(re.search(r'id="hs-page">(.*?)</script>', text, re.S).group(1))
        block = [b for b in pj["blocks"] if b["type"] == "linked-sites"][0]
        self.assertEqual([i["href"] for i in block["props"]["items"]], ["https://friend.pages.dev"])

    def test_export_import_and_history_carry_linked_sites(self):
        self.m.add_linked("Funk Studio", PLAIN)
        self.m.save("linked")
        self.assertEqual(sm.SiteModel(self.root / "hub", self.reg).data["linked_sites"][0]["label"], "Funk Studio")
        self.assertIn("linked_sites changed", sm.diff_models({"pages": [], "linked_sites": []}, self.m.data))


class TestService(Base):
    def test_check_does_not_change_anything_and_uses_the_probe(self):
        svc = ss.SiteService(self.store)
        svc.apply("template.load", {"id": "personal"})
        svc.apply("linked.add", {"label": "Funk", "url": PLAIN})
        before = json.dumps(svc.model.data, sort_keys=True)
        with mock.patch.object(ls, "probe", return_value={"reachable": True, "notes": [], "cloudflare": True, "embeddable": True, "status": 200}) as p:
            out = svc.apply("linked.check", {"id": "funk"})
            self.assertTrue(out["reachable"])
            self.assertEqual(p.call_args[0][0], PLAIN)
            svc.apply("linked.check", {"url": "https://other.pages.dev"})
            self.assertEqual(p.call_args[0][0], "https://other.pages.dev")
        self.assertEqual(json.dumps(svc.model.data, sort_keys=True), before)
        with self.assertRaises(sm.ModelError):
            svc.apply("linked.check", {})

    def test_refused_edits_roll_back(self):
        svc = ss.SiteService(self.store)
        svc.apply("template.load", {"id": "personal"})
        before = json.dumps(svc.model.data, sort_keys=True)
        with self.assertRaises(sm.ModelError):
            svc.apply("linked.add", {"label": "Funk", "url": KEYED, "own_page": True})
        self.assertEqual(json.dumps(svc.model.data, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main()
