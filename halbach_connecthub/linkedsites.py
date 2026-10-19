"""Linked sites: other people's (or your own) sites on Cloudflare or anywhere, shown from this site.

A linked site is a registry entry (label, https address, description). It can appear as a card in a "Linked sites"
block, as a menu link, and/or on its own page that either links out or embeds it. Halbach never hosts it or holds
any key for it. This module holds the pure helpers: address checks, secret-in-address detection, and a polite probe.
Stdlib only.
"""

import http.client
import ipaddress
import socket
import ssl
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
        if _ip_is_private(ip):
            return True
    return False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection that dials a vetted IP but keeps the original host for SNI and Host."""

    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, vetted_ip=None, **kw):
        super().__init__(host, port, timeout=timeout, **kw)
        self._vetted_ip = vetted_ip

    def connect(self):
        addr = self._vetted_ip or self.host
        self.sock = socket.create_connection((addr, self.port), self.timeout, self.source_address)
        if getattr(self, "_tunnel_host", None):
            self._tunnel()
        sni = self.server_hostname if self.server_hostname is not None else self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=sni)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, vetted_ip):
        super().__init__()
        self._vetted_ip = vetted_ip

    def https_open(self, req):
        return self.do_open(lambda h, **kw: _PinnedHTTPSConnection(h, vetted_ip=self._vetted_ip, **kw), req)


def _ip_is_private(ip):
    """Private/loopback/etc. check that also covers IPv4-mapped IPv6 (::ffff:a.b.c.d)."""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return (mapped.is_private or mapped.is_loopback or mapped.is_link_local
                or mapped.is_reserved or mapped.is_multicast)
    return False


def _name_is_private(host):
    return (not host) or host in ("localhost",) or host.endswith((".local", ".internal", ".localhost"))


def probe(url, opener=None, resolver=None):
    """Ask the site how it answers (one GET, nothing stored). Returns a plain-language dict and never raises.

    The host is resolved to an IP exactly once and that IP is validated and then pinned for the connection, so a
    DNS rebinding race cannot swap a public address for a private one between the check and the connect.
    """
    out = {"reachable": False, "status": None, "cloudflare": looks_cloudflare(url), "embeddable": None, "notes": []}
    host = host_of(url)
    if not url.lower().startswith("https://"):
        out["notes"].append("Only https addresses can be linked.")
        return out
    if _name_is_private(host):
        out["notes"].append("That address is on this PC or a private network, so visitors could not open it.")
        return out

    # Resolve once, validate every address, pin the first good one.
    vetted_ip = None
    resolving = (resolver is not None) or (opener is None)
    if resolving:
        resolve_fn = resolver if resolver is not None else socket.getaddrinfo
        try:
            infos = resolve_fn(host, None) if resolver is None else resolve_fn(host)
        except Exception:
            infos = []
        ips = []
        private_found = False
        for info in (infos or []):
            ipstr = None
            if isinstance(info, str):
                ipstr = info
            elif isinstance(info, (tuple, list)):
                cand = info[-1] if info else None
                if isinstance(cand, (tuple, list)) and cand:
                    ipstr = cand[0]
                elif isinstance(cand, str):
                    ipstr = cand
            if ipstr is None:
                continue
            ipstr = str(ipstr).split("%", 1)[0]
            try:
                ip = ipaddress.ip_address(ipstr)
            except ValueError:
                continue
            if _ip_is_private(ip):
                private_found = True
                break
            if str(ip) not in ips:
                ips.append(str(ip))
        if private_found:
            out["notes"].append("That address resolves to a private network, so it was not probed.")
            return out
        if ips:
            vetted_ip = ips[0]
        elif resolver is not None:
            out["notes"].append("Could not resolve %s." % host)
            return out
        # real path, unresolvable: fall through and let the connection attempt fail (matches prior behaviour)
    else:
        # opener supplied by caller (e.g. tests): legacy check, no pinning available
        if is_private_host(host):
            out["notes"].append("That address is on this PC or a private network, so visitors could not open it.")
            return out

    if vetted_ip is not None and opener is None:
        opener = urllib.request.build_opener(_PinnedHTTPSHandler(vetted_ip), _NoRedirect)

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
