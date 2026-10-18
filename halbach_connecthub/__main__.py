"""python -m halbach_connecthub  (or: halbach)  ->  start the hub and open its window."""

import argparse
import os
import shutil
import sys
import subprocess
import webbrowser

from . import __version__
from .server import serve
from .single import InstanceLock
from .store import home_dir


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
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "submit":
        from .submit import main as submit_main
        return submit_main(args[1:])
    if args and args[0] == "site":
        from .sitecli import main as site_main
        return site_main(args[1:])
    ap = argparse.ArgumentParser(prog="halbach", description="Halbach ConnectHub: choose what your site shows.")
    ap.add_argument("--port", type=int, default=8780, help="local port (default 8780; 0 = any free port)")
    ap.add_argument("--live-port", type=int, default=8782, help="port for the live hub (default 8782)")
    ap.add_argument("--home", help="where to keep state (default: %%LOCALAPPDATA%%\\HalbachConnectHub)")
    ap.add_argument("--no-open", action="store_true", help="don't open a window")
    ap.epilog = ("To send something for review from a script or chat:  halbach submit <folder|file> --name NAME\n"
                 "To build or check the modular site without the window:  halbach site check | build | export | import")
    ap.add_argument("--version", action="version", version="halbach-connecthub " + __version__)
    a = ap.parse_args(args)

    home = a.home or home_dir()
    lock = InstanceLock(home)
    if not lock.acquire():
        # Never start a second hub on the same data: two would overwrite each other's state.
        where = lock.running_url()
        print("Halbach ConnectHub is already running%s." % ((" at " + where) if where else " (starting up)"))
        if where and not a.no_open:
            _app_window(where)
        return 0
    try:
        srv, hub = serve(a.home, a.port, live_port=a.live_port, inbox=True)
    except OSError:
        print("Port %d is in use; picking a free one." % a.port)
        srv, hub = serve(a.home, 0, live_port=a.live_port, inbox=True)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    lock.publish_info(url, hub.live_srv.server_address[1])
    hub.store.log("info", "Hub started")
    print("Halbach ConnectHub %s running at %s  (Ctrl+C to stop)" % (__version__, url))
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
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
