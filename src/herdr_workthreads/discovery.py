"""Find the repositories the user may pick from."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config

SKIP_NAMES = {"worktrees", "threads"}


@dataclass(frozen=True, order=True)
class Repo:
    name: str
    path: Path


def is_primary_checkout(path: Path) -> bool:
    """True for a repository's main working tree. Linked worktrees have a `.git` file."""
    return (path / ".git").is_dir()


def scan_folder(folder: Path) -> list[Repo]:
    found = []
    try:
        children = sorted(folder.iterdir())
    except OSError:
        return found
    for child in children:
        if not child.is_dir() or child.name.startswith(".") or child.name in SKIP_NAMES:
            continue
        if is_primary_checkout(child):
            found.append(Repo(name=child.name, path=child))
    return found


def scan(config: Config) -> list[Repo]:
    seen: dict[Path, Repo] = {}
    for folder in config.repo_directories:
        for repo in scan_folder(folder):
            seen.setdefault(repo.path.resolve(), repo)
    for path in config.repos:
        seen.setdefault(path.resolve(), Repo(name=path.name, path=path))
    return sorted(seen.values(), key=lambda repo: (repo.name.casefold(), str(repo.path)))
