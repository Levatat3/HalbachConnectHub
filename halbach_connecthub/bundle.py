"""Validate and unpack an area bundle (a zip a collaborator sends). Everything here assumes the zip is hostile.

A bundle holds exactly:
    area.json          {"format": 1, "title", "description", "pages": [{"name", "title", "blocks": [...]}]}
                       (or a single page as "blocks": [...] at the top level)
    files/<path>       pictures and documents the blocks refer to as "files/<path>"

Nothing is ever extracted with extractall: each entry is checked, then streamed with a hard cap into a temporary
folder that is only renamed into place when the whole bundle passed. Stdlib only.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import unicodedata
import zipfile
import zlib

from . import csscheck
from . import siteschema as sc

FORMAT = 1
MAX_NAME_LEN = 120
MAX_SEGMENT_LEN = 80
MAX_DEPTH = 8
MAX_RATIO = 20
RATIO_FLOOR = 64 * 1024           # tiny files may compress well; the ratio rule only matters above this size
MAX_AREA_JSON = 256 * 1024
MAX_PAGES = 20
MAX_BLOCKS = 100
MAX_PIXELS = 40_000_000
MAX_SIDE_DEFAULT = 8000
CHUNK = 64 * 1024

DEFAULT_LIMITS = {"max_file_mb": 50, "max_bundle_mb": 80, "max_total_mb": 500, "max_files": 500, "max_image_px": MAX_SIDE_DEFAULT,
                  "extensions": ["md", "txt", "png", "jpg", "jpeg", "gif", "webp", "pdf"]}
DEFAULT_BLOCKS = ["hero", "text", "cards", "links", "image", "divider", "youtube", "twitch"]
# Block types an owner may ever allow for an area: nothing that takes a module, an outside page, or runs code.
ALLOWABLE_BLOCKS = {"hero", "text", "cards", "links", "image", "divider", "youtube", "twitch", "embed"}   # embed is never on by default: it also needs the owner's host list
KNOWN_EXT = {"md", "txt", "png", "jpg", "jpeg", "gif", "webp", "pdf", "css"}   # the only types this version can validate (css only when the owner allows it)
DANGEROUS_PARTS = {"html", "htm", "xhtml", "js", "mjs", "svg", "xml", "swf", "exe", "dll", "bat", "cmd", "com", "ps1", "vbs", "vbe",
                   "lnk", "scr", "msi", "jar", "php", "py", "sh", "pif", "hta", "cpl", "reg", "wsf", "zip", "iso", "dmg", "app", "apk"}
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {"com%d" % i for i in range(1, 10)} | {"lpt%d" % i for i in range(1, 10)}
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_()+,@~\-][A-Za-z0-9 _.()+,@~\-]*$")
BAD_UNICODE = re.compile("[​-‏‪-‮⁦-⁩﻿\u0000-\u001f\u007f]")
TOP_KEYS = {"format", "title", "description", "pages", "blocks", "design"}
FILE_REF = re.compile(r"files/[A-Za-z0-9_()+,@~/. \-]+")


class BundleError(Exception):
    """The bundle was refused. .reasons holds plain sentences the sender can read (never server paths)."""

    def __init__(self, reasons):
        reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(reasons[:3]))
        self.reasons = reasons


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- names
def check_name(name):
    """Return None if an archive entry name is safe, else a short reason."""
    if not name or len(name) > MAX_NAME_LEN:
        return "a file name is empty or longer than %d characters" % MAX_NAME_LEN
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return "'%s' is not a plain relative path" % _show(name)
    if BAD_UNICODE.search(name) or unicodedata.normalize("NFC", name) != name:
        return "'%s' contains hidden or control characters" % _show(name)
    segs = name.split("/")
    if len(segs) > MAX_DEPTH:
        return "'%s' is nested more than %d folders deep" % (_show(name), MAX_DEPTH)
    for seg in segs:
        if seg in ("", ".", "..") or len(seg) > MAX_SEGMENT_LEN:
            return "'%s' has an empty, relative or too long part" % _show(name)
        if not SEGMENT_RE.match(seg):
            return "'%s' uses characters that are not allowed (use letters, digits, spaces and . _ - ( ) + , @ ~)" % _show(name)
        if seg.endswith((" ", ".")):
            return "'%s' ends with a space or a dot" % _show(name)
        if seg.split(".")[0].lower() in RESERVED_NAMES:
            return "'%s' uses a reserved Windows name" % _show(name)
    return None


def _show(name):
    return "".join(c if 32 <= ord(c) < 127 else "?" for c in str(name))[:60]


def extension_problem(name, allowed):
    base = name.rsplit("/", 1)[-1].lower()
    parts = base.split(".")
    if len(parts) < 2:
        return "'%s' has no file type" % _show(name)
    ext = parts[-1]
    inner = [p for p in parts[1:-1] if p in DANGEROUS_PARTS]
    if inner:
        return "'%s' hides a program or script type inside its name" % _show(name)
    if ext in DANGEROUS_PARTS or ext not in allowed or ext not in KNOWN_EXT:
        return "'%s' is not an allowed file type (allowed: %s)" % (_show(name), ", ".join(sorted(e for e in allowed if e in KNOWN_EXT)))
    return None


# --------------------------------------------------------------------------- content sniffing
def _image_size(ext, head):
    """(width, height) from the start of an image file, or None."""
    try:
        if ext == "png":
            if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
                return None
            return struct.unpack(">II", head[16:24])
        if ext == "gif":
            if head[:6] not in (b"GIF87a", b"GIF89a"):
                return None
            return struct.unpack("<HH", head[6:10])
        if ext in ("jpg", "jpeg"):
            if head[:3] != b"\xff\xd8\xff":
                return None
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xFF, 0x00) or 0xD0 <= marker <= 0xD9 or marker == 0x01:
                    i += 1 if marker == 0xFF else 2
                    continue
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", head[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", head[i + 2:i + 4])[0]
            return None
        if ext == "webp":
            if head[:4] != b"RIFF" or head[8:12] != b"WEBP":
                return None
            kind = head[12:16]
            if kind == b"VP8 ":
                return struct.unpack("<HH", head[26:30])[0] & 0x3FFF, struct.unpack("<HH", head[26:30])[1] & 0x3FFF
            if kind == b"VP8L":
                b = struct.unpack("<I", head[21:25])[0]
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            if kind == b"VP8X":
                return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
            return None
    except (struct.error, IndexError):
        return None
    return None


# --------------------------------------------------------------------------- image sanitization
def _sanitize_jpeg(path, rel, warnings):
    """Strip camera metadata (EXIF/GPS, XMP, Photoshop IRB) from a JPEG. Raises BundleError if truncated/corrupted."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        raise BundleError("'%s' is damaged." % _show(rel))
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise BundleError("'%s' is not a valid JPG picture" % _show(rel))
    out = bytearray(b"\xff\xd8")          # SOI
    i = 2
    removed = False
    removed_exif = False
    while i + 1 < len(data):
        if data[i] != 0xFF:
            raise BundleError("'%s' is damaged." % _show(rel))
        marker = data[i + 1]
        if marker == 0xD9:                  # EOI
            out.extend(b"\xff\xd9")
            i += 2
            break
        if marker in (0xFF, 0x00) or marker == 0x01 or 0xD0 <= marker <= 0xD8:
            out.extend(data[i:i + 2])       # RSTn / TEM / fill - kept byte for byte
            i += 2
            continue
        if i + 4 > len(data):
            raise BundleError("'%s' is damaged or truncated." % _show(rel))
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seglen < 2:
            raise BundleError("'%s' is damaged." % _show(rel))
        segend = i + 2 + seglen
        if segend > len(data):
            raise BundleError("'%s' is damaged or truncated." % _show(rel))
        drop = False
        if marker == 0xE1:                  # APP1
            payload = data[i + 4:segend]
            if payload.startswith(b"Exif\x00\x00") or payload.startswith(b"http://ns.adobe.com/xap/"):
                drop = True
                removed = True
                if payload.startswith(b"Exif\x00\x00"):
                    removed_exif = True
        if marker == 0xED:                  # APP13 (Photoshop IRB)
            drop = True
            removed = True
        if not drop:
            out.extend(data[i:segend])
        i = segend
        if marker == 0xDA:                  # SOS: entropy-coded scan data follows until EOI
            j = i
            while j + 1 < len(data):
                if data[j] == 0xFF and data[j + 1] == 0xD9:
                    out.extend(data[j:j + 2])
                    i = j + 2
                    break
                out.append(data[j])
                j += 1
            else:
                raise BundleError("'%s' is damaged or truncated." % _show(rel))
            break
    else:
        raise BundleError("'%s' is damaged or truncated." % _show(rel))
    if removed:
        with open(path, "wb") as f:
            f.write(out)
        warnings.append("'%s' camera data was removed." % _show(rel))
        if removed_exif:
            warnings.append("'%s' orientation data was removed; the picture may display sideways." % _show(rel))
    return removed


PNG_TEXT_KEYS = {"location", "gps", "geotag", "latitude", "longitude", "gpslatitude", "gpslongitude", "gpsinfo"}
PNG_TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}


def _sanitize_png(path, rel, warnings):
    """Strip PNG text chunks whose key names a place (Location/GPS). Raises BundleError if truncated/corrupted."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        raise BundleError("'%s' is damaged." % _show(rel))
    if len(data) < 12 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise BundleError("'%s' is not a valid PNG picture" % _show(rel))
    out = bytearray(data[:8])               # signature
    i = 8
    removed = False
    while i + 12 <= len(data):
        length = struct.unpack(">I", data[i:i + 4])[0]
        ctype = data[i + 4:i + 8]
        cdata_start = i + 8
        cdata_end = cdata_start + length
        crc_start = cdata_end
        crc_end = crc_start + 4
        if crc_end > len(data):
            raise BundleError("'%s' is damaged or truncated." % _show(rel))
        chunk_data = data[cdata_start:cdata_end]
        if zlib.crc32(ctype + chunk_data) & 0xFFFFFFFF != struct.unpack(">I", data[crc_start:crc_end])[0]:
            raise BundleError("'%s' is damaged." % _show(rel))
        drop = False
        if ctype == b"eXIf":
            drop = True
            removed = True
        if ctype in PNG_TEXT_CHUNKS and b"\x00" in chunk_data:
            try:
                key = chunk_data.split(b"\x00", 1)[0].decode("latin-1").lower()
                # iTXt puts the keyword before the first NUL too, so the split above already gives the key;
                # drop anything that names a place or is an XMP block.
                if "xmp" in key or any(g in key for g in PNG_TEXT_KEYS):
                    drop = True
                    removed = True
            except Exception:
                drop = False
        if not drop:
            out.extend(data[i:i + 8 + length + 4])
        i += 12 + length
        if ctype == b"IEND":
            break
    else:
        raise BundleError("'%s' is damaged or truncated." % _show(rel))
    if removed:
        with open(path, "wb") as f:
            f.write(out)
        warnings.append("'%s' camera data was removed." % _show(rel))
    return removed


def check_content(path, rel, ext, limits, warnings):
    """Make sure the bytes are what the extension claims. Returns None or a reason."""
    # Strip camera metadata first. If the segment structure is malformed we leave the file
    # exactly as it was (never rewrite hostile bytes into a half-parsed shape) and let the
    # checks below refuse it with the usual message.
    if ext in ("jpg", "jpeg"):
        try:
            _sanitize_jpeg(path, rel, warnings)
        except BundleError:
            warnings.append("'%s' camera data could not be verified or removed; treat the file as untrusted." % _show(rel))
        else:
            try:
                with open(path, "rb") as fh:
                    d = fh.read()
            except OSError:
                d = b""
            if b"Exif\x00\x00" in d or b"http://ns.adobe.com/xap/" in d:
                warnings.append("'%s' may still contain camera data." % _show(rel))
    elif ext == "png":
        try:
            _sanitize_png(path, rel, warnings)
        except BundleError:
            warnings.append("'%s' camera data could not be verified or removed; treat the file as untrusted." % _show(rel))
        else:
            try:
                with open(path, "rb") as fh:
                    d = fh.read()
            except OSError:
                d = b""
            if b"eXIf" in d:
                warnings.append("'%s' may still contain camera data." % _show(rel))
    elif ext in ("webp", "gif"):
        warnings.append("'%s' camera data was not checked (this format is not supported by the stripper)." % _show(rel))
    with open(path, "rb") as f:
        head = f.read(4 * 1024 * 1024 if ext in ("jpg", "jpeg") else 64 * 1024)
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        size = _image_size(ext, head)
        if not size:
            return "'%s' is not a valid %s picture" % (_show(rel), ext.upper())
        w, h = size
        side = int(limits.get("max_image_px", MAX_SIDE_DEFAULT))
        if w < 1 or h < 1 or w > side or h > side or w * h > MAX_PIXELS:
            return "'%s' is %dx%d pixels; pictures may be at most %d on a side" % (_show(rel), w, h, side)
        return None
    if ext == "pdf":
        return None if head[:5] == b"%PDF-" else "'%s' is not a PDF" % _show(rel)
    if ext == "css":
        try:
            with open(path, "rb") as f:
                data = f.read(csscheck.MAX_CSS_BYTES + 1)
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "'%s' is not UTF-8 text" % _show(rel)
        return None if len(data) <= csscheck.MAX_CSS_BYTES else "'%s' is larger than %d KB" % (_show(rel), csscheck.MAX_CSS_BYTES // 1024)
    if ext in ("md", "txt"):
        if b"\x00" in head:
            return "'%s' is not a text file" % _show(rel)
        try:
            with open(path, "rb") as f:
                f.read().decode("utf-8")
        except UnicodeDecodeError:
            return "'%s' is not UTF-8 text" % _show(rel)
        return None
    return "'%s' is not an allowed file type" % _show(rel)


# --------------------------------------------------------------------------- archive
def inspect_archive(zf, limits):
    """Check every entry's metadata before any byte is read. Returns [(ZipInfo, name)] for the regular files."""
    infos = zf.infolist()
    max_files = int(limits.get("max_files", 500))
    if len(infos) > max_files + 50:
        raise BundleError("The bundle has too many entries (the limit is %d files)." % max_files)
    seen, out, total, packed = set(), [], 0, 0
    allowed = {e.lower() for e in limits.get("extensions", DEFAULT_LIMITS["extensions"])}
    for info in infos:
        name = info.filename
        if name.endswith("/"):
            bad = check_name(name.rstrip("/")) if name.rstrip("/") else "an empty folder name"
            if bad:
                raise BundleError(bad)
            continue
        bad = check_name(name)
        if bad:
            raise BundleError(bad)
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):             # 0 = the tool recorded no file type at all
            raise BundleError("'%s' is a link or special file, which is not allowed." % _show(name))
        if info.flag_bits & 0x1:
            raise BundleError("The bundle is password protected, which is not allowed.")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BundleError("'%s' uses an unsupported compression method." % _show(name))
        key = name.casefold()
        if key in seen:
            raise BundleError("Two files have the same name once capitals are ignored: '%s'." % _show(name))
        seen.add(key)
        if name != "area.json":
            if not name.startswith("files/"):
                raise BundleError("'%s' is not allowed. A bundle holds only area.json and a files/ folder." % _show(name))
            if name.lower().endswith(".css") and "css" not in allowed:
                raise BundleError("'%s' is a stylesheet, which the owner has not allowed this area to send." % _show(name))
            bad = extension_problem(name, allowed)
            if bad:
                raise BundleError(bad)
        if info.file_size > int(limits.get("max_file_mb", 50)) * 1024 * 1024:
            raise BundleError("'%s' is larger than %d MB." % (_show(name), int(limits.get("max_file_mb", 50))))
        if info.file_size > RATIO_FLOOR and info.compress_size > 0 and info.file_size / info.compress_size > MAX_RATIO:
            raise BundleError("'%s' is suspiciously compressible (a zip bomb check)." % _show(name))
        total += info.file_size
        packed += info.compress_size
        out.append((info, name))
    if sum(1 for _, n in out if n != "area.json") > max_files:
        raise BundleError("The bundle has too many files (the limit is %d)." % max_files)
    if total > int(limits.get("max_bundle_mb", 80)) * 1024 * 1024:
        raise BundleError("The bundle is larger than %d MB once unpacked." % int(limits.get("max_bundle_mb", 80)))
    if total > RATIO_FLOOR and packed > 0 and total / packed > MAX_RATIO:
        raise BundleError("The bundle is suspiciously compressible (a zip bomb check).")
    if not any(n == "area.json" for _, n in out):
        raise BundleError("The bundle has no area.json.")
    return out


def stream_entry(zf, info, dest, hard_cap):
    """Copy one entry to dest, never writing more than the header promised (and never more than hard_cap)."""
    written = 0
    with zf.open(info) as src, open(dest, "wb") as out:
        while True:
            chunk = src.read(CHUNK)
            if not chunk:
                break
            written += len(chunk)
            if written > info.file_size or written > hard_cap:
                raise BundleError("'%s' is bigger than its own header says (refused)." % _show(info.filename))
            out.write(chunk)
    if written != info.file_size:
        raise BundleError("'%s' is damaged." % _show(info.filename))
    return written


# --------------------------------------------------------------------------- the area document
def _rewrite_refs(value, slug, files, errors, where):
    """Turn "files/x.png" into "/<slug>/files/x.png" everywhere in a block's props; refuse paths that leave the area."""
    if isinstance(value, str):
        if value.startswith("files/"):
            if value not in files:
                errors.append("%s refers to %s, which is not in the bundle" % (where, _show(value)))
                return value
            return "/%s/%s" % (slug, value)
        if FILE_REF.search(value) and "](files/" in value:                                  # markdown links and images
            def repl(m):
                ref = m.group(1)
                if ref not in files:
                    errors.append("%s refers to %s, which is not in the bundle" % (where, _show(ref)))
                    return m.group(0)
                return m.group(0)
            return re.sub(r"\]\((files/[^)\s]+)\)", repl, value)
        return value
    if isinstance(value, list):
        return [_rewrite_refs(v, slug, files, errors, where) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_refs(v, slug, files, errors, where) for k, v in value.items()}
    return value


_MD_TARGET = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)")


def _check_address(value, slug, errors, where):
    """One address an area wrote: https or mailto, or a path inside the area. Anything else is refused with a reason."""
    if not isinstance(value, str):
        return
    v = value.strip()
    if re.match(r"^[a-z][a-z0-9+.\-]*:", v, re.I) and not re.match(r"^(https://|mailto:)", v, re.I):
        errors.append("%s has an address that is not https or mailto (%s)" % (where, _show(v[:30])))
    elif v.startswith("//"):
        errors.append("%s has a protocol-relative address" % where)
    elif v.startswith("/") and not (v == "/%s" % slug or v.startswith("/%s/" % slug)):
        errors.append("%s links outside the area (%s)" % (where, _show(v[:40])))
    elif v.startswith("/") and ".." in v.split("/"):
        errors.append("%s has a relative path that leaves the area" % where)


def _check_props_addresses(clean, fields, slug, errors, where):
    """Apply the address rules ONLY to fields that are addresses (as the block's manifest declares them).

    Ordinary text such as "Note: hello" or "/help is a command" is not an address and is left alone. Links inside
    Markdown text are addresses too, so each `[label](target)` target is checked.
    """
    for f in fields:
        v = clean.get(f.get("key"))
        t = f.get("type")
        if t in ("url", "image"):
            _check_address(v, slug, errors, where)
        elif t in ("links", "cards"):
            for item in v or []:
                if isinstance(item, dict):
                    _check_address(item.get("url"), slug, errors, where)
        elif t == "markdown" and isinstance(v, str):
            for target in _MD_TARGET.findall(v):
                _check_address(target, slug, errors, where)


def _embed_problem(props, hosts):
    """Why an area's embed block is refused, or None. An area may only embed a host its owner listed by name."""
    if props.get("module"):
        return "an embed cannot show one of the owner's modules"
    host = sc.embed_url_host(props.get("url") or "")
    if not host:
        return "the embed address must be a plain https address (no port, no user@, no spaces)"
    if host not in hosts:
        return "the owner has not allowed embedding %s%s" % (
            _show(host), (" (allowed: %s)" % ", ".join(hosts)) if hosts else " (this area may not embed any site)")
    return None


def parse_area_doc(raw, area, registry, files, warnings, root=None):
    """Validate area.json against the area's rules. Returns the canonical document (or raises BundleError)."""
    if len(raw) > MAX_AREA_JSON:
        raise BundleError("area.json is larger than %d KB." % (MAX_AREA_JSON // 1024))
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise BundleError("area.json is not valid JSON.")
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise BundleError("area.json must be an object with \"format\": %d." % FORMAT)
    for k in doc:
        if k not in TOP_KEYS:
            warnings.append("area.json key '%s' was ignored (a bundle can only describe its own pages)." % _show(k))
    errors = []
    try:
        title = sc.clean_line(doc.get("title"), 80)
        desc = sc.clean_line(doc.get("description"), 300)
    except sc.FieldError as e:
        raise BundleError(str(e))
    raw_pages = doc.get("pages")
    if raw_pages is None:
        raw_pages = [{"name": "", "title": title, "blocks": doc.get("blocks", [])}]
    if not isinstance(raw_pages, list) or not raw_pages or len(raw_pages) > MAX_PAGES:
        raise BundleError("area.json needs between 1 and %d pages." % MAX_PAGES)
    allowed_blocks = set(area.get("blocks") or DEFAULT_BLOCKS) & ALLOWABLE_BLOCKS
    ctx = sc.SchemaContext(module_ok=lambda *a: False, page_ok=lambda p: False, area_ok=lambda a: False)
    pages, names = [], set()
    for pi, rp in enumerate(raw_pages):
        if not isinstance(rp, dict):
            raise BundleError("Page %d is not an object." % (pi + 1))
        pname = "" if pi == 0 and not rp.get("name") else sc.slugify(str(rp.get("name") or ""))
        if pi > 0 and not pname:
            raise BundleError("Page %d needs a name (letters, digits and dashes)." % (pi + 1))
        try:
            if pname:
                sc.validate_slug(pname)
        except sc.FieldError as e:
            raise BundleError("Page %d: %s" % (pi + 1, e))
        if pname in names or pname in ("files",):
            raise BundleError("Two pages share the name '%s' (or it is reserved)." % pname)
        names.add(pname)
        blocks_in = rp.get("blocks")
        if not isinstance(blocks_in, list) or len(blocks_in) > MAX_BLOCKS:
            raise BundleError("Page %d needs a list of at most %d blocks." % (pi + 1, MAX_BLOCKS))
        blocks = []
        for bi, b in enumerate(blocks_in):
            where = "Page %d, block %d" % (pi + 1, bi + 1)
            if not isinstance(b, dict) or not isinstance(b.get("type"), str):
                raise BundleError("%s is not a block." % where)
            btype = b["type"]
            if btype not in allowed_blocks:
                raise BundleError("%s uses the block '%s', which this area does not allow (allowed: %s)." % (
                    where, _show(btype), ", ".join(sorted(allowed_blocks))))
            plug = registry.block(btype)
            if not plug:
                raise BundleError("%s uses the block '%s', which is not available on this site." % (where, _show(btype)))
            props = b.get("props", {})
            if not isinstance(props, dict):
                raise BundleError("%s has settings that are not an object." % where)
            props = _rewrite_refs(props, area["slug"], files, errors, where)
            clean, errs = sc.validate_props(plug.manifest.get("fields", []), props, ctx)
            errors += ["%s: %s" % (where, e["message"]) for e in errs]
            _check_props_addresses(clean, plug.manifest.get("fields", []), area["slug"], errors, where)
            if btype == "embed":
                problem = _embed_problem(clean, area.get("embed_hosts") or [])
                if problem:
                    errors.append("%s: %s" % (where, problem))
            blocks.append({"id": "b%d" % (bi + 1), "type": btype, "props": clean})
        try:
            ptitle = sc.clean_line(rp.get("title"), 80) or title
        except sc.FieldError as e:
            raise BundleError(str(e))
        pages.append({"name": pname, "title": ptitle, "blocks": blocks})
    design = {}
    custom_css = bool(area.get("custom_css"))
    if doc.get("design") not in (None, {}):
        themes = [t for t in (area.get("themes") or []) if registry.theme(t)]
        if not themes and not custom_css:
            warnings.append("The 'design' section was ignored: the owner has not enabled custom designs for this area.")
        else:
            try:
                design = sc.clean_area_design(doc["design"], themes, allow_css=custom_css)
            except sc.FieldError as e:
                errors.append("design: %s" % e)
            logo = (design.get("brand") or {}).get("logo")
            if logo and logo not in files:
                errors.append("design: the logo %s is not in the bundle" % _show(logo))
    css_name = design.get("css")
    for n in sorted(f for f in files if f.lower().endswith(".css")):
        if n != css_name:
            errors.append("%s is not used: name it in design.css (an area has one stylesheet)" % _show(n))
    if css_name:
        if css_name not in files:
            errors.append("design: the stylesheet %s is not in the bundle" % _show(css_name))
        elif root is None:
            errors.append("design: the stylesheet cannot be checked here")
        else:
            with open(os.path.join(root, *css_name.split("/")), "rb") as fh:
                res = csscheck.check(fh.read().decode("utf-8"), set(files), area["slug"])
            errors += ["%s: %s" % (_show(css_name), p) for p in res.problems]
            warnings += ["%s: %s" % (_show(css_name), w) for w in res.warnings]
    if errors:
        raise BundleError(errors[:8])
    out = {"format": FORMAT, "title": title, "description": desc, "pages": pages}
    if design:
        out["design"] = design
    return out


# --------------------------------------------------------------------------- the whole thing
def validate_bundle(zip_path, dest, area, registry, existing_bytes=0):
    """Validate zip_path against the area's rules and, only if everything passes, unpack it into dest.

    dest is created (it must not exist). Returns {"doc", "files", "warnings", "size"}. Raises BundleError.
    On any failure nothing is left behind at dest.
    """
    limits = dict(DEFAULT_LIMITS, **(area.get("limits") or {}))
    if area.get("custom_css"):                                  # a stylesheet is only a possible file type when the owner allowed it
        limits["extensions"] = sorted(set(limits.get("extensions") or []) | {"css"})
    area = dict(area, limits=limits)
    tmp = str(dest) + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    warnings = []
    try:
        try:
            zf = zipfile.ZipFile(zip_path)
        except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
            raise BundleError("That is not a valid zip file.")
        with zf:
            entries = inspect_archive(zf, limits)
            os.makedirs(os.path.join(tmp, "files"))
            files, cap = {}, int(limits["max_file_mb"]) * 1024 * 1024
            area_raw = b""
            for info, name in entries:
                if name == "area.json":
                    area_raw = zf.read(info) if info.file_size <= MAX_AREA_JSON else b"x" * (MAX_AREA_JSON + 1)
                    continue
                target = os.path.join(tmp, *name.split("/"))
                real_tmp = os.path.realpath(tmp)
                if not os.path.realpath(os.path.dirname(target) if os.path.exists(os.path.dirname(target)) else tmp).startswith(real_tmp):
                    raise BundleError("A file would be written outside its folder (refused).")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                size = stream_entry(zf, info, target, cap)
                ext = name.rsplit(".", 1)[-1].lower()
                bad = check_content(target, name, ext, limits, warnings)
                if bad:
                    raise BundleError(bad)
                files[name] = {"size": size, "sha256": sha256_file(target)}
            total_new = sum(f["size"] for f in files.values())
            if existing_bytes + total_new > int(limits["max_total_mb"]) * 1024 * 1024:
                raise BundleError("The area would be larger than its %d MB total limit." % int(limits["max_total_mb"]))
            doc = parse_area_doc(area_raw, area, registry, files, warnings, root=tmp)
            with open(os.path.join(tmp, "area.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(doc, f, indent=1)
        os.replace(tmp, dest)
        return {"doc": doc, "files": files, "warnings": warnings, "size": total_new}
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, OverflowError, ValueError, KeyError, OSError):
        raise BundleError("The zip is damaged or could not be unpacked safely.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
