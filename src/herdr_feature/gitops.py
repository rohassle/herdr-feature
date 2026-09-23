"""Thin git wrapper plus the parsers and decisions the commands need."""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .ui import Abort

FETCH_TIMEOUT_SECONDS = 60
NETWORK_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10",
}
COMMON_DEFAULTS = ("main", "master", "develop")


def git_binary() -> str:
    return shutil.which("git") or "/usr/bin/git"


class GitError(Abort):
    pass


def run(
    repo: Path | str,
    *args: str,
    timeout: float | None = None,
    network: bool = False,
) -> subprocess.CompletedProcess:
    env = {**os.environ, **NETWORK_ENV} if network else None
    return subprocess.run(
        [git_binary(), "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


def must(repo: Path | str, *args: str) -> str:
    result = run(repo, *args)
    if result.returncode != 0:
        raise GitError(f"git -C {repo} {' '.join(args)}\n{result.stderr.strip()}")
    return result.stdout.strip()


def ref_exists(repo: Path, ref: str) -> bool:
    return run(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def rev_parse(repo: Path, ref: str) -> str | None:
    result = run(repo, "rev-parse", "--verify", "--quiet", ref)
    return result.stdout.strip() if result.returncode == 0 else None


def has_remote(repo: Path, remote: str = "origin") -> bool:
    return run(repo, "remote", "get-url", remote).returncode == 0


# --- default branch -----------------------------------------------------------


@dataclass
class Base:
    """Where new branches start from in one repository."""

    branch: str            # e.g. "main"
    remote: str | None     # "origin", or None for a repository without a remote
    ref: str               # fully qualified ref to branch from

    @property
    def display(self) -> str:
        return f"{self.remote}/{self.branch}" if self.remote else f"{self.branch} (no remote)"


def _local_default(repo: Path) -> str | None:
    for name in COMMON_DEFAULTS:
        if ref_exists(repo, f"refs/heads/{name}"):
            return name
    configured = run(repo, "config", "init.defaultBranch").stdout.strip()
    if configured and ref_exists(repo, f"refs/heads/{configured}"):
        return configured
    head = run(repo, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    return head or None


def detect_base(repo: Path, *, allow_network: bool = True) -> Base:
    """Detect the default branch of `repo` (see plan: default-branch chain)."""
    if not has_remote(repo):
        local = _local_default(repo)
        if not local:
            raise GitError(f"{repo.name}: no remote and no recognisable default branch.")
        return Base(branch=local, remote=None, ref=f"refs/heads/{local}")

    head = run(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD").stdout.strip()
    if head.startswith("origin/"):
        name = head[len("origin/"):]
        return Base(branch=name, remote="origin", ref=f"refs/remotes/origin/{name}")

    if allow_network:
        probe = run(repo, "ls-remote", "--symref", "origin", "HEAD", timeout=20, network=True)
        if probe.returncode == 0:
            for line in probe.stdout.splitlines():
                if line.startswith("ref:") and line.rstrip().endswith("HEAD"):
                    name = line.split()[1].removeprefix("refs/heads/")
                    run(repo, "remote", "set-head", "origin", "--auto", timeout=20, network=True)
                    return Base(branch=name, remote="origin", ref=f"refs/remotes/origin/{name}")

    for name in COMMON_DEFAULTS:
        if ref_exists(repo, f"refs/remotes/origin/{name}"):
            return Base(branch=name, remote="origin", ref=f"refs/remotes/origin/{name}")
    local = _local_default(repo)
    if local and ref_exists(repo, f"refs/remotes/origin/{local}"):
        return Base(branch=local, remote="origin", ref=f"refs/remotes/origin/{local}")
    if local:
        return Base(branch=local, remote="origin", ref=f"refs/heads/{local}")
    raise GitError(
        f"{repo.name}: cannot tell which branch is the default.\n"
        f"Run: git -C {repo} remote set-head origin --auto"
    )


# --- fetch --------------------------------------------------------------------


@dataclass
class FetchResult:
    repo: Path
    ok: bool
    reason: str = ""


def classify_fetch_error(stderr: str) -> str:
    text = stderr.strip()
    lowered = text.lower()
    if "could not resolve host" in lowered or "connection timed out" in lowered or "network is unreachable" in lowered:
        return "network unreachable"
    if "permission denied (publickey)" in lowered or "authentication failed" in lowered:
        return "authentication failed"
    if "couldn't find remote ref" in lowered:
        return "default branch not on remote"
    if "repository not found" in lowered:
        return "repository not found"
    return text.splitlines()[-1] if text else "fetch failed"


def fetch(repo: Path, branch: str) -> FetchResult:
    try:
        result = run(
            repo, "fetch", "--quiet", "--no-tags", "origin", branch,
            timeout=FETCH_TIMEOUT_SECONDS, network=True,
        )
    except subprocess.TimeoutExpired:
        return FetchResult(repo, False, f"timed out after {FETCH_TIMEOUT_SECONDS}s")
    if result.returncode == 0:
        return FetchResult(repo, True)
    return FetchResult(repo, False, classify_fetch_error(result.stderr))


def fetch_all(
    targets: Iterable[tuple[Path, str]],
    *,
    on_done: Callable[[FetchResult], None] | None = None,
    workers: int = 6,
) -> list[FetchResult]:
    targets = list(targets)
    results: list[FetchResult] = []
    if not targets:
        return results
    with ThreadPoolExecutor(max_workers=min(workers, len(targets))) as pool:
        for outcome in pool.map(lambda pair: fetch(*pair), targets):
            results.append(outcome)
            if on_done:
                on_done(outcome)
    return results


# --- worktrees ----------------------------------------------------------------


@dataclass
class WorktreeInfo:
    path: str
    head: str | None = None
    branch: str | None = None       # short name, e.g. "main"
    bare: bool = False
    detached: bool = False
    prunable: bool = False
    locked: bool = False


def parse_worktree_list(text: str) -> list[WorktreeInfo]:
    entries: list[WorktreeInfo] = []
    current: WorktreeInfo | None = None
    for line in text.splitlines():
        if line.startswith("worktree "):
            current = WorktreeInfo(path=line[len("worktree "):])
            entries.append(current)
        elif current is None:
            continue
        elif line.startswith("HEAD "):
            current.head = line[len("HEAD "):]
        elif line.startswith("branch "):
            current.branch = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "bare":
            current.bare = True
        elif line == "detached":
            current.detached = True
        elif line.startswith("prunable"):
            current.prunable = True
        elif line.startswith("locked"):
            current.locked = True
    return entries


def worktrees(repo: Path) -> list[WorktreeInfo]:
    return parse_worktree_list(must(repo, "worktree", "list", "--porcelain"))


def checked_out_at(repo: Path, branch: str) -> str | None:
    for info in worktrees(repo):
        if info.branch == branch:
            return info.path
    return None


def is_registered(repo: Path, path: Path) -> bool:
    wanted = path.resolve()
    for info in worktrees(repo):
        try:
            if Path(info.path).resolve() == wanted:
                return True
        except OSError:
            continue
    return False


@dataclass
class BranchPlan:
    """How to create one worktree, decided in preflight."""

    branch: str
    source: str            # "new" | "origin" | "local"
    start: str             # commit-ish handed to `git worktree add`
    branch_created: bool


def plan_branch(repo: Path, branch: str, base: Base) -> BranchPlan:
    if ref_exists(repo, f"refs/heads/{branch}"):
        return BranchPlan(branch=branch, source="local", start=branch, branch_created=False)
    if base.remote and ref_exists(repo, f"refs/remotes/{base.remote}/{branch}"):
        return BranchPlan(
            branch=branch, source="origin", start=f"{base.remote}/{branch}", branch_created=True
        )
    return BranchPlan(branch=branch, source="new", start=base.ref, branch_created=True)


def worktree_add(repo: Path, path: Path, plan: BranchPlan) -> None:
    if plan.source == "new":
        args = ["worktree", "add", "--no-track", "-b", plan.branch, str(path), plan.start]
    elif plan.source == "origin":
        args = ["worktree", "add", "--track", "-b", plan.branch, str(path), plan.start]
    else:
        args = ["worktree", "add", str(path), plan.branch]
    result = run(repo, *args)
    if result.returncode != 0:
        detail = result.stderr.strip()
        hint = ""
        if "git-lfs" in detail or "filter" in detail and "lfs" in detail:
            hint = "\nThis repository needs git-lfs: brew install git-lfs"
        raise GitError(f"{repo.name}: git {' '.join(args)}\n{detail}{hint}")


def worktree_remove(repo: Path, path: Path) -> str | None:
    """Remove a worktree; returns a note when git had nothing to remove."""
    result = run(repo, "worktree", "remove", "--force", "--force", str(path))
    run(repo, "worktree", "prune")
    if result.returncode == 0:
        return None
    detail = result.stderr.strip()
    if "is not a working tree" in detail or "not a valid path" in detail or "No such file" in detail:
        return "worktree was already gone; pruned its registration"
    raise GitError(f"{repo.name}: git worktree remove {path}\n{detail}")


def branch_delete(repo: Path, branch: str) -> str | None:
    result = run(repo, "branch", "-D", branch)
    if result.returncode == 0:
        return None
    return result.stderr.strip()


def set_upstream(repo: Path, branch: str, remote: str) -> None:
    run(repo, "branch", f"--set-upstream-to={remote}/{branch}", branch)


def has_submodules(path: Path) -> bool:
    return (path / ".gitmodules").is_file()


# --- state --------------------------------------------------------------------


@dataclass
class State:
    kind: str       # missing | dirty | never-pushed | unpushed | clean | detached
    detail: str

    @property
    def is_dirty(self) -> bool:
        return self.kind == "dirty"

    @property
    def needs_care(self) -> bool:
        return self.kind in ("dirty", "unpushed", "never-pushed")


def worktree_state(path: Path) -> State:
    if not path.is_dir():
        return State("missing", "missing from disk")
    status = run(path, "status", "--porcelain")
    if status.returncode != 0:
        return State("missing", "not a git worktree")
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    if changes:
        return State("dirty", f"{len(changes)} uncommitted")
    branch = run(path, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
    if not branch:
        return State("detached", "detached HEAD")
    upstream = run(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode != 0:
        return State("never-pushed", "never pushed")
    ahead = run(path, "rev-list", "--count", "@{u}..HEAD").stdout.strip()
    if ahead and ahead != "0":
        return State("unpushed", f"{ahead} unpushed")
    return State("clean", "clean")


def recent_log(path: Path, count: int = 40) -> str:
    result = run(path, "log", "--oneline", "--decorate", "--color=always", f"-n{count}")
    return result.stdout if result.returncode == 0 else result.stderr


def current_branch(path: Path) -> str:
    return run(path, "symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip() or "(detached)"
