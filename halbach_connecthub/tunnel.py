"""Start and stop a Cloudflare named tunnel that points at the live hub.

Halbach ConnectHub never downloads or installs cloudflared, and never handles a Cloudflare login:
you install it and run the one-time setup yourself (see SETUP_STEPS); this only starts and
stops the already-configured tunnel while the hub is open. Stdlib only.
"""

import collections
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from .store import ValidationError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

SETUP_STEPS = [
    "Install cloudflared from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
    "cloudflared tunnel login          (opens your browser; choose your domain and click Authorize)",
    "cloudflared tunnel create {name}",
    "cloudflared tunnel route dns {name} {host}",
    "Then press Start tunnel here.",
]


def find_cloudflared(store):
    p = (store.state.get("settings") or {}).get("cloudflared_path")
    if p and Path(p).is_file():
        return p
    found = shutil.which("cloudflared")
    if found:
        return found
    local = store.home / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    return str(local) if local.is_file() else None


class Tunnel:
    def __init__(self, store, port_getter, popen=subprocess.Popen):
        self.store = store
        self.port_getter = port_getter
        self.popen = popen
        self.proc = None
        self.lines = collections.deque(maxlen=40)
        self.connected = False
        self.lock = threading.Lock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self):
        s = self.store.state.get("settings") or {}
        name, host = s.get("tunnel_name") or "halbach", s.get("live_host") or "live.example.com"
        with self.lock:
            return {"installed": bool(find_cloudflared(self.store)), "running": self.running(),
                    "connected": self.connected and self.running(), "name": s.get("tunnel_name") or "",
                    "lines": list(self.lines)[-8:],
                    "setup": [x.format(name=name, host=host) for x in SETUP_STEPS]}

    def start(self):
        binary = find_cloudflared(self.store)
        if not binary:
            raise ValidationError("cloudflared isn't installed. See the setup steps below.")
        s = self.store.state.get("settings") or {}
        name = s.get("tunnel_name")
        if not name or not NAME_RE.match(name):
            raise ValidationError("Set the tunnel name in Settings (the one you passed to 'tunnel create').")
        if self.running():
            raise ValidationError("The tunnel is already running.")
        port = self.port_getter()
        cmd = [binary, "tunnel", "--no-autoupdate", "run", "--url", "http://127.0.0.1:%d" % port, name]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = self.popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", creationflags=flags)
        except OSError as e:
            raise ValidationError("Could not start cloudflared: %s" % e)
        self.connected = False
        self.store.log("info", "Tunnel starting (%s)" % name)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            line = line.rstrip()
            with self.lock:
                self.lines.append(line[-200:])
            if "Registered tunnel connection" in line and not self.connected:
                self.connected = True
                self.store.log("ok", "Tunnel connected")
            low = line.lower()
            if " err " in low or "failed to" in low:
                self.store.log("warn", "Tunnel: " + line[-160:])
        self.connected = False
        self.store.log("info", "Tunnel stopped")

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.connected = False
