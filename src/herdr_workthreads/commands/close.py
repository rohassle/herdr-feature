"""close: shut a thread's Herdr workspaces (thread workspace and nested worktree
workspaces). Files, worktrees and branches stay exactly as they are (ADR 0002)."""

from __future__ import annotations

from .. import herdr, ui
from ..config import Config
from ..manifest import Thread
from . import common


def run(config: Config, thread: Thread | None = None) -> None:
    threads = common.load_threads(config)
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    if thread is None:
        open_threads = [f for f in threads if f.name in live or repo_live.get(f.name)]
        if not open_threads:
            raise ui.Abort("No thread has an open workspace.")
        thread = common.choose_thread(
            open_threads,
            live,
            prompt_text="close> ",
            header="Enter: close this thread's workspaces (files are kept)   Esc: cancel",
            repo_live=repo_live,
        )
    ids = common.thread_workspace_ids(thread, live, repo_live)
    if not ids:
        ui.warn(f"{thread.name} has no open workspace.")
        ui.pause()
        return

    ui.heading(f"Close {thread.name}")
    for folder, workspace_id in repo_live.get(thread.name, {}).items():
        ui.step(f"{workspace_id:<8} {folder}")
    if live.get(thread.name):
        ui.step(f"{live[thread.name]:<8} (thread root)")
    busy = herdr.busy_panes(*ids)
    if busy:
        ui.warn(f"{len(busy)} agent(s) still working or waiting:")
        for pane in busy:
            ui.step(f"{pane['pane_id']:<8} {pane.get('agent') or ''} {pane.get('agent_status')}")
        if not ui.confirm("Close anyway? Running processes will be killed.", default=False):
            raise ui.Cancelled()
    elif not ui.confirm(
        f"Close {len(ids)} workspace(s) of {thread.name!r}? Worktrees and branches are kept.", default=True
    ):
        raise ui.Cancelled()

    closed = common.close_thread_workspaces(thread, live, repo_live)
    for workspace_id in closed:
        ui.ok(f"closed {workspace_id}")
    print(f"\n{thread.name}: {len(closed)} workspace(s) closed; files and branches kept. Reopen with Enter.")
    ui.pause()
