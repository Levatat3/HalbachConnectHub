import http.client
import io
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk

from halbach_connecthub import areaclient, areas, bundle, dropserver, live
from halbach_connecthub import registry as rg
from halbach_connecthub import sitebuilder as sb
from halbach_connecthub import sitemodel as sm
from halbach_connecthub import store as st
from halbach_connecthub import __main__ as entry

KEY_RE = r"^[a-z2-9]{5}(-[a-z2-9]{5}){4}-[a-z2-9]$"


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def good_zip(path, text="hello", extra=None):
    doc = zk.area_json([{"type": "hero", "props": {"heading": "Funk's workshop"}},
                        {"type": "image", "props": {"url": "files/pic.png", "alt": "pic"}},
                        {"type": "text", "props": {"markdown": text}}])
    return zk.make_zip(path, [("area.json", doc), ("files/pic.png", zk.png())] + (extra or []))


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.clock = Clock()
        self.store = st.Store(self.root / "hub")
        self.reg = rg.Registry()
        self.svc = areas.AreaService(self.store, self.reg, min_free_bytes=0, clock=self.clock)

    def tearDown(self):
        self._t.cleanup()

    def make_area(self, name="Workshop", **kw):
        view, key = self.svc.create(name, **kw)
        return view, key


class TestKeys(unittest.TestCase):
    def test_passphrases_are_long_random_and_hashed(self):
        import re
        k = areas.new_passphrase()
        self.assertRegex(k, KEY_RE)
        self.assertNotEqual(k, areas.new_passphrase())
        h = areas.hash_key(k)
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn(k.replace("-", ""), h)
        self.assertTrue(areas.verify_key(k, h))
        self.assertTrue(areas.verify_key(k.upper().replace("-", " "), h))          # dashes, spaces and case do not matter
        self.assertFalse(areas.verify_key(k[:-1] + ("a" if k[-1] != "a" else "b"), h))
        self.assertFalse(areas.verify_key("", h))
        self.assertFalse(areas.verify_key(k, "garbage"))
        self.assertFalse(areas.verify_key(k, "scrypt$1$1$1$x$y"))
        self.assertNotEqual(areas.hash_key(k), areas.hash_key(k))                  # salted


class TestService(Base):
    def test_create_shows_the_key_once_and_stores_only_a_hash(self):
        view, key = self.make_area()
        self.assertEqual(view["slug"], "workshop")
        self.assertNotIn("hash", json.dumps(view))
        self.assertNotIn(key, json.dumps(view))
        self.assertNotIn(key.replace("-", ""), (self.store.path).read_text(encoding="utf-8"))
        self.assertEqual(view["key"]["hint"], key.replace("-", "")[-4:])
        self.assertEqual(st.Store(self.root / "hub").state["areas"][0]["id"], "workshop")

    def test_addresses_must_be_free_and_safe(self):
        self.make_area()
        for bad in ("Workshop", "Modules", "assets", "api", "", "x" * 60):
            with self.subTest(bad=bad), self.assertRaises(st.ValidationError):
                self.svc.create("Other", slug=bad if bad else "/")
        taken = areas.AreaService(self.store, self.reg, slug_taken=lambda s: s == "play", min_free_bytes=0, clock=self.clock)
        with self.assertRaises(st.ValidationError):
            taken.create("Play", slug="play")
        with self.assertRaises(st.ValidationError):
            self.svc.create("   ")
        with self.assertRaises(st.ValidationError):
            self.svc.create("X", trust="always")                                   # only the three known levels exist
        view, _ = self.svc.create("Auto", trust="auto")                            # auto is a real level: approved and published by itself
        self.assertEqual(view["trust"], "auto")

    def test_limits_and_blocks_are_clamped_to_what_is_safe(self):
        view, _ = self.make_area(limits={"max_file_mb": 5, "extensions": ["png", "html", "exe", "pdf"]}, blocks=["text", "embed", "live-view", "hero"])
        self.assertEqual(view["limits"]["extensions"], ["png", "pdf"])
        self.assertEqual(view["blocks"], ["text", "hero"])
        with self.assertRaises(st.ValidationError):
            self.svc.update("workshop", limits={"max_file_mb": 9999})
        with self.assertRaises(st.ValidationError):
            self.svc.update("workshop", limits={"max_files": 0})
        v = self.svc.update("workshop", blocks=["text", "dropbox", "module-list"])
        self.assertEqual(v["blocks"], ["text"])

    def test_wrong_keys_unknown_areas_and_switched_off_areas_all_look_the_same(self):
        _, key = self.make_area()
        self.assertIsNotNone(self.svc.authenticate("workshop", key, "1.2.3.4"))
        self.assertIsNone(self.svc.authenticate("workshop", "wrong", "1.2.3.5"))
        self.assertIsNone(self.svc.authenticate("nosuch", key, "1.2.3.6"))
        self.svc.update("workshop", enabled=False)
        self.assertIsNone(self.svc.authenticate("workshop", key, "1.2.3.7"))
        self.svc.update("workshop", enabled=True)
        self.svc.revoke_key("workshop")
        self.assertIsNone(self.svc.authenticate("workshop", key, "1.2.3.8"))

    def test_regenerating_the_key_kills_the_old_one_at_once(self):
        _, old = self.make_area()
        new = self.svc.regenerate_key("workshop")
        self.assertNotEqual(old, new)
        self.assertIsNone(self.svc.authenticate("workshop", old, "9.9.9.9"))
        self.assertIsNotNone(self.svc.authenticate("workshop", new, "9.9.9.9"))

    def test_brute_force_locks_the_address_out_then_recovers(self):
        _, key = self.make_area()
        for i in range(areas.LOCK_AFTER):
            self.assertIsNone(self.svc.authenticate("workshop", "guess%d" % i, "6.6.6.6"))
        with self.assertRaises(areas.Locked):
            self.svc.authenticate("workshop", key, "6.6.6.6")                      # even the right key, while locked
        self.assertIsNotNone(self.svc.authenticate("workshop", key, "7.7.7.7"))     # other addresses are fine
        self.clock.advance(areas.LOCK_SECONDS + 1)
        self.assertIsNotNone(self.svc.authenticate("workshop", key, "6.6.6.6"))

    def test_upload_rate_limit_per_address(self):
        self.make_area()
        for _ in range(areas.UPLOADS_PER_WINDOW):
            self.assertTrue(self.svc.allow_upload("workshop", "5.5.5.5"))
        self.assertFalse(self.svc.allow_upload("workshop", "5.5.5.5"))
        self.assertTrue(self.svc.allow_upload("workshop", "5.5.5.6"))
        self.clock.advance(areas.UPLOAD_WINDOW + 1)
        self.assertTrue(self.svc.allow_upload("workshop", "5.5.5.5"))

    def test_audit_never_holds_the_key_or_the_real_address(self):
        _, key = self.make_area()
        self.svc.authenticate("workshop", "wrong", "203.0.113.9")
        text = (self.svc.dir("workshop") / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("203.0.113.9", text)
        self.assertNotIn(key, text)
        self.assertIn("auth-failed", text)
        self.assertFalse(any("key" in r["detail"].lower() and "hash" in r["detail"].lower() for r in self.svc.audit_log("workshop")))

    def test_unknown_area_failures_do_not_create_folders_from_attacker_input(self):
        self.svc.authenticate("../../evil", "x", "1.1.1.1")
        self.svc.authenticate("a" * 500, "x", "1.1.1.1")
        names = sorted(p.name for p in self.svc.root.iterdir())
        self.assertEqual(names, ["_unknown"])


class TestReviewFlow(Base):
    def submit(self, name="workshop", text="hello", **zip_kw):
        z = self.root / ("in-%s.zip" % text.replace(" ", "_"))
        good_zip(z, text, **zip_kw)
        return self.svc.ingest(name, z, "ip")

    def test_a_valid_bundle_waits_for_review_and_changes_nothing_public(self):
        self.make_area()
        meta = self.submit()
        self.assertEqual((meta["state"], meta["files"], meta["pages"], meta["blocks"]), ("pending", 1, 1, 3))
        self.assertEqual(self.svc.trees(), [])
        self.assertEqual(self.svc.pending_count(), 1)
        self.assertTrue(meta["changes"]["first"])

    def test_approve_publishes_a_versioned_snapshot_and_reject_keeps_a_note(self):
        self.make_area()
        a = self.submit(text="one")
        approved = self.svc.approve("workshop", a["id"], note="nice")
        self.assertEqual((approved["state"], approved["version"]), ("approved", "v0001"))
        self.assertEqual([t["slug"] for t in self.svc.trees()], ["workshop"])
        b = self.submit(text="two")
        self.assertEqual(b["changes"]["files_added"] + b["changes"]["files_changed"], [])      # same picture
        self.svc.reject("workshop", b["id"], note="please resize the hero")
        s = self.svc.sender_status("workshop")
        self.assertEqual([x["state"] for x in s], ["rejected", "approved"])
        self.assertEqual(s[0]["note"], "please resize the hero")
        self.assertEqual(self.svc.versions("workshop"), ["v0001"])
        with self.assertRaises(st.ValidationError):
            self.svc.approve("workshop", b["id"])                                  # already decided

    def test_rollback_selects_an_older_version_without_deleting_anything(self):
        self.make_area()
        self.svc.approve("workshop", self.submit(text="one")["id"])
        self.svc.approve("workshop", self.submit(text="two")["id"])
        self.assertEqual(self.svc.get("workshop")["current"], "v0002")
        self.svc.rollback("workshop", "v0001")
        doc = json.loads((self.svc.current_tree("workshop") / "area.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["pages"][0]["blocks"][2]["props"]["markdown"], "one")
        self.assertEqual(self.svc.versions("workshop"), ["v0001", "v0002"])
        with self.assertRaises(st.ValidationError):
            self.svc.rollback("workshop", "v0099")
        with self.assertRaises(st.ValidationError):
            self.svc.rollback("workshop", "../../x")

    def test_the_same_bundle_twice_is_recognised(self):
        self.make_area()
        first = self.submit()
        again = self.submit()
        self.assertTrue(again.get("duplicate"))
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(self.svc.submissions("workshop")), 1)

    def test_invalid_bundles_are_recorded_with_readable_reasons_and_no_content(self):
        self.make_area()
        z = self.root / "bad.zip"
        zk.make_zip(z, [("area.json", zk.area_json()), ("files/x.exe", b"MZ")])
        meta = self.svc.ingest("workshop", z, "ip")
        self.assertEqual(meta["state"], "invalid")
        self.assertTrue(meta["reasons"])
        self.assertFalse((self.svc._sub_dir("workshop", meta["id"]) / "tree").exists())
        self.assertEqual(self.svc.pending_count(), 0)

    def test_trusted_areas_approve_themselves_but_a_paused_one_does_not(self):
        self.make_area(trust="trusted")
        meta = self.submit(text="one")
        self.assertEqual((meta["state"], meta["decided_by"]), ("approved", "auto"))
        self.svc.update("workshop", paused=True)
        self.assertEqual(self.submit(text="two")["state"], "pending")

    def test_rejected_and_invalid_records_expire_after_thirty_days(self):
        self.make_area()
        z = self.root / "bad.zip"
        zk.make_zip(z, [("area.json", zk.area_json()), ("files/x.exe", b"MZ")])
        sid = self.svc.ingest("workshop", z, "ip")["id"]
        self.assertEqual(len(self.svc.submissions("workshop")), 1)
        self.clock.advance(areas.REJECT_KEEP_SECONDS + 10)
        self.assertEqual(self.svc.submissions("workshop"), [])
        self.assertFalse(self.svc._sub_dir("workshop", sid).exists())

    def test_the_area_quota_counts_what_is_already_live(self):
        self.make_area(limits={"max_total_mb": 1})
        zip1 = self.root / "a.zip"
        zk.make_zip(zip1, [("area.json", zk.area_json()), ("files/big.txt", os.urandom(700 * 1024).hex()[:700 * 1024].encode())])
        self.svc.approve("workshop", self.svc.ingest("workshop", zip1, "ip")["id"])
        zip2 = self.root / "b.zip"
        zk.make_zip(zip2, [("area.json", zk.area_json()), ("files/more.txt", os.urandom(400 * 1024).hex()[:400 * 1024].encode())])
        meta = self.svc.ingest("workshop", zip2, "ip")
        self.assertEqual(meta["state"], "invalid")
        self.assertTrue(any("total limit" in r for r in meta["reasons"]))

    def test_deleting_an_area_removes_its_files(self):
        self.make_area()
        self.svc.approve("workshop", self.submit()["id"])
        self.svc.delete("workshop")
        self.assertIsNone(self.svc.get("workshop"))
        self.assertFalse(self.svc.dir("workshop").exists())

    def test_a_crash_leaves_no_half_written_state_after_recovery(self):
        self.make_area()
        q = self.svc.dir("workshop") / "quarantine"
        q.mkdir(parents=True)
        (q / "half.zip").write_bytes(b"PK")
        part = self.svc.dir("workshop") / "approved" / "v0001.part"
        part.mkdir(parents=True)
        areas.AreaService(self.store, self.reg, min_free_bytes=0, clock=self.clock)
        self.assertFalse(q.exists())
        self.assertFalse(part.exists())

    def test_publishing_notices_new_area_content(self):
        self.make_area()
        self.assertEqual([p["id"] for p in self.store.pending() if p["id"] == "__areas__"], [])
        self.svc.approve("workshop", self.submit()["id"])
        self.assertEqual([p["id"] for p in self.store.pending() if p["id"] == "__areas__"], ["__areas__"])
        self.store.mark_published()
        self.assertEqual([p["id"] for p in self.store.pending() if p["id"] == "__areas__"], [])


class TestBuilderMerge(Base):
    def setUp(self):
        super().setUp()
        self.model = sm.SiteModel(self.root / "hub", self.reg)
        self.model.load_template("personal")
        self.stage = self.root / "stage"

    def live(self, **kw):
        self.make_area(**kw)
        z = self.root / "in.zip"
        good_zip(z, "see ![pic](files/pic.png)")
        self.svc.approve("workshop", self.svc.ingest("workshop", z, "ip")["id"])

    def build(self, **kw):
        return sb.build_site(self.model, self.reg, self.store, self.stage, areas=self.svc.trees(), **kw)

    def test_approved_content_becomes_pages_and_files_only_under_its_address(self):
        self.live()
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertTrue((self.stage / "workshop" / "index.html").is_file())
        self.assertTrue((self.stage / "workshop" / "files" / "pic.png").is_file())
        text = (self.stage / "workshop" / "index.html").read_text(encoding="utf-8")
        pj = json.loads(__import__("re").search(r'id="hs-page">(.*?)</script>', text, __import__("re").S).group(1))
        types = [b["type"] for b in pj["blocks"]]
        self.assertEqual(types, ["hero", "image", "text"])
        self.assertEqual(pj["blocks"][1]["props"]["url"], "/workshop/files/pic.png")
        self.assertEqual(pj["blocks"][2]["props"]["base"], "/workshop/")
        self.assertIn('href="/workshop/"', (self.stage / "index.html").read_text(encoding="utf-8"))          # in the menu
        self.assertEqual(sorted(p.name for p in self.stage.iterdir() if p.is_dir() and p.name == "workshop"), ["workshop"])

    def test_area_pages_get_a_stricter_policy_and_their_files_can_never_run(self):
        self.model.set_settings(csp="enforce")
        self.live()
        self.build()
        h = (self.stage / "_headers").read_text(encoding="utf-8")
        self.assertIn("/workshop/*\n  ! Content-Security-Policy", h)
        self.assertIn("/workshop/files/*\n  ! Content-Security-Policy\n  Content-Security-Policy: default-src 'none'; sandbox", h)
        rules = sb.parse_headers(h)
        files = sb.headers_for(rules, "/workshop/files/x.pdf")
        self.assertEqual(files["Content-Security-Policy"], "default-src 'none'; sandbox")
        page = sb.headers_for(rules, "/workshop/")
        self.assertNotIn("twitch", page["Content-Security-Policy"])                      # only what its own blocks need

    def test_an_area_cannot_take_a_page_address_a_reserved_name_or_a_folder(self):
        self.live()
        self.model.add_page("Workshop", slug="workshop")
        self.assertFalse(self.build().ok)
        self.model.delete_page("workshop")
        self.assertTrue(self.build().ok)
        extra = self.root / "extra"
        (extra / "workshop").mkdir(parents=True)
        (extra / "workshop" / "index.html").write_text("x", encoding="utf-8")
        res = sb.build_site(self.model, self.reg, self.store, self.stage, extra_dir=extra, areas=self.svc.trees())
        self.assertFalse(res.ok)

    def test_blocks_the_area_is_no_longer_allowed_are_left_out_not_published(self):
        self.live()
        self.svc.update("workshop", blocks=["text"])
        res = self.build()
        self.assertTrue(res.ok, res.errors)
        self.assertTrue(any("left out" in w["message"] for w in res.warnings))
        text = (self.stage / "workshop" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('"type":"hero"', text.replace(" ", ""))

    def test_a_symlink_in_an_approved_tree_is_never_copied(self):
        self.live()
        tree = self.svc.current_tree("workshop")
        secret = self.root / "secret.txt"
        secret.write_text("TOPSECRET", encoding="utf-8")
        try:
            os.symlink(secret, tree / "files" / "leak.txt")
        except (OSError, NotImplementedError):
            self.skipTest("this system will not create symlinks without privileges")
        res = self.build()
        self.assertTrue(res.ok)
        self.assertFalse((self.stage / "workshop" / "files" / "leak.txt").exists())

    def test_empty_and_switched_off_areas_publish_nothing_but_a_paused_one_keeps_its_page(self):
        self.make_area()
        self.assertEqual(self.svc.trees(), [])                                  # nothing approved yet
        z = self.root / "in.zip"
        good_zip(z)
        self.svc.approve("workshop", self.svc.ingest("workshop", z, "ip")["id"])
        self.svc.update("workshop", paused=True)
        self.assertEqual(len(self.svc.trees()), 1)                               # paused only stops new submissions
        self.svc.update("workshop", enabled=False)
        self.assertEqual(self.svc.trees(), [])

    def test_the_owner_can_preview_a_pending_submission_before_approving(self):
        self.make_area()
        z = self.root / "p.zip"
        good_zip(z, "pending text")
        sid = self.svc.ingest("workshop", z, "ip")["id"]
        self.assertEqual(self.svc.trees(), [])
        res = sb.build_site(self.model, self.reg, self.store, self.stage, areas=self.svc.trees(("workshop", sid)), include_drafts=True)
        self.assertTrue(res.ok, res.errors)
        self.assertIn("pending text", (self.stage / "workshop" / "index.html").read_text(encoding="utf-8"))
        res = sb.build_site(self.model, self.reg, self.store, self.stage, areas=self.svc.trees())
        self.assertFalse((self.stage / "workshop").exists())


class DropBase(Base):
    def setUp(self):
        super().setUp()
        self.srv, self.state = live.start_live_hub(self.store, 0)
        self.state.areas = self.svc
        self.port = self.srv.server_address[1]
        self.view, self.key = self.make_area()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, key="__ok__", headers=None, ctype="application/zip", host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if key == "__ok__":
            key = self.key
        if key is not None:
            h["Authorization"] = "Bearer " + key
        if body is not None:
            h["Content-Type"] = ctype
        h.update(headers or {})
        c.request(method, path, body, h)
        r = c.getresponse()
        raw = r.read()
        out = (r.status, json.loads(raw.decode() or "{}") if raw else {}, dict((k.lower(), v) for k, v in r.getheaders()))
        c.close()
        return out

    def zip_bytes(self, text="hello"):
        z = self.root / "up.zip"
        good_zip(z, text)
        return z.read_bytes()

    def raw(self, request_bytes, read=True, wait=0.0, half_close=False):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        s.sendall(request_bytes)
        if half_close:
            s.shutdown(socket.SHUT_WR)
        if wait:
            time.sleep(wait)
        data = b""
        if read:
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if b"\r\n\r\n" in data and b"Content-Length" in data:
                        head, _, rest = data.partition(b"\r\n\r\n")
                        n = int([l for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0].split(b":")[1])
                        if len(rest) >= n:
                            break
            except socket.timeout:
                pass
        s.close()
        status = int(data.split(b" ", 2)[1]) if data else None
        return status, data

    def head(self, extra=b"", length=None, key=None, ctype=b"application/zip"):
        h = b"POST /drop/workshop/bundle HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n" % self.port
        h += b"Authorization: Bearer " + (key or self.key).encode() + b"\r\n"
        if ctype:
            h += b"Content-Type: " + ctype + b"\r\n"
        if length is not None:
            h += b"Content-Length: %d\r\n" % length
        return h + extra + b"Connection: close\r\n\r\n"


class TestDropServer(DropBase):
    def quarantine_empty(self):
        q = self.svc.dir("workshop") / "quarantine"
        return not q.exists() or not any(q.iterdir())

    def test_everything_needs_the_key_and_every_failure_looks_alike(self):
        answers = []
        for key, aid in ((None, "workshop"), ("wrong", "workshop"), (self.key, "nosuch"), ("wrong", "nosuch"), ("", "workshop")):
            s, body, _ = self.call("GET", "/drop/%s/limits" % aid, key=key)
            answers.append((s, json.dumps(body)))
        self.assertEqual(set(answers), {(401, json.dumps({"error": "Not authorised"}))})
        for path in ("status", "limits"):
            self.assertEqual(self.call("GET", "/drop/workshop/%s" % path, key=None)[0], 401)

    def test_limits_and_status_with_the_key(self):
        s, body, _ = self.call("GET", "/drop/workshop/limits")
        self.assertEqual(s, 200)
        self.assertEqual(body["slug"], "workshop")
        self.assertNotIn("hash", json.dumps(body))
        self.assertEqual(self.call("GET", "/drop/workshop/status")[1], {"submissions": [], "paused": False})

    def test_a_valid_upload_is_received_pending_then_recognised_as_a_duplicate(self):
        data = self.zip_bytes()
        s, body, _ = self.call("POST", "/drop/workshop/bundle", data)
        self.assertEqual((s, body["state"], body["duplicate"]), (201, "pending", False))
        s2, body2, _ = self.call("POST", "/drop/workshop/bundle", data)
        self.assertEqual((s2, body2["duplicate"], body2["id"]), (200, True, body["id"]))
        st_ = self.call("GET", "/drop/workshop/status")[1]["submissions"]
        self.assertEqual([x["state"] for x in st_], ["pending"])
        self.assertTrue(self.quarantine_empty())

    def test_an_invalid_bundle_answers_422_with_reasons_and_leaves_nothing(self):
        z = self.root / "bad.zip"
        zk.make_zip(z, [("area.json", zk.area_json()), ("files/evil.html", b"<script>alert(1)</script>")])
        s, body, _ = self.call("POST", "/drop/workshop/bundle", z.read_bytes())
        self.assertEqual(s, 422)
        self.assertTrue(any("allowed file type" in r for r in body["reasons"]))
        self.assertNotIn(str(self.root), json.dumps(body))
        self.assertEqual(self.svc.trees(), [])
        self.assertTrue(self.quarantine_empty())
        self.assertEqual(self.call("GET", "/drop/workshop/status")[1]["submissions"][0]["state"], "invalid")

    def test_nothing_is_read_before_the_key_is_checked(self):
        status, data = self.raw(self.head(length=10_000_000, key="wrongwrongwrong"), wait=0.2)
        self.assertEqual(status, 401)
        self.assertTrue(self.quarantine_empty())

    def test_size_and_type_rules_are_enforced_before_any_body_arrives(self):
        cap = self.svc.get("workshop")["limits"]["max_bundle_mb"] * 1024 * 1024
        self.assertEqual(self.raw(self.head(length=cap + 1))[0], 413)
        self.assertEqual(self.raw(self.head(length=None))[0], 411)
        self.assertEqual(self.raw(self.head(length=100, ctype=b"text/html"))[0], 415)
        self.assertEqual(self.raw(self.head(length=100, ctype=b"application/json"))[0], 415)
        self.assertEqual(self.raw(self.head(length=3))[0], 400)
        self.assertEqual(self.raw(self.head(extra=b"", length=-5))[0], 411)
        self.assertTrue(self.quarantine_empty())

    def test_a_client_that_lies_about_the_length_or_hangs_up_leaves_nothing(self):
        status, _ = self.raw(self.head(length=5000) + b"PK" + b"x" * 100, half_close=True)      # promises 5000, sends 102, hangs up
        self.assertEqual(status, 400)
        self.assertTrue(self.quarantine_empty())
        self.assertEqual(self.svc.pending_count(), 0)

    def test_a_slow_upload_is_cut_off(self):
        with mock.patch.object(dropserver, "READ_TIMEOUT", 0.4):
            started = time.time()
            status, _ = self.raw(self.head(length=5000) + b"PK" + b"x" * 50, wait=1.5)
            self.assertEqual(status, 408)
            self.assertLess(time.time() - started, 5)
        self.assertTrue(self.quarantine_empty())

    def test_paused_revoked_and_disabled_areas(self):
        self.make_area("Other")                                    # keeps the door open so a closed area answers 401
        data = self.zip_bytes()
        self.svc.update("workshop", paused=True)
        self.assertEqual(self.call("POST", "/drop/workshop/bundle", data)[0], 423)
        self.assertEqual(self.call("GET", "/drop/workshop/status")[1]["paused"], True)
        self.svc.update("workshop", paused=False, enabled=False)
        self.assertEqual(self.call("POST", "/drop/workshop/bundle", data)[0], 401)
        self.svc.update("workshop", enabled=True)
        self.svc.revoke_key("workshop")
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 401)

    def test_brute_force_is_locked_out_at_the_door(self):
        for i in range(areas.LOCK_AFTER):
            self.assertEqual(self.call("GET", "/drop/workshop/limits", key="guess%d" % i)[0], 401)
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 429)               # right key, still locked
        self.clock.advance(areas.LOCK_SECONDS + 1)
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 200)

    def test_upload_rate_limit_and_the_concurrency_cap_and_the_disk_floor(self):
        with mock.patch.object(areas, "UPLOADS_PER_WINDOW", 2):
            self.assertIn(self.call("POST", "/drop/workshop/bundle", self.zip_bytes("a"))[0], (201,))
            self.assertIn(self.call("POST", "/drop/workshop/bundle", self.zip_bytes("b"))[0], (201,))
            self.assertEqual(self.call("POST", "/drop/workshop/bundle", self.zip_bytes("c"))[0], 429)
        self.clock.advance(areas.UPLOAD_WINDOW + 1)
        self.assertTrue(self.svc.upload_slots.acquire(blocking=False))
        self.assertTrue(self.svc.upload_slots.acquire(blocking=False))
        try:
            s, body, h = self.call("POST", "/drop/workshop/bundle", self.zip_bytes("d"))
            self.assertEqual(s, 503)
            self.assertEqual(h.get("retry-after"), "60")
        finally:
            self.svc.upload_slots.release()
            self.svc.upload_slots.release()
        self.svc.min_free_bytes = 10 ** 18
        self.assertEqual(self.call("POST", "/drop/workshop/bundle", self.zip_bytes("e"))[0], 503)

    def test_wrong_methods_wrong_paths_and_the_old_read_only_behaviour(self):
        self.assertEqual(self.call("GET", "/drop/workshop/bundle")[0], 405)
        self.assertEqual(self.call("POST", "/drop/workshop/limits", b"x")[0], 405)
        self.assertEqual(self.call("GET", "/drop/workshop/other")[0], 404)
        self.assertEqual(self.call("GET", "/drop/")[0], 404)
        self.assertEqual(self.call("GET", "/drop/../status")[0], 404)
        self.assertEqual(self.call("PUT", "/drop/workshop/bundle", b"x")[0], 405)
        self.assertEqual(self.call("DELETE", "/m/anything")[0], 405)
        s, body, _ = self.call("POST", "/m/anything", b"x", key=None)
        self.assertEqual((s, body), (405, {"error": "read only"}))
        self.assertEqual(self.call("GET", "/status", key=None)[0], 200)

    def test_only_the_live_host_may_be_used(self):
        self.assertEqual(self.call("GET", "/drop/workshop/limits", host="evil.example")[0], 403)

    def test_drop_routes_do_not_exist_when_areas_are_off(self):
        self.state.areas = None
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 404)
        self.assertEqual(self.call("POST", "/drop/workshop/bundle", b"x")[0], 405)

    def test_with_no_usable_area_the_live_hub_is_exactly_as_read_only_as_before(self):
        self.svc.revoke_key("workshop")
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 404)
        s, body, _ = self.call("POST", "/drop/workshop/bundle", b"x")
        self.assertEqual((s, body), (405, {"error": "read only"}))
        self.svc.regenerate_key("workshop")
        self.svc.update("workshop", enabled=False)
        self.assertEqual(self.call("GET", "/drop/workshop/limits")[0], 404)

    def test_trusted_area_approves_on_receipt(self):
        self.svc.update("workshop", trust="trusted")
        s, body, _ = self.call("POST", "/drop/workshop/bundle", self.zip_bytes())
        self.assertEqual((s, body["state"], body["version"]), (201, "approved", "v0001"))

    def test_a_crash_inside_ingest_is_a_clean_503_with_no_details(self):
        with mock.patch.object(self.svc, "ingest", side_effect=RuntimeError(r"C:\secret\path boom")):
            s, body, _ = self.call("POST", "/drop/workshop/bundle", self.zip_bytes())
        self.assertEqual(s, 503)
        self.assertNotIn("secret", json.dumps(body))
        self.assertTrue(self.quarantine_empty())
        self.assertEqual(self.svc.upload_slots._value, 2)                           # the slot was given back


class TestClient(DropBase):
    def setUp(self):
        super().setUp()
        self.url = "http://127.0.0.1:%d" % self.port
        self.folder = self.root / "mine"
        areaclient.init_folder(self.folder)
        (self.folder / "files" / "example.png").write_bytes(zk.png())

    def test_init_then_send_end_to_end_and_the_owner_decision_comes_back(self):
        client = areaclient.Area(self.url, "workshop", self.key)
        lim = client.limits()
        zip_path = self.root / "out.zip"
        size = areaclient.build_zip(self.folder, zip_path, lim["limits"])
        self.assertGreater(size, 0)
        code, body = client.send_zip(zip_path)
        self.assertEqual((code, body["state"]), (201, "pending"))
        self.svc.reject("workshop", body["id"], note="try a shorter title")
        final = client.wait(body["id"], seconds=0)
        self.assertEqual((final["state"], final["note"]), ("rejected", "try a shorter title"))

    def test_client_refuses_what_the_server_would_refuse_before_uploading(self):
        for name, data in (("files/run.exe", b"MZ"), ("files/page.html", b"<b>"), ("files/a.png.exe", b"x")):
            (self.folder / name).write_bytes(data)
            with self.assertRaises(areaclient.ClientError, msg=name):
                areaclient.build_zip(self.folder, self.root / "o.zip", {})
            (self.folder / name).unlink()
        with self.assertRaises(areaclient.ClientError):
            areaclient.build_zip(self.root / "empty", self.root / "o.zip", {})

    def test_wrong_key_and_bad_addresses(self):
        with self.assertRaises(areaclient.ClientError) as cm:
            areaclient.Area(self.url, "workshop", "nope").limits()
        self.assertIn("not accepted", str(cm.exception))
        for bad in ("http://example.com", "ftp://x", "https://x.dev/?k=SECRET", "https://u:p@x.dev", "nonsense", ""):
            with self.subTest(url=bad), self.assertRaises(areaclient.ClientError):
                areaclient.Area(bad, "w", "k")

    def run_cli(self, *args, env=None):
        buf = io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), redirect_stdout(buf):
            code = entry.main(["area", "--home", str(self.root / "client-home")] + list(args))
        return code, buf.getvalue()

    def connect(self):
        """Run the connect command with icacls mocked out (the tests never touch the real one)."""
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(areaclient.subprocess, "run", return_value=done):
            return self.run_cli("--url", self.url, "--area", "workshop", "connect", env={"HALBACH_AREA_KEY": self.key})

    def test_cli_connect_send_status_and_the_key_never_lands_in_the_folder(self):
        code, out = self.connect()
        self.assertEqual(code, 0, out)
        saved = (self.root / "client-home" / areaclient.CONNECTION_FILE)
        self.assertTrue(saved.is_file())
        code, out = self.run_cli("send", str(self.folder))
        self.assertEqual(code, 0, out)
        self.assertIn("is pending", out)
        for f in self.folder.rglob("*"):
            if f.is_file():
                self.assertNotIn(self.key.encode(), f.read_bytes())
        self.assertFalse((self.folder / ".halbach-bundle.zip").exists())
        code, out = self.run_cli("status")
        self.assertIn("pending", out)
        again_code, again = self.run_cli("send", str(self.folder))
        self.assertIn("already received", again)

    def test_cli_shows_the_reasons_when_a_bundle_is_refused(self):
        self.connect()
        doc = json.loads((self.folder / "area.json").read_text(encoding="utf-8"))
        doc["pages"][0]["blocks"].append({"type": "embed", "props": {"url": "https://evil.example"}})
        (self.folder / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        code, out = self.run_cli("send", str(self.folder))
        self.assertEqual(code, 1)
        self.assertIn("refused", out)
        self.assertIn("does not allow", out)

    def test_cli_needs_a_connection(self):
        code, out = self.run_cli("send", str(self.folder))
        self.assertEqual(code, 1)
        self.assertIn("Not connected", out)


class TestValidateOffline(unittest.TestCase):
    """halbach area validate checks a folder against the owner's rules with no connection and no server."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.folder = self.root / "mine"
        areaclient.init_folder(self.folder)
        (self.folder / "files" / "example.png").write_bytes(zk.png())

    def tearDown(self):
        self._t.cleanup()

    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = entry.main(["area", "--home", str(self.root / "client-home")] + list(args))
        return code, buf.getvalue()

    def temp_leftovers(self):
        return list(Path(tempfile.gettempdir()).glob("halbach-validate-*"))

    def test_a_valid_folder_passes_with_a_summary_and_without_a_connection_file(self):
        code, out = self.run_cli("validate", str(self.folder))
        self.assertEqual(code, 0, out)
        self.assertIn("Looks good: 1 page(s), 2 block(s), 1 file(s).", out)
        self.assertFalse((self.root / "client-home" / areaclient.CONNECTION_FILE).exists())     # worked offline
        self.assertFalse((self.folder / ".halbach-bundle.zip").exists())                       # nothing left in the folder
        self.assertEqual(self.temp_leftovers(), [])                                            # nothing left in the temp folder

    def test_an_exe_is_refused_with_a_reason_and_nothing_left_behind(self):
        (self.folder / "files" / "run.exe").write_bytes(b"MZ")
        code, out = self.run_cli("validate", str(self.folder))
        self.assertEqual(code, 1)
        self.assertIn("run.exe", out)
        self.assertIn("allowed file type", out)
        self.assertEqual(self.temp_leftovers(), [])

    def test_a_bad_block_is_refused_with_readable_reasons(self):
        doc = json.loads((self.folder / "area.json").read_text(encoding="utf-8"))
        doc["pages"][0]["blocks"].append({"type": "embed", "props": {"url": "https://evil.example"}})
        (self.folder / "area.json").write_text(json.dumps(doc), encoding="utf-8")
        code, out = self.run_cli("validate", str(self.folder))
        self.assertEqual(code, 1)
        self.assertIn("refused", out)
        self.assertIn("does not allow", out)
        self.assertEqual(self.temp_leftovers(), [])

    def test_limits_from_a_file_change_what_passes(self):
        (self.folder / "files" / "notes.txt").write_text("hi", encoding="utf-8")
        code, out = self.run_cli("validate", str(self.folder))
        self.assertEqual(code, 0, out)
        lim = self.root / "limits.json"
        lim.write_text(json.dumps({"extensions": ["md", "png"]}), encoding="utf-8")
        code, out = self.run_cli("validate", str(self.folder), "--limits", str(lim))
        self.assertEqual(code, 1)
        self.assertIn("notes.txt", out)
        self.assertIn("allowed file type", out)

    def test_a_folder_without_area_json_says_what_is_missing(self):
        code, out = self.run_cli("validate", str(self.root / "nowhere"))
        self.assertEqual(code, 1)
        self.assertIn("area.json", out)
        self.assertEqual(self.temp_leftovers(), [])


class TestConnectionFilePermissions(unittest.TestCase):
    """area-connection.json holds the passphrase: on Windows its ACL is narrowed to the current account only.

    icacls is always mocked; nothing in this suite touches the real one.
    """

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def save(self, run_patch):
        """save_connection() under the given subprocess.run patch. Returns (path, printed lines, the mock)."""
        buf = io.StringIO()
        with run_patch as patched, redirect_stdout(buf):
            p = areaclient.save_connection("https://live.example.com", "workshop", "the-key", home=self.root)
        return p, [ln for ln in buf.getvalue().splitlines() if ln.strip()], patched

    @unittest.skipUnless(os.name == "nt", "icacls only exists on Windows")
    def test_icacls_gets_the_file_and_only_this_account(self):
        user = os.environ.get("USERNAME")
        self.assertTrue(user, "this test needs USERNAME in the environment")
        p, lines, patched = self.save(mock.patch.object(areaclient.subprocess, "run",
                                                        return_value=mock.Mock(returncode=0, stderr="")))
        self.assertEqual(lines, [])                                       # quiet when it worked
        args, kw = patched.call_args
        self.assertEqual(args[0], ["icacls", str(p), "/inheritance:r", "/grant:r", "%s:F" % user])
        self.assertEqual(len(args), 1)                                    # a list, never a shell line
        self.assertNotIn("shell", kw)
        self.assertEqual(kw.get("capture_output"), True)
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["key"], "the-key")

    @unittest.skipUnless(os.name == "nt", "icacls only exists on Windows")
    def test_icacls_refusing_still_saves_the_connection_with_one_line_of_warning(self):
        p, lines, _ = self.save(mock.patch.object(areaclient.subprocess, "run",
                                                  return_value=mock.Mock(returncode=1, stderr="Access is denied.\n")))
        self.assertEqual(len(lines), 1)
        self.assertIn("warning:", lines[0])
        self.assertIn("Access is denied.", lines[0])
        self.assertEqual(areaclient.load_connection(home=self.root)["key"], "the-key")   # still written

    @unittest.skipUnless(os.name == "nt", "icacls only exists on Windows")
    def test_a_missing_icacls_is_one_line_and_not_a_crash(self):
        p, lines, _ = self.save(mock.patch.object(areaclient.subprocess, "run",
                                                  side_effect=FileNotFoundError("icacls")))
        self.assertEqual(len(lines), 1)
        self.assertIn("warning:", lines[0])
        self.assertTrue(p.is_file())
        self.assertEqual(areaclient.load_connection(home=self.root)["area"], "workshop")

    @unittest.skipUnless(os.name == "nt", "icacls only exists on Windows")
    def test_without_a_user_name_nothing_is_run_and_the_file_is_still_saved(self):
        with mock.patch.dict(os.environ, {"USERNAME": ""}):
            p, lines, patched = self.save(mock.patch.object(areaclient.subprocess, "run"))
        patched.assert_not_called()
        self.assertEqual(len(lines), 1)
        self.assertIn("USERNAME", lines[0])
        self.assertTrue(p.is_file())

    def test_off_windows_nothing_extra_is_run(self):
        p = Path("area-connection.json")                                 # never touched: the guard returns first
        with mock.patch.object(areaclient.os, "name", "posix"), mock.patch.object(areaclient.subprocess, "run") as run:
            areaclient._restrict_to_current_user(p)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
