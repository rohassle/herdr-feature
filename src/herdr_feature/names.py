"""Feature, suffix, branch and folder naming rules. Pure functions, no I/O except
`check_ref_format`, which shells out to git."""

from __future__ import annotations

import re
import subprocess

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def validate_name(value: str, what: str = "feature name") -> str | None:
    """Return an error message, or None when `value` is acceptable."""
    if not value:
        return f"{what} is empty."
    if not NAME_RE.match(value):
        return (
            f"{what} '{value}' must start with a letter or digit and contain only "
            "letters, digits, '.', '_' and '-' (max 80 characters)."
        )
    if ".." in value:
        return f"{what} '{value}' must not contain '..'."
    if value.endswith(".lock"):
        return f"{what} '{value}' must not end in '.lock'."
    return None


def branch_for(prefix: str, feature: str, suffix: str | None = None) -> str:
    branch = f"{prefix}{feature}"
    if suffix:
        branch = f"{branch}-{suffix}"
    return branch


def folder_for(repo_name: str, suffix: str | None = None) -> str:
    return f"{repo_name}@{suffix}" if suffix else repo_name


def same_name(left: str, right: str) -> bool:
    """Feature names collide case-insensitively: APFS folders and loose refs do."""
    return left.casefold() == right.casefold()


def check_ref_format(branch: str, git: str = "git") -> str | None:
    """Return git's objection to `branch` as a branch name, or None when valid."""
    result = subprocess.run(
        [git, "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return None
    detail = result.stderr.strip() or f"'{branch}' is not a valid branch name."
    return detail
