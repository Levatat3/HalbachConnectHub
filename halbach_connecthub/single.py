"""One hub per state folder.

Two hubs sharing a state file silently overwrite each other (each keeps its own copy in memory), which
once switched off modules that had been published. The OS holds the lock, so it is released
automatically if the hub crashes or is killed; a stale lock can never block a new start.
Stdlib only.
"""

import json
import os
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:             # not Windows
    msvcrt = None
    import fcntl


class InstanceLock:
    """Hold this for as long as the hub runs. acquire() is False if another hub already has the folder."""

    def __init__(self, home):
        self.home = Path(home)
        self.lock_path = self.home / "hub.lock"
        self.info_path = self.home / "hub.json"
        self._fh = None

    def acquire(self):
        self.home.mkdir(parents=True, exist_ok=True)
        fh = open(self.lock_path, "a+b")
        try:
            if msvcrt:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def publish_info(self, url, live_port):
        """Tell later launches where the running hub is, so they can open its window."""
        info = {"pid": os.getpid(), "url": url, "live_port": live_port, "started": int(time.time())}
        tmp = self.info_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(info), encoding="utf-8")
        os.replace(tmp, self.info_path)

    def running_url(self):
        """URL of the hub that holds the lock, if it said so."""
        try:
            return json.loads(self.info_path.read_text(encoding="utf-8")).get("url")
        except (OSError, ValueError):
            return None

    def release(self):
        if self._fh is None:
            return
        try:
            self.info_path.unlink()
        except OSError:
            pass
        try:
            if msvcrt:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._fh.close()
        self._fh = None
