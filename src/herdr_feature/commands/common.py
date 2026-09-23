"""Shared building blocks: pickers, preflight, execution with rollback."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from .. import gitops, herdr, manifest, names, ui
from ..config import Config
from ..discovery import Repo, scan
from ..manifest import Feature, Worktree


# --- lookups ------------------------------------------------------------------


def load_features(config: Config) -> list[Feature]:
    return manifest.discover(config.features_directory)


def status_word(feature: Feature, live: dict[str, str]) -> str:
    if not feature.readable:
        return "[corrupt]"
    if feature.version > manifest.VERSION:
        return "[newer version]"
    if feature.status == manifest.STATUS_CREATING:
        return "[interrupted]"
    return "open" if feature.name in live else "closed"


def choose_feature(
    features: list[Feature],
    live: dict[str, str],
    *,
    prompt_text: str,
    header: str,
    only_mutable: bool = False,
) -> Feature:
    candidates = [f for f in features if (f.mutable if only_mutable else True)]
    if not candidates:
        raise ui.Abort("There are no features to choose from. Create one with 'new'.")
    rows = []
    for feature in candidates:
        count = f"{len(feature.worktrees)} worktree{'s' if len(feature.worktrees) != 1 else ''}"
        rows.append(
            ui.encode_row(
                str(feature.root),
                f"{feature.name:<32}",
                f"{status_word(feature, live):<10}",
                count if feature.readable else feature.error or "",
            )
        )
    keys = ui.pick(
        rows,
        prompt_text=prompt_text,
        header=header,
        preview=ui.preview_command("preview-feature"),
    )
    chosen = keys[0]
    for feature in candidates:
        if str(feature.root) == chosen:
            return feature
    raise ui.Cancelled()


def pick_repos(config: Config, feature: Feature | None = None) -> list[Repo]:
    repos = scan(config)
    if not repos:
        scanned = ", ".join(str(folder) for folder in config.repo_directories) or "(none)"
        raise ui.Abort(f"No git repositories found. Scanned: {scanned}")
    present = {wt.repo_path for wt in feature.worktrees} if feature else set()
    rows = []
    for repo in repos:
        marker = "●" if str(repo.path) in present else " "
        rows.append(ui.encode_row(str(repo.path), f"{marker} {repo.name:<40}", str(repo.path)))
    header = "Tab: mark   Enter: confirm   Esc: cancel"
    if feature:
        header += "   ● already in this feature (adds a second worktree)"
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
    feature: Feature,
    request: Request,
    claims: dict[tuple[str, str], str],
) -> tuple[str, Path, str]:
    folder = names.folder_for(request.repo.name, request.suffix)
    path = feature.root / folder
    branch = names.branch_for(feature.branch_prefix, feature.name, request.suffix)
    if folder in feature.folders():
        raise ui.Abort(f"{feature.name} already has a worktree folder named {folder}.")
    if path.exists() or path.is_symlink():
        raise ui.Abort(f"{path} already exists on disk.")
    problem = names.check_ref_format(branch)
    if problem:
        raise ui.Abort(f"branch name {branch!r} is not valid:\n{problem}")
    owner = claims.get((str(request.repo.path), branch))
    if owner:
        raise ui.Abort(
            f"branch {branch!r} in {request.repo.name} is already used by feature {owner!r}.\n"
            "Pick a different suffix, or add that feature's worktree instead."
        )
    return folder, path, branch


def preflight(
    config: Config,
    feature: Feature,
    requests: list[Request],
    all_features: list[Feature],
) -> list[Planned]:
    """Check everything before any mutation. Interactive only for fetch failures and
    branches that are checked out elsewhere."""
    claims = manifest.branch_claims(all_features)
    for wt in feature.worktrees:
        claims.setdefault((wt.repo_path, wt.branch), feature.name)

    # Names, paths and branches first: cheap, and they catch the common mistakes.
    targets: dict[int, tuple[str, Path, str]] = {}
    seen_folders: set[str] = set()
    for index, request in enumerate(requests):
        folder, path, branch = _validate_target(feature, request, claims)
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
    fetch_targets = [
        (Path(key), base.branch) for key, base in bases.items() if base.remote
    ]
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
            folder, path, branch = _validate_target(feature, request, claims)
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


def execute(feature: Feature, planned: list[Planned], *, is_new: bool) -> list[Worktree]:
    """Create every worktree, recording each in the manifest as it lands. Any failure
    rolls back this run's worktrees and branches (never pre-existing ones)."""
    feature.status = manifest.STATUS_CREATING
    feature.save()
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
            feature.worktrees.append(worktree)
            created.append(worktree)
            feature.save()
            if item.plan.source == "local" and item.base.remote:
                if gitops.ref_exists(item.repo.path, f"refs/remotes/{item.base.remote}/{item.branch}"):
                    upstream = gitops.run(item.path, "rev-parse", "--abbrev-ref", "@{u}")
                    if upstream.returncode != 0:
                        gitops.set_upstream(item.repo.path, item.branch, item.base.remote)
            if gitops.has_submodules(item.path):
                ui.warn(f"{item.repo.name} has submodules; they are not initialised in the new worktree.")
        feature.status = manifest.STATUS_READY
        feature.save()
    except BaseException:
        rollback(feature, created, is_new=is_new)
        raise
    return created


def rollback(feature: Feature, created: list[Worktree], *, is_new: bool) -> None:
    if created or is_new:
        ui.heading("Rolling back")
    for worktree in reversed(created):
        repo = Path(worktree.repo_path)
        try:
            gitops.worktree_remove(repo, feature.path_of(worktree))
        except ui.Abort as error:
            ui.warn(str(error))
        if worktree.branch_created:
            gitops.branch_delete(repo, worktree.branch)
        ui.step(f"removed {worktree.folder}")
        if worktree in feature.worktrees:
            feature.worktrees.remove(worktree)
    if is_new:
        shutil.rmtree(feature.root, ignore_errors=True)
        ui.step(f"removed {feature.root}")
    else:
        feature.status = manifest.STATUS_READY
        try:
            feature.save()
        except Exception as error:  # the manifest is best-effort during rollback
            ui.warn(f"could not update manifest: {error}")


def cleanup_interrupted(feature: Feature) -> None:
    """A manifest left in `creating` state: undo whatever it lists."""
    ui.warn(
        f"{feature.name} was interrupted while being created; it lists "
        f"{len(feature.worktrees)} worktree(s)."
    )
    if not ui.confirm("Remove them and start over?", default=False, key="cleanup-interrupted"):
        raise ui.Cancelled()
    rollback(feature, list(feature.worktrees), is_new=True)


def format_state_table(feature: Feature) -> list[str]:
    lines = []
    for worktree in feature.worktrees:
        state = gitops.worktree_state(feature.path_of(worktree))
        lines.append(f"{worktree.folder:<40} {worktree.branch:<40} {state.detail}")
    return lines


def live_map(features: list[Feature]) -> dict[str, str]:
    try:
        return herdr.map_live(features)
    except herdr.HerdrError as error:
        ui.warn(f"could not query Herdr for open workspaces: {error}")
        return {}
