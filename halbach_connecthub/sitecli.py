"""halbach site ...  Build, check and move the modular site without opening the window.

    halbach site check                      list problems and advice (exit 1 if anything blocks publishing)
    halbach site build --out DIR [--preview] build the site into DIR (what Publish would upload)
    halbach site export FILE                save the site (pages, design, settings) as one JSON file
    halbach site import FILE                replace the site with an export (close the window first)
    halbach site import-classic [--dry-run] read the classic site folder into pages (close the window first;
                                            --dry-run only prints what it understood)

Uses the same state folder as the window (--home to point elsewhere). Stdlib only.
"""

import argparse
import json
import sys
from pathlib import Path

from .legacyimport import format_report
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
    ic = sub.add_parser("import-classic")
    ic.add_argument("--dry-run", action="store_true", help="print what the import understood and save nothing")
    a = ap.parse_args(argv)

    home = Path(a.home) if a.home else home_dir()
    store = Store(home)
    svc = SiteService(store)

    if a.cmd == "import-classic" and a.dry_run:
        return _dry_run(svc)                       # reads only, so it is fine with the window open

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
            for line in format_report(svc.import_report, svc.import_notes):
                print(line)
    except ModelError as e:
        print("Refused:", e)
        return 1
    c = svc.check()
    print("Done. %d page%s, %d problem%s to fix." % (len(svc.model.data["pages"]), "" if len(svc.model.data["pages"]) == 1 else "s",
                                                    len(c["errors"]), "" if len(c["errors"]) == 1 else "s"))
    return 0


def _dry_run(svc):
    """import-classic --dry-run: read the classic site, print what the import understood, save nothing."""
    report = []
    try:
        model, notes = svc.classic_import(report=report)
    except ModelError as e:
        print("Refused:", e)
        return 1
    saved = svc.model.data                                   # check the site as the import would leave it, then put ours back
    svc.model.data = svc.model._migrate(model)
    try:
        c = svc.check()
    finally:
        svc.model.data = saved
    for line in format_report(report, notes):
        print(line)
    print("Dry run: %d page%s, %d problem%s to fix. Nothing was saved." % (len(model["pages"]), "" if len(model["pages"]) == 1 else "s",
                                                                         len(c["errors"]), "" if len(c["errors"]) == 1 else "s"))
    return 0
