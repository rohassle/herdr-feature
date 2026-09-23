"""menu: choose a command from a list. Runs the chosen command in this same popup."""

from __future__ import annotations

from .. import ui
from ..config import Config
from . import add, drop, install_cli, new, open_, remove

COMMANDS = [
    ("new", "New feature: pick repositories, create worktrees, open a workspace", new.run),
    ("add", "Add worktrees from more repositories to the current feature", add.run),
    ("open", "Open or focus an existing feature", open_.run),
    ("drop", "Drop individual worktrees from the current feature", drop.run),
    ("remove", "Remove a feature: worktrees, folder, workspace, optionally branches", remove.run),
    ("install-cli", "Put the herdr-feature command on PATH (for agents and scripts)", install_cli.run),
]


def run(config: Config) -> None:
    rows = [ui.encode_row(key, f"{key:<8}", text) for key, text, _ in COMMANDS]
    keys = ui.pick(rows, prompt_text="feature> ", header="Enter: choose   Esc: cancel")
    for key, _, handler in COMMANDS:
        if key == keys[0]:
            handler(config)
            return
    raise ui.Cancelled()
