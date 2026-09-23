"""new: create a thread from a set of repositories and open its workspace."""

from __future__ import annotations

from .. import herdr, manifest, names, ui
from ..config import Config
from ..lock import mutation_lock
from . import common


def run(config: Config) -> None:
    threads = common.load_threads(config)

    def validate(value: str) -> str | None:
        problem = names.validate_name(value)
        if problem:
            return problem
        existing = manifest.find(threads, value)
        if existing and existing.status == manifest.STATUS_READY:
            return f"a thread named {existing.name!r} already exists; use 'open' or 'add'."
        return None

    ui.heading("New thread")
    name = ui.prompt("thread name", validator=validate)
    root = config.threads_directory / name

    with mutation_lock():
        existing = manifest.find(threads, name)
        if existing is not None and existing.readable and existing.status == manifest.STATUS_CREATING:
            common.cleanup_interrupted(existing)
            threads = [f for f in threads if f is not existing]
        elif root.exists():
            raise ui.Abort(f"{root} already exists but is not a known thread. Move it away first.")

        repos = common.pick_repos(config)
        thread = manifest.Thread(name=name, root=root, branch_prefix=config.branch_prefix)
        requests = [common.Request(repo) for repo in repos]
        planned = common.preflight(config, thread, requests, threads)
        common.show_plan(planned)
        if not ui.confirm(f"Create thread {name!r} with {len(planned)} worktree(s)?", default=True):
            raise ui.Cancelled()

        config.threads_directory.mkdir(parents=True, exist_ok=True)
        try:
            root.mkdir()
        except FileExistsError:
            raise ui.Abort(f"{root} appeared while planning; try again.") from None
        common.execute(thread, planned, is_new=True)

    ui.heading("Opening workspaces" if config.repo_workspaces else "Opening workspace")
    try:
        opened = common.open_workspaces(config, thread, focus=True)
    except herdr.HerdrError as error:
        ui.warn(f"the thread is on disk but its workspace could not be created:\n  {error}")
        ui.warn("use 'open' to try again.")
        ui.pause()
        return
    common.report_opened(thread, opened)
    if opened.failures:
        ui.warn("use 'open' to try the missing ones again.")
        ui.pause()
    print(f"\n{thread.name}: {len(planned)} worktree(s) ready.")
