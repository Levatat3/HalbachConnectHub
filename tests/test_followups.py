"""Two things Funk's Claude found on the first real use: a card link under the wrong key must be an error, not vanish; and the offline
check must judge links to the area's own pages with the area's real address."""
import json
import tempfile
import unittest
from pathlib import Path

from halbach_connecthub import areaclient
from halbach_connecthub import siteschema as sc
from halbach_connecthub.bundle import BundleError


def doc(url=None, **extra):
    item = {"title": "Music", "text": "Songs", "url": url} if url else {"title": "Music", "text": "Songs"}
    item.update(extra)
    return {"format": 1, "title": "W", "description": "", "pages": [
        {"name": "", "title": "Home", "blocks": [{"type": "cards", "props": {"items": [item]}}]},
        {"name": "music", "title": "Music", "blocks": [{"type": "text", "props": {"markdown": "hi"}}]}]}


class FakeCtx:
    def page_ok(self, p):
        return True

    def module_ok(self, *a):
        return True


class TestCardLinkKeys(unittest.TestCase):
    FIELD = {"key": "items", "type": "cards"}

    def clean(self, item):
        return sc._clean_value(self.FIELD, [item], FakeCtx())

    def test_a_link_under_another_name_is_an_error_that_says_what_to_use(self):
        for wrong in ("href", "link", "to", "target"):
            with self.assertRaises(sc.FieldError) as cm:
                self.clean({"title": "Music", "text": "x", wrong: "/music/"})
            self.assertIn("url or page", str(cm.exception))
            self.assertIn(wrong, str(cm.exception))

    def test_the_right_keys_and_no_link_at_all_still_work(self):
        self.assertEqual(self.clean({"title": "A", "text": "x", "url": "https://example.com"})[0]["url"], "https://example.com")
        self.assertEqual(self.clean({"title": "A", "text": "x", "page": "home"})[0]["page"], "home")
        self.assertNotIn("url", self.clean({"title": "A", "text": "x"})[0])


class TestOfflineCheckUsesTheRealSlug(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.folder = Path(self._t.name)

    def tearDown(self):
        self._t.cleanup()

    def write(self, d):
        (self.folder / "area.json").write_text(json.dumps(d), encoding="utf-8")

    def test_a_link_to_the_areas_own_page_passes_with_its_real_slug(self):
        self.write(doc("/funkas-workshop/music/"))
        res = areaclient.validate_folder(self.folder, None, slug="funkas-workshop")
        self.assertEqual(res["pages"], 2)

    def test_without_the_slug_it_is_still_refused_and_a_foreign_link_always_is(self):
        self.write(doc("/funkas-workshop/music/"))
        with self.assertRaises(BundleError):
            areaclient.validate_folder(self.folder, None)
        self.write(doc("/someone-else/music/"))
        with self.assertRaises(BundleError):
            areaclient.validate_folder(self.folder, None, slug="funkas-workshop")

    def test_a_malformed_slug_falls_back_to_the_stand_in(self):
        self.write(doc("/x/music/"))
        with self.assertRaises(BundleError):
            areaclient.validate_folder(self.folder, None, slug="../x")

    def test_a_bundle_with_a_wrongly_named_card_link_is_refused_with_the_reason(self):
        self.write(doc(None, href="/funkas-workshop/music/"))
        with self.assertRaises(BundleError) as cm:
            areaclient.validate_folder(self.folder, None, slug="funkas-workshop")
        self.assertIn("url or page", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
