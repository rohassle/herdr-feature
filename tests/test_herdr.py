import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import SRC  # noqa: F401
from herdr_workthreads import herdr, manifest


class _Mode:
    def __init__(self, mode: str):
        self.thread_workspace = mode in ("thread", "both")
        self.repo_workspaces = mode in ("repos", "both")


def _entry(folder: str) -> manifest.Worktree:
    return manifest.Worktree(
        repo_name=folder,
        repo_path=f"/repos/{folder}",
        folder=folder,
        suffix=None,
        branch=f"feat-{folder}",
        branch_created=True,
        branch_source="new",
        base_ref=None,
        base_commit=None,
        remote=None,
    )


class Mapping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.plugin_root = base / "plugin"
        self.plugin_root.mkdir()
        self.threads = base / "threads"
        self.one = manifest.Thread(name="one", root=self.threads / "one")
        self.one.save()
        self.two = manifest.Thread(name="two", root=self.threads / "two")
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

    def test_current_thread_from_cwd(self):
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
            self.assertIs(herdr.current_thread([self.one, self.two], ctx), self.one)

    def test_nested_worktree_workspaces_are_not_the_thread_workspace(self):
        nested_checkout = str(self.one.root / "alpha")
        panes = [
            {"pane_id": "w5:p1", "workspace_id": "w5", "cwd": nested_checkout, "foreground_cwd": None},
        ]
        workspaces = [
            {
                "workspace_id": "w5",
                "label": "one",
                "worktree": {"checkout_path": nested_checkout, "is_linked_worktree": True},
            },
            {
                "workspace_id": "w6",
                "label": "alpha",
                "worktree": {"checkout_path": "/repos/alpha", "is_linked_worktree": False},
            },
        ]
        with (
            mock.patch.object(herdr, "panes", return_value=panes),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            self.assertEqual(herdr.map_live([self.one], heal=False), {})
            self.assertEqual(herdr.map_repo_live([self.one]), {})  # no manifest entry yet
            self.one.worktrees.append(_entry("alpha"))
            self.assertEqual(herdr.map_repo_live([self.one]), {"one": {"alpha": "w5"}})
            ctx = herdr.Context(
                workspace_id="w5", workspace_cwd=None, focused_pane_cwd=None, workspace_label=None
            )
            self.assertIs(herdr.current_thread([self.one, self.two], ctx), self.one)

    def test_thread_and_nested_workspaces_coexist(self):
        root = str(self.one.root)
        nested_checkout = str(self.one.root / "alpha")
        self.one.worktrees.append(_entry("alpha"))
        panes = [
            {"pane_id": "w5:p1", "workspace_id": "w5", "cwd": nested_checkout, "foreground_cwd": None},
            {"pane_id": "w7:p1", "workspace_id": "w7", "cwd": root, "foreground_cwd": None},
        ]
        workspaces = [
            {
                "workspace_id": "w5",
                "label": "one",
                "worktree": {"checkout_path": nested_checkout, "is_linked_worktree": True},
            },
            {"workspace_id": "w7", "label": "one"},
        ]
        with (
            mock.patch.object(herdr, "panes", return_value=panes),
            mock.patch.object(herdr, "workspaces", return_value=workspaces),
        ):
            self.assertEqual(herdr.map_live([self.one], heal=False), {"one": "w7"})
            self.assertEqual(herdr.repo_workspaces(self.one), {"alpha": "w5"})

    def test_open_thread_respects_mode_and_is_idempotent(self):
        self.one.worktrees.append(_entry("alpha"))
        self.one.worktrees.append(_entry("beta"))
        calls = []

        def fake_call(*args):
            calls.append(args)
            if args[:2] == ("workspace", "create"):
                return {"workspace": {"workspace_id": "wF", "label": "one"}}
            if args[:2] == ("worktree", "open"):
                folder = Path(args[args.index("--path") + 1]).name
                return {"workspace": {"workspace_id": f"w-{folder}"}, "already_open": False}
            raise AssertionError(args)

        existing = {"alpha": "w-alpha"}
        with (
            mock.patch.object(herdr, "call", side_effect=fake_call),
            mock.patch.object(herdr, "repo_workspaces", return_value=existing),
            mock.patch.object(herdr, "focus_workspace") as focus,
        ):
            opened = herdr.open_thread(_Mode("both"), self.one, focus=True)
            self.assertEqual(opened.workspace_id, "wF")
            self.assertEqual(opened.repo_workspaces, {"alpha": "w-alpha", "beta": "w-beta"})
            self.assertTrue(opened.created)
            focus.assert_called_once_with("wF")
            opened_paths = [a[a.index("--path") + 1] for a in calls if a[:2] == ("worktree", "open")]
            self.assertEqual(opened_paths, [str(self.one.root / "beta")])

            calls.clear()
            opened = herdr.open_thread(_Mode("repos"), self.one, focus=True, workspace_id=None)
            self.assertIsNone(opened.workspace_id)
            self.assertEqual(opened.any, "w-alpha")
            self.assertFalse(any(a[:2] == ("workspace", "create") for a in calls))

            calls.clear()
            opened = herdr.open_thread(_Mode("thread"), self.one, focus=False, workspace_id="wLIVE")
            self.assertEqual(opened.workspace_id, "wLIVE")
            self.assertEqual(calls, [])
            self.assertFalse(opened.created)

    def test_open_thread_collects_nested_failures(self):
        self.one.worktrees.append(_entry("alpha"))

        def fake_call(*args):
            raise herdr.HerdrError("worktree_not_found", "nope", args)

        with (
            mock.patch.object(herdr, "call", side_effect=fake_call),
            mock.patch.object(herdr, "repo_workspaces", return_value={}),
        ):
            opened = herdr.open_thread(_Mode("repos"), self.one, focus=False)
        self.assertEqual(opened.repo_workspaces, {})
        self.assertEqual(len(opened.failures), 1)
        self.assertIn("alpha", opened.failures[0])

    def test_error_parsing(self):
        completed = mock.Mock(returncode=1, stdout="", stderr='{"error":{"code":"ui_busy","message":"busy"}}')
        with mock.patch.object(herdr.subprocess, "run", return_value=completed):
            with self.assertRaises(herdr.HerdrError) as caught:
                herdr.call("workspace", "list")
        self.assertEqual(caught.exception.code, "ui_busy")
