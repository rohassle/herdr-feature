"""Shared building blocks: pickers, preflight, execution with rollback."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .. import gitops, herdr, manifest, names, prs, ui
from ..config import Config
from ..discovery import Repo, scan
from ..manifest import Thread, Worktree

# --- lookups ------------------------------------------------------------------


def load_threads(config: Config) -> list[Thread]:
    return manifest.discover(config.threads_directory)


def status_word(
    thread: Thread, live: dict[str, str], repo_live: dict[str, dict[str, str]] | None = None
) -> str:
    if not thread.readable:
        return "[corrupt]"
    if thread.version > manifest.VERSION:
        return "[newer version]"
    if thread.status == manifest.STATUS_CREATING:
        return "[interrupted]"
    if thread.name in live or (repo_live or {}).get(thread.name):
        return "open"
    return "closed"


def choose_thread(
    threads: list[Thread],
    live: dict[str, str],
    *,
    prompt_text: str,
    header: str,
    only_mutable: bool = False,
    repo_live: dict[str, dict[str, str]] | None = None,
) -> Thread:
    candidates = [f for f in threads if (f.mutable if only_mutable else True)]
    if not candidates:
        raise ui.Abort("There are no threads to choose from. Create one with 'new'.")
    rows = []
    for thread in candidates:
        count = f"{len(thread.worktrees)} worktree{'s' if len(thread.worktrees) != 1 else ''}"
        rows.append(
            ui.encode_row(
                str(thread.root),
                f"{thread.name:<32}",
                f"{status_word(thread, live, repo_live):<10}",
                count if thread.readable else thread.error or "",
            )
        )
    keys = ui.pick(
        rows,
        prompt_text=prompt_text,
        header=header,
        preview=ui.preview_command("preview-thread"),
    )
    chosen = keys[0]
    for thread in candidates:
        if str(thread.root) == chosen:
            return thread
    raise ui.Cancelled()


def pick_repos(config: Config, thread: Thread | None = None) -> list[Repo]:
    repos = scan(config)
    if not repos:
        scanned = ", ".join(str(folder) for folder in config.repo_directories) or "(none)"
        raise ui.Abort(f"No git repositories found. Scanned: {scanned}")
    present = {wt.repo_path for wt in thread.worktrees} if thread else set()
    rows = []
    for repo in repos:
        marker = "●" if str(repo.path) in present else " "
        rows.append(ui.encode_row(str(repo.path), f"{marker} {repo.name:<40}", str(repo.path)))
    header = "Tab: mark   Enter: confirm   Esc: cancel"
    if thread:
        header += "   ● already in this thread (adds a second worktree)"
    keys = ui.pick(
        rows,
        prompt_text="repos> ",
        header=header,
        multi=True,
        preview=ui.preview_command("preview-repo"),
    )
    by_path = {str(repo.path): repo for repo in repos}
    return [by_path[key] for key in keys if key in by_path]


# --- preflight ----------------------------------------------------------------


@dataclass
class Request:
    repo: Repo
    suffix: str | None = None


@dataclass
class Planned:
    request: Request
    folder: str
    path: Path
    branch: str
    base: gitops.Base
    plan: gitops.BranchPlan
    base_commit: str | None

    @property
    def repo(self) -> Repo:
        return self.request.repo

    def describe(self) -> str:
        if self.plan.source == "new":
            how = f"new branch from {self.base.display}"
        elif self.plan.source == "origin":
            how = f"tracks existing {self.plan.start}"
        else:
            how = "reuses existing local branch"
        return f"{self.repo.name:<32} {self.folder:<40} {self.branch}  ({how})"


def _validate_target(
    thread: Thread,
    request: Request,
    claims: dict[tuple[str, str], str],
) -> tuple[str, Path, str]:
    folder = names.folder_for(request.repo.name, request.suffix)
    path = thread.root / folder
    branch = names.branch_for(thread.branch_prefix, thread.name, request.suffix)
    if folder in thread.folders():
        raise ui.Abort(f"{thread.name} already has a worktree folder named {folder}.")
    if path.exists() or path.is_symlink():
        raise ui.Abort(f"{path} already exists on disk.")
    problem = names.check_ref_format(branch)
    if problem:
        raise ui.Abort(f"branch name {branch!r} is not valid:\n{problem}")
    owner = claims.get((str(request.repo.path), branch))
    if owner:
        raise ui.Abort(
            f"branch {branch!r} in {request.repo.name} is already used by thread {owner!r}.\n"
            "Pick a different suffix, or add that thread's worktree instead."
        )
    return folder, path, branch


def preflight(
    config: Config,
    thread: Thread,
    requests: list[Request],
    all_threads: list[Thread],
) -> list[Planned]:
    """Check everything before any mutation. Interactive only for fetch failures and
    branches that are checked out elsewhere."""
    claims = manifest.branch_claims(all_threads)
    for wt in thread.worktrees:
        claims.setdefault((wt.repo_path, wt.branch), thread.name)

    # Names, paths and branches first: cheap, and they catch the common mistakes.
    targets: dict[int, tuple[str, Path, str]] = {}
    seen_folders: set[str] = set()
    for index, request in enumerate(requests):
        folder, path, branch = _validate_target(thread, request, claims)
        if folder in seen_folders:
            raise ui.Abort(f"{folder} was requested twice.")
        seen_folders.add(folder)
        targets[index] = (folder, path, branch)

    # Default branches, one detection per repository.
    ui.heading("Checking repositories")
    bases: dict[str, gitops.Base] = {}
    for request in requests:
        key = str(request.repo.path)
        if key in bases:
            continue
        bases[key] = gitops.detect_base(request.repo.path)
        ui.step(f"{request.repo.name}: default branch {bases[key].display}")

    # Fetch every remote default branch in parallel, then decide about failures once.
    fetch_targets = [(Path(key), base.branch) for key, base in bases.items() if base.remote]
    if fetch_targets:
        ui.heading(f"Fetching {len(fetch_targets)} remote{'s' if len(fetch_targets) != 1 else ''}")

        def report(result: gitops.FetchResult) -> None:
            if result.ok:
                ui.ok(f"{result.repo.name}")
            else:
                ui.fail(f"{result.repo.name}: {result.reason}")

        results = gitops.fetch_all(fetch_targets, on_done=report)
        failures = [result for result in results if not result.ok]
        if failures:
            print(file=ui.OUT)
            answer = ui.choose(
                f"{len(failures)} fetch{'es' if len(failures) != 1 else ''} failed. "
                "Continue from the last fetched state, or abort everything?",
                {"c": "continue with local refs", "a": "abort"},
                default="a",
                key="fetch-failure",
            )
            if answer != "c":
                raise ui.Abort("Aborted; nothing was created.")
            for result in failures:
                base = bases[str(result.repo)]
                if not gitops.ref_exists(result.repo, base.ref):
                    raise ui.Abort(
                        f"{result.repo.name}: {base.ref} does not exist locally, so there is "
                        "nothing to branch from without a fetch."
                    )

    # Decide how each branch is created; resolve branches checked out elsewhere.
    planned: list[Planned] = []
    for index, request in enumerate(requests):
        folder, path, branch = targets[index]
        base = bases[str(request.repo.path)]
        while True:
            plan = gitops.plan_branch(request.repo.path, branch, base)
            if plan.source != "local":
                break
            elsewhere = gitops.checked_out_at(request.repo.path, branch)
            if not elsewhere:
                break
            ui.warn(
                f"{request.repo.name}: branch {branch!r} is already checked out at\n    {elsewhere}\n"
                "  Git allows one checkout per branch. Give this worktree a suffix to use a\n"
                "  different branch, or leave empty to cancel."
            )
            suffix = ui.prompt(
                "suffix",
                validator=lambda value: names.validate_name(value, "suffix"),
                key=f"suffix:{request.repo.name}",
            )
            request.suffix = suffix
            folder, path, branch = _validate_target(thread, request, claims)
            if folder in seen_folders - {targets[index][0]}:
                raise ui.Abort(f"{folder} was requested twice.")
            seen_folders.discard(targets[index][0])
            seen_folders.add(folder)
            targets[index] = (folder, path, branch)
        planned.append(
            Planned(
                request=request,
                folder=folder,
                path=path,
                branch=branch,
                base=base,
                plan=plan,
                base_commit=gitops.rev_parse(request.repo.path, plan.start),
            )
        )
    return planned


def show_plan(planned: list[Planned]) -> None:
    ui.heading("Plan")
    for item in planned:
        ui.step(item.describe())
    print(file=ui.OUT)


# --- execution ----------------------------------------------------------------


def execute(thread: Thread, planned: list[Planned], *, is_new: bool) -> list[Worktree]:
    """Create every worktree, recording each in the manifest as it lands. Any failure
    rolls back this run's worktrees and branches (never pre-existing ones)."""
    thread.status = manifest.STATUS_CREATING
    thread.save()
    created: list[Worktree] = []
    ui.heading("Creating worktrees")
    try:
        for item in planned:
            ui.step(f"{item.repo.name}: {item.folder} on {item.branch}")
            gitops.worktree_add(item.repo.path, item.path, item.plan)
            worktree = Worktree(
                repo_name=item.repo.name,
                repo_path=str(item.repo.path),
                folder=item.folder,
                suffix=item.request.suffix,
                branch=item.branch,
                branch_created=item.plan.branch_created,
                branch_source=item.plan.source,
                base_ref=item.base.ref,
                base_commit=item.base_commit,
                remote=item.base.remote,
            )
            thread.worktrees.append(worktree)
            created.append(worktree)
            thread.save()
            if item.plan.source == "local" and item.base.remote:
                if gitops.ref_exists(item.repo.path, f"refs/remotes/{item.base.remote}/{item.branch}"):
                    upstream = gitops.run(item.path, "rev-parse", "--abbrev-ref", "@{u}")
                    if upstream.returncode != 0:
                        gitops.set_upstream(item.repo.path, item.branch, item.base.remote)
            if gitops.has_submodules(item.path):
                ui.warn(f"{item.repo.name} has submodules; they are not initialised in the new worktree.")
        thread.status = manifest.STATUS_READY
        thread.save()
    except BaseException:
        rollback(thread, created, is_new=is_new)
        raise
    return created


def rollback(thread: Thread, created: list[Worktree], *, is_new: bool) -> None:
    if created or is_new:
        ui.heading("Rolling back")
    for worktree in reversed(created):
        repo = Path(worktree.repo_path)
        try:
            gitops.worktree_remove(repo, thread.path_of(worktree))
        except ui.Abort as error:
            ui.warn(str(error))
        if worktree.branch_created:
            gitops.branch_delete(repo, worktree.branch)
        ui.step(f"removed {worktree.folder}")
        if worktree in thread.worktrees:
            thread.worktrees.remove(worktree)
    if is_new:
        shutil.rmtree(thread.root, ignore_errors=True)
        ui.step(f"removed {thread.root}")
    else:
        thread.status = manifest.STATUS_READY
        try:
            thread.save()
        except Exception as error:  # the manifest is best-effort during rollback
            ui.warn(f"could not update manifest: {error}")


def cleanup_interrupted(thread: Thread) -> None:
    """A manifest left in `creating` state: undo whatever it lists."""
    ui.warn(
        f"{thread.name} was interrupted while being created; it lists {len(thread.worktrees)} worktree(s)."
    )
    if not ui.confirm("Remove them and start over?", default=False, key="cleanup-interrupted"):
        raise ui.Cancelled()
    rollback(thread, list(thread.worktrees), is_new=True)


def format_state_table(thread: Thread) -> list[str]:
    lines = []
    for worktree in thread.worktrees:
        state = gitops.worktree_state(thread.path_of(worktree))
        lines.append(f"{worktree.folder:<40} {worktree.branch:<40} {state.detail}")
    return lines


def live_map(threads: list[Thread]) -> dict[str, str]:
    try:
        return herdr.map_live(threads)
    except herdr.HerdrError as error:
        ui.warn(f"could not query Herdr for open workspaces: {error}")
        return {}


def repo_live_map(threads: list[Thread]) -> dict[str, dict[str, str]]:
    try:
        return herdr.map_repo_live(threads)
    except herdr.HerdrError as error:
        ui.warn(f"could not query Herdr for open worktree workspaces: {error}")
        return {}


def report_opened(thread: Thread, opened: herdr.Opened) -> None:
    if opened.workspace_id:
        ui.ok(f"workspace {opened.workspace_id} ({thread.name}) at {thread.root}")
    for folder, workspace_id in opened.repo_workspaces.items():
        ui.ok(f"workspace {workspace_id} ({folder}) nested under its repository")
    for failure in opened.failures:
        ui.warn(f"could not open a worktree workspace for {failure}")


def open_workspaces(
    config: Config,
    thread: Thread,
    *,
    focus: bool,
    workspace_id: str | None = None,
    only: list[Worktree] | None = None,
) -> herdr.Opened:
    """Open the configured workspaces for a thread and record the thread workspace hint."""
    opened = herdr.open_thread(config, thread, focus=focus, workspace_id=workspace_id, only=only)
    if opened.workspace_id and thread.mutable:
        thread.save()
    return opened


# --- progress: done means the pull request is merged (ADR 0008) ---------------------

PROGRESS_MERGED = "merged"
PROGRESS_OPEN_PR = "open-pr"
PROGRESS_CLOSED_PR = "closed-pr"
PROGRESS_NO_PR = "no-pr"
PROGRESS_UNKNOWN = "unknown"


@dataclass
class Progress:
    kind: str
    detail: str
    pr: prs.PullRequest | None = None

    @property
    def merged(self) -> bool:
        return self.kind == PROGRESS_MERGED


def worktree_progress(worktree: Worktree) -> Progress:
    """Where one worktree stands on its way to merged, from the cached lookup."""
    pr = prs.from_dict(worktree.pr)
    if pr is None:
        return Progress(PROGRESS_UNKNOWN, "not refreshed")
    if pr.error:
        return Progress(PROGRESS_UNKNOWN, pr.error, pr)
    if pr.number is None:
        return Progress(PROGRESS_NO_PR, "no PR", pr)
    label = f"#{pr.number}"
    if pr.state == prs.STATE_MERGED:
        return Progress(PROGRESS_MERGED, f"{label} merged", pr)
    if pr.state == prs.STATE_CLOSED:
        return Progress(PROGRESS_CLOSED_PR, f"{label} closed", pr)
    review = {
        "APPROVED": "approved",
        "CHANGES_REQUESTED": "changes requested",
        "REVIEW_REQUIRED": "review pending",
    }.get(pr.review or "", "")
    detail = f"{label} " + ("draft" if pr.draft else "open") + (f" · {review}" if review else "")
    return Progress(PROGRESS_OPEN_PR, detail, pr)


@dataclass
class ThreadProgress:
    merged: int
    total: int
    unknown: int

    @property
    def done(self) -> bool:
        return self.total > 0 and self.merged == self.total

    def bar(self, width: int = 12) -> str:
        if self.total == 0:
            return "░" * width + " 0/0"
        filled = round(width * self.merged / self.total)
        cells = ["█"] * filled + ["░"] * (width - filled)
        # Unknown entries eat the trailing cells as '?', so a never-refreshed thread reads as such.
        pending = round(width * self.unknown / self.total)
        for index in range(max(0, width - pending), width):
            if cells[index] == "░":
                cells[index] = "?"
        return "".join(cells) + f" {self.merged}/{self.total}"


def thread_progress(thread: Thread) -> ThreadProgress:
    if not thread.readable:
        return ThreadProgress(0, 0, 0)
    kinds = [worktree_progress(wt).kind for wt in thread.worktrees]
    return ThreadProgress(
        merged=sum(kind == PROGRESS_MERGED for kind in kinds),
        total=len(kinds),
        unknown=sum(kind == PROGRESS_UNKNOWN for kind in kinds),
    )


def age(stamp: str | None, *, now: datetime | None = None) -> str:
    """'never', '3m ago', '2h ago', '5d ago' for a manifest timestamp."""
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    seconds = int(((now or datetime.now(UTC)) - then).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def refresh_progress(threads: list[Thread]) -> None:
    """Interactive refresh: look up every pull request, report each, fetch remotes."""
    ui.heading("Refreshing pull requests")

    def report(result: prs.LookupResult) -> None:
        progress = worktree_progress(result.worktree)
        line = f"{result.thread.name}/{result.worktree.folder}: {progress.detail}"
        (ui.fail if progress.kind == PROGRESS_UNKNOWN else ui.ok)(line)

    prs.refresh(threads, on_done=report)
    targets = {
        (wt.repo_path, wt.base_ref.rsplit("/", 1)[-1])
        for thread in threads
        if thread.mutable
        for wt in thread.worktrees
        if wt.remote and wt.base_ref
    }
    if targets:
        ui.heading(f"Fetching {len(targets)} remote{'s' if len(targets) != 1 else ''}")
        gitops.fetch_all(
            [(Path(repo), branch) for repo, branch in sorted(targets)],
            on_done=lambda r: (ui.ok if r.ok else ui.fail)(
                f"{r.repo.name}" + ("" if r.ok else f": {r.reason}")
            ),
        )


# --- closing workspaces (never deletes anything, ADR 0002) --------------------------


def thread_workspace_ids(
    thread: Thread, live: dict[str, str], repo_live: dict[str, dict[str, str]]
) -> list[str]:
    """Every open workspace of a thread: nested worktree workspaces first, then the
    thread workspace. Closing in this order never needs Herdr's --group."""
    nested = list(repo_live.get(thread.name, {}).values())
    thread_id = live.get(thread.name)
    return nested + ([thread_id] if thread_id else [])


def close_thread_workspaces(
    thread: Thread, live: dict[str, str], repo_live: dict[str, dict[str, str]]
) -> list[str]:
    closed = []
    for workspace_id in thread_workspace_ids(thread, live, repo_live):
        if herdr.close_workspace(workspace_id):
            closed.append(workspace_id)
    return closed
