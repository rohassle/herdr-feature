import os
import unittest
from unittest import mock

from .helpers import SRC  # noqa: F401
from herdr_feature import ui


class Rows(unittest.TestCase):
    def test_encode_row_strips_tabs_and_newlines(self):
        self.assertEqual(ui.encode_row("k", "a\tb", "c\nd"), "k\ta b\tc d")

    def test_preview_command_targets_first_column(self):
        self.assertTrue(ui.preview_command("preview-repo").endswith("preview-repo {1}"))


class ScriptedInputs(unittest.TestCase):
    def setUp(self):
        ui._scripted = ui._UNSET

    def tearDown(self):
        ui._scripted = ui._UNSET

    def test_prompt_confirm_choose_from_env(self):
        with mock.patch.dict(os.environ, {"HERDR_FEATURE_INPUTS": "bad name\ngood\n\nc\nfeat"}):
            self.assertEqual(ui.prompt("name", validator=lambda v: "nope" if " " in v else None), "good")
            self.assertFalse(ui.confirm("q?"))
            self.assertEqual(ui.choose("q", {"c": "cont", "a": "abort"}, default="a"), "c")
            self.assertTrue(ui.confirm_typed("feat", "sure?"))
            with self.assertRaises(ui.Abort):
                ui.prompt("more")

    def test_pick_exit_codes(self):
        with mock.patch.object(ui, "find_fzf", return_value="/bin/fzf"):
            for code in (1, 130):
                with mock.patch.object(
                    ui.subprocess, "run", return_value=mock.Mock(returncode=code, stdout="")
                ):
                    with self.assertRaises(ui.Cancelled):
                        ui.pick(["k\tv"], prompt_text="> ")
            with mock.patch.object(
                ui.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="k1\nk2\n")
            ):
                self.assertEqual(ui.pick(["k1\ta", "k2\tb"], prompt_text="> ", multi=True), ["k1", "k2"])
            with mock.patch.object(ui.subprocess, "run", return_value=mock.Mock(returncode=2, stdout="")):
                with self.assertRaises(ui.Abort):
                    ui.pick(["k\tv"], prompt_text="> ")
