import tempfile
import unittest
from pathlib import Path

from .helpers import SRC, git, make_repo  # noqa: F401
from herdr_feature import gitops


PORCELAIN = """worktree /repos/alpha
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /features/x/alpha
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feat/x
locked

worktree /gone
HEAD 3333333333333333333333333333333333333333
detached
prunable gitdir file points to non-existent location
"""


class Parsers(unittest.TestCase):
    def test_parse_worktree_list(self):
        entries = gitops.parse_worktree_list(PORCELAIN)
        self.assertEqual([e.path for e in entries], ["/repos/alpha", "/features/x/alpha", "/gone"])
        self.assertEqual(entries[0].branch, "main")
        self.assertEqual(entries[1].branch, "feat/x")
        self.assertTrue(entries[1].locked)
        self.assertTrue(entries[2].detached)
        self.assertTrue(entries[2].prunable)

    def test_classify_fetch_error(self):
        self.assertEqual(gitops.classify_fetch_error("ssh: Could not resolve hostname x"), "network unreachable")
        self.assertEqual(gitops.classify_fetch_error("git@github.com: Permission denied (publickey)."), "authentication failed")
        self.assertEqual(gitops.classify_fetch_error("fatal: couldn't find remote ref main"), "default branch not on remote")
        self.assertEqual(gitops.classify_fetch_error("line1\nsomething odd"), "something odd")


class RealGit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.repo = make_repo(base / "alpha", origin=base / "alpha.git")
        self.noremote = make_repo(base / "solo", branch="master")
        self.target = base / "features" / "x"

    def tearDown(self):
        self.tmp.cleanup()

    def test_detect_base_with_and_without_remote(self):
        base = gitops.detect_base(self.repo)
        self.assertEqual((base.branch, base.remote, base.ref), ("main", "origin", "refs/remotes/origin/main"))
        solo = gitops.detect_base(self.noremote)
        self.assertEqual((solo.branch, solo.remote, solo.ref), ("master", None, "refs/heads/master"))

    def test_detect_base_when_origin_head_unset(self):
        git(self.repo, "remote", "set-head", "origin", "-d")
        base = gitops.detect_base(self.repo, allow_network=False)
        self.assertEqual(base.ref, "refs/remotes/origin/main")

    def test_new_branch_has_no_upstream_and_state_is_never_pushed(self):
        base = gitops.detect_base(self.repo)
        plan = gitops.plan_branch(self.repo, "feat/x", base)
        self.assertEqual((plan.source, plan.branch_created), ("new", True))
        path = self.target / "alpha"
        gitops.worktree_add(self.repo, path, plan)
        self.assertTrue(gitops.is_registered(self.repo, path))
        self.assertEqual(gitops.worktree_state(path).kind, "never-pushed")
        self.assertEqual(gitops.checked_out_at(self.repo, "feat/x"), str(path.resolve()))

    def test_remote_only_branch_becomes_tracking(self):
        git(self.repo, "branch", "feat/r")
        git(self.repo, "push", "-q", "origin", "feat/r")
        git(self.repo, "branch", "-D", "feat/r")
        base = gitops.detect_base(self.repo)
        plan = gitops.plan_branch(self.repo, "feat/r", base)
        self.assertEqual(plan.source, "origin")
        path = self.target / "alpha"
        gitops.worktree_add(self.repo, path, plan)
        self.assertEqual(gitops.worktree_state(path).kind, "clean")

    def test_local_branch_is_reused_and_dirty_state(self):
        git(self.repo, "branch", "feat/l")
        base = gitops.detect_base(self.repo)
        plan = gitops.plan_branch(self.repo, "feat/l", base)
        self.assertEqual((plan.source, plan.branch_created), ("local", False))
        path = self.target / "alpha"
        gitops.worktree_add(self.repo, path, plan)
        (path / "junk.txt").write_text("x")
        self.assertEqual(gitops.worktree_state(path).kind, "dirty")
        self.assertIsNone(gitops.worktree_remove(self.repo, path))
        self.assertFalse(path.exists())
        self.assertIsNone(gitops.branch_delete(self.repo, "feat/l"))

    def test_remove_after_manual_deletion_prunes(self):
        import shutil
        base = gitops.detect_base(self.repo)
        path = self.target / "alpha"
        gitops.worktree_add(self.repo, path, gitops.plan_branch(self.repo, "feat/m", base))
        shutil.rmtree(path)
        self.assertEqual(gitops.worktree_state(path).kind, "missing")
        gitops.worktree_remove(self.repo, path)  # git may or may not report a note here
        self.assertFalse(gitops.is_registered(self.repo, path))

    def test_fetch_failure_is_classified(self):
        git(self.repo, "remote", "set-url", "origin", str(Path(self.tmp.name) / "missing.git"))
        result = gitops.fetch(self.repo, "main")
        self.assertFalse(result.ok)
        self.assertTrue(result.reason)
