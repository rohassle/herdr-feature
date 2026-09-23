"""remove: delete a feature's worktrees and folder, close its workspace, optionally
delete the branches this plugin created."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import gitops, herdr, manifest, ui
from ..config import Config
from ..lock import mutation_lock
from . import common


def run(config: Config) -> None:
    features = common.load_features(config)
    if not features:
        raise ui.Abort(f"No features under {config.features_directory}.")
    live = common.live_map(features)
    feature = common.choose_feature(
        features,
        live,
        prompt_text="remove> ",
        header="Enter: choose the feature to remove   Esc: cancel",
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
        ui.step(f"{worktree.folder:<40} {worktree.branch:<40} {states[worktree.folder].detail}")
    if not feature.worktrees:
        ui.step("(no worktrees)")

    workspace_id = live.get(feature.name)
    with mutation_lock():
        if workspace_id:
            busy = herdr.busy_panes(workspace_id)
            if busy:
                ui.warn(f"workspace {workspace_id} has {len(busy)} agent(s) still working or waiting:")
                for pane in busy:
                    ui.step(f"{pane['pane_id']:<8} {pane.get('agent') or ''} {pane.get('agent_status')}")
                if not ui.confirm(
                    "Close the workspace anyway? Running processes will be killed.", default=False
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

        if workspace_id:
            ui.heading("Closing workspace")
            herdr.close_workspace(workspace_id)
            ui.ok(f"closed {workspace_id}")

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
        if ui.confirm("Delete these local branches too? (remote branches are never touched)", default=False):
            for worktree in deletable:
                problem = gitops.branch_delete(Path(worktree.repo_path), worktree.branch)
                if problem:
                    ui.fail(f"{worktree.repo_name}: {problem}")
                else:
                    ui.ok(f"{worktree.repo_name}: deleted {worktree.branch}")
    print(f"\n{feature.name} removed.")
    ui.pause()
