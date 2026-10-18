"""Linked sites: other people's (or your own) sites on Cloudflare or anywhere, shown from this site.

A linked site is a registry entry (label, https address, description). It can appear as a card in a "Linked sites"
block, as a menu link, and/or on its own page that either links out or embeds it. Halbach never hosts it or holds
any key for it. This module holds the pure helpers: address checks, secret-in-address detection, and a polite probe.
Stdlib only.
"""

import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request

# Query parameters that usually carry a key. Anything linked on a public site is public, so a key in the address is
# a leak waiting to happen.
SECRET_PARAMS = {"k", "key", "token", "secret", "auth", "sig", "signature", "password", "pass", "pwd", "code",
                 "apikey", "api_key", "access_token", "session", "sid", "otp"}
CLOUDFLARE_SUFFIXES = (".workers.dev", ".pages.dev")
PROBE_TIMEOUT = 8
PROBE_READ = 2048


def secret_params(url):
    """Names (never values) of query/fragment parameters in url that look like keys."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return []
    names = []
    for chunk in (parts.query, parts.fragment):
        for pair in chunk.replace(";", "&").split("&"):
            name = urllib.parse.unquote(pair.split("=", 1)[0]).strip().lower()
            if name in SECRET_PARAMS and name not in names and ("=" in pair):
                names.append(name)
    if parts.username or parts.password:
        names.append("user:password in the address")
    return names


def host_of(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def looks_cloudflare(url):
    h = host_of(url)
    return any(h.endswith(s) for s in CLOUDFLARE_SUFFIXES)


def is_private_host(host):
    """True when host is (or resolves to) this machine or a private network address: never probed."""
    if not host or host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False                                  # cannot resolve: the probe will simply fail
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def probe(url, opener=None):
    """Ask the site how it answers (one GET, nothing stored). Returns a plain-language dict and never raises."""
    out = {"reachable": False, "status": None, "cloudflare": looks_cloudflare(url), "embeddable": None, "notes": []}
    host = host_of(url)
    if not url.lower().startswith("https://"):
        out["notes"].append("Only https addresses can be linked.")
        return out
    if is_private_host(host):
        out["notes"].append("That address is on this PC or a private network, so visitors could not open it.")
        return out
    req = urllib.request.Request(url, headers={"User-Agent": "HalbachConnectHub-linkcheck", "Accept": "text/html,*/*"})
    opener = opener or urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req, timeout=PROBE_TIMEOUT)
        status, headers = r.status, r.headers
        r.read(PROBE_READ)
        r.close()
    except urllib.error.HTTPError as e:
        status, headers = e.code, e.headers
        if 300 <= status < 400:
            out["notes"].append("It redirects to %s." % (urllib.parse.urlsplit(headers.get("Location", "")).netloc or "another address"))
            out["reachable"], out["status"] = True, status
        elif status in (401, 403):
            out["notes"].append("It answered, but asks for a login or key (status %d). Visitors without it cannot use it." % status)
            out["reachable"], out["status"] = True, status
            return _finish(out, headers)
        else:
            out["status"] = status
            out["notes"].append("It answered with an error (status %d)." % status)
            return _finish(out, headers)
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["notes"].append("Could not reach it (%s)." % (getattr(e, "reason", None) or e.__class__.__name__))
        return out
    else:
        out["reachable"], out["status"] = True, status
    return _finish(out, headers)


def _finish(out, headers):
    h = {k.lower(): v for k, v in (headers.items() if headers else [])}
    if "cf-ray" in h or "cloudflare" in h.get("server", "").lower():
        out["cloudflare"] = True
    xfo = h.get("x-frame-options", "").strip().lower()
    csp = h.get("content-security-policy", "")
    ancestors = [d for d in csp.split(";") if d.strip().lower().startswith("frame-ancestors")]
    if xfo in ("deny", "sameorigin") or ancestors:
        out["embeddable"] = False
        out["notes"].append("It tells browsers not to show it inside other sites, so it can be linked but not embedded.")
    else:
        out["embeddable"] = True
    return out
