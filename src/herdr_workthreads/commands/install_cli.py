"""install-cli: put the `herdr-workthreads` command on PATH and optionally install the agent
skill. Both are symlinks into the plugin folder, so plugin updates are picked up and
`uninstall` only needs to remove two links."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .. import ui

CLI_NAME = "herdr-workthreads"
SKILL_NAME = "herdr-workthreads"


def plugin_root() -> Path:
    root = os.environ.get("HERDR_PLUGIN_ROOT")
    if root:
        return Path(root).resolve()
    return Path(__file__).resolve().parents[3]


def bin_dir() -> Path:
    return Path(os.environ.get("HERDR_WORKTHREADS_BIN_DIR", "~/.local/bin")).expanduser()


def skills_dir() -> Path:
    return Path(os.environ.get("HERDR_WORKTHREADS_SKILLS_DIR", "~/.claude/skills")).expanduser()


@dataclass
class LinkResult:
    link: Path
    target: Path
    action: str  # created | replaced | unchanged | skipped


def ensure_link(link: Path, target: Path, *, replace_foreign: bool) -> LinkResult:
    """Point `link` at `target`. Existing links to the target are left alone; links or
    files that are not ours are replaced only when allowed."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        try:
            if link.resolve() == target.resolve():
                return LinkResult(link, target, "unchanged")
        except OSError:
            pass
        link.unlink()
        link.symlink_to(target)
        return LinkResult(link, target, "replaced")
    if link.exists():
        if not replace_foreign:
            return LinkResult(link, target, "skipped")
        if link.is_dir():
            raise ui.Abort(f"{link} is a directory, not a symlink; move it away first.")
        link.unlink()
        link.symlink_to(target)
        return LinkResult(link, target, "replaced")
    link.symlink_to(target)
    return LinkResult(link, target, "created")


def on_path(folder: Path) -> bool:
    entries = os.environ.get("PATH", "").split(os.pathsep)
    wanted = folder.resolve()
    for entry in entries:
        try:
            if entry and Path(entry).resolve() == wanted:
                return True
        except OSError:
            continue
    return False


def install(*, with_skill: bool, replace_foreign: bool) -> dict:
    root = plugin_root()
    cli_target = root / "bin" / CLI_NAME
    if not cli_target.exists():
        raise ui.Abort(f"{cli_target} does not exist; is the plugin checkout complete?")
    cli = ensure_link(bin_dir() / CLI_NAME, cli_target, replace_foreign=replace_foreign)
    result = {
        "cli": {"link": str(cli.link), "target": str(cli.target), "action": cli.action},
        "bin_on_path": on_path(bin_dir()),
        "skill": None,
    }
    if with_skill:
        skill_target = root / "skills" / SKILL_NAME
        skill = ensure_link(skills_dir() / SKILL_NAME, skill_target, replace_foreign=replace_foreign)
        result["skill"] = {"link": str(skill.link), "target": str(skill.target), "action": skill.action}
    return result


def uninstall(*, with_skill: bool) -> dict:
    removed = []
    for link in [bin_dir() / CLI_NAME] + ([skills_dir() / SKILL_NAME] if with_skill else []):
        if link.is_symlink() and "herdr-workthreads" in str(link.resolve()):
            link.unlink()
            removed.append(str(link))
    return {"removed": removed}


def report(result: dict) -> None:
    cli = result["cli"]
    ui.ok(f"{cli['link']} -> {cli['target']} ({cli['action']})")
    if cli["action"] == "skipped":
        ui.warn(f"{cli['link']} exists and is not a symlink; left untouched. Remove it and rerun to replace.")
    if not result["bin_on_path"]:
        ui.warn(
            f"{Path(cli['link']).parent} is not on your PATH. Add it to your shell profile, e.g.\n"
            f'    export PATH="{Path(cli["link"]).parent}:$PATH"'
        )
    skill = result.get("skill")
    if skill:
        ui.ok(f"{skill['link']} -> {skill['target']} ({skill['action']})")


def run(config=None) -> None:
    """Popup flow: install the CLI, then offer the skill."""
    ui.heading("Install the herdr-workthreads command line")
    result = install(with_skill=False, replace_foreign=False)
    report(result)
    print(file=ui.OUT)
    if ui.confirm(
        "Also install the Claude Code skill so agents know these commands?", default=True, key="confirm"
    ):
        result = install(with_skill=True, replace_foreign=False)
        ui.ok(f"{result['skill']['link']} ({result['skill']['action']})")
    print(f"\nTry: {CLI_NAME} list", file=ui.OUT)
    ui.pause()
