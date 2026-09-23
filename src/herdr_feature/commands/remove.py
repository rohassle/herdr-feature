"""remove: delete a feature's worktrees and folder, close its workspace, optionally
delete the branches this plugin created."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import gitops, herdr, manifest, ui
from ..config import Config
from ..lock import mutation_lock
from ..manifest import Feature
from . import common


def run(config: Config, feature: Feature | None = None, *, delete_branches_default: bool = False) -> None:
    features = common.load_features(config)
    if not features:
        raise ui.Abort(f"No features under {config.features_directory}.")
    live = common.live_map(features)
    repo_live = common.repo_live_map(features)
    if feature is not None:
        feature = next((f for f in features if f.root == feature.root), feature)
    else:
        feature = common.choose_feature(
            features,
            live,
            prompt_text="remove> ",
            header="Enter: choose the feature to remove   Esc: cancel",
            repo_live=repo_live,
        )
    if not feature.readable:
        raise ui.Abort(
            f"{feature.name} has an unreadable manifest ({feature.error}), so its worktrees are\n"
            f"unknown. Inspect {feature.root} and remove it by hand."
        )
    if feature.version > manifest.VERSION:
        raise ui.Abort(f"{feature.name} was written by a newer herdr-feature; upgrade the plugin first.")

    ui.heading(f"Remove {feature.name}")
    states = {wt.folder: gitops.worktree_state(feature.path_of(wt)) for wt in feature.worktrees}
    for worktree in feature.worktrees:
        progress = common.worktree_progress(worktree)
        ui.step(
            f"{worktree.folder:<32} {worktree.branch:<32} {progress.detail:<22} {states[worktree.folder].detail}"
        )
    if not feature.worktrees:
        ui.step("(no worktrees)")

    workspace_id = live.get(feature.name)
    nested = repo_live.get(feature.name, {})
    with mutation_lock():
        if workspace_id or nested:
            busy = herdr.busy_panes(*filter(None, [workspace_id, *nested.values()]))
            if busy:
                ui.warn(f"the feature's workspaces have {len(busy)} agent(s) still working or waiting:")
                for pane in busy:
                    ui.step(f"{pane['pane_id']:<8} {pane.get('agent') or ''} {pane.get('agent_status')}")
                if not ui.confirm(
                    "Close the workspaces anyway? Running processes will be killed.", default=False
                ):
                    raise ui.Cancelled()

        careful = [wt for wt in feature.worktrees if states[wt.folder].needs_care]
        print(file=ui.OUT)
        if careful:
            ui.warn("some worktrees hold uncommitted or unpushed work; it will be lost.")
            if not ui.confirm_typed(feature.name, "Remove every worktree and the feature folder?"):
                raise ui.Cancelled()
        elif not ui.confirm(f"Remove every worktree and the folder of {feature.name!r}?", default=False):
            raise ui.Cancelled()

        if workspace_id or nested:
            ui.heading("Closing workspaces")
            for closed_id in common.close_feature_workspaces(feature, live, repo_live):
                ui.ok(f"closed {closed_id}")

        ui.heading("Removing worktrees")
        entries = list(feature.worktrees)
        for worktree in entries:
            note = gitops.worktree_remove(Path(worktree.repo_path), feature.path_of(worktree))
            feature.worktrees.remove(worktree)
            ui.ok(worktree.folder + (f" ({note})" if note else ""))
        shutil.rmtree(feature.root, ignore_errors=True)
        ui.ok(f"removed {feature.root}")

    claims = manifest.branch_claims([f for f in features if f is not feature])
    deletable = [wt for wt in entries if wt.branch_created and (wt.repo_path, wt.branch) not in claims]
    kept = [wt for wt in entries if wt not in deletable]
    if kept:
        ui.heading("Branches kept (not created by this plugin, or shared with another feature)")
        for worktree in kept:
            ui.step(f"{worktree.repo_name:<32} {worktree.branch}")
    if deletable:
        ui.heading("Branches this feature created")
        for worktree in deletable:
            ui.step(f"{worktree.repo_name:<32} {worktree.branch}")
        if ui.confirm(
            "Delete these local branches too? (remote branches are never touched)",
            default=delete_branches_default,
        ):
            for worktree in deletable:
                problem = gitops.branch_delete(Path(worktree.repo_path), worktree.branch)
                if problem:
                    ui.fail(f"{worktree.repo_name}: {problem}")
                else:
                    ui.ok(f"{worktree.repo_name}: deleted {worktree.branch}")
    print(f"\n{feature.name} removed.")
    ui.pause()
