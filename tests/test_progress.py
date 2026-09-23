import unittest
from datetime import UTC, datetime
from pathlib import Path

from .helpers import SRC  # noqa: F401
from herdr_feature import manifest
from herdr_feature.commands import common


def entry(folder: str, pr: dict | None) -> manifest.Worktree:
    return manifest.Worktree(
        repo_name=folder,
        repo_path=f"/r/{folder}",
        folder=folder,
        suffix=None,
        branch=f"feat-{folder}",
        branch_created=True,
        branch_source="new",
        base_ref="refs/remotes/origin/main",
        base_commit=None,
        remote="origin",
        pr=pr,
    )


def pr(state: str | None, **extra) -> dict:
    base = {"number": 1 if state else None, "url": "u", "state": state, "checked_at": "2026-09-23T10:00:00Z"}
    return {**base, **extra}


class WorktreeProgress(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(common.worktree_progress(entry("a", None)).kind, common.PROGRESS_UNKNOWN)
        self.assertEqual(common.worktree_progress(entry("a", pr(None, error="offline"))).detail, "offline")
        self.assertEqual(common.worktree_progress(entry("a", pr(None))).kind, common.PROGRESS_NO_PR)
        merged = common.worktree_progress(entry("a", pr("MERGED")))
        self.assertTrue(merged.merged)
        self.assertEqual(merged.detail, "#1 merged")
        self.assertEqual(common.worktree_progress(entry("a", pr("CLOSED"))).kind, common.PROGRESS_CLOSED_PR)
        open_pr = common.worktree_progress(entry("a", pr("OPEN", review="CHANGES_REQUESTED")))
        self.assertEqual(
            (open_pr.kind, open_pr.detail), (common.PROGRESS_OPEN_PR, "#1 open · changes requested")
        )
        self.assertEqual(common.worktree_progress(entry("a", pr("OPEN", draft=True))).detail, "#1 draft")


class FeatureProgress(unittest.TestCase):
    def feature(self, *prs):
        feature = manifest.Feature(name="f", root=Path("/tmp/f"))
        feature.worktrees = [entry(f"w{i}", p) for i, p in enumerate(prs)]
        return feature

    def test_counts_and_done(self):
        progress = common.feature_progress(self.feature(pr("MERGED"), pr("OPEN"), None))
        self.assertEqual((progress.merged, progress.total, progress.unknown), (1, 3, 1))
        self.assertFalse(progress.done)
        self.assertTrue(common.feature_progress(self.feature(pr("MERGED"), pr("MERGED"))).done)
        self.assertFalse(common.feature_progress(self.feature()).done)

    def test_bar(self):
        self.assertEqual(common.FeatureProgress(2, 4, 0).bar(width=8), "████░░░░ 2/4")
        self.assertEqual(common.FeatureProgress(4, 4, 0).bar(width=8), "████████ 4/4")
        self.assertEqual(common.FeatureProgress(0, 0, 0).bar(width=4), "░░░░ 0/0")
        # Unknown entries show as '?' at the tail, never overwriting merged cells.
        self.assertEqual(common.FeatureProgress(1, 2, 1).bar(width=4), "██?? 1/2")
        self.assertEqual(common.FeatureProgress(0, 1, 1).bar(width=4), "???? 0/1")

    def test_age(self):
        now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        self.assertEqual(common.age(None), "never")
        self.assertEqual(common.age("2026-09-23T11:59:30Z", now=now), "just now")
        self.assertEqual(common.age("2026-09-23T11:30:00Z", now=now), "30m ago")
        self.assertEqual(common.age("2026-09-23T09:00:00Z", now=now), "3h ago")
        self.assertEqual(common.age("2026-09-20T12:00:00Z", now=now), "3d ago")
        self.assertEqual(common.age("garbage"), "unknown")
