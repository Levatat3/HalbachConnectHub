"""python -m connecthub  ->  start the hub and open its window."""

import argparse
import os
import shutil
import subprocess
import webbrowser

from . import __version__
from .server import serve


def _app_window(url):
    """Open in a chromeless Edge/Chrome window if present, else the default browser."""
    for name in ("msedge", "chrome", "google-chrome", "chromium"):
        exe = shutil.which(name)
        if not exe and os.name == "nt":
            for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
                for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
                    cand = os.path.join(base or "", rel)
                    if base and os.path.exists(cand) and name in ("msedge", "chrome"):
                        exe = cand
                        break
        if exe:
            try:
                subprocess.Popen([exe, "--app=" + url, "--window-size=1280,820"])
                return
            except OSError:
                pass
    webbrowser.open(url)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="connecthub", description="Choose what your site shows.")
    ap.add_argument("--port", type=int, default=8780, help="local port (default 8780; 0 = any free port)")
    ap.add_argument("--live-port", type=int, default=8782, help="port for the live hub (default 8782)")
    ap.add_argument("--home", help="where to keep state (default: %%LOCALAPPDATA%%\\ConnectHub)")
    ap.add_argument("--no-open", action="store_true", help="don't open a window")
    ap.add_argument("--version", action="version", version="connecthub " + __version__)
    a = ap.parse_args(argv)

    try:
        srv, hub = serve(a.home, a.port, live_port=a.live_port)
    except OSError:
        print("Port %d is in use; picking a free one." % a.port)
        srv, hub = serve(a.home, 0, live_port=a.live_port)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    hub.store.log("info", "Hub started")
    print("ConnectHub %s running at %s  (Ctrl+C to stop)" % (__version__, url))
    print("Live hub (for the tunnel) on 127.0.0.1:%d" % hub.live_srv.server_address[1])
    if not a.no_open:
        _app_window(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        hub.close()
        hub.store.log("info", "Hub stopped")
        srv.server_close()


if __name__ == "__main__":
    main()
