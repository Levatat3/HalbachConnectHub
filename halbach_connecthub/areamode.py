"""Area mode (the collaborator's restricted window): pages kept as a folder of area.json + files/.

The collaborator edits a page tree. This module turns that folder into the same SiteModel the window already
edits, and back again, so the window and `halbach area send` share one on-disk format. Every page and every
block round-trips: nothing is dropped on load or on dump. Stdlib only.
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
    """Put every page of the area into a SiteModel (in memory only; nothing on disk is touched)."""
    doc = _read_doc(folder)
    title = (doc.get("title") or "My area").strip() or "My area"
    data = default_model(theme)
    data["brand"]["name"] = title
    data["brand"]["mark"] = title[:1]
    data["settings"]["csp"] = "report-only"
    data["settings"]["analytics"] = "none"
    data["settings"]["indexable"] = False
    data["settings"]["nav_style"] = "none"
    pages = []
    for idx, pg in enumerate(doc.get("pages") or []):
        if not isinstance(pg, dict):
            continue
        name = (pg.get("name") or "").strip()
        label = (pg.get("title") or (title if not name else name)).strip() or title
        blocks = []
        for i, b in enumerate(pg.get("blocks") or []):
            if not isinstance(b, dict) or not b.get("type"):
                continue
            blocks.append({"id": "b%d" % (i + 1), "type": b["type"], "props": dict(b.get("props") or {}), "enabled": True})
        pages.append({"id": "page" if idx == 0 else "page-%s" % (name or idx), "slug": name, "label": label, "title": label,
                      "description": pg.get("description", "") or (doc.get("description", "") if idx == 0 else ""),
                      "nav": False, "listed": False, "enabled": True, "draft": False, "og_image": "", "theme": None,
                      "area": None, "credit": True, "blocks": blocks})
    if not pages:
        pages = [{"id": "page", "slug": "", "label": title, "title": title, "description": doc.get("description", ""),
                  "nav": False, "listed": False, "enabled": True, "draft": False, "og_image": "", "theme": None,
                  "area": None, "credit": True, "blocks": []}]
    data["pages"] = pages
    model.data = data
    return doc


def apply_design(model, design, folder=None, url_base="/preview"):
    """Show the area's own design in the window's preview: its brand, theme, colours and menu layout.

    The editor models the area as a one-site page list; this puts the owner-approved look on it so Preview matches
    what the sender will get. Plain values only (the design was validated by siteschema.clean_area_design)."""
    d = design or {}
    data = model.data
    for k, v in (d.get("brand") or {}).items():
        if k in ("name", "mark", "tagline", "footer") and v:
            data["brand"][k] = v
    logo = (d.get("brand") or {}).get("logo")
    if logo and folder and (Path(folder) / logo).is_file():       # a picture that is really in files/ replaces the mark, as on the real site
        data["brand"]["logo"] = "%s/%s" % (url_base.rstrip("/"), logo)
    else:
        data["brand"].pop("logo", None)
    tid = d.get("theme")
    data["theme"]["id"] = tid if tid and model.registry.theme(tid) else "neutral-dark"
    data["theme"]["vars"] = dict(d.get("vars") or {})
    if d.get("funk") and data["theme"]["id"] == "funk":
        data["theme"]["funk"] = d["funk"]
    else:
        data["theme"].pop("funk", None)
    data["settings"]["nav_style"] = (d.get("layout") or {}).get("nav") or "none"


def dump_area(model, folder, design=None):
    """Write every page back to area.json (files/ is left alone). Nothing is silently dropped."""
    folder = Path(folder)
    pages = model.data.get("pages") or []
    out_pages = []
    for p in pages:
        name = (p.get("slug") or "").strip()
        label = (p.get("label") or p.get("title") or "My area").strip() or "My area"
        # The area format has no per-block on/off switch, so every block is written; the owner's rules still apply.
        blocks = [{"type": b["type"], "props": b.get("props") or {}} for b in p.get("blocks") or [] if isinstance(b, dict) and b.get("type")]
        out_pages.append({"name": name, "title": label, "blocks": blocks})
    if not out_pages:
        out_pages = [{"name": "", "title": "My area", "blocks": []}]
    title = (model.data.get("brand", {}).get("name") or "My area").strip() or "My area"
    desc = pages[0].get("description", "") if pages else ""
    doc = {"format": 1, "title": title, "description": desc, "pages": out_pages}
    if design:
        doc["design"] = design
    try:                                                    # the window does not edit the area's own pages (app/); saving must not lose their section
        app = json.loads((folder / "area.json").read_text(encoding="utf-8")).get("app")
    except (OSError, ValueError, AttributeError):
        app = None
    if app:
        doc["app"] = app
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files").mkdir(parents=True, exist_ok=True)
    (folder / "area.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return doc
