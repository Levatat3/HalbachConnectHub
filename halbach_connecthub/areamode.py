"""Area mode (the collaborator's restricted window): one page kept as a folder of area.json + files/.

The collaborator edits a single-page tree. This module turns that folder into the same SiteModel the window already
edits, and back again, so the window and `halbach area send` share one on-disk format. Stdlib only.
"""

import json
from pathlib import Path

from .sitemodel import ModelError, default_model


def _read_doc(folder):
    folder = Path(folder)
    path = folder / "area.json"
    if not path.is_file():
        raise ModelError("There is no area.json in %s. Start one with: halbach area init %s" % (folder, folder))
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ModelError("area.json is not valid JSON (%s)." % e)
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("pages"), list):
        raise ModelError("area.json is not an area file.")
    return doc


def load_area(folder, model, theme="neutral-dark"):
    """Put the single-page area into a SiteModel (in memory only; nothing on disk is touched)."""
    doc = _read_doc(folder)
    title = (doc.get("title") or "My area").strip() or "My area"
    page = doc["pages"][0] if doc["pages"] else {}
    label = (page.get("title") or title).strip() or title
    blocks = []
    for i, b in enumerate(page.get("blocks") or []):
        if not isinstance(b, dict) or not b.get("type"):
            continue
        blocks.append({"id": "b%d" % (i + 1), "type": b["type"], "props": dict(b.get("props") or {}), "enabled": True})
    data = default_model(theme)
    data["brand"]["name"] = title
    data["brand"]["mark"] = title[:1]
    data["settings"]["csp"] = "report-only"
    data["settings"]["analytics"] = "none"
    data["settings"]["indexable"] = False
    data["settings"]["nav_style"] = "none"
    data["pages"] = [{"id": "home", "slug": "", "label": label, "title": label, "description": doc.get("description", ""),
                      "nav": False, "listed": False, "enabled": True, "draft": False, "og_image": "", "theme": None,
                      "area": None, "credit": True, "blocks": blocks}]
    model.data = data
    return doc


def dump_area(model, folder):
    """Write the single page back to area.json (at most one page; files/ is left alone)."""
    folder = Path(folder)
    pages = model.data.get("pages") or []
    page = pages[0] if pages else {"label": "My area", "title": "My area", "blocks": []}
    blocks = [{"type": b["type"], "props": b.get("props") or {}} for b in page.get("blocks") or [] if b.get("enabled", True)]
    title = (page.get("label") or page.get("title") or "My area").strip() or "My area"
    doc = {"format": 1, "title": title, "description": page.get("description", ""),
           "pages": [{"name": "", "title": title, "blocks": blocks}]}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files").mkdir(parents=True, exist_ok=True)
    (folder / "area.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc
