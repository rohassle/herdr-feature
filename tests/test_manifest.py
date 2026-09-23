import json
import tempfile
import unittest
from pathlib import Path

from .helpers import SRC  # noqa: F401
from herdr_workthreads import manifest


def worktree(**overrides):
    base = dict(
        repo_name="alpha",
        repo_path="/repos/alpha",
        folder="alpha",
        suffix=None,
        branch="feat/x",
        branch_created=True,
        branch_source="new",
        base_ref="refs/remotes/origin/main",
        base_commit="abc",
        remote="origin",
    )
    base.update(overrides)
    return manifest.Worktree(**base)


class ManifestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.threads = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        thread = manifest.Thread(name="x", root=self.threads / "x", branch_prefix="feat/")
        thread.worktrees.append(worktree())
        thread.worktrees.append(
            worktree(
                folder="alpha@api",
                suffix="api",
                branch="feat/x-api",
                branch_created=False,
                branch_source="local",
            )
        )
        thread.remember_workspace("wQ", "x")
        thread.save()
        loaded = manifest.load(thread.root)
        self.assertEqual(loaded.name, "x")
        self.assertEqual(loaded.workspace["id"], "wQ")
        self.assertEqual([wt.folder for wt in loaded.worktrees], ["alpha", "alpha@api"])
        self.assertEqual(loaded.path_of(loaded.worktrees[1]), thread.root / "alpha@api")
        self.assertEqual(len(loaded.worktrees_for(Path("/repos/alpha"))), 2)
        self.assertFalse(list(thread.root.glob(".thread.*.tmp")), "temp file left behind")

    def test_discover_reports_corrupt_and_sorts(self):
        good = manifest.Thread(name="b", root=self.threads / "b")
        good.save()
        bad = self.threads / "a"
        bad.mkdir()
        (bad / manifest.MANIFEST_NAME).write_text("{not json")
        (self.threads / "not-a-thread").mkdir()
        found = manifest.discover(self.threads)
        self.assertEqual([f.name for f in found], ["a", "b"])
        self.assertFalse(found[0].readable)
        self.assertTrue(found[1].readable)

    def test_newer_version_is_read_only(self):
        root = self.threads / "n"
        root.mkdir()
        data = manifest.Thread(name="n", root=root).to_dict()
        data["version"] = 99
        (root / manifest.MANIFEST_NAME).write_text(json.dumps(data))
        loaded = manifest.load(root)
        self.assertFalse(loaded.mutable)
        with self.assertRaises(manifest.ManifestError):
            loaded.save()

    def test_claims_and_find(self):
        one = manifest.Thread(name="one", root=self.threads / "one")
        one.worktrees.append(worktree(branch="feat/one"))
        two = manifest.Thread(name="two", root=self.threads / "two")
        two.worktrees.append(worktree(branch="feat/two"))
        claims = manifest.branch_claims([one, two])
        self.assertEqual(claims[("/repos/alpha", "feat/one")], "one")
        self.assertIs(manifest.find([one, two], "TWO"), two)
        self.assertIsNone(manifest.find([one, two], "three"))

    def test_malformed_entries_raise(self):
        root = self.threads / "m"
        root.mkdir()
        (root / manifest.MANIFEST_NAME).write_text(
            json.dumps({"version": 1, "thread": "m", "worktrees": [{"folder": "x"}]})
        )
        with self.assertRaises(manifest.ManifestError):
            manifest.load(root)


class LegacyFeatureManifest(unittest.TestCase):
    def test_feature_json_is_migrated_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "pay-1"
            root.mkdir()
            legacy = root / manifest.LEGACY_MANIFEST_NAME
            legacy.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "feature": "pay-1",
                        "status": "ready",
                        "workspace": {"id": "w1", "label": "pay-1"},
                        "worktrees": [],
                    }
                )
            )
            found = manifest.discover(Path(tmp))
            self.assertEqual([t.name for t in found], ["pay-1"])
            self.assertEqual(found[0].version, manifest.VERSION)
            self.assertTrue((root / manifest.MANIFEST_NAME).exists(), "rewritten under the new name")
            self.assertFalse(legacy.exists(), "legacy file removed")
            reloaded = manifest.load(root)
            self.assertEqual((reloaded.name, reloaded.workspace["id"]), ("pay-1", "w1"))

    def test_interrupted_legacy_manifest_is_read_but_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "x"
            root.mkdir()
            legacy = root / manifest.LEGACY_MANIFEST_NAME
            legacy.write_text(
                json.dumps({"version": 1, "feature": "x", "status": "creating", "worktrees": []})
            )
            thread = manifest.load(root)
            self.assertEqual(thread.status, manifest.STATUS_CREATING)
            self.assertTrue(legacy.exists())
