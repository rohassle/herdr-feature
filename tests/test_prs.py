import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import SRC  # noqa: F401
from herdr_feature import manifest, prs
from herdr_feature.ui import Abort

OPEN = '[{"number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN", "isDraft": false, "mergedAt": null, "reviewDecision": "APPROVED", "title": "Retry payments"}]'
MERGED = '[{"number": 7, "url": "https://github.com/o/r/pull/7", "state": "MERGED", "isDraft": false, "mergedAt": "2026-09-20T10:00:00Z", "reviewDecision": "", "title": "Done"}]'
DRAFT = '[{"number": 3, "url": "u", "state": "OPEN", "isDraft": true, "mergedAt": null, "reviewDecision": "", "title": "wip"}]'


class Parsing(unittest.TestCase):
    def test_states(self):
        pr = prs.parse_lookup(OPEN, "", 0)
        self.assertEqual((pr.number, pr.state, pr.review, pr.draft), (12, "OPEN", "APPROVED", False))
        self.assertFalse(pr.merged)
        self.assertTrue(pr.checked_at)
        pr = prs.parse_lookup(MERGED, "", 0)
        self.assertTrue(pr.merged)
        self.assertEqual(pr.merged_at, "2026-09-20T10:00:00Z")
        pr = prs.parse_lookup(DRAFT, "", 0)
        self.assertTrue(pr.draft)
        self.assertIsNone(pr.review)

    def test_no_pr_and_errors(self):
        pr = prs.parse_lookup("[]", "", 0)
        self.assertIsNone(pr.number)
        self.assertIsNone(pr.error)
        pr = prs.parse_lookup(
            "", "none of the git remotes configured for this repository point to a known GitHub host", 1
        )
        self.assertIn("GitHub host", pr.error)
        pr = prs.parse_lookup("not json", "", 0)
        self.assertIn("unreadable", pr.error)

    def test_round_trip_through_manifest(self):
        pr = prs.parse_lookup(OPEN, "", 0)
        restored = prs.from_dict(pr.to_dict())
        self.assertEqual(restored, pr)
        self.assertEqual(prs.from_dict({**pr.to_dict(), "future_field": 1}), pr)
        self.assertIsNone(prs.from_dict(None))

    def test_gh_binary_override_and_missing(self):
        with mock.patch.dict(os.environ, {"HERDR_FEATURE_GH": "/nonexistent/gh"}):
            with self.assertRaises(Abort):
                prs.gh_binary()
        with (
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(prs.shutil, "which", return_value=None),
            mock.patch.object(prs.os, "access", return_value=False),
        ):
            os.environ.pop("HERDR_FEATURE_GH", None)
            with self.assertRaises(Abort) as caught:
                prs.gh_binary()
        self.assertIn("gh was not found", str(caught.exception))


class Lookup(unittest.TestCase):
    def test_lookup_all_stores_results_and_refresh_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            feature = manifest.Feature(name="f", root=Path(tmp) / "f")
            feature.worktrees.append(
                manifest.Worktree(
                    repo_name="a",
                    repo_path="/r/a",
                    folder="a",
                    suffix=None,
                    branch="f",
                    branch_created=True,
                    branch_source="new",
                    base_ref=None,
                    base_commit=None,
                    remote=None,
                )
            )
            feature.save()
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                if cmd[1:3] == ["auth", "status"]:
                    return mock.Mock(returncode=0, stdout="", stderr="")
                return mock.Mock(returncode=0, stdout=MERGED, stderr="")

            with (
                mock.patch.object(prs, "gh_binary", return_value="gh"),
                mock.patch.object(prs.subprocess, "run", side_effect=fake_run),
            ):
                results = prs.refresh([feature])
            self.assertEqual(len(results), 1)
            self.assertEqual(calls[1][1:5], ["pr", "list", "--head", "f"])
            reloaded = manifest.load(feature.root)
            self.assertEqual(reloaded.worktrees[0].pr["state"], "MERGED")
            self.assertEqual(prs.last_checked([reloaded]), reloaded.worktrees[0].pr["checked_at"])

    def test_refresh_requires_login(self):
        with (
            mock.patch.object(prs, "gh_binary", return_value="gh"),
            mock.patch.object(
                prs.subprocess,
                "run",
                return_value=mock.Mock(
                    returncode=1, stdout="", stderr="You are not logged into any GitHub hosts."
                ),
            ),
        ):
            with self.assertRaises(Abort) as caught:
                prs.refresh([])
        self.assertIn("not logged in", str(caught.exception))
