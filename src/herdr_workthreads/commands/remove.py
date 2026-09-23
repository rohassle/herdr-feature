"""remove: delete a thread's worktrees and folder, close its workspace, optionally
delete the branches this plugin created."""

from __future__ import annotations

import shutil
from pathlib import Path

from .. import gitops, herdr, manifest, ui
from ..config import Config
from ..lock import mutation_lock
from ..manifest import Thread
from . import common


def run(config: Config, thread: Thread | None = None, *, delete_branches_default: bool = False) -> None:
    threads = common.load_threads(config)
    if not threads:
        raise ui.Abort(f"No threads under {config.threads_directory}.")
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    if thread is not None:
        thread = next((f for f in threads if f.root == thread.root), thread)
    else:
        thread = common.choose_thread(
            threads,
            live,
            prompt_text="remove> ",
            header="Enter: choose the thread to remove   Esc: cancel",
            repo_live=repo_live,
        )
    if not thread.readable:
        raise ui.Abort(
            f"{thread.name} has an unreadable manifest ({thread.error}), so its worktrees are\n"
            f"unknown. Inspect {thread.root} and remove it by hand."
        )
    if thread.version > manifest.VERSION:
        raise ui.Abort(f"{thread.name} was written by a newer herdr-workthreads; upgrade the plugin first.")

    ui.heading(f"Remove {thread.name}")
    states = {wt.folder: gitops.worktree_state(thread.path_of(wt)) for wt in thread.worktrees}
    for worktree in thread.worktrees:
        progress = common.worktree_progress(worktree)
        ui.step(
            f"{worktree.folder:<32} {worktree.branch:<32} {progress.detail:<22} {states[worktree.folder].detail}"
        )
    if not thread.worktrees:
        ui.step("(no worktrees)")

    workspace_id = live.get(thread.name)
    nested = repo_live.get(thread.name, {})
    with mutation_lock():
        if workspace_id or nested:
            busy = herdr.busy_panes(*filter(None, [workspace_id, *nested.values()]))
            if busy:
                ui.warn(f"the thread's workspaces have {len(busy)} agent(s) still working or waiting:")
                for pane in busy:
                    ui.step(f"{pane['pane_id']:<8} {pane.get('agent') or ''} {pane.get('agent_status')}")
                if not ui.confirm(
                    "Close the workspaces anyway? Running processes will be killed.", default=False
                ):
                    raise ui.Cancelled()

        careful = [wt for wt in thread.worktrees if states[wt.folder].needs_care]
        print(file=ui.OUT)
        if careful:
            ui.warn("some worktrees hold uncommitted or unpushed work; it will be lost.")
            if not ui.confirm_typed(thread.name, "Remove every worktree and the thread folder?"):
                raise ui.Cancelled()
        elif not ui.confirm(f"Remove every worktree and the folder of {thread.name!r}?", default=False):
            raise ui.Cancelled()

        if workspace_id or nested:
            ui.heading("Closing workspaces")
            for closed_id in common.close_thread_workspaces(thread, live, repo_live):
                ui.ok(f"closed {closed_id}")

        ui.heading("Removing worktrees")
        entries = list(thread.worktrees)
        for worktree in entries:
            note = gitops.worktree_remove(Path(worktree.repo_path), thread.path_of(worktree))
            thread.worktrees.remove(worktree)
            ui.ok(worktree.folder + (f" ({note})" if note else ""))
        shutil.rmtree(thread.root, ignore_errors=True)
        ui.ok(f"removed {thread.root}")

    claims = manifest.branch_claims([f for f in threads if f is not thread])
    deletable = [wt for wt in entries if wt.branch_created and (wt.repo_path, wt.branch) not in claims]
    kept = [wt for wt in entries if wt not in deletable]
    if kept:
        ui.heading("Branches kept (not created by this plugin, or shared with another thread)")
        for worktree in kept:
            ui.step(f"{worktree.repo_name:<32} {worktree.branch}")
    if deletable:
        ui.heading("Branches this thread created")
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
    print(f"\n{thread.name} removed.")
    ui.pause()
