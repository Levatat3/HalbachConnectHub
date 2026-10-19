"""Regenerate the golden manifests in tests/golden/ from the current builder output.

Run from the src folder (or anywhere):  python tests/golden/update.py [case ...]
with no arguments every case in goldenbuild.CASES is rebuilt. The tests themselves never
touch the goldens -- run this only after an intentional output change, then review and
commit the diff.
"""
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
TESTS = Path(__file__).resolve().parents[1]
for p in (str(SRC), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import goldenbuild


def main(argv):
    names = list(argv) or list(goldenbuild.CASES)
    unknown = [n for n in names if n not in goldenbuild.CASES]
    if unknown:
        print("Unknown case(s): %s (known: %s)" % (", ".join(unknown), ", ".join(goldenbuild.CASES)))
        return 1
    goldenbuild.GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            stage = goldenbuild.build_case(name, Path(tmp))
            manifest = goldenbuild.manifest_of(stage)
        path = goldenbuild.GOLDEN_DIR / (name + ".json")
        path.write_text(goldenbuild.golden_text(manifest), encoding="utf-8", newline="\n")
        print("%s: %d files" % (path.relative_to(SRC).as_posix(), len(manifest)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
