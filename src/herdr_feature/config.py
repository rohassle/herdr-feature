"""User configuration: a TOML file in the plugin config dir."""

from __future__ import annotations

import os
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .ui import Abort, confirm, warn

DEFAULT_FEATURES_DIRECTORY = "~/.herdr/features"
KNOWN_KEYS = {"repo_directories", "repos", "features_directory", "branch_prefix"}

TEMPLATE = """# herdr-feature configuration
# Location: herdr plugin config-dir feature

# Folders scanned one level deep for git repositories.
repo_directories = ["~/code"]

# Extra repositories anywhere on disk.
repos = []

# Where feature roots are created. Each feature is a folder holding one worktree per
# repository; closing a workspace never deletes anything here.
features_directory = "~/.herdr/features"

# Prepended to every branch the plugin creates. May contain '/', e.g. "rh/" or "feat/".
branch_prefix = ""
"""


@dataclass
class Config:
    path: Path
    repo_directories: list[Path] = field(default_factory=list)
    repos: list[Path] = field(default_factory=list)
    features_directory: Path = Path(DEFAULT_FEATURES_DIRECTORY).expanduser()
    branch_prefix: str = ""
    warnings: list[str] = field(default_factory=list)


def config_path() -> Path:
    override = os.environ.get("HERDR_FEATURE_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if not base:
        base = str(Path.home() / ".config/herdr/plugins/config/feature")
    return Path(base) / "config.toml"


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser()


def _is_git_repo(path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _inside(child: Path, parent: Path) -> bool:
    try:
        return child.resolve().is_relative_to(parent.resolve())
    except OSError:
        return False


def parse_config(raw: dict, path: Path) -> Config:
    """Validate a parsed TOML table. Fatal problems raise Abort; soft ones are warnings."""
    config = Config(path=path)

    for key in raw:
        if key not in KNOWN_KEYS:
            config.warnings.append(f"unknown key '{key}' in {path.name} (ignored)")

    directories = raw.get("repo_directories", [])
    if not isinstance(directories, list) or not all(isinstance(item, str) for item in directories):
        raise Abort(f"{path}: repo_directories must be a list of strings.")
    for item in directories:
        folder = _expand(item)
        if not folder.is_dir():
            config.warnings.append(f"repo_directories entry {item} is not a folder (skipped)")
            continue
        config.repo_directories.append(folder)

    repos = raw.get("repos", [])
    if not isinstance(repos, list) or not all(isinstance(item, str) for item in repos):
        raise Abort(f"{path}: repos must be a list of strings.")
    for item in repos:
        repo = _expand(item)
        if not repo.is_dir() or not _is_git_repo(repo):
            config.warnings.append(f"repos entry {item} is not a git repository (skipped)")
            continue
        config.repos.append(repo)

    features_directory = raw.get("features_directory", DEFAULT_FEATURES_DIRECTORY)
    if not isinstance(features_directory, str) or not features_directory.strip():
        raise Abort(f"{path}: features_directory must be a non-empty string.")
    config.features_directory = _expand(features_directory)
    for folder in config.repo_directories:
        if config.features_directory.resolve() == folder.resolve():
            raise Abort(
                f"{path}: features_directory {features_directory} is also listed in "
                "repo_directories; features must live somewhere else."
            )
    for repo in config.repos + [
        child for folder in config.repo_directories for child in folder.iterdir() if child.is_dir()
    ]:
        if (repo / ".git").exists() and _inside(config.features_directory, repo):
            raise Abort(
                f"{path}: features_directory {features_directory} lies inside the repository "
                f"{repo}; pick a folder outside every repository."
            )

    prefix = raw.get("branch_prefix", "")
    if not isinstance(prefix, str):
        raise Abort(f"{path}: branch_prefix must be a string.")
    if prefix:
        probe = subprocess.run(
            ["git", "check-ref-format", "--branch", f"{prefix}x"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            raise Abort(f"{path}: branch_prefix {prefix!r} is not a valid branch name prefix.")
        if prefix[-1] not in "/-_.":
            config.warnings.append(
                f"branch_prefix {prefix!r} does not end in '/', '-', '_' or '.'; "
                f"branches will look like {prefix}feature-name"
            )
    config.branch_prefix = prefix

    if not config.repo_directories and not config.repos:
        raise Abort(
            f"{path} lists no usable repo_directories or repos.\n"
            "Add at least one folder that contains git repositories."
        )
    return config


def load_config(*, interactive: bool = True) -> Config:
    path = config_path()
    if not path.exists():
        message = f"No configuration yet at {path}\n\nExpected contents:\n\n" + TEMPLATE
        if interactive and confirm(f"{message}\nWrite this template there now?", default=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEMPLATE)
            raise Abort(f"Wrote {path}. Edit repo_directories if needed, then run the command again.")
        raise Abort(message)
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise Abort(f"{path} is not valid TOML: {error}") from error
    config = parse_config(raw, path)
    for message in config.warnings:
        warn(message)
    return config
