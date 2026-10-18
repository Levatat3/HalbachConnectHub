"""Helpers for building (hostile) zips and tiny valid files in tests."""
import json
import struct
import zipfile
import zlib


def chunk(t, d):
    return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)


def png(w=2, h=2):
    raw = b"".join(b"\x00" + b"\x10\x20\x30" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def png_header_only(w, h):
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IEND", b"")


def jpeg(w=3, h=2, exif=False):
    app = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    if exif:
        app += b"\xff\xe1\x00\x10Exif\x00\x00GPSDATA!!"
    return b"\xff\xd8" + app + b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", h, w) + b"\x01\x01\x11\x00" + b"\xff\xd9"


def gif(w=2, h=2):
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00\x00\x00;"


def webp(w=4, h=4):
    return b"RIFF" + struct.pack("<I", 22) + b"WEBPVP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00" + (w - 1).to_bytes(3, "little") + (h - 1).to_bytes(3, "little")


PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def area_json(blocks=None, **extra):
    doc = {"format": 1, "title": "Workshop", "description": "My stuff",
           "pages": [{"name": "", "title": "Workshop", "blocks": blocks if blocks is not None else [{"type": "text", "props": {"markdown": "hello"}}]}]}
    doc.update(extra)
    return json.dumps(doc).encode()


def make_zip(path, entries, compress=zipfile.ZIP_DEFLATED):
    """entries: list of (name, bytes) or (name, bytes, {attr overrides: external_attr, flag_bits, compress_type})."""
    with zipfile.ZipFile(path, "w", compress) as zf:
        for e in entries:
            name, data = e[0], e[1]
            opts = e[2] if len(e) > 2 else {}
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = opts.get("compress_type", compress)
            info.external_attr = opts.get("external_attr", 0o100644 << 16)
            if "flag_bits" in opts:
                info.flag_bits |= opts["flag_bits"]
            zf.writestr(info, data)
    return path


def patch_declared_size(zip_path, entry_name, fake_size):
    """Make the zip's central directory claim a different uncompressed size for one entry (a lying header)."""
    data = bytearray(open(zip_path, "rb").read())
    needle = entry_name.encode()
    i = 0
    while True:
        i = data.find(b"PK\x01\x02", i)
        if i < 0:
            raise AssertionError("entry not found")
        name_len = struct.unpack("<H", data[i + 28:i + 30])[0]
        if bytes(data[i + 46:i + 46 + name_len]) == needle:
            data[i + 24:i + 28] = struct.pack("<I", fake_size)
            break
        i += 4
    open(zip_path, "wb").write(bytes(data))


def patch_flag_bits(zip_path, entry_name, bits):
    """Set general-purpose flag bits (for example 0x1, 'encrypted') in the central directory for one entry."""
    data = bytearray(open(zip_path, "rb").read())
    needle = entry_name.encode()
    i = 0
    while True:
        i = data.find(b"PK", i)
        if i < 0:
            raise AssertionError("entry not found")
        name_len = struct.unpack("<H", data[i + 28:i + 30])[0]
        if bytes(data[i + 46:i + 46 + name_len]) == needle:
            cur = struct.unpack("<H", data[i + 8:i + 10])[0]
            data[i + 8:i + 10] = struct.pack("<H", cur | bits)
            break
        i += 4
    open(zip_path, "wb").write(bytes(data))
