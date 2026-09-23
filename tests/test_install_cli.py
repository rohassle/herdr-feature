import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import SRC  # noqa: F401
from herdr_feature.commands import install_cli


class InstallCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "plugin"
        (self.root / "bin").mkdir(parents=True)
        (self.root / "bin" / "herdr-feature").write_text("#!/bin/sh\n")
        (self.root / "skills" / "herdr-feature").mkdir(parents=True)
        self.env = mock.patch.dict(os.environ, {
            "HERDR_PLUGIN_ROOT": str(self.root),
            "HERDR_FEATURE_BIN_DIR": str(base / "bin"),
            "HERDR_FEATURE_SKILLS_DIR": str(base / "skills"),
            "PATH": f"{base / 'bin'}:/usr/bin",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_install_is_idempotent_and_uninstall_removes(self):
        first = install_cli.install(with_skill=True, replace_foreign=False)
        self.assertEqual((first["cli"]["action"], first["skill"]["action"]), ("created", "created"))
        self.assertTrue(first["bin_on_path"])
        second = install_cli.install(with_skill=True, replace_foreign=False)
        self.assertEqual(second["cli"]["action"], "unchanged")
        link = Path(first["cli"]["link"])
        self.assertTrue(link.is_symlink() and link.resolve() == (self.root / "bin" / "herdr-feature").resolve())
        removed = install_cli.uninstall(with_skill=True)["removed"]
        self.assertEqual(len(removed), 2)
        self.assertFalse(link.exists())

    def test_foreign_file_is_skipped_unless_replace(self):
        link = Path(os.environ["HERDR_FEATURE_BIN_DIR"]) / "herdr-feature"
        link.parent.mkdir(parents=True)
        link.write_text("not ours")
        self.assertEqual(install_cli.install(with_skill=False, replace_foreign=False)["cli"]["action"], "skipped")
        self.assertEqual(install_cli.install(with_skill=False, replace_foreign=True)["cli"]["action"], "replaced")
        self.assertTrue(link.is_symlink())
