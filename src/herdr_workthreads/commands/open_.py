"""open: focus a thread's workspace, or create one for a thread that has none."""

from __future__ import annotations

from .. import manifest, ui
from ..config import Config
from . import common


def run(config: Config) -> None:
    threads = common.load_threads(config)
    if not threads:
        raise ui.Abort(f"No threads under {config.threads_directory}. Create one with 'new'.")
    live = common.live_map(threads)
    repo_live = common.repo_live_map(threads)
    thread = common.choose_thread(
        threads,
        live,
        prompt_text="open> ",
        header="Enter: focus or reopen   Esc: cancel",
        repo_live=repo_live,
    )
    if not thread.readable:
        raise ui.Abort(f"{thread.name} cannot be opened: {thread.error}")
    if thread.status == manifest.STATUS_CREATING:
        raise ui.Abort(
            f"{thread.name} was interrupted while being created. Run 'new' with the same name to clean it up, or 'remove'."
        )

    opened = common.open_workspaces(config, thread, focus=True, workspace_id=live.get(thread.name))
    if opened.created:
        common.report_opened(thread, opened)
        if opened.failures:
            ui.pause()
