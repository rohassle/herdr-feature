import tempfile
import unittest
from pathlib import Path

from .helpers import SRC, git, make_repo  # noqa: F401
from herdr_workthreads import discovery
from herdr_workthreads.config import Config


class Scanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        make_repo(self.work / "beta")
        make_repo(self.work / "Alpha")
        (self.work / "plain-folder").mkdir()
        (self.work / "notes.code-workspace").write_text("{}")
        (self.work / ".hidden").mkdir()
        (self.work / "worktrees").mkdir()
        make_repo(self.work / "worktrees" / "ignored")
        git(self.work / "beta", "worktree", "add", "-q", str(self.work / "beta-linked"), "-b", "linked")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_rules(self):
        config = Config(
            path=Path("x"), repo_directories=[self.work], repos=[self.work / "worktrees" / "ignored"]
        )
        found = discovery.scan(config)
        self.assertEqual([r.name for r in found], ["Alpha", "beta", "ignored"])
        self.assertNotIn("beta-linked", [r.name for r in found])
