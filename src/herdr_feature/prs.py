"""GitHub pull requests per worktree, looked up with the `gh` CLI.

A worktree is done when its pull request is merged; a feature is done when every
worktree is. GitHub access is a requirement, not an option (ADR 0008): without `gh`
the board still lists and opens features, but progress reads "unknown".

Lookups happen only on an explicit refresh and are cached in the manifest as each
worktree's `pr` object, so opening the board never touches the network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .manifest import Feature, Worktree, now
from .ui import Abort

GH_CANDIDATES = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "~/.local/bin/gh")
LOOKUP_TIMEOUT_SECONDS = 30
FIELDS = "number,url,state,isDraft,mergedAt,reviewDecision,title"

STATE_MERGED = "MERGED"
STATE_OPEN = "OPEN"
STATE_CLOSED = "CLOSED"


def gh_binary() -> str:
    override = os.environ.get("HERDR_FEATURE_GH")
    if override:
        if not os.access(override, os.X_OK):
            raise Abort(f"HERDR_FEATURE_GH={override} is not executable.")
        return override
    found = shutil.which("gh")
    if found:
        return found
    for candidate in GH_CANDIDATES:
        path = Path(candidate).expanduser()
        if os.access(path, os.X_OK):
            return str(path)
    raise Abort("gh was not found. Install the GitHub CLI (brew install gh) and run `gh auth login`.")


def check_auth() -> None:
    """Abort with gh's own words when nobody is logged in."""
    result = subprocess.run(
        [gh_binary(), "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
        timeout=LOOKUP_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise Abort("gh is not logged in: " + (detail[-1] if detail else "run `gh auth login`."))


@dataclass
class PullRequest:
    """One lookup result. `number` is None when the branch has no pull request;
    `error` is set when the lookup itself failed (offline, not a GitHub remote, ...)."""

    number: int | None = None
    url: str | None = None
    state: str | None = None  # OPEN | MERGED | CLOSED
    draft: bool = False
    review: str | None = None  # APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | ""
    title: str | None = None
    merged_at: str | None = None
    error: str | None = None
    checked_at: str = ""

    @property
    def merged(self) -> bool:
        return self.state == STATE_MERGED

    def to_dict(self) -> dict:
        return asdict(self)


def from_dict(data: dict | None) -> PullRequest | None:
    if not isinstance(data, dict):
        return None
    known = {field for field in PullRequest.__dataclass_fields__}
    return PullRequest(**{key: value for key, value in data.items() if key in known})


def parse_lookup(stdout: str, stderr: str, returncode: int) -> PullRequest:
    """Turn one `gh pr list --json` run into a PullRequest."""
    checked = now()
    if returncode != 0:
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        return PullRequest(error=lines[-1] if lines else "gh failed", checked_at=checked)
    try:
        items = json.loads(stdout or "[]")
    except json.JSONDecodeError as error:
        return PullRequest(error=f"unreadable gh output: {error}", checked_at=checked)
    if not isinstance(items, list):
        return PullRequest(error="unreadable gh output", checked_at=checked)
    if not items:
        return PullRequest(checked_at=checked)
    item = items[0]
    return PullRequest(
        number=item.get("number"),
        url=item.get("url"),
        state=item.get("state"),
        draft=bool(item.get("isDraft")),
        review=item.get("reviewDecision") or None,
        title=item.get("title"),
        merged_at=item.get("mergedAt"),
        checked_at=checked,
    )


def lookup(path: Path, branch: str) -> PullRequest:
    """Find the pull request whose head is `branch`, asking from inside the worktree so
    gh infers the repository from its `origin`."""
    try:
        result = subprocess.run(
            [gh_binary(), "pr", "list", "--head", branch, "--state", "all", "--limit", "1", "--json", FIELDS],
            cwd=str(path) if path.is_dir() else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=LOOKUP_TIMEOUT_SECONDS,
            env={**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"},
        )
    except subprocess.TimeoutExpired:
        return PullRequest(error=f"timed out after {LOOKUP_TIMEOUT_SECONDS}s", checked_at=now())
    except OSError as error:
        return PullRequest(error=str(error), checked_at=now())
    return parse_lookup(result.stdout, result.stderr, result.returncode)


@dataclass
class LookupResult:
    feature: Feature
    worktree: Worktree
    pr: PullRequest


def lookup_all(
    targets: Iterable[tuple[Feature, Worktree]],
    *,
    on_done: Callable[[LookupResult], None] | None = None,
    workers: int = 6,
) -> list[LookupResult]:
    """Look up every (feature, worktree) in parallel and store the result on the entry.
    Manifests are not saved here; callers save once per feature."""
    targets = list(targets)
    results: list[LookupResult] = []
    if not targets:
        return results

    def one(pair: tuple[Feature, Worktree]) -> LookupResult:
        feature, worktree = pair
        return LookupResult(feature, worktree, lookup(feature.path_of(worktree), worktree.branch))

    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
        for outcome in pool.map(one, targets):
            outcome.worktree.pr = outcome.pr.to_dict()
            results.append(outcome)
            if on_done:
                on_done(outcome)
    return results


def refresh(
    features: list[Feature], *, on_done: Callable[[LookupResult], None] | None = None
) -> list[LookupResult]:
    """Refresh the pull request of every worktree of the given features and save the
    manifests. Requires a working, logged-in gh (Abort otherwise)."""
    check_auth()
    targets = [
        (feature, worktree) for feature in features if feature.mutable for worktree in feature.worktrees
    ]
    results = lookup_all(targets, on_done=on_done)
    for feature in {result.feature.name: result.feature for result in results}.values():
        try:
            feature.save()
        except Exception:  # the cache is best effort; the board shows what it has
            pass
    return results


def last_checked(features: Iterable[Feature]) -> str | None:
    """Oldest `checked_at` across every worktree that has one, or None when never refreshed."""
    stamps = [
        worktree.pr.get("checked_at")
        for feature in features
        if feature.readable
        for worktree in feature.worktrees
        if worktree.pr and worktree.pr.get("checked_at")
    ]
    return min(stamps) if stamps else None
