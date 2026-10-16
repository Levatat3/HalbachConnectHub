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


_JOB = None


def kill_with_parent(proc):
    """Windows: make `proc` die when this process dies, even if this process is force-killed.

    Without it, killing the hub leaves its tunnel connector running and the site's live data
    stays public with no hub in charge of it. Uses a job object with kill-on-close; a no-op
    elsewhere or if the OS refuses. Returns True if the process was attached.
    """
    global _JOB
    handle = getattr(proc, "_handle", None)
    if os.name != "nt" or handle is None:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if _JOB is None:
            job = k.CreateJobObjectW(None, None)
            info = Extended()
            info.Basic.LimitFlags = 0x2000                      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                return False
            _JOB = job                                          # kept open for the life of this process
        return bool(k.AssignProcessToJobObject(_JOB, int(handle)))
    except Exception:
        return False


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
        if not kill_with_parent(self.proc):
            self.store.log("info", "Note: the tunnel will not stop by itself if this program is force-killed")
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
