"""The drop endpoint: the only public door a collaborator's content comes through. Served by the live hub.

    GET  /drop/<area>/limits      the area's rules (needs the key)
    GET  /drop/<area>/status      the sender's recent submissions and how they were decided (needs the key)
    POST /drop/<area>/bundle      upload a zip (needs the key)

Authentication happens before any body is read. The key travels in an Authorization header, never in a URL or a
cookie. A wrong key, an unknown area and a switched-off area all answer the same 401. Stdlib only.
"""

import json
import os
import re
import secrets
import socket
import time

from .areas import Locked

ROUTE = re.compile(r"^/drop/([a-z0-9][a-z0-9-]{0,62})/(limits|status|bundle)/?$")
ACCEPTED_TYPES = ("application/zip", "application/x-zip-compressed", "application/octet-stream")
READ_TIMEOUT = 20            # seconds without a byte
UPLOAD_DEADLINE = 600        # seconds for the whole upload
CHUNK = 64 * 1024
GENERIC = {401: "Not authorised", 404: "Not found", 405: "Method not allowed", 411: "Content-Length is required", 413: "Too large",
           415: "Send the bundle as application/zip", 423: "This area is paused", 429: "Too many attempts, try again later",
           408: "The upload was too slow", 503: "The hub cannot take uploads right now", 400: "Bad request"}


def _reply(h, code, obj=None, extra=None):
    body = json.dumps(obj if obj is not None else {"error": GENERIC.get(code, "Error")}).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    if code in (401, 413, 415, 408, 429, 411):
        h.send_header("Connection", "close")                          # the body (if any) was not read
        h.close_connection = True
    for k, v in (extra or {}).items():
        h.send_header(k, v)
    try:
        h.end_headers()
        h.wfile.write(body)
    except (ConnectionError, OSError):                                   # the sender already hung up; nothing more to say
        h.close_connection = True


def client_ip(h):
    # The tunnel puts the real address in CF-Connecting-IP; direct local connections fall back to the socket address.
    return (h.headers.get("CF-Connecting-IP") or h.client_address[0] or "?").split(",")[0].strip()[:64]


def handle(h, areas, method):
    """Handle one /drop/ request on handler h. Returns True when the request was a drop route (and has been answered)."""
    path = h.path.split("?", 1)[0]
    m = ROUTE.match(path)
    if not m:
        _reply(h, 404)
        return True
    aid, action = m.group(1), m.group(2)
    if (action == "bundle") != (method == "POST"):
        _reply(h, 405)
        return True
    ip = client_ip(h)
    auth = h.headers.get("Authorization") or ""
    key = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    try:
        area = areas.authenticate(aid, key, ip) if key else None
        if not key:
            areas.authenticate(aid, "", ip)                         # same cost and bookkeeping as a wrong key
    except Locked:
        _reply(h, 429)
        return True
    if not area:
        _reply(h, 401)
        return True
    if action == "limits":
        _reply(h, 200, areas.sender_limits(area))
        return True
    if action == "status":
        _reply(h, 200, {"submissions": areas.sender_status(aid), "paused": area["paused"]})
        return True
    return _receive(h, areas, area, ip)


def _delete(path):
    """Remove a file, never raising. The temp upload is removed before replying so the sender can never see a
    quarantine that still holds its bytes."""
    try:
        os.remove(path)
    except OSError:
        pass


def _receive(h, areas, area, ip):
    aid = area["id"]
    if area["paused"]:
        _reply(h, 423)
        return True
    limits = area["limits"]
    cap = int(limits["max_bundle_mb"]) * 1024 * 1024
    raw_len = h.headers.get("Content-Length")
    if raw_len is None or not raw_len.isdigit():
        _reply(h, 411)
        return True
    length = int(raw_len)
    if length > cap:
        _reply(h, 413)
        return True
    if length < 22:                                                  # smaller than an empty zip
        _reply(h, 400)
        return True
    ctype = (h.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in ACCEPTED_TYPES:
        _reply(h, 415)
        return True
    if not areas.free_bytes_ok():
        _reply(h, 503)
        return True
    if not areas.allow_upload(aid, ip):
        _reply(h, 429)
        return True
    if not areas.upload_slots.acquire(blocking=False):
        _reply(h, 503, {"error": "The hub is busy with another upload. Try again in a minute."}, {"Retry-After": "60"})
        return True
    qdir = areas.dir(aid) / "quarantine"
    tmp = qdir / (secrets.token_hex(8) + ".zip")
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        try:
            h.connection.settimeout(READ_TIMEOUT)
        except OSError:
            pass
        got, started = 0, time.time()
        try:
            with open(tmp, "wb") as out:
                while got < length:
                    if time.time() - started > UPLOAD_DEADLINE:
                        raise socket.timeout()
                    chunk = h.rfile.read(min(CHUNK, length - got))
                    if not chunk:
                        break
                    got += len(chunk)
                    out.write(chunk)
        except (socket.timeout, TimeoutError):
            areas.audit(aid, areas.ip_hash(ip), "upload-timeout", got, "")
            _delete(tmp)
            _reply(h, 408)
            return True
        except OSError:
            _delete(tmp)
            _reply(h, 503)
            return True
        if got != length:
            areas.audit(aid, areas.ip_hash(ip), "upload-incomplete", got, "")
            _delete(tmp)
            _reply(h, 400)
            return True
        meta = areas.ingest(aid, tmp, areas.ip_hash(ip))
        _delete(tmp)                                                # before replying: the reply carries the verdict, not the bytes
        if meta["state"] == "invalid":
            _reply(h, 422, {"id": meta["id"], "state": "invalid", "error": "The bundle was refused.", "reasons": meta["reasons"]})
        else:
            _reply(h, 201 if not meta.get("duplicate") else 200,
                   {"id": meta["id"], "state": meta["state"], "duplicate": bool(meta.get("duplicate")), "warnings": meta.get("warnings", []),
                    "version": meta.get("version")})
        return True
    except Exception:                                               # never leak internals to the sender
        areas.audit(aid, areas.ip_hash(ip), "error", 0, "")
        _delete(tmp)
        _reply(h, 503)
        return True
    finally:
        areas.upload_slots.release()
        _delete(tmp)
