"""Golden builds: fixed site models compiled with a fixed theme into a {path: sha256} manifest.

Each case in CASES builds a completely fixed model (template plus pinned theme, and for
'area-site' an approved area tree and a linked site) into a fresh stage folder. The resulting
manifest -- every file's relative path and sha256 -- is compared to the checked-in
tests/golden/<name>.json by tests/test_golden.py. tests/golden/update.py regenerates the
goldens after an intentional output change; the tests themselves never rewrite them, so an
accidental change to the built site always shows up in review.

manifest.json carries a random build id and a generation time. Both are normalised to fixed
values before hashing, so two builds of the same model always produce the same manifest.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zipkit as zk                                    # noqa: E402  (tiny fixed png for the area fixture)

from halbach_connecthub import registry as rg          # noqa: E402
from halbach_connecthub import sitebuilder as sb       # noqa: E402
from halbach_connecthub import sitemodel as sm         # noqa: E402
from halbach_connecthub import store as st             # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES = ("blank", "personal", "streamer", "area-site")
FIXED_THEME = "neutral-dark"                           # pinned on every case: a theme edit shows up in review


class GoldenBuildError(RuntimeError):
    """A golden case no longer builds cleanly."""


def _area_fixture(root):
    """An approved area's tree, exactly the shape build_site reads (area.json plus its files folder).

    area.json matches what bundle.parse_area_doc leaves in an approved tree: block ids b1..bn and
    file references already rewritten to the area's own address.
    """
    tree = root / "workshop-tree"
    (tree / "files").mkdir(parents=True)
    (tree / "files" / "pic.png").write_bytes(zk.png())
    doc = {"format": 1, "title": "Workshop", "description": "Approved content from a collaborator",
           "pages": [{"name": "", "title": "Workshop", "blocks": [
               {"id": "b1", "type": "hero", "props": {"heading": "The workshop", "typed": False}},
               {"id": "b2", "type": "image", "props": {"url": "/workshop/files/pic.png", "alt": "A picture"}},
               {"id": "b3", "type": "text", "props": {"markdown": "See ![pic](/workshop/files/pic.png)"}}]}]}
    (tree / "area.json").write_text(json.dumps(doc), encoding="utf-8")
    return [{"id": "workshop", "name": "Workshop", "slug": "workshop", "nav": True,
             "tree": str(tree), "blocks": ["hero", "image", "text"]}]


def build_case(name, root):
    """Build case `name` into <root>/stage. Returns the stage path; raises GoldenBuildError on failure."""
    if name not in CASES:
        raise GoldenBuildError("Unknown golden case '%s' (known: %s)" % (name, ", ".join(CASES)))
    root = Path(root)
    reg = rg.Registry()
    model = sm.SiteModel(root / "hub", reg)
    store = st.Store(root / "hub")
    stage = root / "stage"
    areas = None
    if name == "area-site":
        model.load_template("personal")
        model.add_linked("Funk's Workshop", "https://workshop.example.com",
                         description="A second site, linked from this one")
        model.add_block(model.data["pages"][0]["id"], "linked-sites", {"ids": [], "style": "cards"})
        areas = _area_fixture(root)
    else:
        model.load_template(name)
    model.set_theme(FIXED_THEME)
    errors, _ = model.validate()
    if errors:
        raise GoldenBuildError("%s: model invalid: %r" % (name, errors))
    res = sb.build_site(model, reg, store, stage, areas=areas)
    if not res.ok:
        raise GoldenBuildError("%s: build failed: %r" % (name, res.errors))
    return stage


def _normalised_manifest(data):
    """manifest.json with its random build id and generation time replaced by fixed values."""
    doc = json.loads(data)
    doc["build"] = "normalised"
    doc["generated"] = 0
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")


def manifest_of(stage):
    """{relative posix path: sha256 hex} for every file in a built stage."""
    stage = Path(stage)
    out = {}
    for f in sorted(p for p in stage.rglob("*") if p.is_file()):
        data = f.read_bytes()
        if f.name == "manifest.json" and f.parent == stage:
            data = _normalised_manifest(data)
        out[f.relative_to(stage).as_posix()] = hashlib.sha256(data).hexdigest()
    return out


def golden_text(manifest):
    """The exact JSON text a golden file holds (stable key order, one file per line)."""
    return json.dumps(manifest, indent=1, sort_keys=True) + "\n"
