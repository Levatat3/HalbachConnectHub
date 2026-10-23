"""The update check and the safe updater. Nothing here talks to the network: GitHub is a fake."""
import hashlib
import http.client
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from halbach_connecthub import server, update

REPO = update.REPO
SHA_NEW = "b" * 40
SHA_OLD = "a" * 40


class Resp(io.BytesIO):
    def __init__(self, data, length=True):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))} if length else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGitHub:
    """Answers by URL. A value is bytes/dict (200), an int (that HTTP error) or an Exception to raise."""

    def __init__(self, routes=None):
        self.routes, self.seen = dict(routes or {}), []

    def open(self, req, timeout=None):
        url = req.full_url
        self.seen.append(url)
        if url not in self.routes:
            raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b"{}"))
        v = self.routes[url]
        if isinstance(v, Exception):
            raise v
        if isinstance(v, int):
            raise urllib.error.HTTPError(url, v, "err", {}, io.BytesIO(b"{}"))
        return Resp(v if isinstance(v, bytes) else json.dumps(v).encode())


def api(path):
    return update.API + path


def release(tag="v0.6.0", **kw):
    return dict({"tag_name": tag, "name": "Halbach " + tag, "body": "Adds the updater.\nFixes things.", "prerelease": False, "draft": False,
                 "published_at": "2026-10-23T10:00:00Z"}, **kw)


def make_repo_zip(version="0.6.0", extra=None, drop=(), init_extra="", core="X = 1\n", top=None):
    """A GitHub-style zipball of a tiny Halbach repository."""
    top = top or "Levatat3-HalbachConnectHub-bbbbbbb"
    files = {"halbach_connecthub/__init__.py": '__version__ = "%s"\n%s' % (version, init_extra),
             "halbach_connecthub/__main__.py": "pass\n", "halbach_connecthub/core.py": core,
             "halbach_connecthub/builtin/theme.css": "a{color:red}\n", "third_party/readme.txt": "third party %s\n" % version,
             "README.md": "readme %s\n" % version, "LICENSE": "MIT\n", "docs/AREAS.md": "areas %s\n" % version, "docs/NEW.md": "new doc\n",
             "tests/test_x.py": "assert True\n", "dist/evil.exe": "MZ", ".github/x.yml": "x: 1\n", "_notes/secret.txt": "not shipped\n"}
    files.update(extra or {})
    for d in drop:
        files.pop(d, None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(top + "/" + name, data)
    return buf.getvalue()


def tree(root):
    out = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root)).replace("\\", "/")] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.tmp = Path(self._t.name)
        self.root = self.tmp / "install"
        self.home = self.tmp / "state"
        self.home.mkdir()
        (self.root / "halbach_connecthub").mkdir(parents=True)
        (self.root / "halbach_connecthub" / "__init__.py").write_text('__version__ = "0.5.0"\n', encoding="utf-8")
        (self.root / "halbach_connecthub" / "__main__.py").write_text("pass\n", encoding="utf-8")
        (self.root / "halbach_connecthub" / "old_only.py").write_text("OLD = 1\n", encoding="utf-8")
        (self.root / "third_party").mkdir()
        (self.root / "third_party" / "readme.txt").write_text("third party 0.5.0\n", encoding="utf-8")
        (self.root / "README.md").write_text("readme 0.5.0\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "AREAS.md").write_text("areas 0.5.0\n", encoding="utf-8")
        (self.root / "BUILD.txt").write_text("Halbach ConnectHub 0.5.0, commit %s\n" % SHA_OLD[:7], encoding="utf-8")
        (self.root / "START-HERE-FUNK.md").write_text("mine\n", encoding="utf-8")
        (self.root / "my-page").mkdir()
        (self.root / "my-page" / "area.json").write_text('{"keep": "me"}', encoding="utf-8")
        (self.home / "state.json").write_text('{"keep": "state"}', encoding="utf-8")
        (self.home / "connection.json").write_text('{"key": "never touched"}', encoding="utf-8")

    def tearDown(self):
        self._t.cleanup()

    def gh(self, zip_bytes=None, rel=None, tag="v0.6.0"):
        routes = {api("/repos/%s/releases/latest" % REPO): rel or release(tag),
                  api("/repos/%s/zipball/%s" % (REPO, tag)): zip_bytes if zip_bytes is not None else make_repo_zip()}
        return FakeGitHub(routes)

    def apply(self, gh, expect="v0.6.0", **kw):
        return update.apply(self.home, expect, opener=gh, root=self.root, **kw)


# --------------------------------------------------------------------------- the check
class TestCheck(Base):
    def test_a_newer_release_is_offered_with_its_notes(self):
        r = update.check(self.gh(), self.root)
        self.assertEqual((r["status"], r["latest"]["version"], r["latest"]["token"]), ("available", "0.6.0", "v0.6.0"))
        self.assertIn("Adds the updater", r["latest"]["notes"])
        self.assertTrue(r["can_apply"])

    def test_same_or_older_is_up_to_date(self):
        for tag in ("v0.5.0", "v0.4.9"):
            self.assertEqual(update.check(FakeGitHub({api("/repos/%s/releases/latest" % REPO): release(tag)}), self.root)["status"], "up-to-date", tag)

    def test_drafts_prereleases_and_odd_tags_are_never_offered(self):
        for bad in (release(prerelease=True), release(draft=True), release("v0.6.0-rc1"), release("nightly")):
            gh = FakeGitHub({api("/repos/%s/releases/latest" % REPO): bad, api("/repos/%s/tags?per_page=50" % REPO): []})
            self.assertEqual(update.check(gh, self.root)["status"], "none", bad["tag_name"])

    def test_without_a_release_the_newest_version_tag_is_used(self):
        gh = FakeGitHub({api("/repos/%s/tags?per_page=50" % REPO): [{"name": "v0.5.0"}, {"name": "v0.7.1"}, {"name": "v0.10.0"}, {"name": "junk"}]})
        r = update.check(gh, self.root)
        self.assertEqual((r["status"], r["latest"]["version"]), ("available", "0.10.0"))

    def test_nothing_published_is_not_an_error(self):
        self.assertEqual(update.check(FakeGitHub(), self.root)["status"], "none")

    def test_network_trouble_comes_back_as_a_message_never_a_crash(self):
        for route, text in ((urllib.error.URLError("no route"), "Could not reach GitHub"), (403, "limiting"), (500, "answered 500")):
            r = update.check(FakeGitHub({api("/repos/%s/releases/latest" % REPO): route}), self.root)
            self.assertEqual(r["status"], "error")
            self.assertIn(text, r["error"])

    def test_garbage_from_github_is_refused(self):
        r = update.check(FakeGitHub({api("/repos/%s/releases/latest" % REPO): b"<html>nope"}), self.root)
        self.assertEqual(r["status"], "error")

    def test_a_branch_channel_compares_commits_not_just_names(self):
        tip = {"sha": SHA_NEW, "commit": {"message": "Newest thing\n\nbody", "committer": {"date": "2026-10-23T10:00:00Z"}}}
        cmp_url = api("/repos/%s/compare/%s...%s" % (REPO, SHA_OLD[:7], SHA_NEW))
        for relation, expect in (("ahead", "available"), ("behind", "up-to-date"), ("identical", "up-to-date"), ("diverged", "available")):
            gh = FakeGitHub({api("/repos/%s/commits/main" % REPO): tip,
                             cmp_url: {"status": relation, "commits": [{"commit": {"message": "second change\nmore"}}, {"commit": {"message": "first change"}}]}})
            r = update.check(gh, self.root, channel="branch:main")
            self.assertEqual(r["status"], expect, relation)
            if expect == "available":
                self.assertEqual(r["latest"]["notes"], "first change\nsecond change")
                self.assertEqual(r["latest"]["token"], SHA_NEW)

    def test_a_branch_check_says_unknown_when_this_commit_is_not_on_github(self):
        gh = FakeGitHub({api("/repos/%s/commits/main" % REPO): {"sha": SHA_NEW, "commit": {"message": "x"}}})
        r = update.check(gh, self.root, channel="branch:main")
        self.assertEqual(r["status"], "unknown")                                            # compare 404: a newer, unpushed copy

    def test_the_branch_tip_being_this_commit_is_up_to_date(self):
        gh = FakeGitHub({api("/repos/%s/commits/main" % REPO): {"sha": SHA_OLD, "commit": {"message": "x"}}})
        self.assertEqual(update.check(gh, self.root, channel="branch:main")["status"], "up-to-date")

    def test_branch_names_that_could_smuggle_a_path_are_refused(self):
        for bad in ("branch:../x", "branch:a b", "branch:", "branch:x?y=1", "branch:" + "a" * 80, "weird", "branch:-x"):
            r = update.check(FakeGitHub(), self.root, channel=bad)
            self.assertEqual(r["status"], "error", bad)

    def test_only_the_pinned_repository_and_github_hosts_are_used(self):
        gh = self.gh()
        update.check(gh, self.root)
        self.assertTrue(all(u.startswith("https://api.github.com/repos/%s/" % REPO) for u in gh.seen), gh.seen)
        with self.assertRaises(update.UpdateError):
            update._get(gh, "https://evil.example/x", 100)
        with self.assertRaises(update.UpdateError):
            update._get(gh, "http://api.github.com/x", 100)

    def test_a_redirect_off_github_is_refused(self):
        h = update._SameHostsOnly()
        with self.assertRaises(update.UpdateError):
            h.redirect_request(mock.Mock(), None, 302, "Found", {}, "https://evil.example/archive.zip")
        with self.assertRaises(update.UpdateError):
            h.redirect_request(mock.Mock(), None, 302, "Found", {}, "http://codeload.github.com/x")


class TestLocalInfo(Base):
    def test_a_zip_copy_can_be_updated_and_its_commit_comes_from_build_txt(self):
        i = update.local_info(self.root)
        self.assertEqual((i["version"], i["commit"], i["source"], i["can_apply"]), ("0.5.0", SHA_OLD[:7], "BUILD.txt", True))

    def test_a_git_checkout_is_never_overwritten(self):
        (self.root / ".git").mkdir()
        i = update.local_info(self.root)
        self.assertFalse(i["can_apply"])
        self.assertIn("git pull", i["why_not"])
        with self.assertRaises(update.UpdateError):
            self.apply(self.gh())

    def test_a_pip_install_is_left_to_pip(self):
        pip_root = self.tmp / "lib" / "site-packages"
        (pip_root / "halbach_connecthub").mkdir(parents=True)
        self.assertFalse(update.local_info(pip_root)["can_apply"])

    def test_the_version_on_disk_is_what_counts_after_an_update_and_before_a_restart(self):
        i = update.local_info(self.root)
        self.assertEqual(i["version"], "0.5.0")
        (self.root / "halbach_connecthub" / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
        i = update.local_info(self.root)
        self.assertEqual((i["version"], i["restart_pending"]), ("9.9.9", True))


# --------------------------------------------------------------------------- the archive
class TestArchive(Base):
    def unpack(self, data):
        return update.unpack(data, self.tmp / "staged", self.root)

    def test_only_the_program_and_its_documents_are_unpacked(self):
        version, n = self.unpack(make_repo_zip())
        self.assertEqual(version, "0.6.0")
        got = set(tree(self.tmp / "staged"))
        self.assertIn("halbach_connecthub/core.py", got)
        self.assertIn("halbach_connecthub/builtin/theme.css", got)
        self.assertIn("third_party/readme.txt", got)
        self.assertIn("README.md", got)
        self.assertIn("docs/AREAS.md", got)
        self.assertNotIn("docs/NEW.md", got)                       # only documents this copy already has
        for left_out in ("tests/test_x.py", "dist/evil.exe", ".github/x.yml", "_notes/secret.txt"):
            self.assertNotIn(left_out, got)

    def refused(self, data, why=""):
        with self.assertRaises(update.UpdateError) as cm:
            self.unpack(data)
        self.assertIn(why.lower(), str(cm.exception).lower())
        return cm.exception

    def test_hostile_archives_are_refused(self):
        def zip_of(entries, **kw):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for name, data in entries:
                    zi = zipfile.ZipInfo(name)
                    for k, v in kw.items():
                        setattr(zi, k, v)
                    z.writestr(zi, data)
            return buf.getvalue()
        good = [("t/halbach_connecthub/__init__.py", '__version__ = "0.6.0"\n'), ("t/halbach_connecthub/__main__.py", "pass\n")]
        self.refused(b"not a zip", "valid zip")
        self.refused(zip_of(good + [("t/../evil.py", "x")]), "unsafe")
        self.refused(zip_of(good + [("/abs.py", "x")]), "unsafe")
        for odd in ("t\\evil.py", "..\\x.py", "a//b.py", "./x.py", "x\x00.py", ""):
            self.assertFalse(update._safe_member(odd), odd)                    # (zipfile itself rewrites backslashes on Windows)
        self.refused(zip_of(good + [("C:/evil.py", "x")]), "unsafe")
        self.refused(zip_of(good + [("t/halbach_connecthub/link.py", "target")], external_attr=(stat.S_IFLNK | 0o777) << 16), "link")
        self.refused(zip_of(good + [("other/halbach_connecthub/x.py", "x")]), "laid out")
        locked = bytearray(zip_of(good))
        for sig, off in ((b"PK", 6), (b"PK", 8)):
            i = 0
            while True:
                i = bytes(locked).find(sig, i)
                if i < 0:
                    break
                locked[i + off] |= 1                                            # the "encrypted" flag, as a real password zip has it
                i += 4
        self.refused(bytes(locked), "password")
        self.refused(zip_of([("t/halbach_connecthub/__init__.py", '__version__ = "0.6.0"\n')]), "program")
        self.refused(zip_of([("t/halbach_connecthub/__init__.py", "nothing\n"), ("t/halbach_connecthub/__main__.py", "pass\n")]), "version")

    def test_programs_and_unknown_file_types_inside_the_program_are_refused(self):
        for name in ("halbach_connecthub/run.exe", "halbach_connecthub/x.dll", "halbach_connecthub/go.bat", "halbach_connecthub/y.pyd",
                     "third_party/setup.ps1", "halbach_connecthub/noext"):
            with self.subTest(name=name):
                self.refused(make_repo_zip(extra={name: "x"}), "not allowed")

    def test_oversized_files_and_zip_bombs_are_refused(self):
        with mock.patch.object(update, "MAX_FILE", 100):
            self.refused(make_repo_zip(extra={"halbach_connecthub/big.py": "x = 1\n" * 100}), "too large")
        with mock.patch.object(update, "MAX_UNPACKED", 500):
            self.refused(make_repo_zip(extra={"halbach_connecthub/a.py": "y" * 400, "halbach_connecthub/b.py": "y" * 400}), "larger than expected")
        with mock.patch.object(update, "MAX_FILES", 3):
            self.refused(make_repo_zip(), "number of files")

    def test_a_file_lying_about_its_size_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("t/halbach_connecthub/__init__.py", '__version__ = "0.6.0"\n')
            z.writestr("t/halbach_connecthub/__main__.py", "pass\n" * 50)
        data = bytearray(buf.getvalue())
        # shrink the declared size of __main__.py in both headers
        import struct
        real = len(("pass\n" * 50).encode())
        for sig, off in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
            i = 0
            while True:
                i = bytes(data).find(sig, i)
                if i < 0:
                    break
                if struct.unpack_from("<I", data, i + off)[0] == real:
                    struct.pack_into("<I", data, i + off, 5)
                i += 4
        with self.assertRaises(update.UpdateError):
            self.unpack(bytes(data))


# --------------------------------------------------------------------------- installing
class TestApply(Base):
    def test_a_confirmed_update_installs_backs_up_and_touches_nothing_else(self):
        outside_before = {k: v for k, v in tree(self.root).items() if k.startswith(("my-page/", "START-HERE"))}
        state_before = tree(self.home)
        res = self.apply(self.gh())
        self.assertEqual((res["ok"], res["from"], res["to"], res["restart"]), (True, "0.5.0", "0.6.0", True))
        self.assertEqual((self.root / "halbach_connecthub" / "__init__.py").read_text(encoding="utf-8").strip(), '__version__ = "0.6.0"')
        self.assertTrue((self.root / "halbach_connecthub" / "core.py").is_file())
        self.assertFalse((self.root / "halbach_connecthub" / "old_only.py").exists())      # a file the new version dropped is gone with the old folder
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.6.0\n")
        self.assertEqual((self.root / "docs" / "AREAS.md").read_text(encoding="utf-8"), "areas 0.6.0\n")
        self.assertFalse((self.root / "docs" / "NEW.md").exists())
        self.assertIn("0.6.0", (self.root / "BUILD.txt").read_text(encoding="utf-8"))
        outside_after = {k: v for k, v in tree(self.root).items() if k.startswith(("my-page/", "START-HERE"))}
        self.assertEqual(outside_after, outside_before)                                    # the owner's own files
        self.assertEqual({k: v for k, v in tree(self.home).items() if not k.startswith("program-backups/")}, state_before)   # state and connection
        left = [p.name for p in self.root.iterdir() if ".old-" in p.name or p.name.startswith(update.WORK_PREFIX)]
        self.assertEqual(left, [])
        b = update.backups(self.home)
        self.assertEqual([x["version"] for x in b], ["0.5.0"])
        self.assertTrue((Path(b[0]["path"]) / "files" / "halbach_connecthub" / "old_only.py").is_file())

    def test_the_owner_must_confirm_exactly_what_was_shown(self):
        for expect in (None, "", "v0.7.0", "v0.5.0"):
            with self.subTest(expect=expect), self.assertRaises(update.UpdateError):
                self.apply(self.gh(), expect=expect)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.5.0\n")

    def before(self):
        return tree(self.root)

    def assert_nothing_changed(self, before):
        self.assertEqual(tree(self.root), before, "a refused or failed update changed the install")
        self.assertEqual([p.name for p in self.root.iterdir() if ".old-" in p.name or p.name.startswith(update.WORK_PREFIX)], [])

    def test_a_download_that_does_not_start_changes_nothing(self):
        before = self.before()
        for bad in (make_repo_zip(core="raise RuntimeError('boom')\n"), make_repo_zip(core="def broken(:\n"), make_repo_zip(init_extra="import nothing_here_exists\n")):
            with self.assertRaises(update.UpdateError) as cm:
                self.apply(self.gh(bad))
            self.assertIn("start-up check", str(cm.exception))
            self.assert_nothing_changed(before)
        self.assertEqual(update.backups(self.home), [])

    def test_older_equal_or_mislabelled_downloads_are_refused(self):
        before = self.before()
        for zb, why in ((make_repo_zip(version="0.4.0"), "older"), (make_repo_zip(version="0.5.0"), "not newer"), (make_repo_zip(version="0.9.0"), "different version")):
            with self.assertRaises(update.UpdateError) as cm:
                self.apply(self.gh(zb))
            self.assertIn(why, str(cm.exception))
            self.assert_nothing_changed(before)

    def test_hostile_downloads_change_nothing(self):
        before = self.before()
        for bad in (make_repo_zip(extra={"halbach_connecthub/x.exe": "MZ"}), make_repo_zip(top="a", extra={"../x.py": "x"}), b"junk"):
            with self.assertRaises(update.UpdateError):
                self.apply(self.gh(bad))
            self.assert_nothing_changed(before)

    def test_network_failure_and_oversize_downloads_change_nothing(self):
        before = self.before()
        gh = self.gh()
        gh.routes[api("/repos/%s/zipball/v0.6.0" % REPO)] = urllib.error.URLError("dropped")
        with self.assertRaises(update.UpdateError):
            self.apply(gh)
        with mock.patch.object(update, "MAX_ARCHIVE", 50), self.assertRaises(update.UpdateError):
            self.apply(self.gh())
        self.assert_nothing_changed(before)

    def test_if_the_installed_copy_does_not_start_the_old_files_are_put_back(self):
        before = self.before()
        real = update.self_check
        calls = []

        def flaky(path, python=None):
            calls.append(str(path))
            if len(calls) >= 2:                                  # the staged copy passed; the installed one "fails"
                raise update.UpdateError("it did not start")
            return real(path, python)
        with mock.patch.object(update, "self_check", flaky), self.assertRaises(update.UpdateError) as cm:
            self.apply(self.gh())
        self.assertIn("put back", str(cm.exception))
        self.assert_nothing_changed(before)

    def test_a_locked_file_midway_puts_everything_back(self):
        before = self.before()
        real = os.replace
        state = {"n": 0}

        def flaky(src, dst):
            if str(dst).endswith("README.md") and ".old-" not in str(dst) and state["n"] == 0:
                state["n"] += 1
                raise PermissionError("locked")                          # only the first attempt: putting things back must work
            return real(src, dst)
        with mock.patch.object(update.os, "replace", flaky), self.assertRaises(update.UpdateError) as cm:
            self.apply(self.gh())
        self.assertIn("Nothing was changed", str(cm.exception))
        self.assertEqual(state["n"], 1)
        self.assert_nothing_changed(before)

    def test_two_updates_cannot_run_at_once(self):
        self.assertTrue(update._apply_lock.acquire(blocking=False))
        try:
            with self.assertRaises(update.UpdateError) as cm:
                self.apply(self.gh())
            self.assertIn("already running", str(cm.exception))
        finally:
            update._apply_lock.release()

    def test_after_an_update_a_second_one_does_not_reinstall_the_same_thing_before_a_restart(self):
        self.apply(self.gh())
        r = update.check(self.gh(), self.root)
        self.assertEqual(r["status"], "up-to-date")

    def test_old_backups_are_pruned(self):
        base = self.home / "program-backups"
        for i in range(8):
            d = base / ("2026010%d-000000-v0.1.%d" % (i, i))
            d.mkdir(parents=True)
            (d / "backup.json").write_text(json.dumps({"version": "0.1.%d" % i, "when": i, "items": []}), encoding="utf-8")
        self.apply(self.gh())
        self.assertEqual(len(update.backups(self.home)), update.KEEP_BACKUPS)


class TestRollback(Base):
    def test_going_back_restores_the_program_files_and_nothing_else(self):
        self.apply(self.gh())
        outside = {k: v for k, v in tree(self.root).items() if k.startswith(("my-page/", "START-HERE"))}
        res = update.rollback(self.home, root=self.root)
        self.assertEqual((res["from"], res["to"]), ("0.6.0", "0.5.0"))
        self.assertEqual((self.root / "halbach_connecthub" / "__init__.py").read_text(encoding="utf-8").strip(), '__version__ = "0.5.0"')
        self.assertTrue((self.root / "halbach_connecthub" / "old_only.py").is_file())
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.5.0\n")
        self.assertIn("0.5.0", (self.root / "BUILD.txt").read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in tree(self.root).items() if k.startswith(("my-page/", "START-HERE"))}, outside)
        self.assertEqual(len(update.backups(self.home)), 1)                                # the backup is kept
        self.assertEqual([p.name for p in self.root.iterdir() if ".old-" in p.name or p.name.startswith(update.WORK_PREFIX)], [])

    def test_without_a_backup_there_is_nothing_to_go_back_to(self):
        with self.assertRaises(update.UpdateError):
            update.rollback(self.home, root=self.root)

    def test_a_broken_backup_is_not_installed(self):
        self.apply(self.gh())
        b = update.backups(self.home)[0]
        (Path(b["path"]) / "files" / "halbach_connecthub" / "old_only.py").write_text("def broken(:\n", encoding="utf-8")
        before = tree(self.root)
        with self.assertRaises(update.UpdateError):
            update.rollback(self.home, root=self.root)
        self.assertEqual(tree(self.root), before)


class TestRememberingChecks(Base):
    def test_due_means_never_checked_or_a_day_old(self):
        self.assertTrue(update.due(self.home))
        update.save_check(self.home, {"checked": 1000, "status": "up-to-date"})
        self.assertFalse(update.due(self.home, now=1000 + 3600))
        self.assertTrue(update.due(self.home, now=1000 + 90000))
        (self.home / "update-check.json").write_text("junk", encoding="utf-8")
        self.assertTrue(update.due(self.home))


# --------------------------------------------------------------------------- in the hub
class TestHub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory()
        cls.srv, cls.hub = server.serve(Path(cls._t.name) / "hub", 0, token="tok")
        cls.port = cls.srv.server_address[1]
        server.run_in_thread(cls.srv)
        cls.hub.update_opener = FakeGitHub({api("/repos/%s/releases/latest" % REPO): release("v9.0.0")})

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls._t.cleanup()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port, "X-Halbach-Token": "tok"}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        c.request(method, path, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r.status, json.loads(raw)

    def test_looking_at_the_status_never_uses_the_network(self):
        self.hub.update_opener.seen.clear()
        s, j = self.req("GET", "/api/update")
        self.assertEqual(s, 200)
        self.assertEqual(self.hub.update_opener.seen, [])
        self.assertIn("current", j)
        self.assertTrue(self.req("GET", "/api/state")[1]["update"]["auto_check"])

    def test_checking_asks_github_and_remembers_the_answer(self):
        s, j = self.req("POST", "/api/update/check", {})
        self.assertEqual((s, j["status"], j["latest"]["version"]), (200, "available", "9.0.0"))
        self.assertEqual(self.req("GET", "/api/update")[1]["status"], "available")
        self.assertEqual(self.req("GET", "/api/state")[1]["update"]["latest"]["version"], "9.0.0")

    def test_the_updater_refuses_a_git_checkout_with_a_plain_message(self):
        self.req("POST", "/api/update/check", {})
        s, j = self.req("POST", "/api/update/apply", {"expect": "v9.0.0"})
        self.assertEqual(s, 400)                                                           # the tests run from a git checkout
        self.assertIn("git", j["error"])

    def test_rollback_without_a_backup_is_a_plain_error(self):
        s, j = self.req("POST", "/api/update/rollback", {})
        self.assertEqual(s, 400)

    def test_settings_for_the_channel_and_the_daily_check_are_validated(self):
        self.assertEqual(self.req("POST", "/api/settings", {"update_channel": "branch:modular-site", "update_check": False})[0], 200)
        s = self.req("GET", "/api/state")[1]
        self.assertEqual((s["update"]["channel"], s["update"]["auto_check"]), ("branch:modular-site", False))
        for bad in ("branch:../x", "somewhere", "branch:a b"):
            self.assertEqual(self.req("POST", "/api/settings", {"update_channel": bad})[0], 400, bad)
        self.req("POST", "/api/settings", {"update_channel": "release", "update_check": True})

    def test_the_repository_cannot_be_changed_from_anywhere(self):
        self.assertEqual(self.req("POST", "/api/settings", {"update_repo": "evil/evil"})[0], 200)
        self.assertEqual(update.REPO, "Levatat3/HalbachConnectHub")
        self.assertNotIn("evil", json.dumps(self.req("GET", "/api/state")[1]["settings"]))

    def test_the_background_check_is_off_unless_the_real_app_asks_for_it(self):
        self.assertIsNone(getattr(self.hub, "_update_stop", None))

    def test_a_collaborators_window_has_no_update_routes(self):
        for path in ("/api/update", "/api/update/check", "/api/update/apply", "/api/update/rollback"):
            self.assertNotIn(path, server.AREA_GET | server.AREA_POST)


class TestWatcher(unittest.TestCase):
    def test_the_daily_check_runs_once_when_due_and_can_be_switched_off(self):
        with tempfile.TemporaryDirectory() as t:
            srv, hub = server.serve(Path(t) / "hub", 0, token="tok")
            hub.update_opener = FakeGitHub({api("/repos/%s/releases/latest" % REPO): release("v9.0.0")})
            hub.start_update_watcher(first_delay=0.05, interval=0.05)
            deadline = time.time() + 5
            while time.time() < deadline and not update.load_check(hub.store.home):
                time.sleep(0.05)
            self.assertEqual(update.load_check(hub.store.home)["status"], "available")
            n = len(hub.update_opener.seen)
            time.sleep(0.4)
            self.assertEqual(len(hub.update_opener.seen), n, "it must not ask again while the last answer is under a day old")
            hub.close()
            srv.server_close()

    def test_switched_off_it_never_asks(self):
        with tempfile.TemporaryDirectory() as t:
            srv, hub = server.serve(Path(t) / "hub", 0, token="tok")
            hub.store.set_settings(update_check=False)
            hub.update_opener = FakeGitHub({})
            hub.start_update_watcher(first_delay=0.05, interval=0.05)
            time.sleep(0.4)
            self.assertEqual(hub.update_opener.seen, [])
            hub.close()
            srv.server_close()


class TestCli(Base):
    def run_cli(self, *argv, answer=None):
        from halbach_connecthub import updatecli
        out = io.StringIO()
        with mock.patch.object(update, "install_root", return_value=self.root), mock.patch.object(update, "default_opener", return_value=self.gh()), \
                mock.patch("sys.stdout", out), mock.patch("builtins.input", return_value=answer or ""):
            code = updatecli.main(["--home", str(self.home)] + list(argv)) if argv[0] != "x" else 0
        return code, out.getvalue()

    def test_check_apply_and_rollback_from_the_command_line(self):
        code, text = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("Newer version available: 0.6.0", text)
        self.assertIn("Adds the updater", text)
        code, text = self.run_cli("apply", answer="n")
        self.assertIn("Nothing was changed", text)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.5.0\n")
        code, text = self.run_cli("apply", "--yes")
        self.assertEqual(code, 0, text)
        self.assertIn("Updated Halbach 0.5.0 -> 0.6.0", text)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.6.0\n")
        code, text = self.run_cli("rollback", "--yes")
        self.assertEqual(code, 0, text)
        self.assertEqual((self.root / "README.md").read_text(encoding="utf-8"), "readme 0.5.0\n")


if __name__ == "__main__":
    unittest.main()
