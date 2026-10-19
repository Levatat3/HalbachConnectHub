"""The collaborator's side of an area: build a bundle from a folder, check it against the area's rules, send it, and
read back how the owner decided.

    halbach area init <folder>                         start a folder (area.json + files/)
    halbach area connect --url U --area A [--key K]     remember where to send (the key is asked for, not echoed)
    halbach area send <folder>                          zip, pre-check, upload, wait for the decision
    halbach area validate <folder>                      check the folder against the rules, offline (no key needed)
    halbach area status                                 recent submissions and what happened to them
    halbach area limits                                 what this area allows

The passphrase is never written into the folder or a URL. It is kept in the state folder (owner-only permissions
where the system supports them; on Windows the connection file's ACL is narrowed to the current account with
icacls), or read from HALBACH_AREA_KEY. Stdlib only.
"""

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from .bundle import BundleError, check_name, extension_problem, DEFAULT_LIMITS, validate_bundle
from .registry import Registry
from .store import home_dir

CONNECTION_FILE = "area-connection.json"
TEMPLATE = {
    "format": 1,
    "title": "My area",
    "description": "A short line about this page.",
    "pages": [{"name": "", "title": "My area", "blocks": [
        {"type": "hero", "props": {"heading": "Welcome", "tagline": "Edit area.json to change this page."}},
        {"type": "text", "props": {"markdown": "Write **Markdown** here. Put pictures in the files folder and show them with ![a picture](files/example.png)."}},
    ]}],
}


class ClientError(Exception):
    pass


def _is_local(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    return host in ("127.0.0.1", "localhost", "::1")


def _base(url):
    url = (url or "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise ClientError("The address must look like https://live.example.com")
    if parts.scheme == "http" and not _is_local(url):
        raise ClientError("Only https addresses can be used (your key would travel unprotected).")
    if parts.query or parts.fragment or parts.username:
        raise ClientError("Give the plain address, without a key or anything after the host name.")
    return "%s://%s" % (parts.scheme, parts.netloc)


def init_folder(folder):
    folder = Path(folder)
    if (folder / "area.json").exists():
        raise ClientError("%s already has an area.json" % folder)
    (folder / "files").mkdir(parents=True, exist_ok=True)
    (folder / "area.json").write_text(json.dumps(TEMPLATE, indent=2), encoding="utf-8")
    return folder / "area.json"


def build_zip(folder, out_path, limits=None):
    """Zip folder (area.json + files/) with the rules the owner's side will apply, so mistakes show up here first."""
    folder = Path(folder)
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    if not (folder / "area.json").is_file():
        raise ClientError("There is no area.json in %s. Start one with: halbach area init %s" % (folder, folder))
    try:
        json.loads((folder / "area.json").read_text(encoding="utf-8"))
    except ValueError as e:
        raise ClientError("area.json is not valid JSON (%s)." % e)
    entries, total = [("area.json", folder / "area.json")], 0
    allowed = {e.lower() for e in limits["extensions"]}
    files_dir = folder / "files"
    if files_dir.is_dir():
        for f in sorted(files_dir.rglob("*")):
            if f.is_symlink():
                raise ClientError("%s is a link; links are not allowed." % f.name)
            if not f.is_file():
                continue
            rel = "files/" + f.relative_to(files_dir).as_posix()
            bad = check_name(rel) or extension_problem(rel, allowed)
            if bad:
                raise ClientError(bad)
            size = f.stat().st_size
            if size > int(limits["max_file_mb"]) * 1024 * 1024:
                raise ClientError("%s is larger than %d MB." % (rel, int(limits["max_file_mb"])))
            total += size
            entries.append((rel, f))
    if len(entries) - 1 > int(limits["max_files"]):
        raise ClientError("That is more than %d files." % int(limits["max_files"]))
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as src:
                zf.writestr(info, src.read())
    size = os.path.getsize(out_path)
    if size > int(limits["max_bundle_mb"]) * 1024 * 1024:
        os.remove(out_path)
        raise ClientError("The bundle is %.1f MB; this area takes at most %d MB." % (size / 1048576, int(limits["max_bundle_mb"])))
    return size


def validate_folder(folder, limits=None, registry=None):
    """Offline check: zip the folder and run the owner's exact validator over it, without contacting anyone.

    Returns {"pages", "blocks", "files", "warnings"}. Raises ClientError (the folder cannot even be zipped) or
    BundleError (the bundle would be refused; .reasons says why). Nothing is left behind in the temp folder.
    """
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    area = {"slug": "area", "limits": limits}                 # the real slug only matters for URLs, not for the rules
    tmp = Path(tempfile.mkdtemp(prefix="halbach-validate-"))
    try:
        zip_path, dest = tmp / "bundle.zip", tmp / "tree"
        build_zip(folder, zip_path, limits)
        result = validate_bundle(zip_path, dest, area, registry or Registry())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    doc = result["doc"]
    return {"pages": len(doc["pages"]), "blocks": sum(len(p["blocks"]) for p in doc["pages"]),
            "files": len(result["files"]), "warnings": result["warnings"]}


class Area:
    def __init__(self, url, area, key, opener=None):
        self.base, self.area, self.key = _base(url), area, key
        self.opener = opener or urllib.request.build_opener()

    def _req(self, method, path, data=None, headers=None, timeout=60):
        h = {"Authorization": "Bearer " + self.key, "User-Agent": "HalbachConnectHub-area"}
        h.update(headers or {})
        req = urllib.request.Request("%s/drop/%s/%s" % (self.base, self.area, path), data=data, headers=h, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8") or "{}")
            except ValueError:
                body = {}
            return e.code, body
        except (urllib.error.URLError, OSError) as e:
            raise ClientError("Could not reach the site's drop address (%s)." % (getattr(e, "reason", None) or e.__class__.__name__))

    def limits(self):
        code, body = self._req("GET", "limits")
        if code == 401:
            raise ClientError("The area name or key is not accepted.")
        if code == 429:
            raise ClientError("Too many wrong keys from here. Wait 15 minutes.")
        if code != 200:
            raise ClientError("The site answered %d." % code)
        return body

    def status(self):
        code, body = self._req("GET", "status")
        if code != 200:
            raise ClientError("The site answered %d." % code)
        return body

    def send_zip(self, zip_path):
        size = os.path.getsize(zip_path)
        with open(zip_path, "rb") as f:
            code, body = self._req("POST", "bundle", data=f, headers={"Content-Type": "application/zip", "Content-Length": str(size)}, timeout=600)
        return code, body

    def wait(self, sid, seconds=0, interval=3):
        """Poll status until submission sid is decided (or the time is up). Returns its last known entry."""
        end, last = time.time() + seconds, None
        while True:
            for s in self.status()["submissions"]:
                if s["id"] == sid:
                    last = s
            if last and last["state"] != "pending":
                return last
            if time.time() >= end:
                return last
            time.sleep(interval)


# --------------------------------------------------------------------------- stored connection + CLI
def _conn_path(home=None):
    return Path(home or home_dir()) / CONNECTION_FILE


def _restrict_to_current_user(p):
    """Windows: chmod cannot touch the ACL, so icacls takes inheritance away and grants only this account.

    Never fatal -- the connection is written either way. A failure prints one line saying the file kept its
    inherited permissions, and nothing else happens.
    """
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME", "")
    if not user:
        print("warning: %s was not limited to your Windows account (USERNAME is not set)." % p.name)
        return
    cmd = ["icacls", str(p), "/inheritance:r", "/grant:r", "%s:F" % user]
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as e:
        why = " ".join(str(e).split()) or e.__class__.__name__           # keep it to one line
        print("warning: %s was saved but not limited to your Windows account (%s)." % (p.name, why))
        return
    if r.returncode != 0:
        why = next((ln.strip() for ln in (r.stderr or "").splitlines() if ln.strip()), "icacls refused")
        print("warning: %s was saved but not limited to your Windows account (%s)." % (p.name, why))


def save_connection(url, area, key, home=None):
    p = _conn_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"url": _base(url), "area": area, "key": key}), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _restrict_to_current_user(p)
    return p


def load_connection(home=None):
    try:
        return json.loads(_conn_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _resolve(a):
    conn = load_connection(a.home)
    url, area = a.url or os.environ.get("HALBACH_AREA_URL") or conn.get("url"), a.area or os.environ.get("HALBACH_AREA") or conn.get("area")
    key = a.key or os.environ.get("HALBACH_AREA_KEY") or conn.get("key")
    if not (url and area and key):
        raise ClientError("Not connected yet. Run: halbach area connect --url https://live.example.com --area NAME")
    return Area(url, area, key)


def main(argv):
    ap = argparse.ArgumentParser(prog="halbach area", description="Send content to someone's Halbach site (an area you were given a key for).")
    ap.add_argument("--home", help="state folder (where the connection is kept)")
    ap.add_argument("--url"), ap.add_argument("--area"), ap.add_argument("--key", help="avoid this; it can end up in your shell history")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("folder")
    sub.add_parser("connect")
    s = sub.add_parser("send")
    s.add_argument("folder")
    s.add_argument("--wait", type=int, default=0, help="seconds to wait for the owner's decision (default: do not wait)")
    sub.add_parser("status")
    sub.add_parser("limits")
    v = sub.add_parser("validate")
    v.add_argument("folder")
    v.add_argument("--limits", help="JSON file of limits to check against (default: this area's standard limits)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            print("Created %s. Edit it, put pictures in %s, then: halbach area send %s" % (init_folder(a.folder), Path(a.folder) / "files", a.folder))
            return 0
        if a.cmd == "connect":
            url = a.url or input("Site's drop address (https://live.example.com): ").strip()
            area = a.area or input("Area name: ").strip()
            key = a.key or os.environ.get("HALBACH_AREA_KEY") or getpass.getpass("Passphrase (hidden): ").strip()
            client = Area(url, area, key)
            client.limits()                                          # fails clearly when something is wrong
            print("Connected. Saved to", save_connection(url, area, key, a.home))
            return 0
        if a.cmd == "validate":                                     # offline; needs no connection file
            limits = None
            if a.limits:
                try:
                    limits = json.loads(Path(a.limits).read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:
                    raise ClientError("Could not read the limits file (%s)." % e)
            try:
                summary = validate_folder(a.folder, limits)
            except BundleError as e:
                print("This folder would be refused:")
                for r in e.reasons:
                    print("  -", r)
                return 1
            print("Looks good: %d page(s), %d block(s), %d file(s)." % (summary["pages"], summary["blocks"], summary["files"]))
            for w in summary["warnings"]:
                print("  note:", w)
            return 0
        client = _resolve(a)
        if a.cmd == "limits":
            print(json.dumps(client.limits(), indent=2))
            return 0
        if a.cmd == "status":
            for sub_ in client.status()["submissions"]:
                print("%s  %-9s %s%s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(sub_["received"])), sub_["state"], sub_["id"],
                                         ("  - " + sub_["note"]) if sub_.get("note") else ""))
                for r in sub_.get("reasons", []):
                    print("    -", r)
            return 0
        lim = client.limits()
        if lim.get("paused"):
            print("This area is paused by its owner; nothing can be sent right now.")
            return 1
        tmp = Path(a.folder) / ".halbach-bundle.zip"
        build_zip(a.folder, tmp, lim["limits"])
        try:
            code, body = client.send_zip(tmp)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        if code in (200, 201):
            print("Sent. Submission %s is %s%s." % (body["id"], body["state"], " (it was already received)" if body.get("duplicate") else ""))
            for w in body.get("warnings", []):
                print("  note:", w)
            if body["state"] == "pending" and a.wait:
                print("Waiting up to %d s for the owner...." % a.wait)
                final = client.wait(body["id"], a.wait)
                if final:
                    print("Result:", final["state"], ("- " + final["note"]) if final.get("note") else "")
            return 0
        if code == 422:
            print("The bundle was refused:")
            for r in body.get("reasons", []):
                print("  -", r)
            return 1
        print("The site answered %d: %s" % (code, body.get("error", "")))
        return 1
    except (ClientError, BundleError) as e:
        print(str(e))
        return 1
