"""Areas: a lane on the site that one other person can send content into, without any keys of yours.

Configuration lives in the hub's state (areas list, key hashes). Everything an area ever received lives under
<home>/areas/<id>/ :

    quarantine/            raw uploads, deleted as soon as they are handled
    submissions/<sid>/     meta.json (+ tree/ with the validated bundle when it passed)
    approved/v0001/        full snapshots of approved content (the newest approved one is live)
    audit.jsonl            one line per attempt

Stdlib only.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time

from . import siteschema as sc
from .bundle import ALLOWABLE_BLOCKS, DEFAULT_BLOCKS, DEFAULT_LIMITS, KNOWN_EXT, BundleError, validate_bundle
from .store import ValidationError

_SAFE_SID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"          # no look-alike characters
KEY_LENGTH = 26
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
LOCK_AFTER = 10
LOCK_SECONDS = 15 * 60
REJECT_KEEP_SECONDS = 30 * 24 * 3600
UPLOADS_PER_WINDOW = 12
UPLOAD_WINDOW = 10 * 60
TRUST_LEVELS = ("review", "trusted", "auto")           # "auto" = approved and published automatically (debounced, freezable)
HARD_CAPS = {"max_file_mb": 90, "max_bundle_mb": 90, "max_total_mb": 5000, "max_files": 2000, "max_image_px": 12000}


class Locked(Exception):
    """Too many wrong keys from this address; try again later."""


# --------------------------------------------------------------------------- keys
def new_passphrase():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(KEY_LENGTH))
    return "-".join(raw[i:i + 5] for i in range(0, len(raw), 5))


def normalize_key(key):
    return "".join(ch for ch in str(key or "").lower() if ch.isalnum())[:80]


def hash_key(key, salt=None, params=SCRYPT):
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(normalize_key(key).encode("utf-8"), salt=salt, n=params["n"], r=params["r"], p=params["p"], dklen=32)
    return "scrypt$%d$%d$%d$%s$%s" % (params["n"], params["r"], params["p"], base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_key(key, stored):
    try:
        scheme, n, r, p, salt, want = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hash_key(key, base64.b64decode(salt), {"n": int(n), "r": int(r), "p": int(p)}).rsplit("$", 1)[1]
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError, MemoryError):
        return False


_DUMMY = hash_key("dummy-key-for-constant-work")


def _clamp_limits(limits):
    out = dict(DEFAULT_LIMITS)
    for k, v in (limits or {}).items():
        if k == "extensions":
            exts = [str(e).lower().lstrip(".") for e in (v or [])]
            out[k] = [e for e in exts if e in KNOWN_EXT]
        elif k in HARD_CAPS:
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise ValidationError("%s must be a whole number" % k)
            if not 1 <= n <= HARD_CAPS[k]:
                raise ValidationError("%s must be between 1 and %d" % (k, HARD_CAPS[k]))
            out[k] = n
    return out


class AreaService:
    def __init__(self, store, registry, slug_taken=None, min_free_bytes=2 * 1024 ** 3, clock=time.time):
        self.store = store
        self.registry = registry
        self.slug_taken = slug_taken or (lambda slug: False)
        self.min_free_bytes = min_free_bytes
        self.clock = clock
        self.root = store.home / "areas"
        self.lock = threading.RLock()
        self.fails = {}                 # ip hash -> [count, locked until]
        self.recent = {}                # (area id, ip hash) -> [upload times]
        self.upload_slots = threading.Semaphore(2)
        self.on_auto_publish = None     # set by the hub: (area id, version that was live before) when an auto area gains content
        self.recover()

    # ---------------------------------------------------------------- state helpers
    @property
    def areas(self):
        return self.store.state.setdefault("areas", [])

    def _save(self):
        with self.store.lock:
            self.store._save()

    def any_enabled(self):
        """True when at least one area can receive uploads. With none, the live hub stays strictly read-only."""
        return any(a["enabled"] and a.get("key") for a in self.areas)

    def get(self, aid):
        for a in self.areas:
            if a["id"] == aid:
                return a
        return None

    def _must(self, aid):
        a = self.get(aid)
        if not a:
            raise ValidationError("There is no area '%s'" % aid)
        return a

    def dir(self, aid):
        return self.root / aid

    def _salt(self):
        with self.store.lock:
            if "area_salt" not in self.store.state:
                self.store.state["area_salt"] = secrets.token_hex(16)
                self.store._save()
            return self.store.state["area_salt"]

    def ip_hash(self, ip):
        return hashlib.sha256((self._salt() + str(ip)).encode()).hexdigest()[:12]

    def recover(self):
        """Clean up anything a crash left half-written."""
        if not self.root.is_dir():
            return
        for area_dir in self.root.iterdir():
            q = area_dir / "quarantine"
            if q.is_dir():
                shutil.rmtree(q, ignore_errors=True)
            for part in area_dir.rglob("*.part"):
                shutil.rmtree(part, ignore_errors=True)

    # ---------------------------------------------------------------- owner: manage areas
    def view(self, a):
        subs = self.submissions(a["id"], limit=200)
        return {"id": a["id"], "name": a["name"], "slug": a["slug"], "enabled": a["enabled"], "paused": a["paused"], "trust": a["trust"],
                "nav": a["nav"], "limits": a["limits"], "blocks": a["blocks"], "created": a["created"], "auto": a.get("auto"),
                "key": {"hint": (a.get("key") or {}).get("hint"), "created": (a.get("key") or {}).get("created"),
                        "last_used": (a.get("key") or {}).get("last_used"), "active": bool(a.get("key"))},
                "current": a.get("current"), "versions": self.versions(a["id"]),
                "pending": sum(1 for s in subs if s["state"] == "pending"), "submissions": subs[:20],
                "bytes": self.approved_bytes(a["id"])}

    def list_view(self):
        with self.lock:
            return [self.view(a) for a in self.areas]

    def pending_count(self):
        with self.lock:
            return sum(1 for a in self.areas for s in self.submissions(a["id"], limit=200) if s["state"] == "pending")

    def create(self, name, slug=None, trust="review", nav=True, limits=None, blocks=None):
        with self.lock:
            name = sc.clean_line(name, 40)
            if not name:
                raise ValidationError("Give the area a name")
            try:
                slug = sc.validate_slug(sc.slugify(name) if slug in (None, "") else slug)
            except sc.FieldError as e:
                raise ValidationError(str(e))
            if not slug:
                raise ValidationError("An area needs its own address (it cannot be the home page)")
            if self.get(slug) or self.slug_taken(slug) or any(a["slug"] == slug for a in self.areas):
                raise ValidationError("The address '/%s' is already used" % slug)
            if trust not in TRUST_LEVELS:
                raise ValidationError("Trust must be one of: %s" % ", ".join(TRUST_LEVELS))
            chosen = [b for b in (blocks or DEFAULT_BLOCKS) if b in ALLOWABLE_BLOCKS]
            key = new_passphrase()
            area = {"id": slug, "name": name, "slug": slug, "enabled": True, "paused": False, "trust": trust, "nav": bool(nav),
                    "limits": _clamp_limits(limits), "blocks": chosen, "created": int(self.clock()), "current": None,
                    "key": {"hash": hash_key(key), "created": int(self.clock()), "last_used": None, "hint": key[-5:].replace("-", "")[-4:]}}
            self.areas.append(area)
            self._save()
            self.dir(slug).mkdir(parents=True, exist_ok=True)
            self.audit(slug, "owner", "created", 0, "")
            return self.view(area), key

    def update(self, aid, **kw):
        with self.lock:
            a = self._must(aid)
            if "name" in kw:
                a["name"] = sc.clean_line(kw["name"], 40) or a["name"]
            for k in ("enabled", "paused", "nav"):
                if k in kw:
                    a[k] = bool(kw[k])
            if "trust" in kw:
                if kw["trust"] not in TRUST_LEVELS:
                    raise ValidationError("Trust must be one of: %s" % ", ".join(TRUST_LEVELS))
                a["trust"] = kw["trust"]
            if "limits" in kw and kw["limits"] is not None:
                a["limits"] = _clamp_limits(dict(a["limits"], **kw["limits"]))
            if "blocks" in kw and kw["blocks"] is not None:
                a["blocks"] = [b for b in kw["blocks"] if b in ALLOWABLE_BLOCKS]
            if a.get("auto") and not (a["trust"] == "auto" and a["enabled"] and not a["paused"]):
                a.pop("auto", None)                     # switched away from auto (or paused/off): nothing publishes by itself
                self.audit(aid, "owner", "auto-publish cancelled", 0, "the area is no longer on auto")
            self._save()
            self.audit(aid, "owner", "updated", 0, ",".join(sorted(k for k in kw)))
            return self.view(a)

    def regenerate_key(self, aid):
        """A new passphrase (shown once). The old one stops working immediately."""
        with self.lock:
            a = self._must(aid)
            key = new_passphrase()
            a["key"] = {"hash": hash_key(key), "created": int(self.clock()), "last_used": None, "hint": key.replace("-", "")[-4:]}
            self._save()
            self.audit(aid, "owner", "key-regenerated", 0, "")
            return key

    def revoke_key(self, aid):
        with self.lock:
            a = self._must(aid)
            a["key"] = None
            self._save()
            self.audit(aid, "owner", "key-revoked", 0, "")

    def delete(self, aid):
        with self.lock:
            a = self._must(aid)
            self.areas.remove(a)
            self._save()
            shutil.rmtree(self.dir(aid), ignore_errors=True)

    # ---------------------------------------------------------------- the sender: authentication
    def authenticate(self, aid, presented, ip):
        """The area when the key is right; None for anything else (the same answer whether or not the area exists).

        Raises Locked after too many wrong keys from one address. Work done is the same either way (a hash is always checked).
        """
        h, now = self.ip_hash(ip), self.clock()
        with self.lock:
            f = self.fails.get(h)
            if f and f[1] > now:
                raise Locked()
            a = self.get(str(aid)[:64])
            ok = False
            if a and a["enabled"] and a.get("key"):
                ok = verify_key(presented, a["key"]["hash"])
            else:
                verify_key(presented, _DUMMY)
            if not ok:
                f = self.fails.setdefault(h, [0, 0])
                f[0] += 1
                if f[0] >= LOCK_AFTER:
                    f[0], f[1] = 0, now + LOCK_SECONDS
                self.audit(str(aid)[:64] if a else "?", h, "auth-failed", 0, "")
                return None
            self.fails.pop(h, None)
            a["key"]["last_used"] = int(now)
            self._save()
            return a

    def allow_upload(self, aid, ip):
        """Per-address upload rate limit (a token window)."""
        key, now = (aid, self.ip_hash(ip)), self.clock()
        with self.lock:
            times = [t for t in self.recent.get(key, []) if now - t < UPLOAD_WINDOW]
            if len(times) >= UPLOADS_PER_WINDOW:
                self.recent[key] = times
                return False
            times.append(now)
            self.recent[key] = times
            return True

    def free_bytes_ok(self):
        try:
            return shutil.disk_usage(str(self.store.home)).free >= self.min_free_bytes
        except OSError:
            return False

    # ---------------------------------------------------------------- submissions
    def _sub_dir(self, aid, sid):
        if not isinstance(sid, str) or not _SAFE_SID.match(sid):
            raise ValidationError("That submission does not exist.")
        return self.dir(aid) / "submissions" / sid

    def _read_meta(self, aid, sid):
        try:
            return json.loads((self._sub_dir(aid, sid) / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_meta(self, aid, sid, meta):
        d = self._sub_dir(aid, sid)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        os.replace(tmp, d / "meta.json")

    def submissions(self, aid, limit=50):
        base = self.dir(aid) / "submissions"
        out, now = [], self.clock()
        if not base.is_dir():
            return out
        for d in sorted(base.iterdir(), reverse=True):
            meta = self._read_meta(aid, d.name)
            if not meta:
                continue
            if meta["state"] in ("rejected", "invalid") and now - meta.get("decided", meta["received"]) > REJECT_KEEP_SECONDS:
                shutil.rmtree(d, ignore_errors=True)
                continue
            out.append(meta)
            if len(out) >= limit:
                break
        return out

    def approved_bytes(self, aid):
        cur = self.current_tree(aid)
        if not cur:
            return 0
        return sum(f.stat().st_size for f in (cur / "files").rglob("*") if f.is_file()) if (cur / "files").is_dir() else 0

    def ingest(self, aid, zip_path, ip_hash="owner"):
        """Validate a received zip and record it as a submission. Returns the submission's meta (never raises for a bad bundle)."""
        with self.lock:
            a = self._must(aid)
            sha = _sha256(zip_path)
            for s in self.submissions(aid, limit=100):
                if s.get("sha256") == sha and s["state"] in ("pending", "approved"):
                    return dict(s, duplicate=True)
            self._seq = getattr(self, "_seq", 0) + 1
            sid = "s%011x%04x%s" % (int(self.clock() * 1000), self._seq % 65536, secrets.token_hex(1))      # sorts in the order received
            meta = {"id": sid, "area": aid, "received": int(self.clock()), "size": os.path.getsize(zip_path), "sha256": sha,
                    "state": "pending", "note": "", "warnings": [], "reasons": [], "files": 0, "pages": 0, "blocks": 0}
            sub = self._sub_dir(aid, sid)
            try:
                res = validate_bundle(zip_path, sub / "tree", a, self.registry, existing_bytes=self.approved_bytes(aid))
            except BundleError as e:
                meta.update(state="invalid", reasons=e.reasons[:8], decided=int(self.clock()))
                self._write_meta(aid, sid, meta)
                self.audit(aid, ip_hash, "rejected-invalid", meta["size"], e.reasons[0] if e.reasons else "")
                return meta
            meta.update(warnings=res["warnings"][:20], files=len(res["files"]), pages=len(res["doc"]["pages"]),
                        blocks=sum(len(p["blocks"]) for p in res["doc"]["pages"]),
                        title=res["doc"]["title"], file_list=[{"path": k, **v} for k, v in sorted(res["files"].items())][:300])
            meta["changes"] = self._changes(aid, res)
            self._write_meta(aid, sid, meta)
            self.audit(aid, ip_hash, "received", meta["size"], "%d files" % meta["files"])
            if a["trust"] in ("trusted", "auto") and not a["paused"]:
                return self.approve(aid, sid, note="approved automatically (%s area)" % a["trust"], by="auto")
            return meta

    def _changes(self, aid, res):
        """A short diff against what is live now: pages and files added, changed, removed."""
        cur = self.current_tree(aid)
        old_files, old_pages = {}, set()
        if cur:
            try:
                old_doc = json.loads((cur / "area.json").read_text(encoding="utf-8"))
                old_pages = {p["name"] for p in old_doc["pages"]}
                for f in (cur / "files").rglob("*"):
                    if f.is_file():
                        old_files["files/" + f.relative_to(cur / "files").as_posix()] = _sha256(f)
            except (OSError, ValueError, KeyError):
                pass
        new_files = {k: v["sha256"] for k, v in res["files"].items()}
        new_pages = {p["name"] for p in res["doc"]["pages"]}
        return {"files_added": sorted(set(new_files) - set(old_files))[:50], "files_removed": sorted(set(old_files) - set(new_files))[:50],
                "files_changed": sorted(k for k in new_files if k in old_files and old_files[k] != new_files[k])[:50],
                "pages_added": sorted(new_pages - old_pages), "pages_removed": sorted(old_pages - new_pages), "first": cur is None}

    # ---------------------------------------------------------------- review
    def _next_version(self, aid):
        vs = self.versions(aid)
        return "v%04d" % (int(vs[-1][1:]) + 1 if vs else 1)

    def versions(self, aid):
        base = self.dir(aid) / "approved"
        return sorted(d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("v")) if base.is_dir() else []

    def approve(self, aid, sid, note="", by="owner"):
        with self.lock:
            a = self._must(aid)
            meta = self._read_meta(aid, sid)
            if not meta or meta["state"] != "pending":
                raise ValidationError("That submission is not waiting for review")
            tree = self._sub_dir(aid, sid) / "tree"
            if not (tree / "area.json").is_file():
                raise ValidationError("That submission has no content to approve")
            ver = self._next_version(aid)
            dest = self.dir(aid) / "approved" / ver
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(tree, str(dest) + ".part")
            os.replace(str(dest) + ".part", dest)
            prev = a["current"]                                 # what an auto-publish would have to roll back to
            a["current"] = ver
            self._save()
            meta.update(state="approved", note=sc.clean_line(note, 300), decided=int(self.clock()), version=ver, decided_by=by)
            self._write_meta(aid, sid, meta)
            self.audit(aid, by, "approved", meta["size"], ver)
            if a["trust"] == "auto":
                self._notify_auto(aid, prev)
            return meta

    def _notify_auto(self, aid, prev):
        """An auto area just gained content: hand it to the auto-publisher (set by the hub).

        A failure here must never undo an approval that already happened, so it is logged instead.
        """
        cb = self.on_auto_publish
        if cb is None:
            return
        try:
            cb(aid, prev)
        except Exception as e:
            self.store.log("warn", "Could not line up the automatic publish: %s" % e)

    def reject(self, aid, sid, note=""):
        with self.lock:
            self._must(aid)
            meta = self._read_meta(aid, sid)
            if not meta or meta["state"] != "pending":
                raise ValidationError("That submission is not waiting for review")
            meta.update(state="rejected", note=sc.clean_line(note, 300), decided=int(self.clock()), decided_by="owner")
            self._write_meta(aid, sid, meta)
            shutil.rmtree(self._sub_dir(aid, sid) / "tree", ignore_errors=True)
            self.audit(aid, "owner", "rejected", meta["size"], meta["note"])
            return meta

    def rollback(self, aid, version):
        with self.lock:
            a = self._must(aid)
            if version not in self.versions(aid):
                raise ValidationError("There is no version '%s'" % version)
            a["current"] = version
            self._save()
            self.audit(aid, "owner", "rolled-back", 0, version)

    def freeze(self, aid, by="owner", reason=""):
        """Stop this area publishing by itself, at once: back to review, any waiting publish cancelled.

        One click, and always in the safe direction: freezing can never put content on the site.
        """
        with self.lock:
            a = self._must(aid)
            waiting = bool(a.get("auto"))
            a["trust"] = "review"
            a.pop("auto", None)
            self._save()
            self.audit(aid, by, "frozen", 0, reason or ("auto-publish cancelled" if waiting else ""))
            return self.view(a)

    def auto_rollback(self, aid, version, why):
        """After an automatic publish whose live check failed: put back what was live before and freeze the area."""
        with self.lock:
            a = self._must(aid)
            a["current"] = version
            a["trust"] = "review"
            a.pop("auto", None)
            self._save()
            self.audit(aid, "auto", "auto-rollback", 0, "%s; back to %s" % (why, version or "nothing live"))
            return self.view(a)

    # ---------------------------------------------------------------- what the builder uses
    def current_tree(self, aid):
        a = self.get(aid)
        if not a or not a.get("current"):
            return None
        p = self.dir(aid) / "approved" / a["current"]
        return p if (p / "area.json").is_file() else None

    def trees(self, preview_submission=None):
        """[{id, slug, name, nav, tree}] for every enabled area with live content. preview_submission = (area id, sid) swaps in a
        pending submission so the owner can see it before approving."""
        out = []
        with self.lock:
            for a in self.areas:
                if not a["enabled"]:
                    continue
                tree = self.current_tree(a["id"])
                if preview_submission and preview_submission[0] == a["id"]:
                    cand = self._sub_dir(a["id"], preview_submission[1]) / "tree"
                    if (cand / "area.json").is_file():
                        tree = cand
                if tree:
                    out.append({"id": a["id"], "slug": a["slug"], "name": a["name"], "nav": a["nav"], "tree": tree,
                                "blocks": a["blocks"]})
        return out

    # ---------------------------------------------------------------- the sender's view
    def sender_status(self, aid):
        return [{"id": s["id"], "received": s["received"], "state": s["state"], "note": s.get("note", ""),
                 "reasons": s.get("reasons", []), "warnings": s.get("warnings", []), "version": s.get("version")}
                for s in self.submissions(aid, limit=20)]

    def sender_limits(self, a):
        return {"limits": a["limits"], "blocks": a["blocks"], "paused": a["paused"], "trust": a["trust"], "slug": a["slug"],
                "format": 1}

    # ---------------------------------------------------------------- audit
    def audit(self, aid, who, what, size, detail):
        try:
            d = self.dir(aid) if aid != "?" else self.root / "_unknown"
            d.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"t": int(self.clock()), "who": who, "what": what, "size": size, "detail": str(detail)[:200]})
            with open(d / "audit.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")
            if os.path.getsize(d / "audit.jsonl") > 512 * 1024:                # keep it small: drop the oldest half
                lines = (d / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                (d / "audit.jsonl").write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
        except OSError:
            pass

    def audit_log(self, aid, limit=100):
        try:
            lines = (self.dir(aid) / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
        return list(reversed(out))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
