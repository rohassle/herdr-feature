import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import SRC  # noqa: F401
from herdr_feature import herdr, manifest


class Mapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.plugin_root = base / "plugin"
        self.plugin_root.mkdir()
        self.features = base / "features"
        self.one = manifest.Feature(name="one", root=self.features / "one")
        self.one.save()
        self.two = manifest.Feature(name="two", root=self.features / "two")
        self.two.remember_workspace("wOLD", "two")
        self.two.save()
        (self.one.root / "alpha").mkdir()
        self.env = mock.patch.dict(os.environ, {"HERDR_PLUGIN_ROOT": str(self.plugin_root)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_pane_cwd_wins_and_heals_hint(self):
        panes = [
            {
                "pane_id": "w1:p1",
                "workspace_id": "w1",
                "cwd": str(self.one.root / "alpha"),
                "foreground_cwd": None,
            },
            {"pane_id": "w1:p2", "workspace_id": "w1", "cwd": str(self.plugin_root), "foreground_cwd": None},
            {"pane_id": "w9:p1", "workspace_id": "w9", "cwd": "/elsewhere", "foreground_cwd": None},
        ]
        workspaces = [{"workspace_id": "w1", "label": "one"}, {"workspace_id": "w9", "label": "other"}]
        with (
            mock.patch.object(herdr, "panes", return_value=panes),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            live = herdr.map_live([self.one, self.two])
        self.assertEqual(live, {"one": "w1"})
        self.assertEqual(manifest.load(self.one.root).workspace["id"], "w1")

    def test_hint_used_when_label_matches(self):
        workspaces = [{"workspace_id": "wOLD", "label": "two"}]
        with (
            mock.patch.object(herdr, "panes", return_value=[]),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            live = herdr.map_live([self.two])
        self.assertEqual(live, {"two": "wOLD"})

    def test_stale_hint_with_different_label_is_ignored(self):
        workspaces = [{"workspace_id": "wOLD", "label": "something-else"}]
        with (
            mock.patch.object(herdr, "panes", return_value=[]),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            live = herdr.map_live([self.two])
        self.assertEqual(live, {})

    def test_label_fallback_only_when_unique(self):
        workspaces = [
            {"workspace_id": "wA", "label": "one"},
            {"workspace_id": "wB", "label": "one"},
            {"workspace_id": "wC", "label": "two"},
        ]
        with (
            mock.patch.object(herdr, "panes", return_value=[]),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            live = herdr.map_live([self.one, self.two], heal=False)
        self.assertEqual(live, {"two": "wC"})

    def test_current_feature_from_cwd(self):
        ctx = herdr.Context(
            workspace_id=None,
            workspace_cwd=str(self.one.root / "alpha"),
            focused_pane_cwd=None,
            workspace_label=None,
        )
        with (
            mock.patch.object(herdr, "panes", return_value=[]),
            mock.patch.object(herdr, "workspaces", return_value=[]),
        ):
            self.assertIs(herdr.current_feature([self.one, self.two], ctx), self.one)

    def test_error_parsing(self):
        completed = mock.Mock(returncode=1, stdout="", stderr='{"error":{"code":"ui_busy","message":"busy"}}')
        with mock.patch.object(herdr.subprocess, "run", return_value=completed):
            with self.assertRaises(herdr.HerdrError) as caught:
                herdr.call("workspace", "list")
        self.assertEqual(caught.exception.code, "ui_busy")
