"""Auto-publishing for areas on the `auto` trust level (Task 2.6).

Every deploy here is a fake: nothing is ever uploaded and no real site is touched. The tests cover the
safeguards as much as the feature - the debounce, the freeze, the rollback after a failed live check -
because those are what make automatic publishing safe to leave on.
"""

import http.client
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk

from halbach_connecthub import areas, autopublish as ap, publisher as pub
from halbach_connecthub import registry as rg
from halbach_connecthub import server
from halbach_connecthub import siteservice as ss
from halbach_connecthub import store as st


class Clock:
    def __init__(self):
        self.t = 1_700_000_000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def good_zip(path, text="hello"):
    doc = zk.area_json([{"type": "hero", "props": {"heading": "Funk's workshop"}},
                        {"type": "image", "props": {"url": "files/pic.png", "alt": "pic"}},
                        {"type": "text", "props": {"markdown": text}}])
    return zk.make_zip(path, [("area.json", doc), ("files/pic.png", zk.png())])


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.clock = Clock()
        self.store = st.Store(self.root / "hub")
        self.site_dir = self.root / "site"
        self.site_dir.mkdir()
        (self.site_dir / "index.html").write_text("<h1>site</h1>", encoding="utf-8")
        self.store.set_settings(site_dir=str(self.site_dir), project="my-site", domain="example.com",
                                site_mode="modular")
        self.reg = rg.Registry()
        self.svc = areas.AreaService(self.store, self.reg, min_free_bytes=0, clock=self.clock)
        self.site = ss.SiteService(self.store)
        self.site.areas = self.svc
        self.site.apply("template.load", {"id": "personal"})
        self.deploys = []                       # one entry per fake upload: what was in the stage
        self.verify_result = True               # what the fake live check says by default
        self.verify_plan = []                   # per upcoming live check: its result (True/False/None)
        self.deploy_plan = []                   # per upcoming upload: an exception to raise, or None
        self.fail_deploy = None                 # set to an exception to make every upload fail
        self._npx = mock.patch.object(pub, "find_npx", return_value="npx")
        self._npx.start()
        self.publisher = pub.Publisher(self.store, deployer=self._deploy, verifier=self._verify, site=self.site)
        self.autopub = ap.AutoPublisher(self.store, self.svc, self.publisher, clock=self.clock, threaded=False)
        self.svc.on_auto_publish = self.autopub.schedule

    def tearDown(self):
        self._npx.stop()
        self._t.cleanup()

    # ---- fakes
    def _deploy(self, stage, project, branch, log):
        stage = Path(stage)
        files = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
        text = {}
        for rel in files:
            try:
                text[rel] = (stage / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
        self.deploys.append({"branch": branch, "files": files, "text": text})
        raise_or_none = self.deploy_plan.pop(0) if self.deploy_plan else self.fail_deploy
        if raise_or_none:
            raise raise_or_none
        return "https://abc123.my-site.pages.dev"

    def _verify(self, host, build):
        return self.verify_plan.pop(0) if self.verify_plan else self.verify_result

    # ---- helpers
    def stage_has(self, deploy, needle):
        return any(needle in t for t in deploy["text"].values())

    def send(self, aid="workshop", text="hello"):
        """A collaborator's upload arrives. On an auto area this approves (and lines up a publish) by itself."""
        z = self.root / ("in-%s-%d.zip" % (aid, len(self.deploys) + hash(text) % 1000))
        good_zip(z, text)
        return self.svc.ingest(aid, str(z), "1.2.3.4")

    def make_auto(self, name="Workshop"):
        view, _ = self.svc.create(name, trust="auto")
        return view

    def audit(self, aid):
        return [r["what"] for r in self.svc.audit_log(aid)]


class TestTrust(unittest.TestCase):
    """Only the three known levels exist; auto is one of them."""

    def test_auto_is_a_real_trust_level_and_still_validated(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            svc = areas.AreaService(st.Store(Path(tmp.name) / "hub"), rg.Registry(), min_free_bytes=0)
            self.assertEqual(areas.TRUST_LEVELS, ("review", "trusted", "auto"))
            view, _ = svc.create("Auto", trust="auto")
            self.assertEqual(view["trust"], "auto")
            with self.assertRaises(st.ValidationError):
                svc.create("X", trust="always")
            with self.assertRaises(st.ValidationError):
                svc.update("auto", trust="sometimes")
            self.assertEqual(svc.update("auto", trust="trusted")["trust"], "trusted")
        finally:
            tmp.cleanup()

    def test_eligible_is_trust_auto_and_on_and_not_paused(self):
        a = {"trust": "auto", "enabled": True, "paused": False}
        self.assertTrue(ap.AutoPublisher.eligible(a))
        for off in ({"trust": "review"}, {"enabled": False}, {"paused": True}):
            self.assertFalse(ap.AutoPublisher.eligible(dict(a, **off)))


class TestSchedule(Base):
    def test_approval_on_an_auto_area_lines_up_a_publish(self):
        self.make_auto()
        meta = self.send(text="first")
        self.assertEqual(meta["state"], "approved")               # auto areas approve valid bundles by themselves
        view = self.svc.view(self.svc.get("workshop"))
        self.assertEqual(view["auto"]["due"], int(self.clock.t))
        self.assertIsNone(view["auto"]["from"])                   # nothing was live before this
        self.assertIn("auto-publish scheduled", self.audit("workshop"))
        self.assertEqual(self.deploys, [])                        # nothing uploads until the debounce allows it

    def test_trusted_areas_never_schedule(self):
        self.svc.create("Trusted", trust="trusted")
        self.assertEqual(self.send("trusted", text="hi")["state"], "approved")
        self.assertIsNone(self.svc.get("trusted").get("auto"))
        self.assertEqual(self.deploys, [])

    def test_classic_mode_never_publishes_and_says_why(self):
        self.store.set_settings(site_mode="classic")
        self.make_auto()
        self.send(text="first")                                   # approved, but the classic build has no area content
        self.assertIsNone(self.svc.get("workshop").get("auto"))
        self.assertIn("auto-publish skipped", self.audit("workshop"))
        self.assertEqual(self.deploys, [])
        self.assertTrue(any(r["level"] == "warn" for r in self.store.activity()))


class TestDebounce(Base):
    def test_one_publish_covers_everything_waiting(self):
        self.make_auto()
        self.svc.create("Studio", trust="auto")
        self.send("workshop", text="one")
        self.send("studio", text="two")
        done = self.autopub.run_due(self.clock.t)
        self.assertEqual(sorted(done), ["studio", "workshop"])
        self.assertEqual(len(self.deploys), 1)                    # one deploy, one upload: the whole site at once
        self.assertEqual(self.audit("workshop").count("auto-published"), 1)
        self.assertEqual(self.audit("studio").count("auto-published"), 1)
        self.assertIsNone(self.svc.get("workshop").get("auto"))   # nothing is waiting any more

    def test_a_second_approval_waits_out_the_full_debounce(self):
        self.make_auto()
        self.send(text="one")
        self.autopub.run_due(self.clock.t)
        self.assertEqual(len(self.deploys), 1)
        self.clock.advance(60)
        self.send(text="two")                                     # 60 s later, still inside the debounce window
        self.assertGreater(self.svc.get("workshop")["auto"]["due"], int(self.clock.t))
        self.assertEqual(self.autopub.run_due(self.clock.t), [])
        self.assertEqual(len(self.deploys), 1)
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(len(self.deploys), 2)

    def test_newer_content_arriving_mid_deploy_is_not_lost(self):
        self.make_auto()
        self.send(text="one")
        # the collaborator sends again while the deploy is running
        real = self.publisher.run_blocking

        def slow(label=None, preview=False):
            self.send(text="two")
            return real(label=label, preview=preview)

        with mock.patch.object(self.publisher, "run_blocking", side_effect=slow):
            self.autopub.run_due(self.clock.t)
        self.assertEqual(len(self.deploys), 1)
        pending = self.svc.get("workshop").get("auto")
        self.assertIsNotNone(pending)                             # it is lined up again, not forgotten
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)
        self.autopub.run_due(self.clock.t)
        self.assertEqual(self.svc.get("workshop")["current"], "v0002")


class TestFreezeAndCancel(Base):
    def test_freeze_cancels_the_waiting_publish_and_returns_to_review(self):
        self.make_auto()
        self.send(text="one")
        view = self.svc.freeze("workshop")
        self.assertEqual(view["trust"], "review")
        self.assertIsNone(view["auto"])
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)
        self.assertEqual(self.autopub.run_due(self.clock.t), [])   # nothing publishes any more
        self.assertEqual(self.deploys, [])
        self.assertIn("frozen", self.audit("workshop"))

    def test_switching_away_or_pausing_cancels_too(self):
        self.make_auto()
        self.send(text="one")
        self.svc.update("workshop", paused=True)
        self.assertIsNone(self.svc.get("workshop").get("auto"))
        self.assertIn("auto-publish cancelled", self.audit("workshop"))
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)
        self.assertEqual(self.autopub.run_due(self.clock.t), [])
        self.assertEqual(self.deploys, [])

    def test_freeze_never_publishes_anything(self):
        self.make_auto()
        self.send(text="one")
        self.svc.freeze("workshop")
        self.svc.freeze("workshop")                               # freezing twice is still safe
        self.assertEqual(self.deploys, [])


class TestPublish(Base):
    def test_the_publish_goes_out_and_the_content_is_confined_to_the_area(self):
        self.make_auto()
        self.send(text="published content")
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(len(self.deploys), 1)
        rels = self.deploys[0]["files"]
        area_files = [r for r in rels if r.startswith("workshop/")]
        self.assertTrue(area_files, "the area's content should live under its own address")
        self.assertEqual([r for r in rels if "pic.png" in r and not r.startswith("workshop/")], [])
        self.assertTrue(self.stage_has(self.deploys[0], "published content"))
        self.assertTrue(any("Published automatically" in r["msg"] for r in self.store.activity()))

    def test_a_deploy_that_fails_is_recorded_and_retries_on_the_next_approval(self):
        self.make_auto()
        self.send(text="one")
        self.fail_deploy = pub.PublishError("Not signed in to Cloudflare.")
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(len(self.deploys), 1)
        self.assertIn("auto-publish failed", self.audit("workshop"))
        self.assertIsNone(self.svc.get("workshop").get("auto"))   # dropped: the next approval tries again
        self.assertEqual(self.svc.get("workshop")["trust"], "auto")   # a failed upload changes nothing about trust
        self.assertTrue(any(r["level"] == "warn" for r in self.store.activity()))

    def test_another_publish_running_waits_instead_of_colliding(self):
        self.make_auto()
        self.send(text="one")
        with mock.patch.object(self.publisher, "run_blocking", side_effect=st.ValidationError("A publish is already running.")):
            self.assertEqual(self.autopub.run_due(self.clock.t), [])
        self.assertEqual(self.deploys, [])
        pending = self.svc.get("workshop")["auto"]
        self.assertEqual(pending["due"], int(self.clock.t) + ap.RETRY_SECONDS)
        self.assertEqual(sum(1 for r in self.store.activity() if "Automatic publish is waiting" in r["msg"]), 1)
        self.autopub.run_due(pending["due"])                       # quietly retries once the other publish is done
        self.assertEqual(self.audit("workshop").count("auto-published"), 1)


class TestRollback(Base):
    """The live check is what makes automatic publishing safe; failing it must put everything back."""

    def setUp(self):
        super().setUp()
        self.make_auto()

    def test_a_failed_live_check_puts_back_the_previous_content_and_freezes(self):
        self.send(text="version one")
        self.autopub.run_due(self.clock.t)                         # publishes fine, verified
        self.assertEqual(self.svc.get("workshop")["current"], "v0001")
        self.send(text="version two")
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)                # out of the debounce, into the danger
        self.verify_plan = [False, True]                           # the live site rejects the new build, then accepts the old one
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(self.svc.get("workshop")["current"], "v0001")   # back to what was live before
        self.assertEqual(self.svc.get("workshop")["trust"], "review")    # and frozen
        self.assertIsNone(self.svc.get("workshop").get("auto"))
        self.assertEqual(len(self.deploys), 3)                     # the good publish, the bad one, then exactly one redeploy
        self.assertTrue(self.stage_has(self.deploys[2], "version one"))
        self.assertFalse(self.stage_has(self.deploys[2], "version two"))
        log = self.audit("workshop")
        self.assertIn("auto-rollback", log)
        self.assertIn("auto-rollback-published", log)

    def test_first_content_rolls_back_to_nothing_live(self):
        self.verify_plan = [False, True]
        self.send(text="the very first")
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertIsNone(self.svc.get("workshop")["current"])     # there was nothing before it
        self.assertEqual(self.svc.get("workshop")["trust"], "review")
        row = next(r for r in self.svc.audit_log("workshop") if r["what"] == "auto-rollback")
        self.assertIn("back to nothing live", row["detail"])

    def test_a_rollback_that_also_fails_leaves_the_area_frozen_and_says_so(self):
        self.send(text="version one")
        self.autopub.run_due(self.clock.t)
        self.send(text="version two")
        self.clock.advance(ap.AUTO_PUBLISH_SECONDS)
        self.verify_plan = [False]                                 # the rollback deploy fails before the live check
        self.deploy_plan = [None, pub.PublishError("The upload took longer than 10 minutes and was stopped.")]
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(len(self.deploys), 3)                     # never a loop: bad upload, one rollback attempt, stop
        self.assertEqual(self.svc.get("workshop")["trust"], "review")
        log = self.audit("workshop")
        self.assertIn("auto-rollback-failed", log)
        self.assertTrue(any(r["level"] == "warn" for r in self.store.activity()))

    def test_no_domain_means_no_live_check_and_no_rollback(self):
        self.store.state["settings"]["domain"] = None
        self.send(text="version one")
        self.assertEqual(self.autopub.run_due(self.clock.t), ["workshop"])
        self.assertEqual(self.svc.get("workshop")["trust"], "auto")   # nothing failed: the check simply never ran
        self.assertEqual(self.svc.get("workshop")["current"], "v0001")


class TestWiring(unittest.TestCase):
    """The window's server exposes the freeze button and starts the auto-publisher."""

    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        tmp = Path(self._t.name)
        self.srv, self.hub = server.serve(tmp / "hub", 0, token="tok")
        self.port = self.srv.server_address[1]
        server.run_in_thread(self.srv)

    def tearDown(self):
        self.hub.close()
        self.srv.shutdown()
        self.srv.server_close()
        self._t.cleanup()

    def api(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok"}
        if data:
            headers["Content-Type"] = "application/json"
        c.request(method, path, data, headers)
        r = c.getresponse()
        out = json.loads(r.read().decode("utf-8"))
        c.close()
        return r.status, out

    def test_freeze_is_a_one_click_route_and_the_hub_runs_an_auto_publisher(self):
        s, j = self.api("POST", "/api/areas/create", {"name": "Workshop", "trust": "auto"})
        self.assertEqual(s, 201)
        self.assertEqual(j["area"]["trust"], "auto")
        self.assertIsNotNone(self.hub.autopublish)
        self.assertEqual(self.hub.areas.on_auto_publish.__self__, self.hub.autopublish)
        self.assertEqual(self.hub.areas.on_auto_publish.__func__, ap.AutoPublisher.schedule)
        s, j = self.api("POST", "/api/areas/workshop/freeze")
        self.assertEqual(s, 200)
        self.assertEqual(j["area"]["trust"], "review")
        self.assertIn("frozen", [r["what"] for r in self.hub.areas.audit_log("workshop")])
        s, j = self.api("POST", "/api/areas/nosuch/freeze")
        self.assertEqual(s, 400)                                   # same validation as every other area action

    def test_the_window_sees_the_pending_publish_and_the_trust_levels(self):
        self.hub.store.set_settings(site_mode="modular", project="p")
        view, _ = self.hub.areas.create("Workshop", trust="auto")
        self.assertEqual(self.hub.areas.view(self.hub.areas.get("workshop"))["trust"], "auto")
        s, j = self.api("GET", "/api/areas")
        self.assertEqual(s, 200)
        self.assertIn("auto", j["areas"][0])                       # the pending publish is in the view for the banner
