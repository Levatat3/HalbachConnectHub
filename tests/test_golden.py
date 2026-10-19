"""Task 1.8: golden builds.

Every fixed model in goldenbuild.CASES is built fresh and its {path: sha256} manifest is compared
to the checked-in tests/golden/<name>.json. The tests never rewrite the goldens: after an
intentional output change run `python tests/golden/update.py` and commit the diff, so accidental
changes to the built site are always visible in review.

The normalisation of manifest.json (random build id, generation time) is proved by the
determinism test below: two independent builds of the same model must hash identically.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import goldenbuild


def manifest_diff(got, want):
    added = sorted(set(got) - set(want))
    removed = sorted(set(want) - set(got))
    changed = sorted(p for p in set(got) & set(want) if got[p] != want[p])
    return added, removed, changed


class TestGolden(unittest.TestCase):
    def test_builds_match_the_checked_in_goldens(self):
        for name in goldenbuild.CASES:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                stage = goldenbuild.build_case(name, Path(tmp))
                got = goldenbuild.manifest_of(stage)
                path = goldenbuild.GOLDEN_DIR / (name + ".json")
                self.assertTrue(path.is_file(), "no golden for '%s'; run tests/golden/update.py" % name)
                want = json.loads(path.read_text(encoding="utf-8"))
                added, removed, changed = manifest_diff(got, want)
                self.assertEqual((added, removed, changed), ([], [], []),
                                 "%s: the built site changed (%d new, %d gone, %d different: %s). "
                                 "If that is intended, run tests/golden/update.py and commit the diff."
                                 % (name, len(added), len(removed), len(changed),
                                    ", ".join((added + removed + changed)[:8])))

    def test_golden_files_exist_exactly_for_the_known_cases(self):
        names = sorted(p.stem for p in goldenbuild.GOLDEN_DIR.glob("*.json"))
        self.assertEqual(names, sorted(goldenbuild.CASES))

    def test_two_builds_of_the_same_model_hash_identically(self):
        """The random build id and the generation time in manifest.json are normalised away."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = goldenbuild.manifest_of(goldenbuild.build_case("area-site", Path(a)))
            second = goldenbuild.manifest_of(goldenbuild.build_case("area-site", Path(b)))
        self.assertEqual(manifest_diff(first, second), ([], [], []))
        self.assertTrue(first["manifest.json"])


if __name__ == "__main__":
    unittest.main()
