import json
import tempfile
import unittest
from pathlib import Path

from .helpers import SRC  # noqa: F401
from herdr_feature import manifest


def worktree(**overrides):
    base = dict(
        repo_name="alpha", repo_path="/repos/alpha", folder="alpha", suffix=None,
        branch="feat/x", branch_created=True, branch_source="new",
        base_ref="refs/remotes/origin/main", base_commit="abc", remote="origin",
    )
    base.update(overrides)
    return manifest.Worktree(**base)


class ManifestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.features = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        feature = manifest.Feature(name="x", root=self.features / "x", branch_prefix="feat/")
        feature.worktrees.append(worktree())
        feature.worktrees.append(worktree(folder="alpha@api", suffix="api", branch="feat/x-api", branch_created=False, branch_source="local"))
        feature.remember_workspace("wQ", "x")
        feature.save()
        loaded = manifest.load(feature.root)
        self.assertEqual(loaded.name, "x")
        self.assertEqual(loaded.workspace["id"], "wQ")
        self.assertEqual([wt.folder for wt in loaded.worktrees], ["alpha", "alpha@api"])
        self.assertEqual(loaded.path_of(loaded.worktrees[1]), feature.root / "alpha@api")
        self.assertEqual(len(loaded.worktrees_for(Path("/repos/alpha"))), 2)
        self.assertFalse(list(feature.root.glob(".feature.*.tmp")), "temp file left behind")

    def test_discover_reports_corrupt_and_sorts(self):
        good = manifest.Feature(name="b", root=self.features / "b")
        good.save()
        bad = self.features / "a"
        bad.mkdir()
        (bad / manifest.MANIFEST_NAME).write_text("{not json")
        (self.features / "not-a-feature").mkdir()
        found = manifest.discover(self.features)
        self.assertEqual([f.name for f in found], ["a", "b"])
        self.assertFalse(found[0].readable)
        self.assertTrue(found[1].readable)

    def test_newer_version_is_read_only(self):
        root = self.features / "n"
        root.mkdir()
        data = manifest.Feature(name="n", root=root).to_dict()
        data["version"] = 99
        (root / manifest.MANIFEST_NAME).write_text(json.dumps(data))
        loaded = manifest.load(root)
        self.assertFalse(loaded.mutable)
        with self.assertRaises(manifest.ManifestError):
            loaded.save()

    def test_claims_and_find(self):
        one = manifest.Feature(name="one", root=self.features / "one")
        one.worktrees.append(worktree(branch="feat/one"))
        two = manifest.Feature(name="two", root=self.features / "two")
        two.worktrees.append(worktree(branch="feat/two"))
        claims = manifest.branch_claims([one, two])
        self.assertEqual(claims[("/repos/alpha", "feat/one")], "one")
        self.assertIs(manifest.find([one, two], "TWO"), two)
        self.assertIsNone(manifest.find([one, two], "three"))

    def test_malformed_entries_raise(self):
        root = self.features / "m"
        root.mkdir()
        (root / manifest.MANIFEST_NAME).write_text(json.dumps({"version": 1, "feature": "m", "worktrees": [{"folder": "x"}]}))
        with self.assertRaises(manifest.ManifestError):
            manifest.load(root)
