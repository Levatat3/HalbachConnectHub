"""Update check and a safe updater (stdlib only).

The check asks GitHub, over https, whether a newer Halbach exists. The updater downloads it only when the owner says so,
and it never runs anything it downloaded until the copy has been checked:

  1. the repository is pinned in this file (never a setting, never read from the network), and every redirect must stay on GitHub;
  2. the archive is size-capped and every entry name is checked (no traversal, no links, only plain program and document files);
  3. it is unpacked to a staging folder and imported in a separate, isolated Python to prove it loads (the running copy is untouched);
  4. the current program files are copied to a backup folder first;
  5. the new files are swapped in, one step at a time, and any failure puts the old ones straight back;
  6. a final import check runs on the installed copy, and if it fails the backup is restored automatically.

Only the program files are replaced: `halbach_connecthub/`, `third_party/`, and a few top-level documents. The owner's state
folder (sites, areas, keys, settings, history), the area connection and anything else on the disk are never touched.
Starting the new version is left to the owner: quit and open Halbach again.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

from . import __version__

REPO = "Levatat3/HalbachConnectHub"                 # pinned: an update can only ever come from this repository
API = "https://api.github.com"
GITHUB_HOSTS = {"api.github.com", "codeload.github.com", "github.com"}
USER_AGENT = "HalbachConnectHub-updater"
TIMEOUT = 20
MAX_ARCHIVE = 40 * 1024 * 1024
MAX_UNPACKED = 150 * 1024 * 1024
MAX_FILE = 25 * 1024 * 1024
MAX_FILES = 4000
MAX_NOTES = 4000
KEEP_BACKUPS = 5
CHECK_MAX_AGE = 24 * 3600

INSTALL_DIRS = ("halbach_connecthub", "third_party")
INSTALL_FILES = ("README.md", "LICENSE", "SECURITY.md", "CHANGELOG.md", "pyproject.toml")
DOCS_DIR = "docs"
OK_EXT = {"py", "js", "css", "json", "html", "md", "txt", "png", "jpg", "jpeg", "gif", "webp", "ico", "svg", "toml", "funktheme", "cfg", "csv"}
OK_BARE = {"LICENSE", "NOTICE", "COPYING", "py.typed"}
SKIP_NAMES = {".gitignore", ".gitattributes", ".gitkeep", ".editorconfig"}
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,59}$")
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
WORK_PREFIX = ".halbach-update-"
_apply_lock = threading.Lock()


class UpdateError(Exception):
    """Something stopped the check or the update. The message is a plain sentence for the owner."""


# --------------------------------------------------------------------------- where this copy lives
def install_root():
    return Path(__file__).resolve().parent.parent


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)           # a background program must never flash a console window
SHA40 = re.compile(r"[0-9a-f]{40}")


def _git_dirs(root):
    """(the folder holding HEAD, the folder holding refs) for a checkout or a linked worktree, or None."""
    dot = Path(root) / ".git"
    try:
        if dot.is_dir():
            return dot, dot
        text = dot.read_text(encoding="utf-8").strip()                 # a worktree: ".git" is a file that points at its folder
        if not text.startswith("gitdir:"):
            return None
        gd = Path(text[len("gitdir:"):].strip())
        gd = gd if gd.is_absolute() else (Path(root) / gd)
        common = gd
        try:
            c = (gd / "commondir").read_text(encoding="utf-8").strip()
            common = (gd / c).resolve() if c else gd
        except OSError:
            pass
        return gd, common
    except OSError:
        return None


def _git_commit(root):
    """The commit a checkout is on, read straight from the .git files. It never starts a program: this is called every time the
    window refreshes, and starting git from a background app flashes a console window on Windows."""
    dirs = _git_dirs(root)
    if not dirs:
        return None
    head_dir, common = dirs
    try:
        head = (head_dir / "HEAD").read_text(encoding="utf-8").strip()
        if SHA40.fullmatch(head):
            return head
        if not head.startswith("ref:"):
            return None
        ref = head[4:].strip()
        if ".." in ref or not ref.startswith("refs/"):
            return None
        for base in (head_dir, common):
            f = base / ref
            if f.is_file():
                sha = f.read_text(encoding="utf-8").strip()
                return sha if SHA40.fullmatch(sha) else None
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].strip() == ref and SHA40.fullmatch(parts[0]):
                    return parts[0]
    except OSError:
        pass
    return None


def _disk_version(root):
    """The version of the program files on disk (after an update this is newer than the copy still running until it is restarted)."""
    try:
        m = re.search(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', (Path(root) / "halbach_connecthub" / "__init__.py").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def local_info(root=None, version=None):
    """What this copy is: version, the commit it was built from (when known), and whether the updater may replace it."""
    root = Path(root or install_root())
    disk = _disk_version(root)
    info = {"version": version or disk or __version__, "running": __version__, "commit": None, "source": "unknown", "can_apply": True, "why_not": "",
            "restart_pending": bool(disk and disk != __version__)}
    if (root / ".git").exists():
        info.update(source="git", commit=_git_commit(root), can_apply=False,
                    why_not="This copy is a git checkout. Update it with git (git pull) so your own changes are never overwritten.")
        return info
    if "site-packages" in root.parts or "dist-packages" in root.parts:
        info.update(source="pip", can_apply=False, why_not="Halbach was installed with pip. Update it with: pip install --upgrade halbach-connecthub")
        return info
    try:
        text = (root / "BUILD.txt").read_text(encoding="utf-8")
        m = re.search(r"commit\s+([0-9a-f]{7,40})", text)
        if m:
            info.update(source="BUILD.txt", commit=m.group(1))
    except OSError:
        pass
    if not (root / "halbach_connecthub" / "__init__.py").is_file():
        info.update(can_apply=False, why_not="The program folder could not be found next to this file.")
    return info


def parse_version(text):
    m = SEMVER.match(str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


# --------------------------------------------------------------------------- talking to GitHub
class _SameHostsOnly(urllib.request.HTTPRedirectHandler):
    """A redirect may only lead to another GitHub address over https."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or (u.hostname or "").lower() not in GITHUB_HOSTS:
            raise UpdateError("GitHub sent the download somewhere else (%s), so it was refused." % (u.hostname or "?")[:60])
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener():
    return urllib.request.build_opener(_SameHostsOnly())


def _get(opener, url, limit, accept="application/vnd.github+json"):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in GITHUB_HOSTS:
        raise UpdateError("Only GitHub addresses are used for updates.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            length = r.headers.get("Content-Length") if hasattr(r, "headers") else None
            if length and length.isdigit() and int(length) > limit:
                raise UpdateError("The download is larger than the %d MB limit." % (limit // (1024 * 1024)))
            data = r.read(limit + 1)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code in (403, 429):
            raise UpdateError("GitHub is limiting requests from this connection right now. Try again in a while.")
        raise UpdateError("GitHub answered %d." % e.code)
    except UpdateError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise UpdateError("Could not reach GitHub (%s)." % (getattr(e, "reason", None) or e.__class__.__name__))
    if len(data) > limit:
        raise UpdateError("The download is larger than the %d MB limit." % (limit // (1024 * 1024)))
    return data


def _json(opener, path):
    data = _get(opener, API + path, 4 * 1024 * 1024)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError("GitHub's answer could not be read.")


def _notes(text):
    return "".join(c for c in str(text or "") if c == "\n" or c == "\t" or 32 <= ord(c))[:MAX_NOTES].strip()


def _branch_ok(name):
    if not BRANCH_RE.match(name or "") or ".." in name:
        raise UpdateError("'%s' is not a branch name Halbach will follow." % (name or "")[:40])
    return name


def check(opener=None, root=None, channel="release", now=None):
    """Ask GitHub whether a newer version exists. Never raises: problems come back as status 'error' with a plain message.

    channel "release" follows the newest tagged version (x.y.z); "branch:<name>" follows the newest commit on that branch.
    Returns {status, current, latest, channel, checked, error, can_apply, why_not}; status is up-to-date, available,
    unknown (cannot tell), none (nothing published yet) or error."""
    root = Path(root or install_root())
    opener = opener or default_opener()
    cur = local_info(root)
    out = {"status": "error", "current": cur, "latest": None, "channel": channel, "checked": int(now or time.time()), "error": "",
           "can_apply": cur["can_apply"], "why_not": cur["why_not"]}
    try:
        if channel == "release":
            latest = _latest_release(opener)
            if latest is None:
                out["status"] = "none"
                return out
            out["latest"] = latest
            mine = parse_version(cur["version"])
            theirs = parse_version(latest["version"])
            out["status"] = "available" if (mine and theirs and theirs > mine) else "up-to-date"
        elif channel.startswith("branch:"):
            branch = _branch_ok(channel[len("branch:"):])
            latest, relation = _branch_tip(opener, branch, cur["commit"])
            out["latest"] = latest
            if not cur["commit"]:
                out["status"] = "unknown"
                out["error"] = "This copy does not say which commit it was built from, so Halbach cannot tell if it is current."
            elif relation in ("ahead", "diverged"):
                out["status"] = "available"                                  # the branch has commits this copy does not
            elif relation in ("identical", "behind"):
                out["status"] = "up-to-date"                                 # this copy is the branch tip, or newer than what is published
            else:
                out["status"] = "unknown"
                out["error"] = "This copy's commit is not on GitHub (it may be newer than what is published), so Halbach cannot tell."
        else:
            raise UpdateError("Unknown update channel '%s'." % channel[:30])
    except UpdateError as e:
        out["status"], out["error"] = "error", str(e)
    return out


def _latest_release(opener):
    rel = _json(opener, "/repos/%s/releases/latest" % REPO)
    if isinstance(rel, dict) and parse_version(rel.get("tag_name")) and not rel.get("prerelease") and not rel.get("draft"):
        tag = rel["tag_name"]
        return {"version": ".".join(str(n) for n in parse_version(tag)), "token": tag, "ref": tag, "sha": None, "name": str(rel.get("name") or tag)[:80],
                "notes": _notes(rel.get("body")), "url": "https://github.com/%s/releases/tag/%s" % (REPO, tag), "published": str(rel.get("published_at") or "")[:30]}
    tags = _json(opener, "/repos/%s/tags?per_page=50" % REPO)
    best = None
    for t in tags or []:
        v = parse_version((t or {}).get("name"))
        if v and (best is None or v > best[0]):
            best = (v, t["name"])
    if not best:
        return None
    tag = best[1]
    return {"version": ".".join(str(n) for n in best[0]), "token": tag, "ref": tag, "sha": None, "name": tag, "notes": "",
            "url": "https://github.com/%s/releases/tag/%s" % (REPO, tag), "published": ""}


def _branch_tip(opener, branch, local_commit):
    """(the newest commit on the branch, how it relates to this copy: ahead | behind | identical | diverged | None)."""
    c = _json(opener, "/repos/%s/commits/%s" % (REPO, urllib.parse.quote(branch, safe="")))
    if not isinstance(c, dict) or not re.fullmatch(r"[0-9a-f]{40}", str(c.get("sha") or "")):
        raise UpdateError("The branch '%s' was not found on GitHub." % branch[:40])
    sha = c["sha"]
    notes = ((c.get("commit") or {}).get("message") or "").splitlines()[0:1]
    lines, relation = list(notes), None
    if local_commit:
        if sha.startswith(local_commit) or local_commit.startswith(sha[:len(local_commit)]):
            relation = "identical"
        elif re.fullmatch(r"[0-9a-f]{7,40}", local_commit):
            cmp = _json(opener, "/repos/%s/compare/%s...%s" % (REPO, local_commit, sha))
            if isinstance(cmp, dict) and cmp.get("status") in ("ahead", "behind", "identical", "diverged"):
                relation = cmp["status"]
                if relation in ("ahead", "diverged") and isinstance(cmp.get("commits"), list):
                    lines = [((x.get("commit") or {}).get("message") or "").splitlines()[0] for x in cmp["commits"][-15:][::-1]]
                    lines = [l for l in lines if l] + (["(and %d earlier changes)" % (len(cmp["commits"]) - 15)] if len(cmp["commits"]) > 15 else [])
    return {"version": None, "token": sha, "ref": sha, "sha": sha, "name": "%s @ %s" % (branch, sha[:7]), "notes": _notes("\n".join(lines)),
            "url": "https://github.com/%s/commit/%s" % (REPO, sha), "published": str(((c.get("commit") or {}).get("committer") or {}).get("date") or "")[:30]}, relation


# --------------------------------------------------------------------------- the archive
def _safe_member(name):
    if not name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name) or "\x00" in name:
        return False
    return all(seg not in ("", ".", "..") for seg in name.rstrip("/").split("/"))


def _ext_ok(rel):
    base = rel.rsplit("/", 1)[-1]
    if base in OK_BARE:
        return True
    return "." in base and base.rsplit(".", 1)[-1].lower() in OK_EXT


def _wanted(rel, local_docs):
    """Is this path (relative to the repository root) one the updater installs?"""
    parts = rel.split("/")
    if parts[0] in INSTALL_DIRS and len(parts) > 1:
        return True
    if len(parts) == 1 and parts[0] in INSTALL_FILES:
        return True
    if parts[0] == DOCS_DIR and len(parts) == 2 and parts[1] in local_docs:
        return True
    return False


def unpack(archive_bytes, dest, root):
    """Validate the archive and unpack only the files the updater installs into dest. Returns (version found inside, file count)."""
    try:
        return _unpack(archive_bytes, dest, root)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OverflowError, OSError):
        raise UpdateError("The download is damaged or could not be unpacked safely.")


def _unpack(archive_bytes, dest, root):
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except (zipfile.BadZipFile, OSError, ValueError):
        raise UpdateError("The download is not a valid zip file.")
    infos = zf.infolist()
    if not infos or len(infos) > MAX_FILES:
        raise UpdateError("The download has an unexpected number of files.")
    tops = {i.filename.split("/", 1)[0] for i in infos if i.filename}
    for i in infos:
        if not _safe_member(i.filename):
            raise UpdateError("The download contains an unsafe file name, so it was refused.")
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise UpdateError("The download contains a link, which is not allowed.")
        if i.flag_bits & 0x1:
            raise UpdateError("The download is password protected, which is not allowed.")
    if len(tops) != 1:
        raise UpdateError("The download is not laid out like a Halbach repository.")
    prefix = tops.pop() + "/"
    local_docs = {p.name for p in (Path(root) / DOCS_DIR).glob("*") if p.is_file()} if (Path(root) / DOCS_DIR).is_dir() else set()
    total, n, version = 0, 0, None
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for i in infos:
        if i.is_dir() or not i.filename.startswith(prefix):
            continue
        rel = i.filename[len(prefix):]
        if not rel or rel.rsplit("/", 1)[-1] in SKIP_NAMES or not _wanted(rel, local_docs):
            continue
        if not _ext_ok(rel):
            raise UpdateError("The download contains a file type that is not allowed ('%s'), so it was refused." % "".join(c if 32 <= ord(c) < 127 else "?" for c in rel)[:60])
        if i.file_size > MAX_FILE:
            raise UpdateError("A file in the download is too large.")
        target = dest / rel
        if not str(target.resolve()).startswith(str(dest.resolve()) + os.sep):
            raise UpdateError("The download would write outside its folder, so it was refused.")
        target.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with zf.open(i) as src, open(target, "wb") as out:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > i.file_size or written > MAX_FILE:
                    raise UpdateError("A file in the download is bigger than it says, so it was refused.")
                out.write(chunk)
        total += written
        n += 1
        if total > MAX_UNPACKED:
            raise UpdateError("The download is larger than expected once unpacked.")
    init = dest / "halbach_connecthub" / "__init__.py"
    if not init.is_file() or not (dest / "halbach_connecthub" / "__main__.py").is_file():
        raise UpdateError("The download does not contain the Halbach program.")
    m = re.search(r'^__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', init.read_text(encoding="utf-8"), re.M)
    if not m:
        raise UpdateError("The download does not say which version it is.")
    version = m.group(1)
    return version, n


CHECK_CODE = r"""
import sys, importlib, pkgutil
root = sys.argv[1]
sys.path.insert(0, root)
import halbach_connecthub as h
bad = []
for m in pkgutil.walk_packages(h.__path__, "halbach_connecthub."):
    if m.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(m.name)
    except Exception as e:
        bad.append("%s: %s" % (m.name, str(e)[:100]))
print("VERSION", h.__version__)
if bad:
    print("BAD", "; ".join(bad))
    sys.exit(2)
"""


def self_check(path, python=None):
    """Import every module of the program at `path` in a separate, isolated Python. Returns the version it reports."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([python or sys.executable, "-I", "-B", "-c", CHECK_CODE, str(path)], capture_output=True, text=True, timeout=90, env=env,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        raise UpdateError("The new version could not be test-started (%s)." % e.__class__.__name__)
    out = (r.stdout or "").strip().splitlines()
    ver = next((l.split(" ", 1)[1] for l in out if l.startswith("VERSION ")), None)
    if r.returncode != 0 or not ver:
        why = next((l[4:] for l in out if l.startswith("BAD ")), None) or (r.stderr or "").strip().splitlines()[-1:] or ["it did not start"]
        raise UpdateError("The new version failed its start-up check, so nothing was changed (%s)." % (why if isinstance(why, str) else why[0])[:200])
    return ver


# --------------------------------------------------------------------------- the swap
def _items(root, staged):
    """(name, is_dir) for every top-level thing the update installs that exists in the staged copy."""
    out = []
    for d in INSTALL_DIRS:
        if (staged / d).is_dir():
            out.append((d, True))
    for f in INSTALL_FILES:
        if (staged / f).is_file():
            out.append((f, False))
    if (staged / DOCS_DIR).is_dir():
        for p in sorted((staged / DOCS_DIR).iterdir()):
            if p.is_file():
                out.append((DOCS_DIR + "/" + p.name, False))
    return out


def _copy_in(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if Path(src).is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _remove(p):
    p = Path(p)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def _swap_in(root, staged, items, tag):
    """Move each current item aside and the new one into place. On any failure everything already moved is put back."""
    done = []
    try:
        for name, _is_dir in items:
            cur, new, aside = Path(root) / name, Path(staged) / name, Path(root) / (name + ".old-" + tag)
            had = cur.exists()
            if had:
                os.replace(cur, aside)
            try:
                cur.parent.mkdir(parents=True, exist_ok=True)
                os.replace(new, cur)
            except OSError:
                if had:
                    os.replace(aside, cur)
                raise
            done.append((cur, aside, had))
    except OSError as e:
        for cur, aside, had in reversed(done):
            _remove(cur)
            if had and aside.exists():
                try:
                    os.replace(aside, cur)
                except OSError:
                    pass
        raise UpdateError("Windows would not let the program files be replaced (%s). Nothing was changed; close anything using the Halbach folder and try again." % e.__class__.__name__)
    return done


def _write_build(root, version, sha):
    try:
        text = "Halbach ConnectHub %s%s\n(updated from GitHub on %s)\n" % (version, (", commit " + sha) if sha else "", time.strftime("%Y-%m-%d %H:%M"))
        (Path(root) / "BUILD.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass


def _clear_stale(root):
    """Staging folders left by an update that was interrupted (older than a day) are removed."""
    for d in Path(root).glob(WORK_PREFIX + "*"):
        try:
            if d.is_dir() and time.time() - d.stat().st_mtime > 86400:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _prune(base, keep):
    kept = sorted((p for p in Path(base).glob("*") if p.is_dir()), key=lambda p: p.name)
    for old in kept[:-keep] if len(kept) > keep else []:
        shutil.rmtree(old, ignore_errors=True)


def backups(home):
    base = Path(home) / "program-backups"
    out = []
    for d in sorted((p for p in base.glob("*") if p.is_dir()), key=lambda p: p.name, reverse=True) if base.is_dir() else []:
        try:
            meta = json.loads((d / "backup.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": d.name, "version": meta.get("version"), "when": meta.get("when"), "path": str(d)})
    return out


def apply(home, expect, opener=None, root=None, channel="release", python=None, log=None):
    """Download, check, back up and install the update the owner confirmed (`expect` is the token check() showed them).

    Returns {"ok": True, "from", "to", "backup", "restart": True}. Raises UpdateError; when it does, the installed program is
    exactly as it was (or has been put back from the backup)."""
    log = log or (lambda *a: None)
    root = Path(root or install_root())
    opener = opener or default_opener()
    home = Path(home)
    if not _apply_lock.acquire(blocking=False):
        raise UpdateError("An update is already running.")
    tag = time.strftime("%Y%m%d-%H%M%S")
    work = root / (WORK_PREFIX + tag)                     # beside the program, so the final move is a rename on the same drive
    _clear_stale(root)
    try:
        cur = local_info(root)
        if not cur["can_apply"]:
            raise UpdateError(cur["why_not"])
        res = check(opener, root, channel)
        if res["status"] == "error":
            raise UpdateError(res["error"])
        latest = res["latest"]
        if res["status"] != "available" or not latest:
            raise UpdateError("There is no newer version to install.")
        if not expect or expect != latest["token"]:
            raise UpdateError("The newest version changed since you looked. Check again, then confirm the update.")
        log("Downloading Halbach %s" % (latest["version"] or latest["name"]))
        data = _get(opener, "%s/repos/%s/zipball/%s" % (API, REPO, urllib.parse.quote(latest["ref"], safe="")), MAX_ARCHIVE, accept="application/vnd.github+json")
        if not data:
            raise UpdateError("GitHub has no download for that version.")
        staged = work / "new"
        version, nfiles = unpack(data, staged, root)
        old_v, new_v = parse_version(cur["version"]), parse_version(version)
        if not new_v or (old_v and new_v < old_v):
            raise UpdateError("The download is older (%s) than this copy (%s), so it was not installed." % (version, cur["version"]))
        if channel == "release" and (not old_v or new_v <= old_v):
            raise UpdateError("The download is not newer than this copy, so it was not installed.")
        if channel == "release" and latest.get("version") and latest["version"] != version:
            raise UpdateError("The download says it is version %s but GitHub announced %s, so it was not installed (different version)." % (version, latest["version"]))
        got = self_check(staged, python)
        if got != version:
            raise UpdateError("The new version reports a different version number than it should, so it was not installed.")
        items = _items(root, staged)
        bdir = home / "program-backups" / (tag + "-v" + cur["version"])
        for name, _d in items:
            if (root / name).exists():
                _copy_in(root / name, bdir / "files" / name)
        if (root / "BUILD.txt").is_file():
            _copy_in(root / "BUILD.txt", bdir / "files" / "BUILD.txt")
        (bdir).mkdir(parents=True, exist_ok=True)
        (bdir / "backup.json").write_text(json.dumps({"version": cur["version"], "commit": cur["commit"], "when": int(time.time()), "items": [n for n, _ in items]}), encoding="utf-8")
        log("Installing Halbach %s (backup kept at %s)" % (version, bdir.name))
        done = _swap_in(root, staged, items, tag)
        try:
            self_check(root, python)
        except UpdateError:
            for cur_p, aside, had in reversed(done):                      # the installed copy does not start: put the old files back
                _remove(cur_p)
                if had and aside.exists():
                    os.replace(aside, cur_p)
            raise UpdateError("The new version did not start after it was installed, so the old version was put back. Nothing was lost.")
        for _cur, aside, _had in done:
            _remove(aside)
        _write_build(root, version, latest["sha"])
        _prune(home / "program-backups", KEEP_BACKUPS)
        return {"ok": True, "from": cur["version"], "to": version, "files": nfiles, "backup": bdir.name, "restart": True,
                "from_commit": cur["commit"], "to_commit": latest["sha"]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        _apply_lock.release()


def rollback(home, root=None, python=None, which=None):
    """Put back the program files saved by the last update (or the one named `which`). The owner's data is never touched."""
    root = Path(root or install_root())
    home = Path(home)
    cur = local_info(root)
    if not cur["can_apply"]:
        raise UpdateError(cur["why_not"])
    found = backups(home)
    pick = next((b for b in found if b["id"] == which), None) if which else (found[0] if found else None)
    if not pick:
        raise UpdateError("There is no earlier version saved to go back to.")
    if not _apply_lock.acquire(blocking=False):
        raise UpdateError("An update is already running.")
    try:
        bdir = Path(pick["path"])
        meta = json.loads((bdir / "backup.json").read_text(encoding="utf-8"))
        staged = bdir / "files"
        got = self_check(staged, python)
        items = [(n, (staged / n).is_dir()) for n in meta.get("items", []) if (staged / n).exists()]
        tag = time.strftime("%Y%m%d-%H%M%S") + "r"
        work = root / (WORK_PREFIX + tag)
        try:
            for name, _d in items:                                           # swap from a copy so the backup itself stays
                _copy_in(staged / name, work / name)
            done = _swap_in(root, work, items, tag)
        except (OSError, shutil.Error) as e:
            shutil.rmtree(work, ignore_errors=True)
            raise UpdateError("The earlier version could not be put back (%s). Nothing was changed." % e.__class__.__name__)
        try:
            self_check(root, python)
        except UpdateError:
            for cur_p, aside, had in reversed(done):
                _remove(cur_p)
                if had and aside.exists():
                    os.replace(aside, cur_p)
            raise UpdateError("The earlier version did not start, so the current one was kept.")
        for _cur, aside, _had in done:
            _remove(aside)
        if (staged / "BUILD.txt").is_file():
            shutil.copy2(staged / "BUILD.txt", root / "BUILD.txt")
        shutil.rmtree(work, ignore_errors=True)
        return {"ok": True, "from": cur["version"], "to": got, "restart": True}
    finally:
        _apply_lock.release()


# --------------------------------------------------------------------------- remembering the last check
def _cache_path(home):
    return Path(home) / "update-check.json"


def save_check(home, result):
    try:
        p = _cache_path(home)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(result), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def load_check(home):
    try:
        d = json.loads(_cache_path(home).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def due(home, now=None, max_age=CHECK_MAX_AGE):
    d = load_check(home)
    return not d or (now or time.time()) - d.get("checked", 0) > max_age
