import unittest
from halbach_connecthub import quality


class TestQualityExtra(unittest.TestCase):
    def test_same_title_warns(self):
        data = {
            "pages": [
                {"id": "a", "label": "Home", "title": "Hello", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": []},
                {"id": "b", "label": "About", "title": "hello", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": []},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("same title" in w["message"] or "Two pages have the same title" in w["message"] for w in res))

    def test_title_or_desc_too_long_warns(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "x" * 61, "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": []},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("search results" in w["message"] for w in res))
        data["pages"][0]["title"] = "ok"
        data["pages"][0]["description"] = "d" * 161
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("search results" in w["message"] for w in res))

    def test_empty_alt_image_in_text_warns(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "A", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": [
                    {"id": "b1", "type": "text", "enabled": True, "props": {"markdown": "![alt]() goes here or ![](pic.png)"}}
                ]},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("image with no alt text" in w["message"] for w in res))

    def test_multiple_heading_blocks_warns(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "A", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": [
                    {"id": "b1", "type": "text", "enabled": True, "props": {"markdown": "# One"}},
                    {"id": "b2", "type": "text", "enabled": True, "props": {"markdown": "# Two"}},
                ]},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("more than one big heading block" in w["message"] for w in res))

    def test_og_image_empty_with_base_url_info(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "A", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": []},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": "https://example.com"},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("social image" in w["message"] and w.get("level") == "info" for w in res))

    def test_enabled_page_no_blocks_warns(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "A", "description": "", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": []},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        self.assertTrue(any("no content blocks" in w["message"] for w in res))

    def test_negative_cases(self):
        data = {
            "pages": [
                {"id": "a", "label": "A", "title": "A", "description": "short", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "img.png", "blocks": [
                    {"id": "b1", "type": "text", "enabled": True, "props": {"markdown": "# One\nSome text"}}
                ]},
                {"id": "b", "label": "B", "title": "Different", "description": "also short", "nav": True, "listed": True, "enabled": True, "draft": False, "og_image": "", "blocks": [
                    {"id": "b1", "type": "text", "enabled": True, "props": {"markdown": "![alt text](pic.png)"}}
                ]},
            ],
            "settings": {"csp": "enforce", "indexable": True, "base_url": ""},
            "theme": {"id": "neutral-dark"},
            "redirects": [],
        }
        res = quality.check_site(data, {"--text": "#fff", "--bg": "#000", "--muted": "#ccc", "--accent": "#7cc4ff"})
        # should not warn for these cases
        msgs = " ".join(w["message"] for w in res)
        self.assertNotIn("same title", msgs)
        self.assertNotIn("no content blocks", msgs)
        self.assertNotIn("image with no alt text", msgs)
        self.assertNotIn("big heading block", msgs)