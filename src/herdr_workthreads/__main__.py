"""Entry point. The popup runs `python -m herdr_workthreads` with WORKTHREADS_ACTION in the
environment (set by bin/action.sh); a command name may also be given as argv[1]."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from . import gitops, manifest, ui
from .config import load_config

ACTIONS = ("menu", "board", "new", "add", "open", "close", "drop", "remove", "install-cli")


def preview_repo(path: str) -> int:
    repo = Path(path)
    print(f"{repo.name}  [{gitops.current_branch(repo)}]\n{repo}\n")
    print(gitops.recent_log(repo), end="")
    return 0


def preview_thread(root: str) -> int:
    try:
        thread = manifest.load(Path(root))
    except manifest.ManifestError as error:
        print(f"unreadable manifest: {error}")
        return 0
    from .commands import common

    progress = common.thread_progress(thread)
    print(f"{thread.name}  ({'done' if progress.done else thread.status})  {progress.bar()}")
    print(f"{thread.root}\n")
    if thread.workspace:
        print(f"last workspace: {thread.workspace.get('id')}  seen {thread.workspace.get('seen_at')}\n")
    for worktree in thread.worktrees:
        state = gitops.worktree_state(thread.path_of(worktree))
        wp = common.worktree_progress(worktree)
        print(f"{worktree.folder}\n    {worktree.branch}\n    {wp.detail}  ·  {state.detail}")
        if wp.pr and wp.pr.url:
            print(f"    {wp.pr.url}")
        if wp.pr and wp.pr.title:
            print(f"    {wp.pr.title}")
    if not thread.worktrees:
        print("(no worktrees)")
    return 0


def dispatch(action: str) -> None:
    from .commands import add, board, close, drop, install_cli, new, open_, remove

    handlers = {
        "menu": board.run,
        "board": board.run,
        "new": new.run,
        "add": add.run,
        "open": open_.run,
        "close": close.run,
        "drop": drop.run,
        "remove": remove.run,
        "install-cli": install_cli.run,
    }
    if action not in handlers:
        raise ui.Abort(f"unknown command {action!r}. Expected one of: {', '.join(ACTIONS)}")
    if action == "install-cli":
        install_cli.run()  # needs no configuration
        return
    config = load_config()
    handlers[action](config)


def main(argv: list[str]) -> int:
    action = argv[1] if len(argv) > 1 else os.environ.get("WORKTHREADS_ACTION", "menu")
    if action == "cli":
        from .cli import main as cli_main

        return cli_main(argv[2:])
    if action == "preview-repo" and len(argv) > 2:
        return preview_repo(argv[2])
    if action == "preview-thread" and len(argv) > 2:
        return preview_thread(argv[2])

    try:
        dispatch(action)
    except ui.Cancelled:
        return 0
    except ui.Abort as error:
        ui.restore_terminal()
        print(f"\n{error}\n", file=sys.stderr)
        ui.pause()
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception:
        ui.restore_terminal()
        traceback.print_exc()
        print("\nherdr-workthreads hit an unexpected error (details above).", file=sys.stderr)
        ui.pause()
        return 1
    finally:
        ui.restore_terminal()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
