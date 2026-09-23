import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import SRC, make_repo  # noqa: F401
from herdr_workthreads import config as cfg
from herdr_workthreads.ui import Abort


class ConfigParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        (self.base / "work").mkdir()
        make_repo(self.base / "work" / "alpha")
        self.path = self.base / "config.toml"

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults_and_expansion(self):
        parsed = cfg.parse_config({"repo_directories": [str(self.base / "work")]}, self.path)
        self.assertEqual(parsed.repo_directories, [self.base / "work"])
        self.assertEqual(parsed.branch_prefix, "")
        self.assertEqual(parsed.threads_directory, Path("~/.herdr/workthreads").expanduser())
        self.assertEqual(parsed.warnings, [])

    def test_unknown_key_and_missing_dir_warn(self):
        parsed = cfg.parse_config(
            {"repo_directories": [str(self.base / "work"), str(self.base / "nope")], "bogus": 1},
            self.path,
        )
        self.assertEqual(len(parsed.warnings), 2)

    def test_threads_inside_repo_is_fatal(self):
        with self.assertRaises(Abort):
            cfg.parse_config(
                {
                    "repo_directories": [str(self.base / "work")],
                    "threads_directory": str(self.base / "work" / "alpha" / "threads"),
                },
                self.path,
            )

    def test_bad_prefix_is_fatal_and_odd_prefix_warns(self):
        with self.assertRaises(Abort):
            cfg.parse_config(
                {"repo_directories": [str(self.base / "work")], "branch_prefix": "a//"}, self.path
            )
        parsed = cfg.parse_config(
            {"repo_directories": [str(self.base / "work")], "branch_prefix": "feat"}, self.path
        )
        self.assertTrue(any("branch_prefix" in w for w in parsed.warnings))

    def test_workspaces_mode(self):
        base = {"repo_directories": [str(self.base / "work")]}
        parsed = cfg.parse_config(base, self.path)
        self.assertEqual(parsed.workspaces, "thread")
        self.assertTrue(parsed.thread_workspace)
        self.assertFalse(parsed.repo_workspaces)
        parsed = cfg.parse_config({**base, "workspaces": "both"}, self.path)
        self.assertTrue(parsed.thread_workspace and parsed.repo_workspaces)
        parsed = cfg.parse_config({**base, "workspaces": "repos"}, self.path)
        self.assertFalse(parsed.thread_workspace)
        self.assertTrue(parsed.repo_workspaces)
        with self.assertRaises(Abort):
            cfg.parse_config({**base, "workspaces": "nested"}, self.path)

    def test_legacy_keys_are_honoured_with_a_warning(self):
        parsed = cfg.parse_config(
            {
                "repo_directories": [str(self.base / "work")],
                "features_directory": str(self.base / "old"),
                "workspaces": "feature",
            },
            self.path,
        )
        self.assertEqual(parsed.threads_directory, self.base / "old")
        self.assertEqual(parsed.workspaces, "thread")
        self.assertEqual(len([w for w in parsed.warnings if "still honoured" in w]), 2)

    def test_no_repos_is_fatal(self):
        with self.assertRaises(Abort):
            cfg.parse_config({"repo_directories": []}, self.path)

    def test_config_path_override(self):
        with mock.patch.dict(os.environ, {"HERDR_WORKTHREADS_CONFIG": "/x/y.toml"}):
            self.assertEqual(cfg.config_path(), Path("/x/y.toml"))
        with mock.patch.dict(os.environ, {"HERDR_PLUGIN_CONFIG_DIR": "/cfg"}, clear=False):
            os.environ.pop("HERDR_WORKTHREADS_CONFIG", None)
            self.assertEqual(cfg.config_path(), Path("/cfg/config.toml"))
