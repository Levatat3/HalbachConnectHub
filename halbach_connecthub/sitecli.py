"""halbach site ...  Build, check and move the modular site without opening the window.

    halbach site check                      list problems and advice (exit 1 if anything blocks publishing)
    halbach site build --out DIR [--preview] build the site into DIR (what Publish would upload)
    halbach site export FILE                save the site (pages, design, settings) as one JSON file
    halbach site import FILE                replace the site with an export (close the window first)
    halbach site import-classic             read the classic site folder into pages (close the window first)

Uses the same state folder as the window (--home to point elsewhere). Stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

from .single import InstanceLock
from .sitemodel import ModelError
from .siteservice import SiteService
from .store import Store, home_dir


def _print_problems(items, mark):
    for it in items:
        print("  %s %s" % (mark, it["message"]))


def main(argv):
    ap = argparse.ArgumentParser(prog="halbach site", description="Build and check the modular site from the command line.")
    ap.add_argument("--home", help="state folder (default: the same one the window uses)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    b = sub.add_parser("build")
    b.add_argument("--out", required=True, help="folder to build into (it is emptied first)")
    b.add_argument("--preview", action="store_true", help="include drafts and use /preview/ addresses")
    e = sub.add_parser("export")
    e.add_argument("file")
    i = sub.add_parser("import")
    i.add_argument("file")
    sub.add_parser("import-classic")
    a = ap.parse_args(argv)

    home = Path(a.home) if a.home else home_dir()
    store = Store(home)
    svc = SiteService(store)

    if a.cmd in ("import", "import-classic"):
        lock = InstanceLock(home)
        if not lock.acquire():
            print("Halbach is open on this state folder. Close it first so the two don't overwrite each other.")
            return 2
        try:
            return _change(svc, a)
        finally:
            lock.release()

    if a.cmd == "check":
        c = svc.check()
        print("%d problem%s, %d note%s" % (len(c["errors"]), "" if len(c["errors"]) == 1 else "s", len(c["warnings"]), "" if len(c["warnings"]) == 1 else "s"))
        _print_problems(c["errors"], "x")
        _print_problems(c["warnings"], "-")
        return 1 if c["errors"] else 0

    if a.cmd == "build":
        out = Path(a.out)
        res = svc.build(out, preview=a.preview)
        if not res.ok:
            print("The site has problems, nothing was built:")
            _print_problems(res.errors, "x")
            return 1
        print("Built %d pages, %d files into %s" % (len(res.pages), res.files, out))
        _print_problems(res.warnings, "-")
        return 0

    if a.cmd == "export":
        Path(a.file).write_text(json.dumps(svc.export_package(), indent=1), encoding="utf-8")
        print("Saved %d pages to %s" % (len(svc.model.data["pages"]), a.file))
        return 0
    return 2


def _change(svc, a):
    try:
        if a.cmd == "import":
            try:
                pkg = json.loads(Path(a.file).read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                print("Could not read %s: %s" % (a.file, e))
                return 1
            svc.apply("import.package", {"package": pkg})
        else:
            svc.apply("import.classic", {})
            for n in svc.import_notes:
                print("note:", n)
    except ModelError as e:
        print("Refused:", e)
        return 1
    c = svc.check()
    print("Done. %d page%s, %d problem%s to fix." % (len(svc.model.data["pages"]), "" if len(svc.model.data["pages"]) == 1 else "s",
                                                    len(c["errors"]), "" if len(c["errors"]) == 1 else "s"))
    return 0
