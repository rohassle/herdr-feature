"""Entry point. The popup runs `python -m herdr_feature` with FEATURE_ACTION in the
environment (set by bin/action.sh); a command name may also be given as argv[1]."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

from . import gitops, manifest, ui
from .config import load_config

ACTIONS = ("menu", "new", "add", "open", "drop", "remove", "install-cli")


def preview_repo(path: str) -> int:
    repo = Path(path)
    print(f"{repo.name}  [{gitops.current_branch(repo)}]\n{repo}\n")
    print(gitops.recent_log(repo), end="")
    return 0


def preview_feature(root: str) -> int:
    try:
        feature = manifest.load(Path(root))
    except manifest.ManifestError as error:
        print(f"unreadable manifest: {error}")
        return 0
    print(f"{feature.name}  ({feature.status})\n{feature.root}\n")
    if feature.workspace:
        print(f"last workspace: {feature.workspace.get('id')}  seen {feature.workspace.get('seen_at')}\n")
    for worktree in feature.worktrees:
        state = gitops.worktree_state(feature.path_of(worktree))
        print(f"{worktree.folder}\n    {worktree.branch}  {state.detail}")
    if not feature.worktrees:
        print("(no worktrees)")
    return 0


def dispatch(action: str) -> None:
    from .commands import add, drop, install_cli, menu, new, open_, remove

    handlers = {
        "menu": menu.run,
        "new": new.run,
        "add": add.run,
        "open": open_.run,
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
    action = argv[1] if len(argv) > 1 else os.environ.get("FEATURE_ACTION", "menu")
    if action == "cli":
        from .cli import main as cli_main

        return cli_main(argv[2:])
    if action == "preview-repo" and len(argv) > 2:
        return preview_repo(argv[2])
    if action == "preview-feature" and len(argv) > 2:
        return preview_feature(argv[2])

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
        print("\nherdr-feature hit an unexpected error (details above).", file=sys.stderr)
        ui.pause()
        return 1
    finally:
        ui.restore_terminal()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
