import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = str(Path(__file__).resolve().parent.parent)

PARENT = """
import subprocess, sys, time
sys.path.insert(0, {repo!r})
from halbach_connecthub.tunnel import kill_with_parent
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
print("attached", kill_with_parent(child), child.pid, flush=True)
time.sleep(120)
"""


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


@unittest.skipUnless(os.name == "nt", "job objects are Windows only")
class TestKillWithParent(unittest.TestCase):
    def test_child_dies_when_the_parent_is_force_killed(self):
        p = subprocess.Popen([sys.executable, "-c", PARENT.format(repo=REPO)], stdout=subprocess.PIPE, text=True)
        try:
            _, attached, pid = p.stdout.readline().split()
            self.assertEqual(attached, "True")
            pid = int(pid)
            self.assertTrue(alive(pid))
            p.kill()                                    # force-kill: no chance to clean up
            p.wait(timeout=10)
            end = time.time() + 10
            while time.time() < end and alive(pid):
                time.sleep(0.2)
            self.assertFalse(alive(pid), "the child survived its parent")
        finally:
            p.kill()
            p.stdout.close()

    def test_without_a_real_process_it_is_a_harmless_no_op(self):
        from halbach_connecthub.tunnel import kill_with_parent

        class Fake:
            pass
        self.assertFalse(kill_with_parent(Fake()))


if __name__ == "__main__":
    unittest.main()
