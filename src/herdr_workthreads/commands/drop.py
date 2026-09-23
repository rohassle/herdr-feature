"""drop: remove individual worktrees from a thread. Branches are kept."""

from __future__ import annotations

from pathlib import Path

from .. import gitops, herdr, ui
from ..config import Config
from ..lock import mutation_lock
from ..manifest import Thread
from . import common
from .add import resolve_target


def run(config: Config, thread: Thread | None = None) -> None:
    threads = common.load_threads(config)
    if thread is not None:
        thread = next((f for f in threads if f.root == thread.root), thread)
        if not thread.mutable:
            raise ui.Abort(f"{thread.name} cannot be changed right now.")
    else:
        thread, _ = resolve_target(config, threads, verb="drop worktrees from")
    if not thread.worktrees:
        raise ui.Abort(f"{thread.name} has no worktrees to drop.")
    ui.heading(f"Drop worktrees from {thread.name}")

    with mutation_lock():
        rows = []
        states = {}
        for worktree in thread.worktrees:
            state = gitops.worktree_state(thread.path_of(worktree))
            states[worktree.folder] = state
            rows.append(
                ui.encode_row(
                    worktree.folder, f"{worktree.folder:<40}", f"{worktree.branch:<40}", state.detail
                )
            )
        keys = ui.pick(
            rows, prompt_text="drop> ", header="Tab: mark   Enter: confirm   Esc: cancel", multi=True
        )
        chosen = [wt for wt in thread.worktrees if wt.folder in set(keys)]
        if not chosen:
            raise ui.Cancelled()

        try:
            all_panes = herdr.panes()
        except herdr.HerdrError:
            all_panes = []
        affected = []
        for worktree in chosen:
            for pane in herdr.panes_inside(thread.path_of(worktree), all_panes):
                affected.append((worktree, pane))
        if affected:
            ui.warn("these panes are working inside worktrees you are about to drop:")
            for worktree, pane in affected:
                title = pane.get("terminal_title_stripped") or pane.get("terminal_title") or ""
                ui.step(f"{pane['pane_id']:<8} {worktree.folder:<32} {title}")
            if not ui.confirm("Drop anyway? Their shells will be left in a deleted folder.", default=False):
                raise ui.Cancelled()

        careful = [wt for wt in chosen if states[wt.folder].needs_care]
        if careful:
            ui.warn("these worktrees have work that exists nowhere else:")
            for worktree in careful:
                ui.step(f"{worktree.folder:<40} {states[worktree.folder].detail}")
            if not ui.confirm_typed(thread.name, "Uncommitted or unpushed work will be lost."):
                raise ui.Cancelled()
        elif not ui.confirm(
            f"Drop {len(chosen)} worktree(s) from {thread.name!r}? Branches are kept.", default=True
        ):
            raise ui.Cancelled()

        ui.heading("Dropping")
        for workspace_id in herdr.close_repo_workspaces(thread, chosen):
            ui.ok(f"closed worktree workspace {workspace_id}")
        for worktree in chosen:
            note = gitops.worktree_remove(Path(worktree.repo_path), thread.path_of(worktree))
            thread.worktrees.remove(worktree)
            thread.save()
            ui.ok(f"{worktree.folder}" + (f" ({note})" if note else "") + f"; branch {worktree.branch} kept")

    print(f"\n{thread.name}: {len(thread.worktrees)} worktree(s) remain.")
    ui.pause()
