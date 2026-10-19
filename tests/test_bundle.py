import json
import os
import struct
import zlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from halbach_connecthub import bundle
from halbach_connecthub import registry as rg

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk

AREA = {"id": "workshop", "slug": "workshop", "blocks": list(bundle.DEFAULT_BLOCKS),
        "limits": {"max_file_mb": 5, "max_bundle_mb": 10, "max_total_mb": 20, "max_files": 20, "max_image_px": 4000,
                  "extensions": ["md", "txt", "png", "jpg", "jpeg", "gif", "webp", "pdf"]}}
REG = rg.Registry()


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.root = Path(self._t.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.zip = self.root / "in.zip"

    def tearDown(self):
        self._t.cleanup()

    def snapshot(self):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p != self.zip)

    def run_bundle(self, entries=None, area=None, **kw):
        if entries is not None:
            zk.make_zip(self.zip, entries, **kw)
        return bundle.validate_bundle(self.zip, self.work / "tree", area or AREA, REG)

    def refused(self, entries=None, expect=None, area=None, **kw):
        import shutil
        shutil.rmtree(self.work / "tree", ignore_errors=True)
        before = self.snapshot()
        with self.assertRaises(bundle.BundleError) as cm:
            self.run_bundle(entries, area, **kw)
        text = " ".join(cm.exception.reasons).lower()
        if expect:
            self.assertIn(expect.lower(), text, text)
        self.assertEqual(self.snapshot(), before, "a refused bundle left something on disk")
        self.assertFalse((self.work / "tree").exists())
        self.assertFalse((self.work / "tree.part").exists())
        for r in cm.exception.reasons:
            self.assertNotIn(str(self.root), r, "a reason leaked a server path")
        return cm.exception


GOOD_DOC = ("area.json", zk.area_json())


class TestAccepted(Base):
    def test_a_real_bundle_unpacks_and_its_file_references_point_into_the_area(self):
        doc = zk.area_json([{"type": "hero", "props": {"heading": "Hi"}},
                            {"type": "image", "props": {"url": "files/pics/cat.png", "alt": "a cat"}},
                            {"type": "text", "props": {"markdown": "see ![cat](files/pics/cat.png)"}},
                            {"type": "links", "props": {"items": [{"label": "site", "url": "https://example.com"}, {"label": "mail", "url": "mailto:a@b.co"}]}}])
        res = self.run_bundle([("area.json", doc), ("files/pics/cat.png", zk.png()), ("files/notes.md", b"# notes"), ("files/guide.pdf", zk.PDF),
                               ("files/a.jpg", zk.jpeg()), ("files/b.gif", zk.gif()), ("files/c.webp", zk.webp())])
        tree = self.work / "tree"
        self.assertEqual(sorted(res["files"]), ["files/a.jpg", "files/b.gif", "files/c.webp", "files/guide.pdf", "files/notes.md", "files/pics/cat.png"])
        self.assertTrue((tree / "files" / "pics" / "cat.png").is_file())
        canon = json.loads((tree / "area.json").read_text(encoding="utf-8"))
        self.assertEqual(canon["pages"][0]["blocks"][1]["props"]["url"], "/workshop/files/pics/cat.png")
        self.assertFalse((self.work / "tree.part").exists())

    def test_zips_from_tools_that_record_no_file_type_are_fine(self):
        for attr in (0, 0o644 << 16, 0o100644 << 16, 0x20):
            with self.subTest(attr=attr):
                self.run_bundle([("area.json", zk.area_json(), {"external_attr": attr}), ("files/a.png", zk.png(), {"external_attr": attr})])
                import shutil
                shutil.rmtree(self.work / "tree")

    def test_raw_html_in_markdown_is_just_text(self):
        res = self.run_bundle([("area.json", zk.area_json([{"type": "text", "props": {"markdown": "<script>alert(1)</script> hi"}}]))])
        self.assertEqual(len(res["doc"]["pages"][0]["blocks"]), 1)        # accepted: the renderer shows it as text, never HTML

    def test_unknown_top_level_keys_are_ignored_and_reported(self):
        res = self.run_bundle([("area.json", zk.area_json(slug="/", nav=True, theme="funk", redirects=[{"from": "/", "to": "https://evil.example"}]))])
        text = " ".join(res["warnings"])
        for k in ("slug", "nav", "theme", "redirects"):
            self.assertIn("'%s'" % k, text)
        self.assertNotIn("redirects", res["doc"])

    def test_exif_is_flagged(self):
        res = self.run_bundle([GOOD_DOC, ("files/a.jpg", zk.jpeg(exif=True))])
        self.assertTrue(any("camera data was removed" in w for w in res["warnings"]))

    def test_crafted_jpeg_with_app1_gps_blob_comes_out_without_it_and_still_passes_check_content(self):
        res = self.run_bundle([GOOD_DOC, ("files/a.jpg", zk.jpeg(exif=True))])
        cleaned = (self.work / "tree" / "files" / "a.jpg").read_bytes()
        self.assertNotIn(b"Exif\x00\x00", cleaned)
        self.assertIn(b"\xff\xd8", cleaned)          # SOI still there
        self.assertIn(b"\xff\xd9", cleaned)          # EOI still there
        self.assertEqual(bundle._image_size("jpg", cleaned)[:2], (3, 2))   # dimensions still readable
        self.assertEqual(res["files"]["files/a.jpg"]["sha256"], bundle.sha256_file(self.work / "tree" / "files" / "a.jpg"))

    def test_jpeg_app13_photoshop_irb_is_removed(self):
        payload = b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x00IRBDATA"
        app13 = b"\xff\xed" + struct.pack(">H", 2 + len(payload)) + payload
        jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + app13 + \
              b"\xff\xc0\x00\x0b\x08" + struct.pack(">HH", 2, 3) + b"\x01\x01\x11\x00" + b"\xff\xd9"
        res = self.run_bundle([GOOD_DOC, ("files/a.jpg", jpg)])
        self.assertNotIn(b"Photoshop", (self.work / "tree" / "files" / "a.jpg").read_bytes())
        self.assertTrue(any("camera data was removed" in w for w in res["warnings"]))

    def test_png_with_location_text_chunk_is_removed(self):
        res = self.run_bundle([GOOD_DOC, ("files/a.png", zk.png_with_text(b"Location", b"51.5,-0.12"))])
        cleaned = (self.work / "tree" / "files" / "a.png").read_bytes()
        self.assertNotIn(b"Location", cleaned)
        self.assertTrue(any("camera data was removed" in w for w in res["warnings"]))

    def test_png_with_exif_chunk_is_removed(self):
        # eXIf chunk (PNG extension) carries EXIF/GPS; must be dropped.
        png = zk.png_with_text.__wrapped__ if hasattr(zk.png_with_text, "__wrapped__") else None
        import struct as _s, zlib as _z
        raw = b"\x00" + b"\x10\x20\x30" * 2
        data = (b"\x89PNG\r\n\x1a\n"
                + zk.chunk(b"IHDR", _s.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                + zk.chunk(b"eXIf", b"Exif\x00\x00GPSDATA!!")
                + zk.chunk(b"IDAT", _z.compress(raw))
                + zk.chunk(b"IEND", b""))
        res = self.run_bundle([GOOD_DOC, ("files/a.png", data)])
        cleaned = (self.work / "tree" / "files" / "a.png").read_bytes()
        self.assertNotIn(b"eXIf", cleaned)
        self.assertTrue(any("camera data was removed" in w for w in res["warnings"]))

    def test_png_with_xmp_in_itxt_is_removed(self):
        xmp = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<XMP>gps</XMP>"
        data = (b"\x89PNG\r\n\x1a\n"
                + zk.chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
                + zk.chunk(b"iTXt", xmp)
                + zk.chunk(b"IDAT", zlib.compress(b"\x00\x10\x20\x30" * 4))
                + zk.chunk(b"IEND", b""))
        res = self.run_bundle([GOOD_DOC, ("files/a.png", data)])
        cleaned = (self.work / "tree" / "files" / "a.png").read_bytes()
        self.assertNotIn(b"XMP", cleaned)
        self.assertTrue(any("camera data was removed" in w for w in res["warnings"]))

    def test_malformed_jpeg_segment_is_refused_not_corrupted(self):
        # APP1 claims a length far beyond the file: sanitiser raises, file kept, refused as invalid picture
        bad = b"\xff\xd8\xff\xe1\xff\xff" + b"Exif\x00\x00" + b"\xff\xd9"
        self.refused([GOOD_DOC, ("files/a.jpg", bad)], expect="valid JPG")

    def test_truncated_jpeg_is_refused_not_corrupted(self):
        # cut the image data off after APP1: no SOF0 and no EOI remain, so it is refused as an invalid picture
        jpg = zk.jpeg(exif=True)
        cut = jpg.index(b"\xff\xc0")                      # everything before the SOF0 frame header
        self.refused([GOOD_DOC, ("files/a.jpg", jpg[:cut])], expect="valid JPG")

    def test_jpeg_with_a_segment_length_that_overruns_the_file_is_refused(self):
        # APP1 claims a length longer than the bytes that remain
        bad = b"\xff\xd8\xff\xe1\xff\xff" + b"Exif\x00\x00short" + b"\xff\xd9"
        self.refused([GOOD_DOC, ("files/a.jpg", bad)], expect="valid JPG")

    def test_duplicate_submission_is_the_same_bytes(self):
        res = self.run_bundle([GOOD_DOC, ("files/a.png", zk.png())])
        self.assertEqual(res["files"]["files/a.png"]["sha256"], bundle.sha256_file(self.work / "tree" / "files" / "a.png"))


class TestHostileArchives(Base):
    def test_zip_slip_and_path_tricks(self):
        for name in ("../evil.png", "files/../../evil.png", "files/../x.png", "/etc/passwd", "C:/win/x.png", "c:x.png", "..\\evil.png", "files\\..\\..\\evil.png",
                     "files/a\x00.png", "files/a\nb.png", "files/a\u202epng.exe", "files/a\u200b.png", "files//x.png", "files/./x.png", "./files/x.png"):
            with self.subTest(name=name):
                self.refused([GOOD_DOC, (name, zk.png())])

    def test_symlinks_and_special_files(self):
        self.refused([GOOD_DOC, ("files/link.png", b"/etc/passwd", {"external_attr": 0o120777 << 16})], expect="link")
        self.refused([GOOD_DOC, ("files/dev.png", b"", {"external_attr": 0o060644 << 16})], expect="link or special")

    def test_encrypted_and_exotic_compression(self):
        zk.make_zip(self.zip, [GOOD_DOC, ("files/a.png", zk.png())])
        zk.patch_flag_bits(self.zip, "files/a.png", 0x1)
        self.refused(None, expect="password")
        self.refused([GOOD_DOC, ("files/a.png", zk.png(), {"compress_type": zipfile.ZIP_LZMA})], expect="compression")
        self.refused([GOOD_DOC, ("files/a.png", zk.png(), {"compress_type": zipfile.ZIP_BZIP2})], expect="compression")

    def test_zip_bomb_is_refused_before_a_byte_is_read(self):
        big = b"\x00" * (30 * 1024 * 1024)
        area = dict(AREA, limits=dict(AREA["limits"], max_file_mb=50, max_bundle_mb=60))
        self.refused([GOOD_DOC, ("files/bomb.txt", big)], expect="compress", area=area)

    def test_many_small_files_over_the_count(self):
        entries = [GOOD_DOC] + [("files/f%d.txt" % i, b"x") for i in range(25)]
        self.refused(entries, expect="too many")

    def test_a_header_that_lies_about_the_size_is_cut_off(self):
        zk.make_zip(self.zip, [GOOD_DOC, ("files/a.txt", b"A" * 200000)])
        zk.patch_declared_size(self.zip, "files/a.txt", 100)
        self.refused(None, expect="damaged")

    def test_oversize_file_and_oversize_bundle(self):
        import random
        rnd = random.Random(1)
        blob = bytes(rnd.getrandbits(8) for _ in range(6 * 1024 * 1024))                # incompressible, over the 5 MB file limit
        self.refused([GOOD_DOC, ("files/big.txt", blob)], expect="larger than 5 MB")
        area = dict(AREA, limits=dict(AREA["limits"], max_bundle_mb=1, max_file_mb=1))
        entries = [GOOD_DOC] + [("files/f%d.txt" % i, bytes(rnd.getrandbits(8) for _ in range(400000))) for i in range(4)]
        self.refused(entries, expect="larger than 1 MB", area=area)

    def test_names_that_are_too_long_too_deep_or_collide(self):
        self.refused([GOOD_DOC, ("files/" + "a" * 120 + ".png", zk.png())], expect="longer than")
        self.refused([GOOD_DOC, ("files/" + "/".join("d" * 3 for _ in range(9)) + "/x.png", zk.png())], expect="deep")
        self.refused([GOOD_DOC, ("files/Cat.png", zk.png()), ("files/cat.PNG", zk.png())], expect="same name")
        self.refused([GOOD_DOC, ("files/a.png.", zk.png())], expect="dot")
        self.refused([GOOD_DOC, ("files/a .png", zk.png()), ("files/b.png ", zk.png())])
        for reserved in ("CON.png", "aux.txt", "Com1.md", "LPT9.png", "nul.txt"):
            with self.subTest(reserved=reserved):
                self.refused([GOOD_DOC, ("files/" + reserved, b"x" if reserved.endswith(("txt", "md")) else zk.png())], expect="reserved")

    def test_nothing_outside_area_json_and_files(self):
        self.refused([GOOD_DOC, ("evil.html", b"<script>")], expect="only area.json")
        self.refused([GOOD_DOC, ("README.txt", b"hi")], expect="only area.json")
        self.refused([GOOD_DOC, ("pages/x.json", b"{}")], expect="only area.json")

    def test_not_a_zip_and_no_area_json(self):
        self.zip.write_bytes(b"not a zip at all")
        self.refused(None, expect="not a valid zip")
        self.refused([("files/a.png", zk.png())], expect="no area.json")
        self.zip.write_bytes(b"")
        self.refused(None, expect="not a valid zip")


class TestHostileFiles(Base):
    def test_programs_scripts_and_markup_are_not_files_an_area_may_hold(self):
        for name in ("files/a.exe", "files/a.html", "files/a.htm", "files/a.js", "files/a.svg", "files/a.xml", "files/a.swf", "files/a.bat",
                     "files/a.ps1", "files/a.lnk", "files/a.php", "files/a.zip", "files/a.jar", "files/a.hta", "files/noext", "files/.htaccess"):
            with self.subTest(name=name):
                self.refused([GOOD_DOC, (name, b"payload")])

    def test_double_extensions_hide_nothing(self):
        for name in ("files/photo.exe.png", "files/photo.png.exe", "files/photo.html.png", "files/x.js.md", "files/a.svg.png", "files/a.PNG.EXE"):
            with self.subTest(name=name):
                self.refused([GOOD_DOC, (name, zk.png())])

    def test_the_bytes_must_match_the_extension(self):
        self.refused([GOOD_DOC, ("files/a.png", b"<html>not a picture")], expect="valid PNG")
        self.refused([GOOD_DOC, ("files/a.jpg", zk.png())], expect="valid JPG")
        self.refused([GOOD_DOC, ("files/a.gif", zk.jpeg())], expect="valid GIF")
        self.refused([GOOD_DOC, ("files/a.pdf", b"MZ\x90\x00 executable")], expect="not a PDF")
        self.refused([GOOD_DOC, ("files/a.md", b"text\x00with nul")], expect="not a text file")
        self.refused([GOOD_DOC, ("files/a.txt", b"\xff\xfe\x00bad utf8 \x80\x81")], expect="text")
        self.refused([GOOD_DOC, ("files/a.png", zk.png()[:20])], expect="valid PNG")

    def test_oversized_pictures_are_refused_by_their_header(self):
        self.refused([GOOD_DOC, ("files/a.png", zk.png_header_only(20000, 20000))], expect="pixels")
        wide = dict(AREA, limits=dict(AREA["limits"], max_image_px=12000))
        self.refused([GOOD_DOC, ("files/a.png", zk.png_header_only(9000, 9000))], expect="pixels", area=wide)         # 81 megapixels
        self.refused([GOOD_DOC, ("files/a.gif", zk.gif(9000, 10))], expect="pixels")
        self.refused([GOOD_DOC, ("files/a.png", zk.png_header_only(0, 5))], expect="pixels")


class TestAreaDocument(Base):
    def blocks(self, *bl):
        return [GOOD_DOC[0:0] or ("area.json", zk.area_json(list(bl)))]

    def test_blocks_the_area_may_not_use(self):
        for t, p in (("embed", {"url": "https://evil.example"}), ("live-view", {"module": "x"}), ("ruffle-game", {"swf": "/a.swf"}),
                     ("dropbox", {}), ("module-list", {}), ("github-projects", {"user": "x"}), ("linked-sites", {}), ("nonsense", {})):
            with self.subTest(block=t):
                self.refused(self.blocks({"type": t, "props": p}), expect="does not allow")

    def test_an_owner_can_narrow_the_allowed_blocks(self):
        area = dict(AREA, blocks=["text"])
        self.refused(self.blocks({"type": "hero", "props": {"heading": "x"}}), expect="does not allow", area=area)

    def test_addresses_must_be_https_mailto_or_inside_the_area(self):
        for bad in ("javascript:alert(1)", "http://example.com", "//evil.example/x", "data:text/html,hi", "/modules/secret", "/", "/other/", "/workshop/../x",
                    "file:///c:/x", "ftp://x"):
            with self.subTest(url=bad):
                self.refused(self.blocks({"type": "links", "props": {"items": [{"label": "x", "url": bad}]}}))

    def test_references_to_files_that_are_not_in_the_bundle(self):
        self.refused(self.blocks({"type": "image", "props": {"url": "files/missing.png"}}), expect="not in the bundle")
        self.refused(self.blocks({"type": "text", "props": {"markdown": "![x](files/missing.png)"}}), expect="not in the bundle")

    def test_cards_cannot_point_at_site_pages_and_nothing_may_name_a_module(self):
        self.refused(self.blocks({"type": "cards", "props": {"items": [{"title": "t", "page": "home"}]}}))
        self.refused(self.blocks({"type": "image", "props": {"module": "secret-module"}}))

    def test_page_structure_limits(self):
        pages = [{"name": "p%d" % i, "title": "t", "blocks": []} for i in range(25)]
        self.refused([("area.json", json.dumps({"format": 1, "title": "x", "pages": pages}).encode())], expect="pages")
        two = [{"name": "", "blocks": []}, {"name": "same", "blocks": []}, {"name": "same", "blocks": []}]
        self.refused([("area.json", json.dumps({"format": 1, "title": "x", "pages": two}).encode())], expect="share the name")
        self.refused([("area.json", json.dumps({"format": 1, "title": "x", "pages": [{"name": "", "blocks": []}, {"name": "files", "blocks": []}]}).encode())])
        self.refused(self.blocks(*[{"type": "divider", "props": {}} for _ in range(101)]), expect="at most")

    def test_bad_json_and_wrong_format_and_huge_document(self):
        self.refused([("area.json", b"{not json")], expect="not valid JSON")
        self.refused([("area.json", b"[1,2]")], expect="format")
        self.refused([("area.json", json.dumps({"format": 2, "pages": []}).encode())], expect="format")
        self.refused([("area.json", b'{"format":1,"title":"' + os.urandom(160000).hex().encode() + b'"}')], expect="larger than")
        self.refused([("area.json", b"\xff\xfe\x00")], expect="not valid JSON")

    def test_quota_for_the_whole_area(self):
        area = dict(AREA, limits=dict(AREA["limits"], max_total_mb=1))
        zk.make_zip(self.zip, [GOOD_DOC, ("files/a.txt", b"x" * 1000)])
        with self.assertRaises(bundle.BundleError):
            bundle.validate_bundle(self.zip, self.work / "tree", area, REG, existing_bytes=1024 * 1024)
        self.assertFalse((self.work / "tree").exists())


class TestNoSideEffectsOnSuccessOrFailure(Base):
    def test_extraction_never_writes_outside_the_destination(self):
        self.run_bundle([GOOD_DOC, ("files/deep/er/a.png", zk.png())])
        for p in self.root.rglob("*"):
            self.assertTrue(str(p).startswith(str(self.root)))
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ["tree"])

    def test_a_late_failure_removes_everything_already_unpacked(self):
        self.refused([GOOD_DOC, ("files/a.png", zk.png()), ("files/b.png", zk.png()), ("files/z.png", b"not a png")], expect="valid PNG")


if __name__ == "__main__":
    unittest.main()
