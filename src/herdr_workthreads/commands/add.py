"""add: add worktrees from more repositories to an existing thread."""

from __future__ import annotations

from .. import herdr, names, ui
from ..config import Config
from ..lock import mutation_lock
from ..manifest import Thread
from . import common


def resolve_target(config: Config, threads: list[Thread], *, verb: str) -> tuple[Thread, dict[str, str]]:
    live = common.live_map(threads)
    current = herdr.current_thread(threads, herdr.context())
    if current is not None and current.mutable:
        return current, live
    thread = common.choose_thread(
        threads,
        live,
        prompt_text="thread> ",
        header=f"Choose the thread to {verb}",
        only_mutable=True,
        repo_live=common.repo_live_map(threads),
    )
    return thread, live


def run(config: Config, thread: Thread | None = None) -> None:
    threads = common.load_threads(config)
    if thread is not None:
        thread = next((f for f in threads if f.root == thread.root), thread)
        if not thread.mutable:
            raise ui.Abort(f"{thread.name} cannot be changed right now ({common.status_word(thread, {})}).")
        live = common.live_map(threads)
    else:
        thread, live = resolve_target(config, threads, verb="add worktrees to")
    ui.heading(f"Add worktrees to {thread.name}")
    others = [f for f in threads if f is not thread]

    with mutation_lock():
        repos = common.pick_repos(config, thread)
        requests = []
        for repo in repos:
            suffix = None
            if thread.worktrees_for(repo.path):
                used = {wt.suffix for wt in thread.worktrees_for(repo.path)}
                ui.warn(
                    f"{repo.name} is already in this thread as "
                    + ", ".join(wt.folder for wt in thread.worktrees_for(repo.path))
                )

                def validate(value: str, used=used, repo=repo) -> str | None:
                    problem = names.validate_name(value, "suffix")
                    if problem:
                        return problem
                    if value in used:
                        return f"{repo.name}@{value} already exists in this thread."
                    return None

                suffix = ui.prompt(f"suffix for the extra {repo.name} worktree", validator=validate)
            requests.append(common.Request(repo, suffix))

        planned = common.preflight(config, thread, requests, others)
        common.show_plan(planned)
        if not ui.confirm(f"Add {len(planned)} worktree(s) to {thread.name!r}?", default=True):
            raise ui.Cancelled()
        added = common.execute(thread, planned, is_new=False)

    print(f"\n{thread.name}: added {len(planned)} worktree(s).")
    is_open = thread.name in live or bool(herdr.repo_workspaces(thread))
    if is_open:
        if config.repo_workspaces:
            ui.heading("Opening worktree workspaces")
            opened = common.open_workspaces(
                config, thread, focus=False, workspace_id=live.get(thread.name), only=added
            )
            common.report_opened(thread, opened)
            ui.pause()
        return
    if ui.confirm("The thread has no open workspace. Open it now?", default=True):
        opened = common.open_workspaces(config, thread, focus=True)
        common.report_opened(thread, opened)
        if opened.failures:
            ui.pause()
        return
    ui.pause()
