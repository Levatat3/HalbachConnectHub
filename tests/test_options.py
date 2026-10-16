import http.client
import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import live, server
from halbach_connecthub import publisher as pub
from halbach_connecthub import store as st


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.s = st.Store(self.tmp / "hub")
        self.live = self.s.add_module("live", "Pets", "127.0.0.1:8792/pets.json")
        d = self.tmp / "sprites"
        d.mkdir()
        (d / "a.png").write_bytes(b"\x89PNG")
        self.static = self.s.add_module("static", "Sprites", str(d))

    def tearDown(self):
        self._t.cleanup()


class TestSetOptions(Base):
    def test_live_options_are_stored(self):
        m = self.s.set_options(self.live["id"], view="pets", hide=["Alpha", " Beta ", "Alpha", "", "x" * 61], poll="15")
        self.assertEqual((m["view"], m["hide"], m["poll"]), ("pets", ["Alpha", "Beta"], 15))

    def test_hide_accepts_a_comma_separated_string(self):
        self.assertEqual(self.s.set_options(self.live["id"], hide="A, B ,,C")["hide"], ["A", "B", "C"])
        self.assertEqual(self.s.set_options(self.live["id"], hide="")["hide"], [])

    def test_validation(self):
        i = self.live["id"]
        for kw in ({"view": "evil"}, {"poll": 0}, {"poll": 61}, {"poll": "abc"}, {"listed": False}, {"hide": ["a"] * 0 + [str(n) for n in range(60)]}):
            with self.assertRaises(st.ValidationError, msg=str(kw)):
                self.s.set_options(i, **kw)
        with self.assertRaises(st.ValidationError):
            self.s.set_options(self.static["id"], view="pets")
        with self.assertRaises(st.ValidationError):
            self.s.set_options(self.static["id"], hide=["x"])

    def test_static_listed(self):
        self.assertFalse(self.s.set_options(self.static["id"], listed=False)["listed"])
        self.assertTrue(self.s.set_options(self.static["id"], listed=True)["listed"])

    def test_survives_restart(self):
        self.s.set_options(self.live["id"], view="pets", hide=["Alpha"])
        again = st.Store(self.s.home)
        m = [x for x in again.state["modules"] if x["id"] == self.live["id"]][0]
        self.assertEqual((m["view"], m["hide"]), ("pets", ["Alpha"]))


class TestHiding(unittest.TestCase):
    FIELDS = [{"name": "bots", "allowed": True}, {"name": "t", "allowed": True}, {"name": "secret_note", "allowed": True}]

    def data(self):
        return {"t": 1, "bots": [{"name": "MyBot", "pets": [1]}, {"name": "Alpha", "pets": []}, {"name": "Beta"}]}

    def test_hidden_items_are_dropped_from_a_list_inside_an_allowed_field(self):
        out = live.filter_payload(self.data(), self.FIELDS, hide=["Alpha", "Beta"])
        self.assertEqual([b["name"] for b in out["bots"]], ["MyBot"])
        self.assertEqual(out["t"], 1)

    def test_hidden_items_are_dropped_from_a_top_level_list(self):
        fields = [{"name": "name", "allowed": True}]
        out = live.filter_payload([{"name": "A"}, {"name": "B"}], fields, hide=["B"])
        self.assertEqual(out, [{"name": "A"}])

    def test_nothing_hidden_by_default(self):
        self.assertEqual(len(live.filter_payload(self.data(), self.FIELDS)["bots"]), 3)

    def test_hiding_does_not_touch_other_lists(self):
        out = live.filter_payload({"bots": [{"name": "X"}], "tags": ["Alpha"]}, [{"name": "bots", "allowed": True}, {"name": "tags", "allowed": True}], hide=["Alpha"])
        self.assertEqual(out["tags"], ["Alpha"])                    # a list of strings is not an item list

    def test_hidden_names_never_appear_in_the_output(self):
        out = json.dumps(live.filter_payload(self.data(), self.FIELDS, hide=["Alpha", "Beta"]))
        self.assertNotIn("Alpha", out)
        self.assertNotIn("Beta", out)


class TestApiAndManifest(unittest.TestCase):
    def test_options_endpoint_and_manifest(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            srv, hub = server.serve(tmp / "hub", 0, token="tok")
            server.run_in_thread(srv)
            port = srv.server_address[1]

            def api(path, body):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                c.request("POST", path, json.dumps(body).encode(), {"Host": "127.0.0.1:%d" % port, "X-Halbach-Token": "tok", "Content-Type": "application/json"})
                r = c.getresponse()
                out = json.loads(r.read())
                c.close()
                return r.status, out
            try:
                lm = hub.store.add_module("live", "Pets", "127.0.0.1:8792/pets.json")
                sprites = tmp / "sp"
                sprites.mkdir()
                (sprites / "a.png").write_bytes(b"\x89PNG")
                sm = hub.store.add_module("static", "Pet sprites", str(sprites))
                site = tmp / "site"
                site.mkdir()
                (site / "index.html").write_text("x")
                hub.store.set_settings(site_dir=str(site), project="p", live_host="live.example.com")
                self.assertEqual(api("/api/modules/%s/options" % lm["id"], {"view": "pets", "hide": "Alpha, Beta", "poll": 15})[0], 200)
                self.assertEqual(api("/api/modules/%s/options" % sm["id"], {"listed": False})[0], 200)
                s, err = api("/api/modules/%s/options" % lm["id"], {"view": "nope"})
                self.assertEqual(s, 400)
                for m in (lm, sm):
                    hub.store.set_on(m["id"], True)
                man = pub.build_stage(hub.store, tmp / "stage")
                live_item = [x for x in man["modules"] if x["kind"] == "live"][0]
                self.assertEqual(live_item["view"], "pets")
                self.assertEqual(live_item["poll"], 15)
                self.assertEqual(live_item["tab"], {"id": "pets", "label": "PETS"})
                self.assertNotIn("hide", live_item)                  # who is hidden is never published
                static_item = [x for x in man["modules"] if x["kind"] == "static"][0]
                self.assertFalse(static_item["listed"])
                self.assertTrue((tmp / "stage" / "modules" / sm["id"] / "a.png").exists())
            finally:
                srv.shutdown()
                srv.server_close()
                hub.close()

    def test_default_modules_have_no_extra_manifest_keys(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            s = st.Store(tmp / "hub")
            site = tmp / "site"
            site.mkdir()
            (site / "index.html").write_text("x")
            s.set_settings(site_dir=str(site), project="p")
            m = s.add_module("live", "L", "127.0.0.1:1/x")
            s.set_on(m["id"], True)
            item = pub.build_stage(s, tmp / "stage")["modules"][0]
            self.assertEqual(sorted(item), ["desc", "id", "kind", "name"])


if __name__ == "__main__":
    unittest.main()
