"""halbach update check | apply | rollback  -> look for a newer Halbach, install it after you confirm, or go back."""
import argparse
import sys

from . import update
from .store import home_dir


def _channel(home):
    try:
        import json
        from pathlib import Path
        s = json.loads((Path(home) / "state.json").read_text(encoding="utf-8")).get("settings") or {}
        return s.get("update_channel") or "release"
    except (OSError, ValueError):
        return "release"


def _show(res):
    cur = res["current"]
    print("This copy: Halbach %s%s (%s)" % (cur["version"], (", commit " + cur["commit"][:7]) if cur.get("commit") else "", cur["source"]))
    latest = res.get("latest")
    if res["status"] == "available":
        print("Newer version available: %s" % (latest.get("version") or latest.get("name")))
        if latest.get("notes"):
            print("What changed:\n  " + latest["notes"].replace("\n", "\n  "))
        print("More: %s" % latest["url"])
    elif res["status"] == "up-to-date":
        print("You are up to date.")
    elif res["status"] == "none":
        print("Nothing has been published on GitHub to update to yet.")
    elif res["status"] == "unknown":
        print(res["error"])
    else:
        print("Could not check: %s" % res["error"])
    if res["status"] == "available" and not res["can_apply"]:
        print(res["why_not"])


def main(argv):
    ap = argparse.ArgumentParser(prog="halbach update", description="Check GitHub for a newer Halbach and install it safely. Your sites, areas, keys and settings are never touched.")
    ap.add_argument("cmd", choices=["check", "apply", "rollback"])
    ap.add_argument("--home", help="state folder (where backups are kept)")
    ap.add_argument("--channel", help="'release' (default) or 'branch:NAME'")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    a = ap.parse_args(argv)
    home = a.home or home_dir()
    channel = a.channel or _channel(home)
    try:
        if a.cmd == "check":
            res = update.check(channel=channel)
            update.save_check(home, res)
            _show(res)
            return 0 if res["status"] != "error" else 1
        if a.cmd == "apply":
            res = update.check(channel=channel)
            _show(res)
            if res["status"] != "available":
                return 0 if res["status"] in ("up-to-date", "none") else 1
            if not res["can_apply"]:
                return 1
            if not a.yes:
                print("\nThis will download the update from GitHub, check it starts, save your current program files as a backup, then replace them.")
                print("Your sites, areas, keys and settings are not touched. You will need to quit and open Halbach again afterwards.")
                if input("Install it? [y/N] ").strip().lower() not in ("y", "yes"):
                    print("Nothing was changed.")
                    return 0
            out = update.apply(home, res["latest"]["token"], channel=channel, log=print)
            what = ("%s -> %s" % (out["from"], out["to"])) if out["from"] != out["to"] or not (out.get("from_commit") and out.get("to_commit")) \
                else ("%s (%s -> %s)" % (out["to"], out["from_commit"][:7], out["to_commit"][:7]))
            print("Updated Halbach %s. Quit Halbach and open it again to use it. (To go back: halbach update rollback)" % what)
            return 0
        found = update.backups(home)
        if not found:
            print("There is no earlier version saved.")
            return 1
        print("Go back to Halbach %s (saved %s)?" % (found[0]["version"], found[0]["id"]))
        if not a.yes and input("Restore it? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Nothing was changed.")
            return 0
        out = update.rollback(home)
        print("Went back to Halbach %s. Quit Halbach and open it again to use it." % out["to"])
        return 0
    except update.UpdateError as e:
        print("Stopped: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
