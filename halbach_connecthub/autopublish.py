"""Automatic publishing for areas on the `auto` trust level, and the safeguards around it.

An auto area's approved content is published by itself instead of waiting for the owner's Publish
button. Everything around that is deliberate:

* **debounced** - at most one automatic publish every AUTO_PUBLISH_SECONDS (5 minutes), counted
  across the whole hub, because one deploy is always the whole site. A collaborator cannot make the
  hub upload in a loop, however fast valid submissions arrive;
* **the normal build only** - it goes through publisher.deploy_site like any publish, where the area
  merge confines area content to /<area>/ (sitebuilder), so an automatic publish can only ever change
  what that area is allowed to change;
* **freezable** - the waiting publish lives in the state, so the window can show a standing banner and
  cancel it with one click (areas.freeze); a paused, switched-off or de-autoed area never publishes;
* **checked afterwards** - the live check in deploy_site decides how it went. If the site does not
  match what was uploaded, everything this publish pushed goes back to the version that was live
  before, the areas are frozen (trust returns to `review`) and that previous content is deployed once
  more - once, never in a loop. A deploy that fails outright rolls nothing back (the site still holds
  the previous build); the attempt is recorded, nothing retries by itself until the next approval.

No thread runs until something is actually waiting. Stdlib only.
"""

import threading
import time

from .store import ValidationError

AUTO_PUBLISH_SECONDS = 5 * 60      # at most one automatic publish every five minutes
RETRY_SECONDS = 30                 # wait this long before trying again when a publish is already running


class AutoPublisher:
    """Turns "an auto area approved something" into at most one debounced, verified deploy."""

    def __init__(self, store, areas, publisher, clock=time.time, threaded=True):
        self.store = store
        self.areas = areas                  # AreaService
        self.publisher = publisher          # Publisher: the one place deploys are claimed, so auto and manual never overlap
        self.clock = clock
        self.threaded = threaded            # tests drive run_due() themselves
        self._wake = threading.Event()
        self._stop = False
        self._thread = None
        self._waiting = None                # the last "cannot run yet" reason, so retries stay quiet in the log

    # ---------------------------------------------------------------- state
    def _state(self):
        return self.store.state.setdefault("auto_publish", {})

    def _save(self):
        with self.store.lock:
            self.store._save()

    def _last(self):
        """When the last automatic publish attempt started (0 when there never was one)."""
        return int(self._state().get("last") or 0)

    def _attempted(self, now):
        """Count a deploy against the debounce, whether it worked or not."""
        with self.store.lock:
            self._state()["last"] = int(now)
            self.store._save()

    @staticmethod
    def eligible(a):
        """An area publishes by itself only while it is on auto, on and not paused."""
        return a.get("trust") == "auto" and bool(a.get("enabled")) and not a.get("paused")

    # ---------------------------------------------------------------- lining one up
    def schedule(self, aid, prev):
        """An auto area just approved content: line up one debounced publish for it (with any other waiting area).

        prev is the version that was live before that approval - what a rollback would go back to.
        Returns True when something is now waiting.
        """
        now = int(self.clock())
        with self.areas.lock:
            a = self.areas.get(aid)
            if a is None or not self.eligible(a):
                return False
            if a.get("auto"):
                return True                                   # already waiting; the first approval in keeps what it waits for
            if (self.store.state.get("settings") or {}).get("site_mode") != "modular":
                # the classic site is uploaded as a folder, which never contains area content
                self.areas.audit(aid, "auto", "auto-publish skipped", 0, "the modular site is off")
                self.store.log("warn", "Auto-publish for '%s' waited: the modular site is off, so its content is not part of the site."
                               % (a.get("name") or aid))
                return False
            due = max(now, self._last() + AUTO_PUBLISH_SECONDS, int(a.get("auto_last") or 0) + AUTO_PUBLISH_SECONDS)
            a["auto"] = {"due": due, "from": prev, "at": now}
            self.areas._save()
            self.areas.audit(aid, "auto", "auto-publish scheduled", 0,
                             ("in %d seconds" % (due - now)) if due > now else "now")
        self.arm()
        self._wake.set()
        return True

    # ---------------------------------------------------------------- running what is due
    def run_due(self, now=None):
        """Run one automatic publish if something has been waiting long enough. Returns the areas it covered."""
        now = int(self.clock() if now is None else now)
        with self.areas.lock:
            self._drop_stale()
            due = [a for a in self.areas.areas if a.get("auto") and int(a["auto"]["due"]) <= now and self.eligible(a)]
            if not due:
                return []
            if self._last() + AUTO_PUBLISH_SECONDS > now:     # the debounce is hub-wide: one deploy is always the whole site
                for a in due:
                    a["auto"]["due"] = self._last() + AUTO_PUBLISH_SECONDS
                self.areas._save()
                return []
            pending = [{"id": a["id"], "from": a["auto"].get("from"), "target": a.get("current")} for a in due]
        return self._publish(pending)

    def _drop_stale(self):
        """Forget anything waiting for an area that can no longer publish by itself. Call with the areas lock held."""
        gone = False
        for a in list(self.areas.areas):
            if a.get("auto") and not self.eligible(a):
                a.pop("auto", None)                           # no audit: freeze/update already wrote their own line
                gone = True
        if gone:
            self.areas._save()

    # ---------------------------------------------------------------- one publish
    def _publish(self, pending):
        try:
            status = self.publisher.run_blocking(label="Published automatically")
        except ValidationError as e:                          # another publish is running, or this hub cannot publish yet
            return self._wait_again(pending, str(e))
        self._attempted(int(self.clock()))
        self._waiting = None
        message = str(status.get("message") or "")
        if status.get("state") != "done":
            # nothing new could go live: drop the wait (the next approval tries again) and say so
            with self.areas.lock:
                for p in pending:
                    a = self.areas.get(p["id"])
                    if a is None:
                        continue
                    a["auto_last"] = int(self.clock())
                    if a.get("current") != p["target"] and a.get("auto"):
                        a["auto"]["due"] = int(self.clock()) + AUTO_PUBLISH_SECONDS   # newer content: try after the debounce
                    else:
                        a.pop("auto", None)
                    self.areas.audit(p["id"], "auto", "auto-publish failed", 0, message)
                self.areas._save()
            self.store.log("warn", "Automatic publish failed: %s" % message)
            return [p["id"] for p in pending]
        if status.get("verified") is False:                    # uploaded, but the live site does not match it
            return self._rollback(pending)
        with self.areas.lock:
            done = []
            for p in pending:
                a = self.areas.get(p["id"])
                if a is None:
                    continue
                a["auto_last"] = int(self.clock())
                if a.get("current") == p["target"]:
                    a.pop("auto", None)                        # exactly what was deployed: nothing more is waiting
                elif a.get("auto"):
                    a["auto"]["due"] = int(self.clock()) + AUTO_PUBLISH_SECONDS   # newer content arrived while it was deploying
                self.areas.audit(p["id"], "auto", "auto-published", 0, p["target"] or "empty")
                done.append(p["id"])
            self.areas._save()
        if done:
            self.store.log("ok", "Published automatically: %s." % ", ".join(done))
        return done

    def _rollback(self, pending):
        """The live site does not match what was just deployed: put the previous content back, freeze the
        areas, then deploy that previous content once more. Bounded on purpose - never a loop."""
        why = "the live site did not match the new build"
        with self.areas.lock:
            for p in pending:
                if self.areas.get(p["id"]):
                    self.areas.auto_rollback(p["id"], p["from"], why)
            self.areas._save()
        self.store.log("warn", "The site did not confirm the new build, so %s was put back and frozen."
                       % ", ".join(p["id"] for p in pending))
        try:
            status = self.publisher.run_blocking(label="Published the previous version")
        except ValidationError as e:
            status = {"state": "error", "message": str(e), "verified": None}
        self._attempted(int(self.clock()))
        self._waiting = None
        ok = status.get("state") == "done" and status.get("verified") is not False
        detail = str(status.get("message") or message_fallback(status))
        with self.areas.lock:
            for p in pending:
                if self.areas.get(p["id"]):
                    self.areas.audit(p["id"], "auto", "auto-rollback-published" if ok else "auto-rollback-failed", 0, detail)
            self.areas._save()
        if ok:
            self.store.log("ok", "Published the version that was live before: %s." % ", ".join(p["id"] for p in pending))
        else:
            self.store.log("warn", "The site did not confirm the rolled-back build either (%s). The areas stay frozen: check "
                           "the site and publish yourself." % detail)
        return [p["id"] for p in pending]

    def _wait_again(self, pending, reason):
        """Another publish is running, or this hub cannot publish yet: try again shortly, quietly."""
        now = int(self.clock())
        with self.areas.lock:
            for p in pending:
                a = self.areas.get(p["id"])
                if a and a.get("auto"):
                    a["auto"]["due"] = now + RETRY_SECONDS
            self.areas._save()
        if reason != self._waiting:                            # one line per new reason, not one per retry
            self._waiting = reason
            self.store.log("warn", "Automatic publish is waiting: %s" % reason)
        return []

    # ---------------------------------------------------------------- the waiting thread (no thread until something waits)
    def arm(self):
        """Start the waiting thread, if this hub uses threads and something is actually waiting."""
        if not self.threaded or self._stop:
            return
        with self.areas.lock:
            pending = any(a.get("auto") for a in self.areas.areas)
        if not pending or (self._thread and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._loop, name="halbach-auto-publish", daemon=True)
        self._thread.start()

    def close(self):
        self._stop = True
        self._wake.set()

    def _seconds_until_due(self):
        with self.areas.lock:
            dues = [int(a["auto"]["due"]) for a in self.areas.areas if a.get("auto")]
        if not dues:
            return None                                        # nothing waiting: only a schedule() wakes us
        return min(dues) - int(self.clock())

    def _loop(self):
        while not self._stop:
            delay = self._seconds_until_due()
            if delay is not None and delay <= 0:
                try:
                    self.run_due()
                except Exception as e:                         # never let the thread die quietly
                    self.store.log("warn", "Automatic publish hit a problem: %s" % e)
                    time.sleep(RETRY_SECONDS)
                continue
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop:
                return


def message_fallback(status):
    return str(status.get("message") or ("error" if status.get("state") == "error" else ""))
