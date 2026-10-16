import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from halbach_connecthub.single import InstanceLock

REPO = str(Path(__file__).resolve().parent.parent)


class TestLock(unittest.TestCase):
    def test_second_holder_is_refused_until_the_first_releases(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = InstanceLock(t), InstanceLock(t)
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            a.release()
            self.assertTrue(b.acquire())
            b.release()

    def test_different_folders_do_not_conflict(self):
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            a, b = InstanceLock(t1), InstanceLock(t2)
            self.assertTrue(a.acquire() and b.acquire())
            a.release()
            b.release()

    def test_info_roundtrip_and_cleanup(self):
        with tempfile.TemporaryDirectory() as t:
            a = InstanceLock(t)
            a.acquire()
            a.publish_info("http://127.0.0.1:1234/", 5678)
            self.assertEqual(InstanceLock(t).running_url(), "http://127.0.0.1:1234/")
            a.release()
            self.assertIsNone(InstanceLock(t).running_url())

    def test_release_without_acquire_is_harmless(self):
        with tempfile.TemporaryDirectory() as t:
            InstanceLock(t).release()


def start_hub(home):
    env = dict(os.environ, PYTHONPATH=REPO, PYTHONUNBUFFERED="1")
    return subprocess.Popen([sys.executable, "-m", "halbach_connecthub", "--no-open", "--port", "0", "--live-port", "0",
                             "--home", str(home)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=REPO)


class TestRealProcesses(unittest.TestCase):
    def test_second_launch_does_not_start_a_rival_and_a_killed_hub_leaves_no_stale_lock(self):
        with tempfile.TemporaryDirectory() as t:
            first = start_hub(t)
            try:
                line = first.stdout.readline()
                self.assertIn("running at", line)
                url = re.search(r"(http://127\.0\.0\.1:\d+/)", line).group(1)

                second = start_hub(t)
                out, _ = second.communicate(timeout=20)
                self.assertEqual(second.returncode, 0)
                self.assertIn("already running at " + url, out)
                self.assertNotIn("running at http", out.replace("already running at", ""))   # it did not start its own server

                info = json.loads((Path(t) / "hub.json").read_text())
                self.assertEqual(info["url"], url)
            finally:
                first.kill()
                first.wait(timeout=10)
                first.stdout.close()
            time.sleep(0.5)
            third = start_hub(t)                                      # the first was killed: its lock died with it
            try:
                self.assertIn("running at", third.stdout.readline())
            finally:
                third.kill()
                third.wait(timeout=10)
                third.stdout.close()


if __name__ == "__main__":
    unittest.main()
